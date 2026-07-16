from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
import io
from typing import Any

import pandas as pd

try:
    import akshare as ak
except Exception:  # pragma: no cover - optional data source
    ak = None


NEGATIVE_KEYWORDS = [
    "减持", "处罚", "问询", "立案", "诉讼", "仲裁", "亏损", "下滑", "终止",
    "风险", "退市", "冻结", "质押", "担保", "违约", "监管", "警示", "解禁",
]
POSITIVE_KEYWORDS = [
    "增持", "回购", "中标", "预增", "增长", "分红", "权益分派", "投资进展",
    "订单", "合作", "突破", "买入", "增持评级", "利润分配",
]


def _date_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return text[:10]
    return parsed.strftime("%Y-%m-%d")


def _classify(title: str, source_type: str = "") -> tuple[str, str]:
    title = str(title or "")
    hits_neg = [word for word in NEGATIVE_KEYWORDS if word in title]
    hits_pos = [word for word in POSITIVE_KEYWORDS if word in title]
    if hits_neg and not hits_pos:
        return "negative", "、".join(hits_neg[:3])
    if hits_pos and not hits_neg:
        return "positive", "、".join(hits_pos[:3])
    if hits_neg and hits_pos:
        return "mixed", "、".join((hits_neg + hits_pos)[:4])
    if "研报" in source_type:
        return "neutral", "研报观点"
    return "neutral", "未命中风险关键词"


def _row_value(row, names: list[str], default: Any = "") -> Any:
    for name in names:
        if name in row and not pd.isna(row[name]):
            return row[name]
    return default


def _with_quiet_akshare(fn, *args, **kwargs):
    buffer = io.StringIO()
    with redirect_stdout(buffer), redirect_stderr(buffer):
        return fn(*args, **kwargs)


def _fetch_disclosures(symbol: str, start: str, end: str, limit: int) -> list[dict]:
    if ak is None:
        return []
    try:
        df = _with_quiet_akshare(
            ak.stock_zh_a_disclosure_report_cninfo,
            symbol=symbol,
            market="沪深京",
            start_date=start,
            end_date=end,
        )
    except Exception:
        return []
    if df is None or df.empty:
        return []
    items: list[dict] = []
    for _, row in df.head(limit).iterrows():
        title = str(_row_value(row, ["公告标题", "标题"], ""))
        sentiment, reason = _classify(title, "公告")
        items.append({
            "source": "cninfo",
            "type": "announcement",
            "title": title,
            "published_at": _date_text(_row_value(row, ["公告时间", "公告日期"], "")),
            "url": str(_row_value(row, ["公告链接", "网址"], "")),
            "sentiment_hint": sentiment,
            "risk_hint": reason,
        })
    return items


def _fetch_research(symbol: str, limit: int) -> list[dict]:
    if ak is None:
        return []
    try:
        df = _with_quiet_akshare(ak.stock_research_report_em, symbol=symbol)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    items: list[dict] = []
    for _, row in df.head(limit).iterrows():
        title = str(_row_value(row, ["报告名称"], ""))
        rating = str(_row_value(row, ["东财评级"], ""))
        sentiment, reason = _classify(f"{title} {rating}", "研报")
        items.append({
            "source": "eastmoney",
            "type": "research",
            "title": title,
            "published_at": _date_text(_row_value(row, ["日期"], "")),
            "url": str(_row_value(row, ["报告PDF链接"], "")),
            "sentiment_hint": sentiment,
            "risk_hint": reason or rating,
            "rating": rating,
            "institution": str(_row_value(row, ["机构"], "")),
            "industry": str(_row_value(row, ["行业"], "")),
        })
    return items


def _fetch_news(symbol: str, limit: int) -> list[dict]:
    if ak is None:
        return []
    try:
        df = _with_quiet_akshare(ak.stock_news_em, symbol=symbol)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    items: list[dict] = []
    for _, row in df.head(limit).iterrows():
        title = str(_row_value(row, ["新闻标题", "标题", "title"], ""))
        sentiment, reason = _classify(title, "新闻")
        items.append({
            "source": "eastmoney",
            "type": "news",
            "title": title,
            "published_at": _date_text(_row_value(row, ["发布时间", "时间", "date"], "")),
            "url": str(_row_value(row, ["新闻链接", "链接", "url"], "")),
            "sentiment_hint": sentiment,
            "risk_hint": reason,
        })
    return items


def fetch_symbol_messages(symbol: str, name: str = "", days: int = 45, limit_each: int = 5) -> dict:
    today = datetime.now().date()
    start = (today - timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    items = (
        _fetch_disclosures(symbol, start, end, limit_each)
        + _fetch_research(symbol, limit_each)
        + _fetch_news(symbol, limit_each)
    )
    items = sorted(
        items,
        key=lambda item: item.get("published_at") or "",
        reverse=True,
    )[: limit_each * 3]
    negative_count = sum(1 for item in items if item.get("sentiment_hint") in {"negative", "mixed"})
    positive_count = sum(1 for item in items if item.get("sentiment_hint") == "positive")
    risk_level = "high" if negative_count >= 2 else "medium" if negative_count == 1 else "low"
    return {
        "symbol": symbol,
        "name": name or symbol,
        "window_days": days,
        "sources": sorted({item["source"] for item in items}),
        "risk_hint": risk_level,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "items": items,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def fetch_messages_for_symbols(symbols: list[dict], days: int = 45, limit_each: int = 5, timeout: int = 45) -> dict[str, dict]:
    unique: dict[str, str] = {}
    for item in symbols:
        symbol = str(item.get("symbol") or "").zfill(6)
        if symbol and symbol != "000000":
            unique.setdefault(symbol, str(item.get("name") or symbol))
    if not unique:
        return {}
    results: dict[str, dict] = {}
    executor = ThreadPoolExecutor(max_workers=min(4, len(unique)), thread_name_prefix="message-fetch")
    futures = {
        executor.submit(fetch_symbol_messages, symbol, name, days, limit_each): symbol
        for symbol, name in unique.items()
    }
    done, pending = wait(futures, timeout=timeout)
    for future in pending:
        future.cancel()
        symbol = futures[future]
        results[symbol] = {
            "symbol": symbol,
            "name": unique.get(symbol, symbol),
            "window_days": days,
            "sources": [],
            "risk_hint": "unknown",
            "items": [],
            "error": "message fetch timeout",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    for future in done:
        symbol = futures[future]
        try:
            results[symbol] = future.result()
        except Exception as exc:
            results[symbol] = {
                "symbol": symbol,
                "name": unique.get(symbol, symbol),
                "window_days": days,
                "sources": [],
                "risk_hint": "unknown",
                "items": [],
                "error": str(exc)[:160],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
    executor.shutdown(wait=False, cancel_futures=True)
    return results
