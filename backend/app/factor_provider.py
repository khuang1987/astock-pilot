from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import io
import re
from typing import Any

import pandas as pd

from .message_provider import fetch_messages_for_symbols

try:
    import akshare as ak
except Exception:  # pragma: no cover - optional data source
    ak = None


def _quiet_call(fn, *args, **kwargs):
    buffer = io.StringIO()
    with redirect_stdout(buffer), redirect_stderr(buffer):
        return fn(*args, **kwargs)


def _number(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier = 100_000_000.0
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10_000.0
        text = text[:-1]
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text) * multiplier
    except Exception:
        return default


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(min(high, max(low, float(value))), 2)


def _rank_score(rank: int, total: int) -> float:
    if total <= 1 or rank <= 0:
        return 50.0
    return _clip(100.0 * (1.0 - (rank - 1) / max(total - 1, 1)))


def _fetch_stock_fund_flow() -> dict[str, dict]:
    if ak is None:
        return {}
    try:
        df = _quiet_call(ak.stock_fund_flow_individual, symbol="即时")
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    total = len(df)
    result: dict[str, dict] = {}
    for idx, row in df.reset_index(drop=True).iterrows():
        symbol = str(row.get("股票代码", "")).zfill(6)
        if not symbol or symbol == "000000":
            continue
        net = _number(row.get("净额"))
        amount = _number(row.get("成交额"))
        turnover = _number(row.get("换手率"))
        pct_chg = _number(row.get("涨跌幅"))
        net_ratio = net / amount * 100 if amount else 0.0
        score = 50.0
        score += max(-28.0, min(28.0, net_ratio * 7.0))
        score += max(-12.0, min(12.0, pct_chg * 2.0))
        if 1.0 <= turnover <= 12.0:
            score += 8.0
        if net > 0:
            score += 8.0
        score = _clip(score)
        result[symbol] = {
            "fund_flow_score": score,
            "fund_rank_score": _rank_score(idx + 1, total),
            "fund_net_amount": round(net, 2),
            "fund_net_ratio": round(net_ratio, 2),
            "fund_turnover": round(turnover, 2),
            "fund_pct_chg": round(pct_chg, 2),
            "fund_source": "ths_stock_fund_flow",
        }
    return result


def _fetch_board_heat(fn_name: str, label_name: str) -> dict:
    if ak is None:
        return {"score": 50.0, "top": [], "source": label_name}
    try:
        df = _quiet_call(getattr(ak, fn_name), symbol="即时")
    except Exception:
        return {"score": 50.0, "top": [], "source": label_name, "error": "fetch failed"}
    if df is None or df.empty:
        return {"score": 50.0, "top": [], "source": label_name}
    top = []
    positive = 0
    total = min(20, len(df))
    for _, row in df.head(total).iterrows():
        pct = _number(row.get("行业-涨跌幅"))
        net = _number(row.get("净额"))
        if pct > 0 and net > 0:
            positive += 1
        top.append({
            "name": str(row.get("行业") or ""),
            "pct_chg": round(pct, 2),
            "net_amount": round(net, 2),
            "leader": str(row.get("领涨股") or ""),
        })
    score = _clip(45 + positive * 2.2 + max(0, _number(df.iloc[0].get("行业-涨跌幅"))) * 4)
    return {"score": score, "top": top[:8], "source": label_name}


def message_risk_score_from_context(message: dict) -> tuple[float, str]:
    risk_hint = str(message.get("risk_hint") or "unknown")
    negative = int(message.get("negative_count") or 0)
    positive = int(message.get("positive_count") or 0)
    if risk_hint == "high" or negative >= 2:
        return 35.0, f"消息风险较高，负面/混合证据 {negative} 条"
    if risk_hint == "medium" or negative == 1:
        return 62.0, f"消息风险中等，负面/混合证据 {negative} 条"
    if positive >= 3:
        return 86.0, f"消息面偏正面，正面证据 {positive} 条"
    if message.get("items"):
        return 75.0, "消息面未见明显负面"
    return 58.0, "消息源不足，按不确定处理"


def _theme_for_symbol(symbol: str, message: dict, industry_heat: dict, concept_heat: dict) -> dict:
    items = message.get("items") or []
    industry = ""
    for item in items:
        industry = item.get("industry") or industry
        if industry:
            break
    titles = " ".join(str(item.get("title") or "") for item in items[:6])
    hot_words = ["机器人", "AI", "人工智能", "新型工业化", "减速器", "算力", "芯片", "新能源", "回购", "增持"]
    hits = [word for word in hot_words if word in titles]
    score = 50.0
    score += min(18.0, len(hits) * 6.0)
    score += max(0.0, float(industry_heat.get("score", 50.0)) - 50.0) * 0.25
    score += max(0.0, float(concept_heat.get("score", 50.0)) - 50.0) * 0.35
    if industry:
        score += 8.0
    return {
        "theme_score": _clip(score),
        "theme_industry": industry,
        "theme_hits": hits[:6],
        "industry_heat_score": industry_heat.get("score", 50.0),
        "concept_heat_score": concept_heat.get("score", 50.0),
    }


def build_factor_context(symbols: list[dict], timeout: int = 70, include_messages: bool = True) -> dict:
    unique: dict[str, str] = {}
    for item in symbols:
        symbol = str(item.get("symbol") or "").zfill(6)
        if symbol and symbol != "000000":
            unique.setdefault(symbol, str(item.get("name") or symbol))
    if not unique:
        return {"factors": {}, "market_factors": {}, "updated_at": datetime.now().isoformat(timespec="seconds")}

    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="factor-fetch")
    futures = {
        executor.submit(_fetch_stock_fund_flow): "funds",
        executor.submit(_fetch_board_heat, "stock_fund_flow_industry", "ths_industry_fund_flow"): "industry",
        executor.submit(_fetch_board_heat, "stock_fund_flow_concept", "ths_concept_fund_flow"): "concept",
    }
    if include_messages:
        futures[executor.submit(fetch_messages_for_symbols, [{"symbol": s, "name": n} for s, n in unique.items()], 45, 5)] = "messages"
    done, pending = wait(futures, timeout=timeout)
    for future in pending:
        future.cancel()
    results: dict[str, Any] = {}
    for future in done:
        key = futures[future]
        try:
            results[key] = future.result()
        except Exception as exc:
            results[key] = {"error": str(exc)[:160]}
    executor.shutdown(wait=False, cancel_futures=True)

    funds = results.get("funds") or {}
    messages = results.get("messages") or {}
    industry_heat = results.get("industry") or {"score": 50.0, "top": []}
    concept_heat = results.get("concept") or {"score": 50.0, "top": []}
    factors: dict[str, dict] = {}
    for symbol, name in unique.items():
        message = messages.get(symbol) or {"risk_hint": "unknown", "items": []}
        message_score, message_note = message_risk_score_from_context(message)
        theme = _theme_for_symbol(symbol, message, industry_heat, concept_heat)
        fund = funds.get(symbol) or {
            "fund_flow_score": 50.0,
            "fund_rank_score": 50.0,
            "fund_net_amount": 0.0,
            "fund_net_ratio": 0.0,
            "fund_turnover": 0.0,
            "fund_pct_chg": 0.0,
            "fund_source": "missing",
        }
        factors[symbol] = {
            "symbol": symbol,
            "name": name,
            **fund,
            **theme,
            "message_risk_score": message_score,
            "message_risk_note": message_note,
            "message_risk_hint": message.get("risk_hint", "unknown"),
            "message_negative_count": message.get("negative_count", 0),
            "message_positive_count": message.get("positive_count", 0),
            "message_items": (message.get("items") or [])[:8],
        }
    return {
        "factors": factors,
        "market_factors": {
            "industry_heat": industry_heat,
            "concept_heat": concept_heat,
        },
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
