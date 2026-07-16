from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError, wait
from datetime import datetime, timedelta

import pandas as pd

from .ai_service import call_ai_review, review_prompt
from .data_provider import fetch_daily_bars, fetch_realtime_quotes, load_stock_universe, normalize_symbol
from .db import get_conn, now_iso
from .factor_provider import build_factor_context, message_risk_score_from_context
from .message_provider import fetch_messages_for_symbols
from .schemas import AISettingsUpdate, BacktestRequest, SimStateUpdate, SyncRequest
from .strategy import build_historical_signals, build_latest_signals, simulate_backtest


_DATA_LOCK = threading.RLock()
_AI_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="astock-ai")
_WORKFLOW_AI_TIMEOUT_SECONDS = 140
_DAILY_FETCH_BATCH_TIMEOUT_SECONDS = 50


def _incomplete_daily_trade_date() -> str:
    now = datetime.now()
    close_cutoff = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return now.strftime("%Y-%m-%d") if now < close_cutoff else ""


def _merge_existing_daily_bars(symbol: str, df: pd.DataFrame, days: int) -> pd.DataFrame:
    if df.empty or len(df) >= min(days, 120):
        return df
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, open, high, low, close, volume, amount, pct_chg
            FROM daily_bars
            WHERE symbol=?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (symbol, days),
        ).fetchall()
    if not rows:
        return df
    existing = pd.DataFrame([dict(row) for row in rows])
    merged = pd.concat([existing, df], ignore_index=True)
    merged["trade_date"] = pd.to_datetime(merged["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        merged[col] = pd.to_numeric(merged[col], errors="coerce")
    return (
        merged.dropna(subset=["trade_date", "open", "high", "low", "close"])
        .drop_duplicates(subset=["trade_date"], keep="last")
        .sort_values("trade_date")
        .tail(days)
    )


def _apply_candidate_message_review(conn, trade_date: str | None, limit: int = 60) -> dict:
    if not trade_date:
        return {"updated": 0, "removed": 0, "message": "暂无候选交易日"}
    rows = conn.execute(
        """
        SELECT s.*, st.name
        FROM signals s
        JOIN stocks st ON st.symbol = s.symbol
        WHERE s.trade_date=?
        ORDER BY s.score DESC
        LIMIT ?
        """,
        (trade_date, limit),
    ).fetchall()
    if not rows:
        return {"updated": 0, "removed": 0, "message": "暂无候选信号"}
    messages = fetch_messages_for_symbols(
        [{"symbol": row["symbol"], "name": row["name"]} for row in rows],
        days=45,
        limit_each=5,
        timeout=55,
    )
    updated = 0
    removed = 0
    now = now_iso()
    for row in rows:
        symbol = row["symbol"]
        message = messages.get(symbol) or {}
        message_score, message_note = message_risk_score_from_context(message)
        scores = json.loads(row["strategy_scores"] or "{}")
        reasons = json.loads(row["reasons"] or "[]")
        tags = json.loads(row["strategy_tags"] or "[]")
        old_evidence = float(scores.get("evidence_score") or 0)
        market_multiplier = float(row["score"] or 0) / old_evidence if old_evidence else 1.0
        market_multiplier = max(0.0, min(1.08, market_multiplier))
        evidence_v15 = float(scores.get("evidence_score_v15") or old_evidence)
        fund_score = float(scores.get("fund_flow_score") or 50.0)
        theme_score = float(scores.get("theme_score") or 50.0)
        new_evidence = round(min(100.0, max(0.0, evidence_v15 * 0.72 + fund_score * 0.10 + theme_score * 0.08 + message_score * 0.10)), 2)
        new_score = round(min(100.0, new_evidence * market_multiplier), 2)
        if message_score <= 35:
            new_score = round(new_score * 0.82, 2)
        scores.update({
            "message_risk_score": round(message_score, 2),
            "message_negative_count": float(message.get("negative_count") or 0),
            "message_positive_count": float(message.get("positive_count") or 0),
            "message_risk_hint": message.get("risk_hint", "unknown"),
            "message_risk_note": message_note,
            "message_reviewed_at": now,
            "evidence_score": new_evidence,
        })
        if message_score <= 45:
            tags.append("消息风险")
            reasons.append(f"风险面：{message_note}")
        elif message_score >= 78:
            tags.append("消息健康")
            reasons.append(f"风险面：{message_note}")
        tags = list(dict.fromkeys(tags))
        reasons = list(dict.fromkeys(reasons))
        if new_score < 62:
            conn.execute("DELETE FROM signals WHERE symbol=? AND trade_date=?", (symbol, trade_date))
            removed += 1
            continue
        position_pct = min(float(row["position_pct"] or 0), 10.0 if new_score >= 88 else 8.0 if new_score >= 82 else 6.0)
        conn.execute(
            """
            UPDATE signals
            SET score=?, position_pct=?, reasons=?, strategy_tags=?, strategy_scores=?, created_at=?
            WHERE symbol=? AND trade_date=?
            """,
            (
                new_score,
                position_pct,
                json.dumps(reasons, ensure_ascii=False),
                json.dumps(tags, ensure_ascii=False),
                json.dumps(scores, ensure_ascii=False),
                now,
                symbol,
                trade_date,
            ),
        )
        updated += 1
    return {"updated": updated, "removed": removed, "message_symbols": len(messages), "updated_at": now}


def sync_data(req: SyncRequest) -> dict:
    errors: list[str] = []
    total_bars = 0
    signal_count = 0
    daily_status = {}
    incomplete_date = _incomplete_daily_trade_date()
    cleared_signal_dates: set[str] = set()

    if req.symbols:
        with _DATA_LOCK:
            with get_conn() as conn:
                universe = []
                for raw in req.symbols:
                    symbol = normalize_symbol(raw)
                    found = conn.execute("SELECT name FROM stocks WHERE symbol = ?", (symbol,)).fetchone()
                    universe.append((symbol, found["name"] if found else symbol))
    else:
        universe = load_stock_universe(req.symbol_limit)
        required: dict[str, str] = {symbol: name for symbol, name in universe}
        with _DATA_LOCK:
            with get_conn() as conn:
                for row in conn.execute("SELECT symbol, name FROM sim_positions WHERE status = 'open'").fetchall():
                    required.setdefault(row["symbol"], row["name"])
                for row in conn.execute("SELECT w.symbol, COALESCE(s.name, w.symbol) AS name FROM watchlist w LEFT JOIN stocks s ON s.symbol = w.symbol").fetchall():
                    required.setdefault(row["symbol"], row["name"])
        universe = list(required.items())
    requested_symbols = len(universe)
    quick_factor_context = build_factor_context(
        [{"symbol": symbol, "name": name} for symbol, name in universe],
        timeout=70,
        include_messages=False,
    )
    quick_factors_by_symbol = quick_factor_context.get("factors", {})

    with _DATA_LOCK:
        with get_conn() as conn:
            if incomplete_date:
                conn.execute("DELETE FROM daily_bars WHERE trade_date = ?", (incomplete_date,))
                conn.execute("DELETE FROM signals WHERE trade_date = ?", (incomplete_date,))

    batch_size = 12
    for start in range(0, len(universe), batch_size):
        batch = universe[start:start + batch_size]
        bars_by_symbol: dict[str, pd.DataFrame] = {}
        names_by_symbol: dict[str, str] = {}
        touched_dates: set[str] = set()

        executor = ThreadPoolExecutor(max_workers=min(6, len(batch)), thread_name_prefix="daily-fetch")
        future_map = {
            executor.submit(fetch_daily_bars, symbol, req.days): (symbol, name)
            for symbol, name in batch
        }
        done, pending = wait(future_map, timeout=_DAILY_FETCH_BATCH_TIMEOUT_SECONDS)
        for future in pending:
            symbol, _name = future_map[future]
            future.cancel()
            errors.append(f"{symbol}: daily fetch timeout")
        executor.shutdown(wait=False, cancel_futures=True)
        futures = done

        for future in futures:
            symbol, name = future_map[future]
            try:
                df = future.result()
                if incomplete_date and not df.empty:
                    df = df[df["trade_date"].astype(str) != incomplete_date].copy()
                if df.empty:
                    errors.append(f"{symbol}: no bars")
                    continue
                df = _merge_existing_daily_bars(symbol, df, req.days)
                bars_by_symbol[symbol] = df
                names_by_symbol[symbol] = name
                total_bars += len(df)
                touched_dates.add(str(df.iloc[-1]["trade_date"]))
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")

        if not bars_by_symbol:
            continue

        factors_by_symbol = {
            symbol: quick_factors_by_symbol.get(symbol, {})
            for symbol in bars_by_symbol
        }

        with _DATA_LOCK:
            with get_conn() as conn:
                for symbol, df in bars_by_symbol.items():
                    name = names_by_symbol.get(symbol, symbol)
                    conn.execute(
                        "INSERT OR REPLACE INTO stocks(symbol, name, market, updated_at) VALUES (?, ?, ?, ?)",
                        (symbol, name, "A", now_iso()),
                    )
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO daily_bars(symbol, trade_date, open, high, low, close, volume, amount, pct_chg)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                symbol,
                                row.trade_date,
                                float(row.open),
                                float(row.high),
                                float(row.low),
                                float(row.close),
                                float(row.volume),
                                float(row.amount),
                                float(row.pct_chg),
                            )
                            for row in df.itertuples(index=False)
                        ],
                    )
                for trade_date in touched_dates - cleared_signal_dates:
                    conn.execute("DELETE FROM signals WHERE trade_date = ?", (trade_date,))
                    cleared_signal_dates.add(trade_date)
                for signal in build_latest_signals(bars_by_symbol, factors_by_symbol):
                    conn.execute(
                        "INSERT OR REPLACE INTO stocks(symbol, name, market, updated_at) VALUES (?, ?, ?, ?)",
                        (signal.symbol, names_by_symbol.get(signal.symbol, signal.symbol), "A", now_iso()),
                    )
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO signals(
                            symbol, trade_date, score, trend_score, volume_score, risk_score,
                            entry_price, stop_loss, take_profit_1, take_profit_2, position_pct, reasons,
                            strategy_tags, strategy_scores, market_state, market_note, decision, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (*signal.to_db_tuple(), now_iso()),
                    )
                    signal_count += 1

    with _DATA_LOCK:
        with get_conn() as conn:
            refreshed = _refresh_open_positions_from_bars(conn)
            latest = conn.execute("SELECT MAX(trade_date) AS trade_date FROM daily_bars").fetchone()
            latest_date = latest["trade_date"] if latest else None
            candidate_message_review = _apply_candidate_message_review(conn, latest_date)
            latest_symbols = 0
            if latest_date:
                row = conn.execute(
                    "SELECT COUNT(DISTINCT symbol) AS count FROM daily_bars WHERE trade_date = ?",
                    (latest_date,),
                ).fetchone()
                latest_symbols = int(row["count"] or 0) if row else 0
                signal_count = int(conn.execute("SELECT COUNT(*) AS c FROM signals WHERE trade_date=?", (latest_date,)).fetchone()["c"] or 0)
            total_symbols_row = conn.execute("SELECT COUNT(DISTINCT symbol) AS count FROM daily_bars").fetchone()
            daily_status = {
                "latest_date": latest_date,
                "latest_symbols": latest_symbols,
                "total_symbols": int(total_symbols_row["count"] or 0) if total_symbols_row else 0,
                "requested_symbols": requested_symbols,
                "failed_symbols": len(errors),
                "symbol_limit": req.symbol_limit,
                "mode": "symbols" if req.symbols else "limited_universe",
                "is_full_market": not req.symbols and req.symbol_limit >= 4500,
            }

    realtime = {
        "status": "skipped",
        "message": "日线同步完成；实时价由定时刷新或手动刷新更新",
        "updated": 0,
        "updated_at": now_iso(),
        "missing": [],
    }
    return {
        "synced_symbols": len(universe) - len(errors),
        "requested_symbols": requested_symbols,
        "failed_symbols": len(errors),
        "bars": total_bars,
        "signals": signal_count,
        "positions_refreshed": refreshed,
        "candidate_message_review": candidate_message_review,
        "realtime_refreshed": {
            "status": realtime.get("status"),
            "message": realtime.get("message"),
            "updated": realtime.get("updated", 0),
            "updated_at": realtime.get("updated_at"),
            "missing": realtime.get("missing", []),
        },
        "daily_status": daily_status,
        "updated_at": realtime.get("updated_at") or refreshed.get("updated_at"),
        "errors": errors[:20],
    }


def run_daily_workflow(req: SyncRequest) -> dict:
    sync_result = sync_data(req)
    plan_result = generate_trade_plan()
    ai_future = _AI_EXECUTOR.submit(run_ai_review)
    try:
        ai_result = ai_future.result(timeout=_WORKFLOW_AI_TIMEOUT_SECONDS)
    except TimeoutError:
        ai_result = {
            "status": "timeout",
            "message": f"AI 审核超过 {_WORKFLOW_AI_TIMEOUT_SECONDS} 秒，已先返回同步和计划结果；可稍后单独点击 AI 审核。",
        }
    with get_conn() as conn:
        state = _get_sim_state(conn)
        positions = [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()]
        trades = [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT 100").fetchall()]
        report = conn.execute("SELECT * FROM daily_reports ORDER BY created_at DESC LIMIT 1").fetchone()
        automation = _get_automation_state(conn)
        plans = _list_trade_plans_locked(conn)
        latest_ai = _latest_ai_daily_report(conn)
        ai_plan_reviews = _latest_ai_plan_reviews(conn)
        snapshot = _market_snapshot(conn)
    return {
        "status": "ok",
        "message": "日线同步、交易计划、AI 审核已完成",
        "sync": sync_result,
        "plan": plan_result,
        "ai": ai_result,
        **state,
        "positions": positions,
        "account_summary": _account_summary(state, positions),
        "trades": trades,
        "latest_report": _daily_report_dict(report),
        "latest_ai_report": latest_ai,
        "ai_plan_reviews": ai_plan_reviews,
        "automation": automation,
        "market_snapshot": snapshot,
        "plans": plans,
    }


def _candidate_from_row(row) -> dict:
    keys = row.keys()
    realtime = None
    if "realtime_price" in keys and row["realtime_price"] is not None:
        realtime = {
            "price": row["realtime_price"],
            "pct_chg": row["realtime_pct_chg"],
            "updated_at": row["realtime_updated_at"],
            "groups": json.loads(row["realtime_groups"]) if "realtime_groups" in keys and row["realtime_groups"] else [],
        }
    return {
        "symbol": row["symbol"],
        "name": row["name"],
        "trade_date": row["trade_date"],
        "score": row["score"],
        "trend_score": row["trend_score"],
        "volume_score": row["volume_score"],
        "risk_score": row["risk_score"],
        "entry_price": row["entry_price"],
        "stop_loss": row["stop_loss"],
        "take_profit_1": row["take_profit_1"],
        "take_profit_2": row["take_profit_2"],
        "position_pct": row["position_pct"],
        "reasons": json.loads(row["reasons"]),
        "strategy_tags": json.loads(row["strategy_tags"]) if "strategy_tags" in keys else [],
        "strategy_scores": json.loads(row["strategy_scores"]) if "strategy_scores" in keys else {},
        "market_state": row["market_state"] if "market_state" in keys else "neutral",
        "market_note": row["market_note"] if "market_note" in keys else "",
        "decision": row["decision"] if "decision" in keys else "buy",
        "realtime": realtime,
    }


def list_candidates(date: str | None = None) -> list[dict]:
    with get_conn() as conn:
        return _list_candidates_locked(conn, date)


def get_data_status() -> dict:
    with get_conn() as conn:
        latest_daily = conn.execute("SELECT MAX(trade_date) AS d FROM daily_bars").fetchone()["d"]
        latest_daily_symbols = 0
        if latest_daily:
            latest_daily_symbols = conn.execute(
                "SELECT COUNT(DISTINCT symbol) AS count FROM daily_bars WHERE trade_date=?",
                (latest_daily,),
            ).fetchone()["count"]
        total_symbols = conn.execute("SELECT COUNT(DISTINCT symbol) AS count FROM daily_bars").fetchone()["count"]
        latest_signal = conn.execute("SELECT MAX(trade_date) AS d FROM signals").fetchone()["d"]
        signal_row = conn.execute(
            "SELECT COUNT(*) AS count, MAX(score) AS max_score, AVG(score) AS avg_score FROM signals WHERE trade_date=?",
            (latest_signal or "",),
        ).fetchone()
        quote_row = conn.execute(
            "SELECT COUNT(*) AS count, MAX(updated_at) AS updated_at FROM realtime_quotes",
        ).fetchone()
        source_rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(source, ''), 'unknown') AS source, COUNT(*) AS count
            FROM realtime_quotes
            GROUP BY COALESCE(NULLIF(source, ''), 'unknown')
            ORDER BY CASE WHEN COALESCE(NULLIF(source, ''), 'unknown')='unknown' THEN 1 ELSE 0 END, count DESC
            """
        ).fetchall()
        report = conn.execute("SELECT * FROM daily_reports ORDER BY created_at DESC LIMIT 1").fetchone()
        latest_plan_date = conn.execute("SELECT MAX(plan_date) AS d FROM trade_plans").fetchone()["d"]
        plan_row = conn.execute(
            """
            SELECT COUNT(*) AS count,
                   SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                   MAX(trade_date) AS trade_date
            FROM trade_plans
            WHERE plan_date=?
            """,
            (latest_plan_date or "",),
        ).fetchone()
        automation = _get_automation_state(conn)

    latest_daily_symbols = int(latest_daily_symbols or 0)
    total_symbols = int(total_symbols or 0)
    signal_count = int(signal_row["count"] or 0) if signal_row else 0
    complete_pct = round(latest_daily_symbols / total_symbols * 100, 1) if total_symbols else 0
    status = "ok"
    messages = []
    if not latest_daily:
        status = "empty"
        messages.append("暂无日线数据")
    if latest_daily and latest_signal != latest_daily:
        status = "warning"
        messages.append("候选信号与最新日线日期不一致")
    if latest_daily and signal_count == 0:
        status = "warning"
        messages.append("最新交易日暂无候选信号")
    if total_symbols and complete_pct < 80:
        status = "warning"
        messages.append("最新交易日覆盖股票数偏低")
    if automation.get("last_status") == "error":
        status = "warning"
        messages.append("自动任务最近一次执行失败")
    if not messages:
        messages.append("数据链路正常")

    return {
        "status": status,
        "messages": messages,
        "provider_order": ["mootdx", "eastmoney", "sina", "baostock", "akshare/fallback"],
        "daily": {
            "latest_date": latest_daily,
            "latest_symbols": latest_daily_symbols,
            "total_symbols": total_symbols,
            "complete_pct": complete_pct,
        },
        "signals": {
            "latest_date": latest_signal,
            "count": signal_count,
            "max_score": round(float(signal_row["max_score"] or 0), 2) if signal_row else 0,
            "avg_score": round(float(signal_row["avg_score"] or 0), 2) if signal_row else 0,
        },
        "realtime": {
            "count": int(quote_row["count"] or 0) if quote_row else 0,
            "updated_at": quote_row["updated_at"] if quote_row else "",
            "sources": [{"source": row["source"], "count": int(row["count"] or 0)} for row in source_rows],
        },
        "plans": {
            "latest_plan_date": latest_plan_date,
            "trade_date": plan_row["trade_date"] if plan_row else "",
            "count": int(plan_row["count"] or 0) if plan_row else 0,
            "pending": int(plan_row["pending"] or 0) if plan_row else 0,
        },
        "latest_report": _daily_report_dict(report),
        "automation": automation,
        "updated_at": now_iso(),
    }


def _list_candidates_locked(conn, date: str | None = None, limit: int = 50) -> list[dict]:
    if not date:
        row = conn.execute(
            """
            SELECT trade_date AS d
            FROM daily_bars
            ORDER BY trade_date DESC
            LIMIT 1
            """
        ).fetchone()
        date = row["d"] if row else None
    if not date:
        return []
    rows = conn.execute(
        """
        SELECT s.*, st.name,
               q.price AS realtime_price, q.pct_chg AS realtime_pct_chg,
               q.updated_at AS realtime_updated_at, q.groups_json AS realtime_groups
        FROM signals s
        JOIN stocks st ON st.symbol = s.symbol
        LEFT JOIN realtime_quotes q ON q.symbol = s.symbol
        WHERE s.trade_date = ?
        ORDER BY s.score DESC
        LIMIT ?
        """,
        (date, limit),
    ).fetchall()
    return [_candidate_from_row(row) for row in rows]


def _stock_evidence(signal: dict | None, latest_bar: dict | None, quote: dict | None, latestEval: dict | None = None) -> dict:
    technical_score = float(signal.get("score") or 0) if signal else 0
    strategy_scores = signal.get("strategy_scores") or {} if signal else {}
    bars_note = ""
    valuation_status = "warning"
    valuation_value = "暂无"
    if latest_bar:
        close = float((quote or {}).get("price") or latest_bar.get("close") or 0)
        amount = float(latest_bar.get("amount") or 0)
        pct_chg = float(latest_bar.get("pct_chg") or 0)
        valuation_value = f"{close:.2f}"
        valuation_status = "neutral"
        bars_note = f"当前价 {close:.2f}，成交额 {amount / 100000000:.2f} 亿，涨跌 {pct_chg:.2f}%。PE/PB、市值暂未入库，当前以价格位置和流动性作交易估值替代。"

    def _score_status(value: float) -> str:
        if value >= 72:
            return "ok"
        if value > 0:
            return "neutral"
        return "warning"

    def _score_value(key: str, default: float = 0.0) -> float:
        return round(float(strategy_scores.get(key) or default), 2)

    fund_score = _score_value("fund_flow_score")
    theme_score = _score_value("theme_score")
    message_score = _score_value("message_risk_score")
    fund_note = "个股资金流和资金排名因子"
    if "fund_net_ratio" in strategy_scores:
        fund_note = f"资金净流占比 {float(strategy_scores.get('fund_net_ratio') or 0):.2f}%，来源 {strategy_scores.get('fund_source') or '资金流因子'}"
    theme_note = "行业/概念热度、研报和消息主题因子"
    if strategy_scores.get("theme_industry"):
        theme_note = f"行业 {strategy_scores.get('theme_industry')}，行业/概念热度与消息主题综合"
    risk_note = strategy_scores.get("message_risk_note") or "公告、新闻、研报消息风险因子"
    if "message_negative_count" in strategy_scores or "message_positive_count" in strategy_scores:
        risk_note = f"{risk_note}；正面 {int(float(strategy_scores.get('message_positive_count') or 0))} 条，负面/混合 {int(float(strategy_scores.get('message_negative_count') or 0))} 条"
    extended_items = [
        {
            "label": "资金面",
            "status": _score_status(fund_score),
            "value": fund_score if fund_score else "暂无",
            "note": fund_note,
        },
        {
            "label": "题材面",
            "status": _score_status(theme_score),
            "value": theme_score if theme_score else "暂无",
            "note": theme_note,
        },
        {
            "label": "估值面",
            "status": valuation_status,
            "value": valuation_value,
            "note": bars_note or "缺少最新日线，暂不能评估价格位置和流动性。",
        },
        {
            "label": "风险意见",
            "status": "ok" if message_score >= 72 else "warning" if message_score <= 45 and message_score > 0 else "neutral",
            "value": message_score if message_score else "暂无",
            "note": risk_note,
        },
    ]
    score_labels = [
        ("evidence_score", "证据总分"),
        ("evidence_score_v15", "技术证据"),
        ("fund_flow_score", "资金面"),
        ("theme_score", "题材面"),
        ("message_risk_score", "消息风险"),
        ("factor_score", "因子综合"),
        ("trend_breakout", "趋势突破"),
        ("strong_pullback", "强势回调"),
        ("volume_launch", "放量启动"),
        ("relative_strength", "相对强势"),
        ("liquidity_quality", "流动性质量"),
        ("risk_control", "风险控制"),
    ]
    score_items = [
        {
            "label": label,
            "status": _score_status(float(strategy_scores.get(key) or 0)),
            "value": round(float(strategy_scores.get(key) or 0), 2),
            "note": "v1.6 多维证据评分组件",
        }
        for key, label in score_labels
        if key in strategy_scores or key == "evidence_score"
    ]
    data_items = []
    if latest_bar:
        data_items.append({
            "label": "日线",
            "status": "ok",
            "value": latest_bar.get("trade_date"),
            "note": f"收盘 {latest_bar.get('close')}",
        })
    else:
        data_items.append({"label": "日线", "status": "warning", "value": "暂无", "note": "缺少最近日线"})
    if quote:
        data_items.append({
            "label": "实时行情",
            "status": "ok",
            "value": quote.get("source") or "unknown",
            "note": f"{quote.get('updated_at')} · {quote.get('price')}",
        })
    else:
        data_items.append({"label": "实时行情", "status": "warning", "value": "暂无", "note": "等待刷新"})
    return {
        "summary": latestEval.get("summary") if latestEval else (signal.get("market_note") if signal else "暂无最新评估"),
        "technical": {
            "status": "ok" if signal else "warning",
            "score": technical_score,
            "items": [
                {
                    "label": "技术评分",
                    "status": "ok" if technical_score >= 82 else "neutral" if technical_score else "warning",
                    "value": technical_score or "暂无",
                    "note": "、".join(signal.get("strategy_tags") or []) if signal else "暂无候选信号",
                },
                {
                    "label": "市场环境",
                    "status": "ok" if signal and signal.get("market_state") in {"strong", "range"} else "neutral",
                    "value": signal.get("market_state") if signal else "暂无",
                    "note": signal.get("market_note") if signal else "等待最新信号",
                },
            ] + score_items,
        },
        "data_quality": {
            "status": "ok" if latest_bar else "warning",
            "items": data_items,
        },
        "extended_factors": extended_items,
        "planned_factors": extended_items,
    }


def get_stock_detail(symbol: str) -> dict | None:
    symbol = normalize_symbol(symbol)
    with get_conn() as conn:
        stock = conn.execute("SELECT * FROM stocks WHERE symbol = ?", (symbol,)).fetchone()
        if not stock:
            return None
        bars = conn.execute(
            "SELECT * FROM daily_bars WHERE symbol = ? ORDER BY trade_date DESC LIMIT 260",
            (symbol,),
        ).fetchall()
        signal_row = conn.execute(
            """
            SELECT s.*, st.name FROM signals s JOIN stocks st ON st.symbol = s.symbol
            WHERE s.symbol = ? ORDER BY s.trade_date DESC LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        quote_row = conn.execute("SELECT * FROM realtime_quotes WHERE symbol = ?", (symbol,)).fetchone()
        evaluations = _list_stock_evaluations_locked(conn, symbol, 80)
        ai_reviews = _latest_ai_stock_reviews(conn, symbol, 30)
        bar_list = [dict(row) for row in reversed(bars)]
        latest_bar = bar_list[-1] if bar_list else None
        quote = None
        if quote_row:
            quote = {
                "symbol": quote_row["symbol"],
                "name": quote_row["name"],
                "price": quote_row["price"],
                "pct_chg": quote_row["pct_chg"],
                "source": quote_row["source"],
                "groups": json.loads(quote_row["groups_json"]) if quote_row["groups_json"] else [],
                "updated_at": quote_row["updated_at"],
            }
        return {
            "symbol": symbol,
            "name": stock["name"],
            "bars": bar_list,
            "latest_bar": latest_bar,
            "quote": quote,
            "latest_signal": _candidate_from_row(signal_row) if signal_row else None,
            "evaluations": evaluations,
            "ai_reviews": ai_reviews,
            "evidence": _stock_evidence(_candidate_from_row(signal_row) if signal_row else None, latest_bar, quote, latestEval=evaluations[0] if evaluations else None),
        }


def run_backtest(req: BacktestRequest) -> dict:
    with get_conn() as conn:
        bars = pd.read_sql_query(
            "SELECT * FROM daily_bars WHERE trade_date >= date(?, '-260 day') AND trade_date <= ? ORDER BY symbol, trade_date",
            conn,
            params=(req.start_date, req.end_date),
        )
        signals = build_historical_signals(bars, req.start_date, req.end_date)
        summary, trades, equity = simulate_backtest(
            bars,
            signals,
            req.start_date,
            req.end_date,
            req.initial_cash,
            req.max_positions,
            req.fee_rate,
            req.hold_days,
        )
        cur = conn.execute(
            """
            INSERT INTO backtest_runs(params_json, summary_json, trades_json, equity_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                req.model_dump_json(),
                json.dumps(summary, ensure_ascii=False),
                json.dumps(trades, ensure_ascii=False),
                json.dumps(equity, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        run_id = cur.lastrowid
    return {"id": run_id, "summary": summary, "trades": trades, "equity": equity}


def list_watchlist() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT w.*, COALESCE(st.name, q.name, w.symbol) AS name,
                   q.price AS realtime_price, q.pct_chg AS realtime_pct_chg,
                   q.updated_at AS realtime_updated_at,
                   b.close AS latest_close, b.trade_date AS latest_bar_date
            FROM watchlist w
            LEFT JOIN stocks st ON st.symbol = w.symbol
            LEFT JOIN realtime_quotes q ON q.symbol = w.symbol
            LEFT JOIN daily_bars b ON b.symbol = w.symbol
              AND b.trade_date = (SELECT MAX(trade_date) FROM daily_bars WHERE symbol = w.symbol)
            ORDER BY w.created_at DESC
            """
        ).fetchall()
        out = []
        for row in rows:
            signal = conn.execute(
                """
                SELECT s.*, st.name FROM signals s JOIN stocks st ON st.symbol = s.symbol
                WHERE s.symbol = ? ORDER BY s.trade_date DESC LIMIT 1
                """,
                (row["symbol"],),
            ).fetchone()
            added_price = float(row["added_price"] or 0)
            added_price_source = row["added_price_source"]
            if added_price <= 0:
                baseline = conn.execute(
                    """
                    SELECT close, trade_date
                    FROM daily_bars
                    WHERE symbol=? AND trade_date >= ?
                    ORDER BY trade_date ASC
                    LIMIT 1
                    """,
                    (row["symbol"], row["created_at"][:10]),
                ).fetchone()
                if baseline:
                    added_price = float(baseline["close"] or 0)
                    added_price_source = f"回填收盘 {baseline['trade_date']}"
                    conn.execute(
                        "UPDATE watchlist SET added_price=?, added_price_source=? WHERE symbol=? AND added_price=0",
                        (added_price, added_price_source, row["symbol"]),
                    )
            current_price = float(row["realtime_price"] or row["latest_close"] or 0)
            watch_return_pct = round((current_price / added_price - 1) * 100, 2) if current_price and added_price else None
            latest_signal = _candidate_from_row(signal) if signal else None
            spark_rows = conn.execute(
                """
                SELECT trade_date, close
                FROM daily_bars
                WHERE symbol=?
                ORDER BY trade_date DESC
                LIMIT 30
                """,
                (row["symbol"],),
            ).fetchall()
            sparkline = [
                {"trade_date": item["trade_date"], "close": item["close"]}
                for item in reversed(spark_rows)
            ]
            out.append(
                {
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "note": row["note"],
                    "created_at": row["created_at"],
                    "added_price": added_price,
                    "added_price_source": added_price_source,
                    "watch_return_pct": watch_return_pct,
                    "latest_signal": latest_signal,
                    "realtime": {
                        "price": row["realtime_price"],
                        "pct_chg": row["realtime_pct_chg"],
                        "updated_at": row["realtime_updated_at"],
                    } if row["realtime_price"] is not None else None,
                    "latest_close": row["latest_close"],
                    "latest_bar_date": row["latest_bar_date"],
                    "sparkline": sparkline,
                }
            )
        return out


def add_watch(symbol: str, note: str) -> dict:
    symbol = normalize_symbol(symbol)
    with get_conn() as conn:
        price_row = conn.execute(
            """
            SELECT q.price AS realtime_price, b.close AS latest_close
            FROM stocks st
            LEFT JOIN realtime_quotes q ON q.symbol = st.symbol
            LEFT JOIN daily_bars b ON b.symbol = st.symbol
              AND b.trade_date = (SELECT MAX(trade_date) FROM daily_bars WHERE symbol = st.symbol)
            WHERE st.symbol=?
            """,
            (symbol,),
        ).fetchone()
        added_price = 0.0
        added_price_source = ""
        if price_row:
            added_price = float(price_row["realtime_price"] or price_row["latest_close"] or 0)
            added_price_source = "实时价" if price_row["realtime_price"] else "日线收盘" if price_row["latest_close"] else ""
        conn.execute(
            """
            INSERT INTO watchlist(symbol, note, added_price, added_price_source, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET note=excluded.note
            """,
            (symbol, note, added_price, added_price_source, now_iso()),
        )
    return {"ok": True, "symbol": symbol, "added_price": added_price, "added_price_source": added_price_source}


def delete_watch(symbol: str) -> dict:
    with get_conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE symbol = ?", (normalize_symbol(symbol),))
    return {"ok": True}


def list_strategy_versions() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM strategy_versions
            ORDER BY id DESC
            """
        ).fetchall()
        return [
            {
                "id": row["id"],
                "version": row["version"],
                "status": row["status"],
                "timeframe": row["timeframe"],
                "params": json.loads(row["params_json"]),
                "change_note": row["change_note"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]


def _get_sim_state(conn) -> dict:
    row = conn.execute("SELECT * FROM sim_state WHERE id = 1").fetchone()
    rules = _normalize_rules(json.loads(row["rules_json"]))
    return {
        "cash": row["cash"],
        "initial_cash": row["initial_cash"],
        "rules": rules,
        "last_run_trade_date": row["last_run_trade_date"],
        "updated_at": row["updated_at"],
    }


def _normalize_rules(rules: dict | None) -> dict:
    base = {
        "minScore": 82,
        "maxPositions": 4,
        "maxSinglePct": 8,
        "maxDailyTrades": 5,
        "maxDailyBuys": 1,
        "maxDailySells": 4,
        "sellPriority": True,
        "dynamicBuyEnabled": True,
        "weakMaxDailyBuys": 1,
        "weakMaxDailyBuyPct": 5,
        "rangeMaxDailyBuys": 1,
        "rangeMaxDailyBuyPct": 8,
        "strongMaxDailyBuys": 2,
        "strongMaxDailyBuyPct": 15,
        "buyPriceTolerancePct": 0.8,
        "commissionRate": 0.0003,
        "minCommission": 5,
        "stampTaxRate": 0.0005,
        "transferFeeRate": 0.00001,
        "slippagePct": 0.15,
        "rebalanceEnabled": True,
        "rebalanceMinNewScore": 88,
        "rebalanceMinScoreGap": 8,
        "maxDailyRebalances": 1,
        "weakHoldDays": 5,
        "weakReturnPct": -2.5,
        "weakScoreExit": 78,
        "addPositionEnabled": False,
        "addMinProfitPct": 4,
        "addMinScore": 88,
        "maxAddsPerSymbol": 1,
        "addPositionPct": 3,
        "takeProfitSellPct": 50,
        "trailingStopPct": 4,
        "minRemainLot": 100,
    }
    if rules:
        base.update(rules)
    if "maxDailyBuys" not in (rules or {}):
        base["maxDailyBuys"] = min(2, int(base.get("maxDailyTrades") or 2))
    if "maxDailySells" not in (rules or {}):
        base["maxDailySells"] = max(1, int(base.get("maxDailyTrades") or 5))
    base["maxDailyTrades"] = int(base.get("maxDailyBuys", 0)) + int(base.get("maxDailySells", 0))
    return base


def _market_state_for_trade_date(conn, trade_date: str) -> str:
    row = conn.execute(
        """
        SELECT market_state
        FROM signals
        WHERE trade_date=?
        ORDER BY score DESC
        LIMIT 1
        """,
        (trade_date,),
    ).fetchone()
    return str(row["market_state"] if row and row["market_state"] else "neutral")


def _dynamic_buy_profile(rules: dict, market_state: str | None) -> dict:
    state = str(market_state or "neutral").lower()
    if not rules.get("dynamicBuyEnabled", True):
        max_buys = int(rules.get("maxDailyBuys", 1) or 0)
        return {
            "enabled": False,
            "market_state": state,
            "max_buys": max_buys,
            "max_buy_pct": max_buys * float(rules.get("maxSinglePct", 8) or 8),
            "label": "固定额度",
        }
    if state == "strong":
        return {
            "enabled": True,
            "market_state": state,
            "max_buys": int(rules.get("strongMaxDailyBuys", 2) or 0),
            "max_buy_pct": float(rules.get("strongMaxDailyBuyPct", 15) or 0),
            "label": "强市",
        }
    if state == "weak":
        return {
            "enabled": True,
            "market_state": state,
            "max_buys": int(rules.get("weakMaxDailyBuys", 1) or 0),
            "max_buy_pct": float(rules.get("weakMaxDailyBuyPct", 5) or 0),
            "label": "弱市",
        }
    return {
        "enabled": True,
        "market_state": state,
        "max_buys": int(rules.get("rangeMaxDailyBuys", 1) or 0),
        "max_buy_pct": float(rules.get("rangeMaxDailyBuyPct", 8) or 0),
        "label": "震荡/中性",
    }


def _is_buy_trade_action(action: str) -> bool:
    text = str(action or "")
    return "买入" in text or "追加" in text


def _daily_buy_usage(conn, trade_date: str, initial_cash: float) -> dict:
    rows = conn.execute("SELECT action, net_amount, amount FROM sim_trades WHERE trade_date=?", (trade_date,)).fetchall()
    buy_rows = [row for row in rows if _is_buy_trade_action(row["action"])]
    amount = sum(float(row["net_amount"] or row["amount"] or 0) for row in buy_rows)
    return {
        "count": len(buy_rows),
        "amount": amount,
        "pct": round(amount / initial_cash * 100, 4) if initial_cash else 0.0,
    }


def _max_quantity_for_buy_budget(price: float, remaining_budget: float, rules: dict) -> int:
    if price <= 0 or remaining_budget <= 0:
        return 0
    quantity = int(remaining_budget / price / 100) * 100
    while quantity > 0 and _trade_costs(price, quantity, "buy", rules)["net_amount"] > remaining_budget:
        quantity -= 100
    return max(0, quantity)


def _position_dict(row) -> dict:
    keys = row.keys()
    take_profit_stage = row["take_profit_stage"] if "take_profit_stage" in keys else 0
    peak_price = row["peak_price"] if "peak_price" in keys else row["current_price"]
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "name": row["name"],
        "buyPrice": row["buy_price"],
        "quantity": row["quantity"],
        "currentPrice": row["current_price"],
        "stopLoss": row["stop_loss"],
        "takeProfit": row["take_profit"],
        "takeProfitStage": take_profit_stage,
        "peakPrice": peak_price,
        "status": row["status"],
        "sellPrice": row["exit_price"],
        "exitReason": row["exit_reason"],
        "createdAt": row["opened_at"],
        "closedAt": row["closed_at"],
    }


def _account_summary(state: dict, positions: list[dict]) -> dict:
    open_positions = [p for p in positions if p.get("status") != "closed"]
    closed_positions = [p for p in positions if p.get("status") == "closed"]
    market_value = sum(float(p.get("currentPrice") or p.get("buyPrice") or 0) * int(p.get("quantity") or 0) for p in open_positions)
    cost = sum(float(p.get("buyPrice") or 0) * int(p.get("quantity") or 0) for p in open_positions)
    float_pnl = market_value - cost
    realized = sum(
        (float(p.get("sellPrice") or 0) - float(p.get("buyPrice") or 0)) * int(p.get("quantity") or 0)
        for p in closed_positions
        if float(p.get("sellPrice") or 0) > 0
    )
    cash = float(state.get("cash") or 0)
    total_assets = cash + market_value
    return {
        "valuation_basis": "realtime_position_price",
        "cash": round(cash, 2),
        "market_value": round(market_value, 2),
        "position_cost": round(cost, 2),
        "total_assets": round(total_assets, 2),
        "float_pnl": round(float_pnl, 2),
        "float_pnl_pct": round(float_pnl / cost * 100, 2) if cost else 0,
        "realized_pnl": round(realized, 2),
        "open_positions": len(open_positions),
        "position_pct": round(market_value / total_assets * 100, 2) if total_assets else 0,
        "updated_at": state.get("updated_at") or now_iso(),
    }


def _holding_days(opened_at: str, trade_date: str) -> int:
    try:
        return max(0, (datetime.strptime(trade_date, "%Y-%m-%d") - datetime.strptime(opened_at[:10], "%Y-%m-%d")).days)
    except Exception:
        return 0


def _position_return_pct(row, current_price: float) -> float:
    buy_price = float(row["buy_price"] or 0)
    if buy_price <= 0:
        return 0.0
    return round((current_price / buy_price - 1) * 100, 2)


def _stock_evaluation_dict(row) -> dict:
    return {
        "symbol": row["symbol"],
        "trade_date": row["trade_date"],
        "name": row["name"],
        "groups": json.loads(row["groups_json"] or "[]"),
        "score": row["score"],
        "decision": row["decision"],
        "summary": row["summary"],
        "metrics": json.loads(row["metrics_json"] or "{}"),
        "signal": json.loads(row["signal_json"] or "{}"),
        "position": json.loads(row["position_json"] or "{}"),
        "plan": json.loads(row["plan_json"] or "{}"),
        "created_at": row["created_at"],
    }


def _list_stock_evaluations_locked(conn, symbol: str, limit: int = 80) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM stock_evaluations
        WHERE symbol=?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (symbol, limit),
    ).fetchall()
    return [_stock_evaluation_dict(row) for row in rows]


def _build_stock_evaluation_summary(groups: list[str], signal: dict | None, position: dict | None, plan: dict | None, metrics: dict) -> tuple[str, str]:
    score = float(signal["score"]) if signal and signal.get("score") is not None else 0.0
    pnl_pct = metrics.get("pnl_pct")
    plan_type = plan.get("plan_type") if plan else ""
    if plan_type == "sell_stop":
        return "卖出观察", "接近或触发止损，优先控制风险。"
    if plan_type == "sell_take_profit":
        return "止盈观察", "接近止盈区间，观察是否分批兑现。"
    if plan_type == "sell_weak":
        return "弱势卖出", plan.get("reason") or "持仓信号转弱，计划退出。"
    if plan_type == "rebalance_sell":
        return "调仓卖出", plan.get("reason") or "持仓转弱，计划腾出仓位。"
    if plan_type == "rebalance_buy":
        return "调仓买入", plan.get("reason") or "新候选强于弱持仓，计划换入。"
    if plan_type == "next_buy":
        return "买入观察", plan.get("reason") or "候选满足买入观察条件。"
    if plan_type == "add_buy":
        return "追加观察", plan.get("reason") or "持仓盈利且信号延续，允许小仓位追加。"
    if position and position.get("status") == "open":
        if score >= 82:
            return "继续持有", f"持仓仍有高分信号，当前浮动收益 {pnl_pct:.2f}%。"
        if signal:
            return "持仓观察", f"仍有信号但强度一般，当前浮动收益 {pnl_pct:.2f}%。"
        return "持仓转弱", f"最新候选信号消失，当前浮动收益 {pnl_pct:.2f}%，等待止损/调仓规则确认。"
    if signal:
        if score >= 82:
            return "重点观察", "评分达到强信号区间，等待价格条件和仓位规则确认。"
        return "普通观察", "有策略信号，但优先级未达到强买入区间。"
    if "watch" in groups:
        return "自选跟踪", "暂无入选信号，继续观察价格和策略评分变化。"
    return "记录", "纳入当日评估记录。"


def _record_stock_evaluations(conn, trade_date: str, candidates: list[dict], plans: list[dict]) -> list[dict]:
    symbols: dict[str, dict] = {}
    for c in candidates:
        symbols.setdefault(c["symbol"], {"symbol": c["symbol"], "name": c["name"], "groups": set()})
        symbols[c["symbol"]]["name"] = c["name"]
        symbols[c["symbol"]]["groups"].add("candidate")
    positions = conn.execute("SELECT * FROM sim_positions WHERE status='open'").fetchall()
    for p in positions:
        symbols.setdefault(p["symbol"], {"symbol": p["symbol"], "name": p["name"], "groups": set()})
        symbols[p["symbol"]]["name"] = p["name"]
        symbols[p["symbol"]]["groups"].add("holding")
    watch_rows = conn.execute(
        """
        SELECT w.symbol, COALESCE(st.name, q.name, w.symbol) AS name
        FROM watchlist w
        LEFT JOIN stocks st ON st.symbol = w.symbol
        LEFT JOIN realtime_quotes q ON q.symbol = w.symbol
        """
    ).fetchall()
    for w in watch_rows:
        symbols.setdefault(w["symbol"], {"symbol": w["symbol"], "name": w["name"], "groups": set()})
        symbols[w["symbol"]]["name"] = w["name"]
        symbols[w["symbol"]]["groups"].add("watch")

    candidates_by_symbol = {c["symbol"]: c for c in candidates}
    plan_priority = {"sell_stop": 0, "sell_weak": 1, "sell_take_profit": 2, "rebalance_sell": 3, "rebalance_buy": 4, "next_buy": 5, "add_buy": 6, "hold": 7}
    plans_by_symbol: dict[str, dict] = {}
    for plan in sorted(plans, key=lambda item: plan_priority.get(item.get("plan_type"), 9)):
        if plan.get("status") == "skipped":
            continue
        plans_by_symbol.setdefault(plan["symbol"], plan)
    position_by_symbol = {p["symbol"]: p for p in positions}
    latest_closes = _latest_close_map(conn)
    recorded: list[dict] = []
    for symbol, item in symbols.items():
        groups = sorted(item["groups"])
        signal = candidates_by_symbol.get(symbol)
        position_row = position_by_symbol.get(symbol)
        position = _position_dict(position_row) if position_row else {}
        close = latest_closes.get(symbol)
        current_price = float(close["close"]) if close else float(position_row["current_price"]) if position_row else 0.0
        metrics = {"current_price": round(current_price, 2)}
        if position_row:
            buy_price = float(position_row["buy_price"] or 0)
            quantity = int(position_row["quantity"] or 0)
            pnl = (current_price - buy_price) * quantity
            metrics.update({
                "buy_price": round(buy_price, 2),
                "quantity": quantity,
                "market_value": round(current_price * quantity, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": _position_return_pct(position_row, current_price),
                "holding_days": _holding_days(position_row["opened_at"], trade_date),
            })
        plan = plans_by_symbol.get(symbol, {})
        decision, summary = _build_stock_evaluation_summary(groups, signal, position, plan, metrics)
        score = float(signal["score"]) if signal else None
        created_at = now_iso()
        payload = {
            "symbol": symbol,
            "trade_date": trade_date,
            "name": item["name"],
            "groups_json": json.dumps(groups, ensure_ascii=False),
            "score": score,
            "decision": decision,
            "summary": summary,
            "metrics_json": json.dumps(metrics, ensure_ascii=False),
            "signal_json": json.dumps(signal or {}, ensure_ascii=False),
            "position_json": json.dumps(position, ensure_ascii=False),
            "plan_json": json.dumps(plan or {}, ensure_ascii=False),
            "created_at": created_at,
        }
        conn.execute(
            """
            INSERT INTO stock_evaluations(
                symbol, trade_date, name, groups_json, score, decision, summary,
                metrics_json, signal_json, position_json, plan_json, created_at
            ) VALUES (
                :symbol, :trade_date, :name, :groups_json, :score, :decision, :summary,
                :metrics_json, :signal_json, :position_json, :plan_json, :created_at
            )
            ON CONFLICT(symbol, trade_date) DO UPDATE SET
                name=excluded.name,
                groups_json=excluded.groups_json,
                score=excluded.score,
                decision=excluded.decision,
                summary=excluded.summary,
                metrics_json=excluded.metrics_json,
                signal_json=excluded.signal_json,
                position_json=excluded.position_json,
                plan_json=excluded.plan_json,
                created_at=excluded.created_at
            """,
            payload,
        )
        recorded.append({
            "symbol": symbol,
            "name": item["name"],
            "trade_date": trade_date,
            "groups": groups,
            "score": score,
            "decision": decision,
            "summary": summary,
            "metrics": metrics,
            "plan": plan,
            "created_at": created_at,
        })
    return recorded


def _ai_daily_report_dict(row) -> dict | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "report_date": row["report_date"],
        "trade_date": row["trade_date"],
        "risk_level": row["risk_level"],
        "summary": row["summary"],
        "market_view": row["market_view"],
        "candidate_view": row["candidate_view"],
        "position_view": row["position_view"],
        "action_suggestion": row["action_suggestion"],
        "raw": json.loads(row["raw_json"] or "{}"),
        "created_at": row["created_at"],
    }


def _ai_plan_review_dict(row) -> dict:
    return {
        "id": row["id"],
        "trade_date": row["trade_date"],
        "plan_id": row["plan_id"],
        "symbol": row["symbol"],
        "name": row["name"],
        "plan_type": row["plan_type"],
        "verdict": row["verdict"],
        "risk_level": row["risk_level"],
        "reason": row["reason"],
        "suggestion": row["suggestion"],
        "raw": json.loads(row["raw_json"] or "{}"),
        "created_at": row["created_at"],
    }


def _ai_stock_review_dict(row) -> dict:
    return {
        "id": row["id"],
        "trade_date": row["trade_date"],
        "symbol": row["symbol"],
        "name": row["name"],
        "review_kind": row["review_kind"],
        "decision": row["decision"],
        "risk_level": row["risk_level"],
        "summary": row["summary"],
        "suggestion": row["suggestion"],
        "raw": json.loads(row["raw_json"] or "{}"),
        "created_at": row["created_at"],
    }


def _normalize_ai_risk(value: str | None) -> str:
    text = str(value or "medium").strip().lower()
    mapping = {
        "低": "low",
        "较低": "low",
        "low": "low",
        "中": "medium",
        "中等": "medium",
        "中高": "high",
        "较高": "high",
        "高": "high",
        "medium": "medium",
        "high": "high",
    }
    return mapping.get(text, "medium")


def _latest_ai_daily_report(conn) -> dict | None:
    row = conn.execute("SELECT * FROM ai_daily_reports ORDER BY created_at DESC LIMIT 1").fetchone()
    return _ai_daily_report_dict(row)


def _latest_ai_plan_reviews(conn, limit: int = 80) -> list[dict]:
    rows = conn.execute("SELECT * FROM ai_plan_reviews ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_ai_plan_review_dict(row) for row in rows]


def _latest_ai_stock_reviews(conn, symbol: str, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM ai_stock_reviews WHERE symbol=? ORDER BY trade_date DESC, created_at DESC LIMIT ?",
        (symbol, limit),
    ).fetchall()
    return [_ai_stock_review_dict(row) for row in rows]


def _is_risk_exit_plan(plan_type: str) -> bool:
    return plan_type in ("sell_stop", "sell_weak")


def _plan_final_decision(conn, plan, *, require_trade_date: str | None = None) -> dict:
    plan_type = plan["plan_type"]
    if plan_type == "hold":
        return {
            "allow": True,
            "status": "observe",
            "label": "持有观察",
            "reason": "持有计划不产生买卖成交，执行时只记录观察。",
        }
    if _is_risk_exit_plan(plan_type):
        return {
            "allow": True,
            "status": "risk_exit",
            "label": "风控放行",
            "reason": "止损或弱势退出属于风险控制，AI 不阻断。",
        }

    settings_row = conn.execute("SELECT * FROM ai_settings WHERE id=1").fetchone()
    settings = _ai_settings_dict(settings_row)
    if not settings.get("enabled") or not settings.get("block_trade_enabled"):
        return {
            "allow": True,
            "status": "rule_only",
            "label": "规则执行",
            "reason": "AI 最终决策未启用，按规则计划执行。",
        }

    row = conn.execute(
        "SELECT * FROM ai_plan_reviews WHERE plan_id=? ORDER BY created_at DESC LIMIT 1",
        (plan["id"],),
    ).fetchone()
    if not row:
        return {
            "allow": False,
            "status": "missing_ai",
            "label": "待AI审核",
            "reason": "AI 最终决策缺失：暂无该计划审核，跳过执行。",
        }
    review = _ai_plan_review_dict(row)
    if str(review.get("created_at") or "") < str(plan["created_at"] or ""):
        return {
            "allow": False,
            "status": "stale_ai",
            "label": "AI过期",
            "reason": "AI 审核早于当前计划更新时间，需要重新审核后才能执行。",
            "review": review,
        }
    if require_trade_date and str(review.get("created_at") or "")[:10] != require_trade_date:
        return {
            "allow": False,
            "status": "stale_ai",
            "label": "待盘中复核",
            "reason": f"买入计划需要 {require_trade_date} 当天 AI 最终复核后才能实盘成交。",
            "review": review,
        }
    verdict = str(review.get("verdict") or "").lower()
    risk = str(review.get("risk_level") or "").lower()
    if verdict == "reject":
        return {
            "allow": False,
            "status": "rejected",
            "label": "AI否决",
            "reason": f"AI 最终决策否决：{review.get('reason') or review.get('suggestion')}",
            "review": review,
        }
    if risk == "high":
        return {
            "allow": False,
            "status": "high_risk",
            "label": "高风险阻断",
            "reason": f"AI 风险等级为 high，不自动执行：{review.get('reason') or review.get('suggestion')}",
            "review": review,
        }
    if verdict == "approve":
        return {
            "allow": True,
            "status": "approved" if risk == "low" else "approved_caution",
            "label": "AI放行" if risk == "low" else "谨慎放行",
            "reason": f"AI 最终决策放行：{review.get('verdict')} / {review.get('risk_level')}。{review.get('reason') or review.get('suggestion') or ''}",
            "review": review,
        }
    if verdict == "caution":
        return {
            "allow": True,
            "status": "conditional_ai",
            "label": "条件放行",
            "reason": f"AI 给出 caution，交由价格、额度和风控规则做最终触发：{review.get('reason') or review.get('suggestion')}",
            "review": review,
        }
    return {
        "allow": False,
        "status": "blocked",
        "label": "AI阻断",
        "reason": f"AI 最终决策未放行：{review.get('verdict')} / {review.get('risk_level')}，{review.get('reason') or review.get('suggestion')}",
        "review": review,
    }


def _ai_execution_gate(conn, plan, *, require_trade_date: str | None = None) -> dict:
    decision = _plan_final_decision(conn, plan, require_trade_date=require_trade_date)
    return {"allow": decision["allow"], "reason": decision["reason"], "decision": decision, "review": decision.get("review")}


def _record_plan_decision_audit(conn, plan, decision: dict, data_guard: dict | None = None) -> dict:
    data_guard = data_guard or {}
    review = decision.get("review") or {}
    created_at = now_iso()
    conn.execute(
        """
        INSERT INTO plan_decision_audits(
            plan_id, plan_date, trade_date, symbol, name, plan_type,
            decision_status, decision_label, allow_execute, reason,
            ai_review_id, data_guard_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan["id"],
            plan["plan_date"],
            plan["trade_date"],
            plan["symbol"],
            plan["name"],
            plan["plan_type"],
            decision.get("status") or "",
            decision.get("label") or "",
            1 if decision.get("allow") else 0,
            decision.get("reason") or "",
            int(review.get("id") or 0),
            json.dumps(data_guard, ensure_ascii=False),
            created_at,
        ),
    )
    row = conn.execute("SELECT * FROM plan_decision_audits WHERE rowid = last_insert_rowid()").fetchone()
    return _plan_decision_audit_dict(row)


def _plan_decision_audit_dict(row) -> dict | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "plan_id": row["plan_id"],
        "plan_date": row["plan_date"],
        "trade_date": row["trade_date"],
        "symbol": row["symbol"],
        "name": row["name"],
        "plan_type": row["plan_type"],
        "decision_status": row["decision_status"],
        "decision_label": row["decision_label"],
        "allow_execute": bool(row["allow_execute"]),
        "reason": row["reason"],
        "ai_review_id": row["ai_review_id"],
        "data_guard": json.loads(row["data_guard_json"] or "{}"),
        "created_at": row["created_at"],
    }


def _latest_plan_decision_audit(conn, plan_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM plan_decision_audits WHERE plan_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
        (plan_id,),
    ).fetchone()
    return _plan_decision_audit_dict(row)


def _build_ai_review_payload(conn, trade_date: str | None = None) -> dict:
    report = conn.execute("SELECT * FROM daily_reports ORDER BY created_at DESC LIMIT 1").fetchone()
    report_dict = _daily_report_dict(report) if report else None
    trade_date = trade_date or (report_dict["trade_date"] if report_dict else _latest_signal_trade_date(conn))
    state = _get_sim_state(conn)
    positions = [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions WHERE status='open' ORDER BY opened_at DESC, name").fetchall()]
    plans = _list_trade_plans_locked(conn, 40)
    pending_plans = [p for p in plans if p["status"] == "pending"][:20]
    candidates = _list_candidates_locked(conn, trade_date, 20) if trade_date else []
    evaluations = []
    if trade_date:
        rows = conn.execute(
            """
            SELECT * FROM stock_evaluations
            WHERE trade_date=?
            ORDER BY
              CASE
                WHEN groups_json LIKE '%holding%' THEN 0
                WHEN groups_json LIKE '%candidate%' THEN 1
                WHEN groups_json LIKE '%watch%' THEN 2
                ELSE 3
              END,
              COALESCE(score, 0) DESC
            LIMIT 40
            """,
            (trade_date,),
        ).fetchall()
        evaluations = [_stock_evaluation_dict(row) for row in rows]
        review_symbols = {
            item["symbol"]
            for group in (positions, pending_plans, candidates)
            for item in group
            if item.get("symbol")
        }
        evaluations = [item for item in evaluations if item.get("symbol") in review_symbols]
    return {
        "trade_date": trade_date,
        "rules": state["rules"],
        "market_snapshot": _market_snapshot(conn),
        "daily_report": report_dict,
        "candidates": candidates,
        "positions": positions,
        "trade_plans": pending_plans,
        "stock_evaluations": evaluations,
        "message_context": {},
        "instruction": "请审核这些模拟交易计划和个股状态。输出保守、可追溯的风险建议，不要承诺收益。",
    }


def _ai_review_symbol_inputs(payload: dict) -> list[dict]:
    symbols: dict[str, dict] = {}
    for group in ("trade_plans", "positions", "candidates", "stock_evaluations"):
        for item in payload.get(group) or []:
            symbol = str(item.get("symbol") or "").zfill(6)
            if not symbol or symbol == "000000":
                continue
            symbols.setdefault(symbol, {"symbol": symbol, "name": item.get("name") or symbol, "groups": []})
            if group not in symbols[symbol]["groups"]:
                symbols[symbol]["groups"].append(group)
    return list(symbols.values())


def _attach_message_context(payload: dict) -> dict:
    symbols = _ai_review_symbol_inputs(payload)
    payload = dict(payload)
    payload["message_context"] = {
        "description": "公开公告、新闻、研报证据。AI 只能基于这些输入判断消息面风险，不得补充外部事实。",
        "symbols": fetch_messages_for_symbols(symbols, days=45, limit_each=5),
    }
    return payload


def _ai_payload_summary(payload: dict) -> dict:
    messages = payload.get("message_context", {}).get("symbols", {}) if payload.get("message_context") else {}
    return {
        "trade_date": payload.get("trade_date"),
        "candidate_count": len(payload.get("candidates") or []),
        "position_count": len(payload.get("positions") or []),
        "pending_plan_count": len(payload.get("trade_plans") or []),
        "evaluation_count": len(payload.get("stock_evaluations") or []),
        "message_symbol_count": len(messages),
        "message_risks": [
            {
                "symbol": symbol,
                "risk_hint": data.get("risk_hint"),
                "negative_count": data.get("negative_count"),
                "positive_count": data.get("positive_count"),
                "sample_titles": [item.get("title") for item in (data.get("items") or [])[:3]],
            }
            for symbol, data in list(messages.items())[:20]
        ],
        "market_snapshot": payload.get("market_snapshot") or {},
        "rules": payload.get("rules") or {},
        "candidates": [
            {
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "score": item.get("score"),
                "strategy_tags": item.get("strategy_tags"),
                "entry_price": item.get("entry_price"),
                "stop_loss": item.get("stop_loss"),
            }
            for item in (payload.get("candidates") or [])[:10]
        ],
        "positions": [
            {
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "buyPrice": item.get("buyPrice"),
                "currentPrice": item.get("currentPrice"),
                "quantity": item.get("quantity"),
                "stopLoss": item.get("stopLoss"),
                "takeProfit": item.get("takeProfit"),
                "status": item.get("status"),
            }
            for item in (payload.get("positions") or [])[:12]
        ],
        "trade_plans": [
            {
                "id": item.get("id"),
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "plan_type": item.get("plan_type"),
                "action": item.get("action"),
                "trigger_price": item.get("trigger_price"),
                "quantity": item.get("quantity"),
                "reason": item.get("reason"),
            }
            for item in (payload.get("trade_plans") or [])[:12]
        ],
    }


def _store_ai_review_result(conn, payload: dict, result: dict) -> dict:
    trade_date = payload.get("trade_date") or datetime.now().strftime("%Y-%m-%d")
    created_at = now_iso()
    raw = dict(result)
    raw["_prompt"] = review_prompt()
    raw["_input_summary"] = _ai_payload_summary(payload)
    raw_json = json.dumps(raw, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO ai_daily_reports(
            report_date, trade_date, risk_level, summary, market_view,
            candidate_view, position_view, action_suggestion, raw_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().strftime("%Y-%m-%d"),
            trade_date,
            _normalize_ai_risk(result.get("risk_level")),
            result.get("summary", ""),
            result.get("market_view", ""),
            result.get("candidate_view", ""),
            result.get("position_view", ""),
            result.get("action_suggestion", ""),
            raw_json,
            created_at,
        ),
    )
    plans_by_id = {p["id"]: p for p in payload.get("trade_plans", [])}
    plan_reviews = []
    for item in result.get("plan_reviews", []):
        plan = plans_by_id.get(item.get("plan_id"), {})
        row_payload = {
            "trade_date": trade_date,
            "plan_id": item.get("plan_id") or "",
            "symbol": item.get("symbol") or plan.get("symbol") or "",
            "name": plan.get("name") or item.get("symbol") or "",
            "plan_type": plan.get("plan_type") or "",
            "verdict": item.get("verdict") or "caution",
            "risk_level": _normalize_ai_risk(item.get("risk_level")),
            "reason": item.get("reason") or "",
            "suggestion": item.get("suggestion") or "",
            "raw_json": json.dumps(item, ensure_ascii=False),
            "created_at": created_at,
        }
        if not row_payload["plan_id"]:
            continue
        conn.execute(
            """
            INSERT INTO ai_plan_reviews(
                trade_date, plan_id, symbol, name, plan_type, verdict, risk_level,
                reason, suggestion, raw_json, created_at
            ) VALUES (
                :trade_date, :plan_id, :symbol, :name, :plan_type, :verdict, :risk_level,
                :reason, :suggestion, :raw_json, :created_at
            )
            ON CONFLICT(plan_id) DO UPDATE SET
                trade_date=excluded.trade_date,
                symbol=excluded.symbol,
                name=excluded.name,
                plan_type=excluded.plan_type,
                verdict=excluded.verdict,
                risk_level=excluded.risk_level,
                reason=excluded.reason,
                suggestion=excluded.suggestion,
                raw_json=excluded.raw_json,
                created_at=excluded.created_at
            """,
            row_payload,
        )
        plan_reviews.append(row_payload)
    names_by_symbol = {item["symbol"]: item.get("name", item["symbol"]) for item in payload.get("stock_evaluations", [])}
    stock_reviews = []
    for item in result.get("stock_reviews", []):
        symbol = item.get("symbol") or ""
        if not symbol:
            continue
        row_payload = {
            "trade_date": trade_date,
            "symbol": symbol,
            "name": names_by_symbol.get(symbol, symbol),
            "review_kind": "daily",
            "decision": item.get("decision") or "观察",
            "risk_level": _normalize_ai_risk(item.get("risk_level")),
            "summary": item.get("summary") or "",
            "suggestion": item.get("suggestion") or "",
            "raw_json": json.dumps(item, ensure_ascii=False),
            "created_at": created_at,
        }
        conn.execute(
            """
            INSERT INTO ai_stock_reviews(
                trade_date, symbol, name, review_kind, decision, risk_level,
                summary, suggestion, raw_json, created_at
            ) VALUES (
                :trade_date, :symbol, :name, :review_kind, :decision, :risk_level,
                :summary, :suggestion, :raw_json, :created_at
            )
            ON CONFLICT(trade_date, symbol, review_kind) DO UPDATE SET
                name=excluded.name,
                decision=excluded.decision,
                risk_level=excluded.risk_level,
                summary=excluded.summary,
                suggestion=excluded.suggestion,
                raw_json=excluded.raw_json,
                created_at=excluded.created_at
            """,
            row_payload,
        )
        stock_reviews.append(row_payload)
    daily = conn.execute("SELECT * FROM ai_daily_reports ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "daily_report": _ai_daily_report_dict(daily),
        "plan_reviews": plan_reviews,
        "stock_reviews": stock_reviews,
    }


def run_ai_review() -> dict:
    with _DATA_LOCK:
        with get_conn() as conn:
            settings_row = conn.execute("SELECT * FROM ai_settings WHERE id=1").fetchone()
            settings = _ai_settings_dict(settings_row, reveal_key=True)
            if not settings["enabled"]:
                return {"status": "skipped", "message": "AI 审核未启用", "settings": _ai_settings_dict(settings_row)}
            if not settings["api_key"]:
                return {"status": "error", "message": "AI API key 未配置", "settings": _ai_settings_dict(settings_row)}
            payload = _build_ai_review_payload(conn)
    payload = _attach_message_context(payload)
    result = call_ai_review(settings["provider"], settings["api_key"], settings["model"], settings["base_url"], payload)
    with _DATA_LOCK:
        with get_conn() as conn:
            stored = _store_ai_review_result(conn, payload, result)
            settings_row = conn.execute("SELECT * FROM ai_settings WHERE id=1").fetchone()
    return {
        "status": "ok",
        "message": "AI 审核完成",
        "settings": _ai_settings_dict(settings_row),
        **stored,
    }


def _build_single_plan_ai_payload(conn, plan) -> dict:
    plan_dict = _plan_dict(plan)
    trade_date = plan["trade_date"] or _latest_signal_trade_date(conn)
    position_rows = conn.execute(
        "SELECT * FROM sim_positions WHERE symbol=? ORDER BY opened_at DESC",
        (plan["symbol"],),
    ).fetchall()
    positions = [_position_dict(row) for row in position_rows]
    candidates = [
        item for item in _list_candidates_locked(conn, trade_date, 80)
        if item["symbol"] == plan["symbol"]
    ] if trade_date else []
    evaluations = _list_stock_evaluations_locked(conn, plan["symbol"], 10)
    quote_row = conn.execute("SELECT * FROM realtime_quotes WHERE symbol=?", (plan["symbol"],)).fetchone()
    realtime = None
    if quote_row:
        realtime = {
            "symbol": quote_row["symbol"],
            "name": quote_row["name"],
            "price": quote_row["price"],
            "pct_chg": quote_row["pct_chg"],
            "updated_at": quote_row["updated_at"],
        }
    state = _get_sim_state(conn)
    return {
        "trade_date": trade_date,
        "rules": state["rules"],
        "market_snapshot": _market_snapshot(conn),
        "daily_report": _daily_report_dict(conn.execute("SELECT * FROM daily_reports ORDER BY created_at DESC LIMIT 1").fetchone()),
        "candidates": candidates,
        "positions": positions,
        "trade_plans": [plan_dict],
        "stock_evaluations": evaluations,
        "realtime_quote": realtime,
        "message_context": {},
        "instruction": "只审核 trade_plans 中这一条单股计划。请给出该计划是否可以执行、主要风险、触发条件和仓位建议。",
    }


def run_plan_ai_review(plan_id: str) -> dict:
    with _DATA_LOCK:
        with get_conn() as conn:
            settings_row = conn.execute("SELECT * FROM ai_settings WHERE id=1").fetchone()
            settings = _ai_settings_dict(settings_row, reveal_key=True)
            if not settings["enabled"]:
                return {"status": "skipped", "message": "AI 审核未启用", "settings": _ai_settings_dict(settings_row)}
            if not settings["api_key"]:
                return {"status": "error", "message": "AI API key 未配置", "settings": _ai_settings_dict(settings_row)}
            plan = conn.execute("SELECT * FROM trade_plans WHERE id=?", (plan_id,)).fetchone()
            if not plan:
                return {"status": "error", "message": "交易计划不存在"}
            payload = _build_single_plan_ai_payload(conn, plan)
    payload = _attach_message_context(payload)
    result = call_ai_review(settings["provider"], settings["api_key"], settings["model"], settings["base_url"], payload)
    with _DATA_LOCK:
        with get_conn() as conn:
            stored = _store_ai_review_result(conn, payload, result)
            settings_row = conn.execute("SELECT * FROM ai_settings WHERE id=1").fetchone()
            plan_row = conn.execute("SELECT * FROM trade_plans WHERE id=?", (plan_id,)).fetchone()
    plan_review = next((item for item in stored.get("plan_reviews", []) if item.get("plan_id") == plan_id), None)
    return {
        "status": "ok",
        "message": "单股计划 AI 分析完成",
        "settings": _ai_settings_dict(settings_row),
        "plan": _plan_dict(plan_row) if plan_row else None,
        "plan_review": plan_review,
        **stored,
    }


def _weak_position_reasons(position, current_price: float, signal: dict | None, trade_date: str, rules: dict) -> tuple[list[str], float, float, int]:
    signal_score = float(signal["score"]) if signal else 0.0
    ret_pct = _position_return_pct(position, current_price)
    hold_days = _holding_days(position["opened_at"], trade_date)
    weak_reasons = []
    if signal_score and signal_score < float(rules.get("weakScoreExit", 78)):
        weak_reasons.append(f"最新评分 {signal_score:.1f} 低于弱势线")
    if not signal:
        weak_reasons.append("最新候选信号消失")
    if ret_pct <= float(rules.get("weakReturnPct", -2.5)):
        weak_reasons.append(f"浮动收益 {ret_pct:.2f}% 低于弱势阈值")
    if hold_days >= int(rules.get("weakHoldDays", 5)) and ret_pct < 1:
        weak_reasons.append(f"持有 {hold_days} 天仍未走强")
    return weak_reasons, signal_score, ret_pct, hold_days


def _should_exit_weak_position(weak_reasons: list[str], signal: dict | None, ret_pct: float, hold_days: int, rules: dict) -> bool:
    if not weak_reasons:
        return False
    weak_return = float(rules.get("weakReturnPct", -2.5))
    weak_hold_days = int(rules.get("weakHoldDays", 5))
    if ret_pct <= weak_return:
        return True
    if not signal and hold_days >= weak_hold_days:
        return True
    return False


def _rank_rebalance_candidates(open_positions, latest_closes: dict[str, dict], latest_signals: dict[str, dict], trade_date: str, rules: dict) -> list[dict]:
    ranked = []
    for p in open_positions:
        close = latest_closes.get(p["symbol"])
        current_price = float(close["close"] if close else p["current_price"])
        signal = latest_signals.get(p["symbol"])
        weak_reasons, signal_score, ret_pct, hold_days = _weak_position_reasons(p, current_price, signal, trade_date, rules)
        if not weak_reasons:
            continue
        ranked.append({
            "row": p,
            "current_price": current_price,
            "score": signal_score,
            "return_pct": ret_pct,
            "hold_days": hold_days,
            "weak_reasons": weak_reasons,
            "weak_rank": signal_score + ret_pct * 2,
        })
    return sorted(ranked, key=lambda item: item["weak_rank"])


def _latest_bar_for_symbol(conn, symbol: str):
    return conn.execute(
        "SELECT trade_date, close FROM daily_bars WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
        (symbol,),
    ).fetchone()


def _refresh_open_positions_from_bars(conn) -> dict:
    updated = 0
    missing: list[str] = []
    latest_date = ""
    updated_at = now_iso()
    rows = conn.execute("SELECT id, symbol FROM sim_positions WHERE status = 'open'").fetchall()
    for row in rows:
        bar = _latest_bar_for_symbol(conn, row["symbol"])
        if not bar:
            missing.append(row["symbol"])
            continue
        conn.execute("UPDATE sim_positions SET current_price = ? WHERE id = ?", (float(bar["close"]), row["id"]))
        updated += 1
        latest_date = max(latest_date, bar["trade_date"])
    conn.execute("UPDATE sim_state SET updated_at=? WHERE id=1", (updated_at,))
    return {"updated": updated, "missing": missing, "latest_date": latest_date, "updated_at": updated_at}


def _latest_close_map(conn) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT b.symbol, b.trade_date, b.open, b.high, b.low, b.close
        FROM daily_bars b
        JOIN (
            SELECT symbol, MAX(trade_date) AS trade_date
            FROM daily_bars
            GROUP BY symbol
        ) latest ON latest.symbol = b.symbol AND latest.trade_date = b.trade_date
        """
    ).fetchall()
    return {
        row["symbol"]: {
            "trade_date": row["trade_date"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        for row in rows
    }


def _daily_report_dict(row) -> dict | None:
    if not row:
        return None
    keys = row.keys()
    return {
        "id": row["id"] if "id" in keys else None,
        "report_date": row["report_date"],
        "trade_date": row["trade_date"],
        "summary": row["summary"],
        "metrics": json.loads(row["metrics_json"]),
        "snapshot": json.loads(row["snapshot_json"]) if "snapshot_json" in keys and row["snapshot_json"] else {},
        "created_at": row["created_at"],
    }


def _ai_settings_dict(row, reveal_key: bool = False) -> dict:
    api_key = row["api_key"] or ""
    masked_key = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) >= 12 else ("已配置" if api_key else "")
    return {
        "enabled": bool(row["enabled"]),
        "provider": row["provider"],
        "model": row["model"],
        "base_url": row["base_url"] if "base_url" in row.keys() else ("https://api.deepseek.com" if row["provider"] == "deepseek" else "https://api.openai.com/v1"),
        "api_key": api_key if reveal_key else "",
        "masked_key": masked_key,
        "has_api_key": bool(api_key),
        "daily_review_enabled": bool(row["daily_review_enabled"]),
        "plan_review_enabled": bool(row["plan_review_enabled"]),
        "stock_review_enabled": bool(row["stock_review_enabled"]),
        "block_trade_enabled": bool(row["block_trade_enabled"]),
        "updated_at": row["updated_at"],
    }


def get_ai_settings() -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ai_settings WHERE id=1").fetchone()
        return _ai_settings_dict(row)


def update_ai_settings(req: AISettingsUpdate) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ai_settings WHERE id=1").fetchone()
        current = _ai_settings_dict(row, reveal_key=True)
        api_key = current["api_key"] if req.api_key is None else req.api_key.strip()
        payload = {
            "enabled": int(current["enabled"] if req.enabled is None else req.enabled),
            "provider": current["provider"] if req.provider is None else req.provider,
            "model": current["model"] if req.model is None else req.model.strip(),
            "base_url": current["base_url"] if req.base_url is None else req.base_url.strip().rstrip("/"),
            "api_key": api_key,
            "daily_review_enabled": int(current["daily_review_enabled"] if req.daily_review_enabled is None else req.daily_review_enabled),
            "plan_review_enabled": int(current["plan_review_enabled"] if req.plan_review_enabled is None else req.plan_review_enabled),
            "stock_review_enabled": int(current["stock_review_enabled"] if req.stock_review_enabled is None else req.stock_review_enabled),
            "block_trade_enabled": int(current["block_trade_enabled"] if req.block_trade_enabled is None else req.block_trade_enabled),
            "updated_at": now_iso(),
        }
        conn.execute(
            """
            UPDATE ai_settings SET
                enabled=:enabled,
                provider=:provider,
                model=:model,
                base_url=:base_url,
                api_key=:api_key,
                daily_review_enabled=:daily_review_enabled,
                plan_review_enabled=:plan_review_enabled,
                stock_review_enabled=:stock_review_enabled,
                block_trade_enabled=:block_trade_enabled,
                updated_at=:updated_at
            WHERE id=1
            """,
            payload,
        )
        row = conn.execute("SELECT * FROM ai_settings WHERE id=1").fetchone()
        return _ai_settings_dict(row)


def _quote_groups(conn) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for row in conn.execute("SELECT symbol FROM sim_positions WHERE status='open'").fetchall():
        groups.setdefault(row["symbol"], set()).add("holding")
    for row in conn.execute("SELECT symbol FROM watchlist").fetchall():
        groups.setdefault(row["symbol"], set()).add("watch")
    latest = conn.execute("SELECT MAX(trade_date) AS d FROM signals").fetchone()["d"]
    if latest:
        for row in conn.execute("SELECT symbol FROM signals WHERE trade_date=? ORDER BY score DESC LIMIT 50", (latest,)).fetchall():
            groups.setdefault(row["symbol"], set()).add("candidate")
    return groups


def _store_realtime_quotes(conn, quotes: dict[str, dict], groups: dict[str, set[str]]) -> None:
    for symbol, quote in quotes.items():
        conn.execute(
            """
            INSERT INTO realtime_quotes(symbol, name, price, pct_chg, source, groups_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                name=excluded.name,
                price=excluded.price,
                pct_chg=excluded.pct_chg,
                source=excluded.source,
                groups_json=excluded.groups_json,
                updated_at=excluded.updated_at
            """,
            (
                symbol,
                quote.get("name") or symbol,
                float(quote["price"]),
                float(quote.get("pct_chg") or 0),
                quote.get("source") or "",
                json.dumps(sorted(groups.get(symbol, set())), ensure_ascii=False),
                quote.get("updated_at") or now_iso(),
            ),
        )


def _market_snapshot(conn) -> dict:
    rows = conn.execute("SELECT * FROM realtime_quotes ORDER BY updated_at DESC, symbol").fetchall()
    items = []
    for row in rows:
        groups = json.loads(row["groups_json"])
        items.append({
            "symbol": row["symbol"],
            "name": row["name"],
            "price": row["price"],
            "pct_chg": row["pct_chg"],
            "source": row["source"],
            "groups": groups,
            "updated_at": row["updated_at"],
        })
    watched = [item for item in items if "watch" in item["groups"]]
    holdings = [item for item in items if "holding" in item["groups"]]
    candidates = [item for item in items if "candidate" in item["groups"]]
    all_changes = [float(item["pct_chg"] or 0) for item in items]
    return {
        "updated_at": max((item["updated_at"] for item in items), default=""),
        "count": len(items),
        "holding_count": len(holdings),
        "watch_count": len(watched),
        "candidate_count": len(candidates),
        "avg_pct_chg": round(sum(all_changes) / len(all_changes), 2) if all_changes else 0,
        "up_count": sum(1 for change in all_changes if change > 0),
        "down_count": sum(1 for change in all_changes if change < 0),
        "top_gainers": sorted(items, key=lambda item: item["pct_chg"], reverse=True)[:5],
        "top_losers": sorted(items, key=lambda item: item["pct_chg"])[:5],
    }


def get_simulation_state() -> dict:
    with get_conn() as conn:
        _expire_pending_trade_plans(conn)
        state = _get_sim_state(conn)
        positions = [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()]
        trades = [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT 100").fetchall()]
        report = conn.execute("SELECT * FROM daily_reports ORDER BY created_at DESC LIMIT 1").fetchone()
        automation = _get_automation_state(conn)
        return {
            **state,
            "positions": positions,
            "account_summary": _account_summary(state, positions),
            "trades": trades,
            "latest_report": _daily_report_dict(report),
            "latest_ai_report": _latest_ai_daily_report(conn),
            "ai_plan_reviews": _latest_ai_plan_reviews(conn),
            "automation": automation,
            "market_snapshot": _market_snapshot(conn),
        }


def refresh_position_quotes() -> dict:
    with _DATA_LOCK:
        with get_conn() as conn:
            state = _get_sim_state(conn)
            positions = [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()]
            trades = [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT 100").fetchall()]
            report = conn.execute("SELECT * FROM daily_reports ORDER BY created_at DESC LIMIT 1").fetchone()
            automation = _get_automation_state(conn)
            groups = _quote_groups(conn)
            rows = conn.execute("SELECT id, symbol, name FROM sim_positions WHERE status='open'").fetchall()
            symbols = sorted(groups)
            market_snapshot = _market_snapshot(conn)

    if not symbols:
        return {**state, "status": "ok", "message": "暂无股票需要刷新", "updated": 0, "missing": [], "quotes": [], "updated_at": now_iso(), "positions": positions, "account_summary": _account_summary(state, positions), "trades": trades, "latest_report": _daily_report_dict(report), "automation": automation, "market_snapshot": market_snapshot}
    try:
        quotes = fetch_realtime_quotes(symbols)
    except Exception as exc:
        detail = str(exc).split(" (Caused by", 1)[0]
        if "HTTPSConnectionPool" in detail or "ProxyError" in detail:
            detail = "行情源连接失败，请稍后重试"
        elif len(detail) > 120:
            detail = detail[:120] + "..."
        return {
            **state,
            "status": "error",
            "message": f"实时行情刷新失败：{detail}",
            "updated": 0,
            "missing": symbols,
            "quotes": [],
            "updated_at": now_iso(),
            "positions": positions,
            "account_summary": _account_summary(state, positions),
            "trades": trades,
            "latest_report": _daily_report_dict(report),
            "automation": automation,
            "market_snapshot": market_snapshot,
        }
    if not quotes:
        return {
            **state,
            "status": "error",
            "message": "实时行情刷新失败：行情源暂无响应",
            "updated": 0,
            "missing": symbols,
            "quotes": [],
            "updated_at": now_iso(),
            "positions": positions,
            "account_summary": _account_summary(state, positions),
            "trades": trades,
            "latest_report": _daily_report_dict(report),
            "automation": automation,
            "market_snapshot": market_snapshot,
        }

    with _DATA_LOCK:
        with get_conn() as conn:
            _store_realtime_quotes(conn, quotes, groups)
            updated = 0
            missing: list[str] = []
            quote_rows: list[dict] = []
            for row in rows:
                quote = quotes.get(row["symbol"])
                if not quote:
                    missing.append(row["symbol"])
                    continue
                conn.execute("UPDATE sim_positions SET current_price=? WHERE id=?", (quote["price"], row["id"]))
                updated += 1
                quote_rows.append(quote)
            conn.execute("UPDATE sim_state SET updated_at=? WHERE id=1", (now_iso(),))
            state = _get_sim_state(conn)
            positions = [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()]
            trades = [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT 100").fetchall()]
            report = conn.execute("SELECT * FROM daily_reports ORDER BY created_at DESC LIMIT 1").fetchone()
            automation = _get_automation_state(conn)
            snapshot = _market_snapshot(conn)
        return {
            "status": "ok",
            "message": f"已刷新 {len(quotes)} 只关注股票实时价，其中持仓 {updated} 只",
            "updated": updated,
            "missing": missing,
            "quotes": quote_rows,
            "updated_at": now_iso(),
            **state,
            "positions": positions,
            "account_summary": _account_summary(state, positions),
            "trades": trades,
            "latest_report": _daily_report_dict(report),
            "automation": automation,
            "market_snapshot": snapshot,
        }

def update_simulation_state(req: SimStateUpdate) -> dict:
    with get_conn() as conn:
        current = _get_sim_state(conn)
        cash = current["cash"] if req.cash is None else req.cash
        initial_cash = current["initial_cash"] if req.initial_cash is None else req.initial_cash
        rules = current["rules"] if req.rules is None else req.rules.model_dump()
        rules = _normalize_rules(rules)
        conn.execute(
            """
            UPDATE sim_state SET cash = ?, initial_cash = ?, rules_json = ?, updated_at = ? WHERE id = 1
            """,
            (cash, initial_cash, json.dumps(rules, ensure_ascii=False), now_iso()),
        )
    return get_simulation_state()


def _trade_costs(price: float, quantity: int, side: str, rules: dict) -> dict:
    gross = round(price * quantity, 2)
    commission = max(float(rules.get("minCommission", 5)), gross * float(rules.get("commissionRate", 0.0003)))
    transfer = gross * float(rules.get("transferFeeRate", 0.00001))
    tax = gross * float(rules.get("stampTaxRate", 0.0005)) if side == "sell" else 0
    fee = round(commission + transfer, 2)
    tax = round(tax, 2)
    net = round(gross + fee if side == "buy" else gross - fee - tax, 2)
    return {"amount": gross, "fee": fee, "tax": tax, "net_amount": net}


def _execution_price(price: float, side: str, rules: dict) -> float:
    slippage = float(rules.get("slippagePct", 0.15)) / 100
    adjusted = price * (1 + slippage if side == "buy" else 1 - slippage)
    return round(adjusted, 2)


def _insert_trade(conn, trade_date: str, action: str, symbol: str, name: str, price: float, quantity: int, reason: str, rules: dict | None = None, side: str | None = None) -> dict:
    rules = _normalize_rules(rules or {})
    side = side or ("sell" if "卖" in action else "buy")
    costs = _trade_costs(price, quantity, side, rules)
    trade = {
        "id": str(uuid.uuid4()),
        "trade_date": trade_date,
        "action": action,
        "symbol": symbol,
        "name": name,
        "price": round(price, 2),
        "quantity": quantity,
        "amount": costs["amount"],
        "fee": costs["fee"],
        "tax": costs["tax"],
        "net_amount": costs["net_amount"],
        "reason": reason,
        "created_at": now_iso(),
    }
    conn.execute(
        """
        INSERT INTO sim_trades(id, trade_date, action, symbol, name, price, quantity, amount, fee, tax, net_amount, reason, created_at)
        VALUES (:id, :trade_date, :action, :symbol, :name, :price, :quantity, :amount, :fee, :tax, :net_amount, :reason, :created_at)
        """,
        trade,
    )
    return trade


def _next_trade_date(trade_date: str) -> str:
    day = datetime.strptime(trade_date, "%Y-%m-%d").date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.strftime("%Y-%m-%d")


def _is_trade_day(value: str | None = None) -> bool:
    day = datetime.strptime(value, "%Y-%m-%d").date() if value else datetime.now().date()
    return day.weekday() < 5


def _market_phase(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    current = now.time()
    morning_start = now.replace(hour=9, minute=30, second=0, microsecond=0).time()
    morning_end = now.replace(hour=11, minute=30, second=0, microsecond=0).time()
    afternoon_start = now.replace(hour=13, minute=0, second=0, microsecond=0).time()
    afternoon_end = now.replace(hour=14, minute=57, second=0, microsecond=0).time()
    close_end = now.replace(hour=15, minute=0, second=0, microsecond=0).time()
    is_open = _is_trade_day(now.strftime("%Y-%m-%d")) and ((morning_start <= current <= morning_end) or (afternoon_start <= current <= afternoon_end))
    is_close_call = _is_trade_day(now.strftime("%Y-%m-%d")) and (afternoon_end < current <= close_end)
    return {
        "trade_date": now.strftime("%Y-%m-%d"),
        "is_trade_day": _is_trade_day(now.strftime("%Y-%m-%d")),
        "is_open": is_open,
        "is_close_call": is_close_call,
        "session": "continuous" if is_open else "close_call" if is_close_call else "closed",
    }


def _quote_is_today(quote: dict, trade_date: str) -> bool:
    updated_at = str(quote.get("updated_at") or "")
    return updated_at.startswith(trade_date)


def _quote_for_plan(conn, plan, quotes: dict[str, dict], trade_date: str) -> dict | None:
    quote = quotes.get(plan["symbol"])
    if quote and _quote_is_today(quote, trade_date):
        return quote
    row = conn.execute("SELECT * FROM realtime_quotes WHERE symbol=?", (plan["symbol"],)).fetchone()
    if row and str(row["updated_at"] or "").startswith(trade_date):
        return dict(row)
    return None


def _quote_price_for_plan(conn, plan, quotes: dict[str, dict], trade_date: str) -> tuple[float | None, str]:
    quote = _quote_for_plan(conn, plan, quotes, trade_date)
    if quote:
        return float(quote["price"]), ""
    return None, f"{plan['name']} 无 {trade_date} 实时行情，暂不执行"


def _is_buy_plan(plan_type: str) -> bool:
    return plan_type in ("next_buy", "rebalance_buy", "add_buy")


def _plan_data_guard(conn, plan, quotes: dict[str, dict], trade_date: str, quote_error: str = "") -> dict:
    checked_at = now_iso()
    if not _is_buy_plan(plan["plan_type"]):
        return {
            "allow": True,
            "status": "not_required",
            "label": "无需买入熔断",
            "reason": "非买入计划不执行买入数据熔断。",
            "checked_at": checked_at,
        }
    quote = _quote_for_plan(conn, plan, quotes, trade_date)
    if not quote:
        reason = f"{plan['name']} 无 {trade_date} 当天实时行情，买入熔断。"
        if quote_error:
            reason = f"实时行情获取异常：{quote_error[:120]}，买入熔断。"
        return {
            "allow": False,
            "status": "missing_quote",
            "label": "行情缺失",
            "reason": reason,
            "checked_at": checked_at,
        }
    updated_at = str(quote.get("updated_at") or "")
    if not updated_at.startswith(trade_date):
        return {
            "allow": False,
            "status": "stale_quote",
            "label": "行情过期",
            "reason": f"{plan['name']} 行情更新时间 {updated_at or '未知'}，不是 {trade_date} 当天数据，买入熔断。",
            "checked_at": checked_at,
            "quote_updated_at": updated_at,
        }
    price = float(quote.get("price") or 0)
    trigger = float(plan["trigger_price"] or 0)
    if price <= 0:
        return {
            "allow": False,
            "status": "invalid_price",
            "label": "价格异常",
            "reason": f"{plan['name']} 实时价 {price} 无效，买入熔断。",
            "checked_at": checked_at,
            "quote_updated_at": updated_at,
            "current_price": price,
        }
    latest_bar = conn.execute(
        """
        SELECT trade_date, close
        FROM daily_bars
        WHERE symbol=?
        ORDER BY trade_date DESC
        LIMIT 1
        """,
        (plan["symbol"],),
    ).fetchone()
    if not latest_bar or str(latest_bar["trade_date"] or "") < str(plan["trade_date"] or ""):
        latest_bar_date = latest_bar["trade_date"] if latest_bar else "无"
        return {
            "allow": False,
            "status": "stale_daily_bar",
            "label": "日线过期",
            "reason": f"{plan['name']} 最新日线 {latest_bar_date} 早于计划依据 {plan['trade_date']}，买入熔断。",
            "checked_at": checked_at,
            "quote_updated_at": updated_at,
            "current_price": price,
        }
    if trigger <= 0:
        return {
            "allow": False,
            "status": "invalid_trigger",
            "label": "触发价异常",
            "reason": f"{plan['name']} 计划触发价 {trigger} 无效，买入熔断。",
            "checked_at": checked_at,
            "quote_updated_at": updated_at,
            "current_price": price,
            "trigger_price": trigger,
        }
    deviation_pct = abs(price - trigger) / trigger * 100
    tolerance_pct = float(_get_sim_state(conn)["rules"].get("buyPriceTolerancePct", 0.8) or 0.8)
    chase_pct = (price - trigger) / trigger * 100
    if chase_pct > tolerance_pct:
        return {
            "allow": False,
            "status": "price_not_triggered",
            "label": "价格未触发",
            "reason": f"{plan['name']} 实时价 {price:.2f} 高于触发价 {trigger:.2f}，涨幅 {chase_pct:.2f}% 超过买入容忍 {tolerance_pct:.2f}%，不追高。",
            "checked_at": checked_at,
            "quote_updated_at": updated_at,
            "current_price": round(price, 3),
            "trigger_price": round(trigger, 3),
            "deviation_pct": round(deviation_pct, 2),
            "tolerance_pct": round(tolerance_pct, 2),
        }
    if deviation_pct > 8:
        return {
            "allow": False,
            "status": "price_deviation",
            "label": "偏离过大",
            "reason": f"{plan['name']} 实时价 {price:.2f} 与触发价 {trigger:.2f} 偏离 {deviation_pct:.1f}%，买入熔断。",
            "checked_at": checked_at,
            "quote_updated_at": updated_at,
            "current_price": round(price, 3),
            "trigger_price": round(trigger, 3),
            "deviation_pct": round(deviation_pct, 2),
        }
    return {
        "allow": True,
        "status": "ok",
        "label": "数据正常",
        "reason": "实时行情、计划依据和触发价检查通过。",
        "checked_at": checked_at,
        "quote_updated_at": updated_at,
        "current_price": round(price, 3),
        "trigger_price": round(trigger, 3),
        "deviation_pct": round(deviation_pct, 2),
    }


def _position_can_sell(pos, trade_date: str) -> bool:
    opened_at = str(pos["opened_at"] or "")
    return bool(opened_at) and opened_at < trade_date


def _row_get(row, key: str, default=None):
    return row[key] if key in row.keys() else default


def _position_stage(pos) -> int:
    return int(_row_get(pos, "take_profit_stage", 0) or 0)


def _position_peak(pos, current_price: float | None = None) -> float:
    values = [float(_row_get(pos, "peak_price", 0) or 0), float(pos["current_price"] or 0)]
    if current_price is not None:
        values.append(float(current_price or 0))
    return max(values)


def _partial_take_profit_quantity(total_qty: int, rules: dict) -> int:
    total_qty = int(total_qty or 0)
    if total_qty <= 0:
        return 0
    pct = max(1.0, min(100.0, float(rules.get("takeProfitSellPct", 50) or 50)))
    sell_qty = int(total_qty * pct / 100 / 100) * 100
    if sell_qty <= 0:
        sell_qty = min(100, total_qty)
    min_remain = max(100, int(rules.get("minRemainLot", 100) or 100))
    remaining = total_qty - sell_qty
    if remaining < min_remain or remaining < 100:
        return total_qty
    return sell_qty


def _trailing_stop_price(pos, current_price: float, rules: dict) -> float:
    if _position_stage(pos) <= 0:
        return 0.0
    peak = _position_peak(pos, current_price)
    pct = max(0.5, min(30.0, float(rules.get("trailingStopPct", 4) or 4)))
    return round(peak * (1 - pct / 100), 2)


def _trailing_stop_from_peak(peak: float, rules: dict) -> float:
    pct = max(0.5, min(30.0, float(rules.get("trailingStopPct", 4) or 4)))
    return round(float(peak or 0) * (1 - pct / 100), 2)


def _update_position_market_price(conn, pos, current_price: float, rules: dict) -> float:
    peak = _position_peak(pos, current_price)
    stop_loss = float(pos["stop_loss"] or 0)
    if _position_stage(pos) > 0:
        stop_loss = max(stop_loss, _trailing_stop_from_peak(peak, rules))
    conn.execute(
        "UPDATE sim_positions SET current_price=?, peak_price=?, stop_loss=? WHERE id=?",
        (round(current_price, 2), round(peak, 2), round(stop_loss, 2), pos["id"]),
    )
    return peak


def _sell_sim_position(conn, trade_date: str, action: str, pos, price: float, quantity: int, reason: str, rules: dict) -> dict | None:
    old_qty = int(pos["quantity"] or 0)
    sell_qty = min(int(quantity or 0), old_qty)
    if old_qty <= 0 or sell_qty <= 0:
        return None
    trade = _insert_trade(conn, trade_date, action, pos["symbol"], pos["name"], price, sell_qty, reason, rules, "sell")
    if sell_qty >= old_qty:
        conn.execute(
            "UPDATE sim_positions SET status='closed', current_price=?, exit_price=?, exit_reason=?, closed_at=? WHERE id=?",
            (price, price, reason, trade_date, pos["id"]),
        )
        return trade

    peak = max(_position_peak(pos, price), price)
    trailing_stop = _trailing_stop_from_peak(peak, rules)
    stop_loss = max(float(pos["stop_loss"] or 0), trailing_stop)
    remaining = old_qty - sell_qty
    closed_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO sim_positions(
            id, symbol, name, buy_price, quantity, current_price, stop_loss, take_profit,
            take_profit_stage, peak_price, status, exit_price, exit_reason, opened_at, closed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'closed', ?, ?, ?, ?)
        """,
        (
            closed_id,
            pos["symbol"],
            pos["name"],
            pos["buy_price"],
            sell_qty,
            price,
            pos["stop_loss"],
            pos["take_profit"],
            _position_stage(pos),
            peak,
            price,
            reason,
            pos["opened_at"],
            trade_date,
        ),
    )
    conn.execute(
        """
        UPDATE sim_positions
        SET quantity=?, current_price=?, stop_loss=?, take_profit_stage=1, peak_price=?
        WHERE id=?
        """,
        (remaining, price, round(stop_loss, 2), round(peak, 2), pos["id"]),
    )
    return trade


def _execute_trailing_sells_replay(conn, trade_date: str, latest_closes: dict[str, dict], cash: float, sell_count: int, logs: list[str], trades: list[dict], rules: dict, eligible_symbols: set[str] | None = None) -> tuple[float, int]:
    max_sells = int(rules.get("maxDailySells", rules.get("maxDailyTrades", 5)))
    rows = conn.execute("SELECT * FROM sim_positions WHERE status='open' AND take_profit_stage > 0").fetchall()
    for pos in rows:
        if eligible_symbols is not None and pos["symbol"] not in eligible_symbols:
            continue
        if sell_count >= max_sells:
            logs.append("卖出数量达到今日上限，剩余移动止损持仓保留")
            break
        bar = latest_closes.get(pos["symbol"])
        if not bar:
            continue
        peak = max(_position_peak(pos), float(bar.get("high") or bar.get("close") or 0))
        trailing_stop = _trailing_stop_from_peak(peak, rules)
        if float(bar.get("low") or bar.get("close") or 0) > trailing_stop:
            conn.execute(
                "UPDATE sim_positions SET current_price=?, peak_price=?, stop_loss=? WHERE id=?",
                (float(bar["close"]), round(peak, 2), max(float(pos["stop_loss"] or 0), trailing_stop), pos["id"]),
            )
            continue
        price = _execution_price(trailing_stop, "sell", rules)
        reason = f"止盈后回撤触发移动止损，阶段最高 {peak:.2f}，止损 {trailing_stop:.2f}"
        trade = _sell_sim_position(conn, trade_date, "计划卖出", pos, price, int(pos["quantity"]), reason, rules)
        if not trade:
            continue
        cash += float(trade["net_amount"])
        trades.append(trade)
        sell_count += 1
        logs.append(f"{pos['name']} {reason}，卖出 {pos['quantity']} 股")
    return cash, sell_count


def _execute_trailing_sells_live(conn, quotes: dict[str, dict], trade_date: str, cash: float, sell_count: int, logs: list[str], trades: list[dict], rules: dict) -> tuple[float, int]:
    max_sells = int(rules.get("maxDailySells", rules.get("maxDailyTrades", 5)))
    rows = conn.execute("SELECT * FROM sim_positions WHERE status='open' AND take_profit_stage > 0").fetchall()
    for pos in rows:
        if sell_count >= max_sells:
            logs.append("卖出数量达到今日上限，剩余移动止损持仓保留")
            break
        if not _position_can_sell(pos, trade_date):
            continue
        quote = quotes.get(pos["symbol"])
        if quote and _quote_is_today(quote, trade_date):
            current_price = float(quote["price"])
        else:
            row = conn.execute("SELECT * FROM realtime_quotes WHERE symbol=?", (pos["symbol"],)).fetchone()
            if not row or not str(row["updated_at"] or "").startswith(trade_date):
                continue
            current_price = float(row["price"])
        peak = _update_position_market_price(conn, pos, current_price, rules)
        trailing_stop = _trailing_stop_from_peak(peak, rules)
        if current_price > trailing_stop:
            continue
        price = _execution_price(current_price, "sell", rules)
        reason = f"止盈后实时回撤触发移动止损，阶段最高 {peak:.2f}，止损 {trailing_stop:.2f}"
        trade = _sell_sim_position(conn, trade_date, "实时卖出", pos, price, int(pos["quantity"]), reason, rules)
        if not trade:
            continue
        cash += float(trade["net_amount"])
        trades.append(trade)
        sell_count += 1
        logs.append(f"{pos['name']} {reason}，卖出 {pos['quantity']} 股，成交价 {price}")
    return cash, sell_count


def _plan_sort_key(row, rules: dict) -> tuple:
    priority = {"sell_stop": 0, "sell_weak": 1, "sell_take_profit": 2, "rebalance_sell": 3, "rebalance_buy": 4, "next_buy": 5, "add_buy": 6, "hold": 7}
    if not rules.get("sellPriority", True):
        priority = {"next_buy": 0, "add_buy": 1, "rebalance_buy": 2, "sell_stop": 3, "sell_weak": 4, "sell_take_profit": 5, "rebalance_sell": 6, "hold": 7}
    return (row["plan_date"], priority.get(row["plan_type"], 9), row["created_at"])


def _latest_signal_trade_date(conn) -> str | None:
    row = conn.execute(
        """
        SELECT trade_date AS d
        FROM daily_bars
        ORDER BY trade_date DESC
        LIMIT 1
        """
    ).fetchone()
    return row["d"] if row else None


def _plan_dict(row, conn=None) -> dict:
    item = {
        "id": row["id"],
        "plan_date": row["plan_date"],
        "trade_date": row["trade_date"],
        "symbol": row["symbol"],
        "name": row["name"],
        "plan_type": row["plan_type"],
        "action": row["action"],
        "trigger_price": row["trigger_price"],
        "stop_loss": row["stop_loss"],
        "take_profit": row["take_profit"],
        "quantity": row["quantity"],
        "status": row["status"],
        "reason": row["reason"],
        "created_at": row["created_at"],
    }
    if conn is not None:
        item["final_decision"] = _plan_final_decision(conn, row)
        item["latest_decision_audit"] = _latest_plan_decision_audit(conn, row["id"])
    return item


def _upsert_trade_plan(conn, *, plan_date: str, trade_date: str, symbol: str, name: str, plan_type: str, action: str, trigger_price: float, stop_loss: float = 0, take_profit: float = 0, quantity: int = 0, reason: str) -> dict:
    plan_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO trade_plans(
            id, plan_date, trade_date, symbol, name, plan_type, action, trigger_price,
            stop_loss, take_profit, quantity, status, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        ON CONFLICT(plan_date, symbol, plan_type) DO UPDATE SET
            trade_date=excluded.trade_date,
            name=excluded.name,
            action=excluded.action,
            trigger_price=excluded.trigger_price,
            stop_loss=excluded.stop_loss,
            take_profit=excluded.take_profit,
            quantity=excluded.quantity,
            status='pending',
            reason=excluded.reason,
            created_at=excluded.created_at
        """,
        (plan_id, plan_date, trade_date, symbol, name, plan_type, action, round(trigger_price, 2), round(stop_loss, 2), round(take_profit, 2), int(quantity), reason, now_iso()),
    )
    row = conn.execute("SELECT * FROM trade_plans WHERE plan_date=? AND symbol=? AND plan_type=?", (plan_date, symbol, plan_type)).fetchone()
    return _plan_dict(row, conn)


def _retire_stale_trade_plans(conn, plan_date: str, trade_date: str, active_plans: list[dict]) -> int:
    active_keys = {(item["symbol"], item["plan_type"]) for item in active_plans}
    rows = conn.execute(
        """
        SELECT * FROM trade_plans
        WHERE plan_date=? AND trade_date=? AND status='pending'
        """,
        (plan_date, trade_date),
    ).fetchall()
    retired = 0
    for row in rows:
        key = (row["symbol"], row["plan_type"])
        if key in active_keys:
            continue
        conn.execute(
            """
            UPDATE trade_plans
            SET status='skipped',
                reason=?
            WHERE id=?
            """,
            (f"{row['reason']} 计划已按最新评分重算，当前不再满足执行条件，自动作废。", row["id"]),
        )
        retired += 1
    return retired


def _expire_pending_trade_plans(conn, cutoff_date: str | None = None) -> int:
    cutoff_date = cutoff_date or _market_phase()["trade_date"]
    rows = conn.execute(
        """
        SELECT * FROM trade_plans
        WHERE status='pending' AND plan_date < ?
        """,
        (cutoff_date,),
    ).fetchall()
    expired = 0
    for row in rows:
        expire_reason = f"计划日 {row['plan_date']} 已早于当前交易日 {cutoff_date}，未在交易池触发，自动过期。"
        existing_reason = (row["reason"] or "").strip()
        next_reason = existing_reason if expire_reason in existing_reason else f"{existing_reason} {expire_reason}".strip()
        conn.execute(
            """
            UPDATE trade_plans
            SET status='expired',
                reason=?
            WHERE id=?
            """,
            (next_reason, row["id"]),
        )
        _record_plan_decision_audit(
            conn,
            row,
            {
                "allow": False,
                "status": "expired",
                "label": "计划过期",
                "reason": expire_reason,
            },
            {
                "allow": False,
                "status": "expired",
                "label": "计划过期",
                "reason": expire_reason,
                "checked_at": now_iso(),
            },
        )
        expired += 1
    return expired


def list_trade_plans(limit: int = 80) -> list[dict]:
    with get_conn() as conn:
        _expire_pending_trade_plans(conn)
        return _list_trade_plans_locked(conn, limit)


def _list_trade_plans_locked(conn, limit: int = 80) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM trade_plans
        ORDER BY plan_date DESC, created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_plan_dict(row, conn) for row in rows]


def _plan_execution_bar(conn, symbol: str, plan_date: str):
    return conn.execute(
        """
        SELECT * FROM daily_bars
        WHERE symbol=? AND trade_date >= ?
        ORDER BY trade_date
        LIMIT 1
        """,
        (symbol, plan_date),
    ).fetchone()


def generate_trade_plan() -> dict:
    with _DATA_LOCK:
        with get_conn() as conn:
            return _generate_trade_plan_locked(conn)


def _generate_trade_plan_locked(conn) -> dict:
        latest = _latest_signal_trade_date(conn)
        if not latest:
            return {"status": "skipped", "message": "暂无候选信号", "plans": []}
        plan_date = _next_trade_date(latest)
        today = datetime.now().strftime("%Y-%m-%d")
        if plan_date < today:
            message = f"最新有效行情为 {latest}，对应计划日 {plan_date} 已过，跳过重复生成；请等待下一交易日数据更新后再生成计划。"
            report = generate_daily_report(conn, latest, [message], report_kind="stale_plan_skipped")
            return {"status": "skipped", "message": message, "trade_date": latest, "plan_date": plan_date, "plans": _list_trade_plans_locked(conn), "latest_report": report}
        state = _get_sim_state(conn)
        rules = state["rules"]
        plans: list[dict] = []
        latest_closes = _latest_close_map(conn)

        open_positions = conn.execute("SELECT * FROM sim_positions WHERE status='open'").fetchall()
        latest_candidates = _list_candidates_locked(conn, latest)
        latest_signal_by_symbol = {c["symbol"]: c for c in latest_candidates}
        planned_exit_symbols: set[str] = set()
        for p in open_positions:
            close = latest_closes.get(p["symbol"])
            current_price = float(close["close"] if close else p["current_price"])
            peak = _update_position_market_price(conn, p, current_price, rules)
            effective_stop_loss = float(p["stop_loss"] or 0)
            if _position_stage(p) > 0:
                effective_stop_loss = max(effective_stop_loss, _trailing_stop_from_peak(peak, rules))
            if effective_stop_loss and current_price <= effective_stop_loss * 1.01:
                planned_exit_symbols.add(p["symbol"])
                plans.append(_upsert_trade_plan(
                    conn,
                    plan_date=plan_date,
                    trade_date=latest,
                    symbol=p["symbol"],
                    name=p["name"],
                    plan_type="sell_stop",
                    action="次日观察卖出",
                    trigger_price=effective_stop_loss,
                    stop_loss=effective_stop_loss,
                    take_profit=float(p["take_profit"]),
                    quantity=int(p["quantity"]),
                    reason=f"收盘价 {current_price:.2f} 接近/跌破止损 {effective_stop_loss:.2f}，次日优先处理风险。",
                ))
            elif _position_stage(p) == 0 and p["take_profit"] and current_price >= float(p["take_profit"]) * 0.99:
                plans.append(_upsert_trade_plan(
                    conn,
                    plan_date=plan_date,
                    trade_date=latest,
                    symbol=p["symbol"],
                    name=p["name"],
                    plan_type="sell_take_profit",
                    action="次日观察止盈",
                    trigger_price=float(p["take_profit"]),
                    stop_loss=float(p["stop_loss"]),
                    take_profit=float(p["take_profit"]),
                    quantity=int(p["quantity"]),
                    reason=f"收盘价 {current_price:.2f} 接近/达到止盈 {float(p['take_profit']):.2f}，次日观察分批止盈。",
                ))
            else:
                weak_reasons, signal_score, ret_pct, hold_days = _weak_position_reasons(
                    p,
                    current_price,
                    latest_signal_by_symbol.get(p["symbol"]),
                    latest,
                    rules,
                )
                if _should_exit_weak_position(weak_reasons, latest_signal_by_symbol.get(p["symbol"]), ret_pct, hold_days, rules):
                    planned_exit_symbols.add(p["symbol"])
                    plans.append(_upsert_trade_plan(
                        conn,
                        plan_date=plan_date,
                        trade_date=latest,
                        symbol=p["symbol"],
                        name=p["name"],
                        plan_type="sell_weak",
                        action="弱势退出",
                        trigger_price=current_price,
                        stop_loss=float(p["stop_loss"]),
                        take_profit=float(p["take_profit"]),
                        quantity=int(p["quantity"]),
                        reason=f"持仓转弱，次日按交易池退出。原因：{'、'.join(weak_reasons)}。",
                    ))
                    continue
                hold_reason = f"收盘价 {current_price:.2f} 未触发止损/止盈，次日继续按计划观察。"
                if _position_stage(p) > 0:
                    trailing = _trailing_stop_from_peak(peak, rules)
                    hold_reason = f"已完成第一止盈，阶段最高价 {peak:.2f}，移动止损参考 {trailing:.2f}，次日继续观察。"
                plans.append(_upsert_trade_plan(
                    conn,
                    plan_date=plan_date,
                    trade_date=latest,
                    symbol=p["symbol"],
                    name=p["name"],
                    plan_type="hold",
                    action="继续持有",
                    trigger_price=current_price,
                    stop_loss=float(p["stop_loss"]),
                    take_profit=float(p["take_profit"]),
                    quantity=int(p["quantity"]),
                    reason=hold_reason,
                ))

        open_symbols = {row["symbol"] for row in conn.execute("SELECT symbol FROM sim_positions WHERE status='open'").fetchall()}
        candidates = [
            c for c in latest_candidates
            if c["score"] >= float(rules["minScore"]) and c["symbol"] not in open_symbols
        ]
        effective_open_count = len(open_symbols) - len(planned_exit_symbols)
        buy_profile = _dynamic_buy_profile(rules, _market_state_for_trade_date(conn, latest))
        buy_slots = max(0, int(rules["maxPositions"]) - effective_open_count)
        buy_slots = min(buy_slots, int(buy_profile["max_buys"]))
        daily_buy_budget = float(state["initial_cash"]) * float(buy_profile["max_buy_pct"]) / 100
        planned_buy_budget = 0.0
        planned_buy_count = 0
        for c in candidates:
            if planned_buy_count >= buy_slots:
                break
            target_pct = min(float(rules["maxSinglePct"]), float(c["position_pct"]))
            remaining_daily_budget = max(0.0, daily_buy_budget - planned_buy_budget)
            budget = min(float(state["cash"]) - planned_buy_budget, float(state["initial_cash"]) * target_pct / 100, remaining_daily_budget)
            quantity = int(budget / c["entry_price"] / 100) * 100
            if quantity <= 0:
                continue
            planned_buy_budget += float(c["entry_price"]) * quantity
            planned_buy_count += 1
            plans.append(_upsert_trade_plan(
                conn,
                plan_date=plan_date,
                trade_date=latest,
                symbol=c["symbol"],
                name=c["name"],
                plan_type="next_buy",
                action="次日条件买入",
                trigger_price=float(c["entry_price"]),
                stop_loss=float(c["stop_loss"]),
                take_profit=float(c["take_profit_1"]),
                quantity=quantity,
                reason=f"{'、'.join(c.get('strategy_tags') or ['综合策略'])} 评分 {c['score']}。{buy_profile['label']}动态额度：最多 {buy_profile['max_buys']} 笔 / {buy_profile['max_buy_pct']}%。次日价格接近观察价且未明显高开时模拟买入。",
            ))
        if rules.get("rebalanceEnabled", True) and buy_slots <= 0 and candidates:
            weak_positions = _rank_rebalance_candidates(open_positions, latest_closes, latest_signal_by_symbol, latest, rules)
            used_replacements: set[str] = set()
            max_rebalances = min(int(rules.get("maxDailyRebalances", 1)), int(buy_profile["max_buys"]))
            for candidate in candidates:
                if len(used_replacements) >= max_rebalances:
                    break
                if float(candidate["score"]) < float(rules.get("rebalanceMinNewScore", 88)):
                    continue
                weak = next((item for item in weak_positions if item["row"]["symbol"] not in used_replacements), None)
                if not weak:
                    break
                score_gap = float(candidate["score"]) - float(weak["score"] or 0)
                if weak["score"] and score_gap < float(rules.get("rebalanceMinScoreGap", 8)):
                    continue
                position = weak["row"]
                target_pct = min(float(rules["maxSinglePct"]), float(candidate["position_pct"]))
                budget = min(float(state["cash"]) + weak["current_price"] * int(position["quantity"]), float(state["initial_cash"]) * target_pct / 100)
                quantity = int(budget / candidate["entry_price"] / 100) * 100
                if quantity <= 0:
                    continue
                used_replacements.add(position["symbol"])
                conn.execute(
                    "UPDATE trade_plans SET status='skipped' WHERE plan_date=? AND symbol=? AND plan_type='hold'",
                    (plan_date, position["symbol"]),
                )
                plans = [
                    item for item in plans
                    if not (item["symbol"] == position["symbol"] and item["plan_type"] == "hold")
                ]
                plans.append(_upsert_trade_plan(
                    conn,
                    plan_date=plan_date,
                    trade_date=latest,
                    symbol=position["symbol"],
                    name=position["name"],
                    plan_type="rebalance_sell",
                    action="调仓卖出",
                    trigger_price=weak["current_price"],
                    stop_loss=float(position["stop_loss"]),
                    take_profit=float(position["take_profit"]),
                    quantity=int(position["quantity"]),
                    reason=f"为候选 {candidate['name']}({candidate['symbol']}) 评分 {candidate['score']} 腾出仓位；弱持仓原因：{'、'.join(weak['weak_reasons'])}。",
                ))
                plans.append(_upsert_trade_plan(
                    conn,
                    plan_date=plan_date,
                    trade_date=latest,
                    symbol=candidate["symbol"],
                    name=candidate["name"],
                    plan_type="rebalance_buy",
                    action="调仓买入",
                    trigger_price=float(candidate["entry_price"]),
                    stop_loss=float(candidate["stop_loss"]),
                    take_profit=float(candidate["take_profit_1"]),
                    quantity=quantity,
                    reason=f"替换弱持仓 {position['name']}，新候选评分 {candidate['score']}，策略 {'、'.join(candidate.get('strategy_tags') or ['综合策略'])}。",
                ))
        if rules.get("addPositionEnabled", False):
            add_count = 0
            max_adds = int(rules.get("maxAddsPerSymbol", 1))
            for p in open_positions:
                if planned_buy_count >= int(buy_profile["max_buys"]):
                    break
                signal = latest_signal_by_symbol.get(p["symbol"])
                if not signal or float(signal["score"]) < float(rules.get("addMinScore", 88)):
                    continue
                close = latest_closes.get(p["symbol"])
                current_price = float(close["close"] if close else p["current_price"])
                ret_pct = _position_return_pct(p, current_price)
                if ret_pct < float(rules.get("addMinProfitPct", 4)):
                    continue
                if add_count >= max_adds:
                    break
                remaining_daily_budget = max(0.0, daily_buy_budget - planned_buy_budget)
                budget = min(float(state["cash"]) - planned_buy_budget, float(state["initial_cash"]) * float(rules.get("addPositionPct", 3)) / 100, remaining_daily_budget)
                quantity = int(budget / signal["entry_price"] / 100) * 100
                if quantity <= 0:
                    continue
                planned_buy_budget += float(signal["entry_price"]) * quantity
                planned_buy_count += 1
                plans.append(_upsert_trade_plan(
                    conn,
                    plan_date=plan_date,
                    trade_date=latest,
                    symbol=signal["symbol"],
                    name=signal["name"],
                    plan_type="add_buy",
                    action="盈利追加",
                    trigger_price=float(signal["entry_price"]),
                    stop_loss=float(signal["stop_loss"]),
                    take_profit=float(signal["take_profit_1"]),
                    quantity=quantity,
                    reason=f"已有持仓盈利 {ret_pct:.2f}%，且最新评分 {signal['score']}，按小仓位追加。",
                ))
                add_count += 1
        logs = [f"生成 {len(plans)} 条次日交易计划，计划交易日 {plan_date}"]
        retired_count = _retire_stale_trade_plans(conn, plan_date, latest, plans)
        if retired_count:
            logs.append(f"作废 {retired_count} 条不再符合最新评分的旧计划")
        report = generate_daily_report(conn, latest, logs)
        return {"status": "ok", "message": f"已生成 {plan_date} 交易计划", "trade_date": latest, "plan_date": plan_date, "plans": plans, "latest_report": report}


def _ensure_today_trade_plan_locked(conn) -> dict | None:
    today = datetime.now().strftime("%Y-%m-%d")
    latest = _latest_signal_trade_date(conn)
    if not latest:
        return None
    plan_date = _next_trade_date(latest)
    if plan_date != today:
        return None
    existing = conn.execute(
        "SELECT COUNT(*) AS c FROM trade_plans WHERE plan_date=?",
        (today,),
    ).fetchone()["c"]
    if existing:
        return None
    return _generate_trade_plan_locked(conn)


def _pending_execution_symbols(plan_id: str | None = None) -> list[str]:
    today = datetime.now().strftime("%Y-%m-%d")
    with _DATA_LOCK:
        with get_conn() as conn:
            _expire_pending_trade_plans(conn, today)
            if plan_id:
                rows = conn.execute(
                    """
                    SELECT symbol FROM trade_plans WHERE id = ?
                    UNION
                    SELECT symbol FROM sim_positions WHERE status='open'
                    """,
                    (plan_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT symbol FROM trade_plans
                    WHERE status='pending' AND plan_date <= ?
                    UNION
                    SELECT symbol FROM sim_positions
                    WHERE status='open'
                    """,
                    (today,),
                ).fetchall()
            return sorted({row["symbol"] for row in rows})


def execute_trade_plans(force: bool = False, plan_id: str | None = None) -> dict:
    if force:
        with _DATA_LOCK:
            with get_conn() as conn:
                return _execute_trade_plans_replay_locked(conn, force, plan_id)

    if not plan_id:
        with _DATA_LOCK:
            with get_conn() as conn:
                _ensure_today_trade_plan_locked(conn)

    symbols = _pending_execution_symbols(plan_id)
    quotes: dict[str, dict] = {}
    quote_error = ""
    if symbols:
        try:
            quotes = fetch_realtime_quotes(symbols)
        except Exception as exc:
            quote_error = str(exc).split(" (Caused by", 1)[0]

    with _DATA_LOCK:
        with get_conn() as conn:
            if quotes:
                _store_realtime_quotes(conn, quotes, _quote_groups(conn))
            return _execute_trade_plans_live_locked(conn, quotes, quote_error, plan_id)


def _execute_trade_plans_replay_locked(conn, force: bool = False, plan_id: str | None = None) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        if not force:
            _expire_pending_trade_plans(conn, today)
        state = _get_sim_state(conn)
        rules = state["rules"]
        cash = float(state["cash"])
        buy_count = 0
        sell_count = 0
        buy_usage_by_date: dict[str, dict] = {}
        logs: list[str] = []
        trades: list[dict] = []
        latest_closes = _latest_close_map(conn)
        trailing_symbols_before = {
            row["symbol"]
            for row in conn.execute("SELECT symbol FROM sim_positions WHERE status='open' AND take_profit_stage > 0").fetchall()
        }
        if plan_id:
            rows = conn.execute(
                """
                SELECT * FROM trade_plans
                WHERE id = ? AND (status='pending' OR ?)
                ORDER BY plan_date, created_at
                """,
                (plan_id, 1 if force else 0),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM trade_plans
                WHERE status='pending' AND (plan_date <= ? OR ?)
                ORDER BY plan_date, created_at
                """,
                (today, 1 if force else 0),
            ).fetchall()
        if rules.get("sellPriority", True):
            priority = {"sell_stop": 0, "sell_weak": 1, "sell_take_profit": 2, "rebalance_sell": 3, "rebalance_buy": 4, "next_buy": 5, "add_buy": 6, "hold": 7}
            rows = sorted(rows, key=lambda row: (row["plan_date"], priority.get(row["plan_type"], 9), row["created_at"]))
        rebalance_sell_count = 0
        rebalance_buy_count = 0
        for plan in rows:
            bar = _plan_execution_bar(conn, plan["symbol"], plan["plan_date"])
            if not bar:
                logs.append(f"{plan['name']} 暂无 {plan['plan_date']} 后行情，等待同步后再检查")
                continue
            if plan["plan_type"] != "hold":
                ai_gate = _ai_execution_gate(conn, plan)
                _record_plan_decision_audit(conn, plan, ai_gate["decision"])
                if not ai_gate["allow"]:
                    conn.execute("UPDATE trade_plans SET status='skipped', reason=? WHERE id=?", (ai_gate["reason"], plan["id"]))
                    logs.append(f"{plan['name']} {ai_gate['reason']}")
                    continue
            price = float(bar["close"])
            trade_date = bar["trade_date"]
            if plan["plan_type"].startswith("sell") or plan["plan_type"] == "rebalance_sell":
                if sell_count >= int(rules.get("maxDailySells", rules.get("maxDailyTrades", 5))):
                    logs.append("卖出数量达到今日上限，剩余卖出计划保留待检查")
                    continue
                if plan["plan_type"] == "rebalance_sell" and rebalance_sell_count >= int(rules.get("maxDailyRebalances", 1)):
                    logs.append("调仓数量达到今日上限，剩余调仓计划保留")
                    continue
                pos = conn.execute("SELECT * FROM sim_positions WHERE symbol=? AND status='open' LIMIT 1", (plan["symbol"],)).fetchone()
                if not pos:
                    conn.execute("UPDATE trade_plans SET status='skipped' WHERE id=?", (plan["id"],))
                    continue
                stop_trigger = max(float(pos["stop_loss"] or 0), float(plan["stop_loss"] or 0))
                if plan["plan_type"] == "sell_stop" and float(bar["low"]) > stop_trigger:
                    continue
                if plan["plan_type"] == "sell_take_profit" and _position_stage(pos) > 0:
                    conn.execute("UPDATE trade_plans SET status='observed' WHERE id=?", (plan["id"],))
                    continue
                if plan["plan_type"] == "sell_take_profit" and float(bar["high"]) < float(pos["take_profit"]):
                    continue
                price = float(stop_trigger if plan["plan_type"] == "sell_stop" else pos["take_profit"] if plan["plan_type"] == "sell_take_profit" else bar["open"])
                price = _execution_price(price, "sell", rules)
                if plan["plan_type"] == "sell_stop":
                    reason = "按次日止损计划卖出"
                elif plan["plan_type"] == "sell_take_profit":
                    reason = "按次日止盈计划卖出"
                elif plan["plan_type"] == "sell_weak":
                    reason = "按弱持仓退出计划卖出"
                else:
                    reason = "按调仓计划卖出弱持仓"
                quantity = _partial_take_profit_quantity(int(pos["quantity"]), rules) if plan["plan_type"] == "sell_take_profit" else int(pos["quantity"])
                trade = _sell_sim_position(conn, trade_date, "计划卖出", pos, price, quantity, reason, rules)
                if not trade:
                    continue
                cash += float(trade["net_amount"])
                trades.append(trade)
                suffix = "，剩余仓位进入移动止损" if quantity < int(pos["quantity"]) else ""
                logs.append(f"{pos['name']} {reason} {quantity} 股{suffix}")
                conn.execute("UPDATE trade_plans SET status='executed' WHERE id=?", (plan["id"],))
                sell_count += 1
                if plan["plan_type"] == "rebalance_sell":
                    rebalance_sell_count += 1
            elif plan["plan_type"] in ("next_buy", "rebalance_buy", "add_buy"):
                if plan["plan_type"] == "rebalance_buy" and rebalance_buy_count >= int(rules.get("maxDailyRebalances", 1)):
                    logs.append("调仓买入数量达到今日上限，剩余调仓买入计划保留")
                    continue
                exists = conn.execute("SELECT 1 FROM sim_positions WHERE symbol=? AND status='open'", (plan["symbol"],)).fetchone()
                if plan["plan_type"] == "add_buy" and not exists:
                    conn.execute("UPDATE trade_plans SET status='skipped' WHERE id=?", (plan["id"],))
                    logs.append(f"{plan['name']} 已无可追加持仓，追加计划跳过")
                    continue
                if exists and plan["plan_type"] != "add_buy":
                    conn.execute("UPDATE trade_plans SET status='skipped' WHERE id=?", (plan["id"],))
                    continue
                open_count = conn.execute("SELECT COUNT(*) AS c FROM sim_positions WHERE status='open'").fetchone()["c"]
                if open_count >= int(rules["maxPositions"]) and plan["plan_type"] != "add_buy":
                    continue
                limit_price = float(plan["trigger_price"]) * (1 + float(rules.get("buyPriceTolerancePct", 2)) / 100)
                if float(bar["low"]) > limit_price or float(bar["open"]) > limit_price:
                    continue
                price = _execution_price(min(float(bar["open"]), limit_price), "buy", rules)
                profile = _dynamic_buy_profile(rules, _market_state_for_trade_date(conn, plan["trade_date"]))
                usage = buy_usage_by_date.setdefault(trade_date, _daily_buy_usage(conn, trade_date, float(state["initial_cash"])))
                if usage["count"] >= int(profile["max_buys"]):
                    logs.append(f"{plan['name']} 动态买入额度达到上限（{profile['label']}最多 {profile['max_buys']} 笔 / {profile['max_buy_pct']}%），计划保留")
                    continue
                remaining_budget = float(state["initial_cash"]) * float(profile["max_buy_pct"]) / 100 - float(usage["amount"])
                quantity = min(int(plan["quantity"]), _max_quantity_for_buy_budget(price, remaining_budget, rules))
                if quantity <= 0:
                    logs.append(f"{plan['name']} 动态买入资金额度不足（{profile['label']}上限 {profile['max_buy_pct']}%），未买入")
                    continue
                cost = _trade_costs(price, quantity, "buy", rules)["net_amount"]
                if quantity <= 0 or cost > cash:
                    continue
                cash -= cost
                if plan["plan_type"] == "add_buy" and exists:
                    pos = conn.execute("SELECT * FROM sim_positions WHERE symbol=? AND status='open' LIMIT 1", (plan["symbol"],)).fetchone()
                    old_qty = int(pos["quantity"])
                    new_qty = old_qty + quantity
                    avg_price = round((float(pos["buy_price"]) * old_qty + price * quantity) / new_qty, 2)
                    peak = max(_position_peak(pos, price), price)
                    conn.execute(
                        "UPDATE sim_positions SET buy_price=?, quantity=?, current_price=?, stop_loss=?, take_profit=?, peak_price=? WHERE id=?",
                        (avg_price, new_qty, price, plan["stop_loss"], plan["take_profit"], peak, pos["id"]),
                    )
                    action = "计划追加"
                    reason = "按盈利追加计划执行"
                else:
                    position_id = str(uuid.uuid4())
                    conn.execute(
                        """
                        INSERT INTO sim_positions(
                            id, symbol, name, buy_price, quantity, current_price, stop_loss, take_profit,
                            take_profit_stage, peak_price, status, exit_price, exit_reason, opened_at, closed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'open', 0, '', ?, '')
                        """,
                        (position_id, plan["symbol"], plan["name"], price, quantity, price, plan["stop_loss"], plan["take_profit"], price, trade_date),
                    )
                    action = "计划买入" if plan["plan_type"] == "next_buy" else "调仓买入"
                    reason = "按次日买入计划执行" if plan["plan_type"] == "next_buy" else "按调仓计划买入强候选"
                trade = _insert_trade(conn, trade_date, action, plan["symbol"], plan["name"], price, quantity, reason, rules, "buy")
                trades.append(trade)
                logs.append(f"{plan['name']} {reason} {quantity} 股")
                conn.execute("UPDATE trade_plans SET status='executed' WHERE id=?", (plan["id"],))
                buy_count += 1
                usage["count"] += 1
                usage["amount"] += cost
                usage["pct"] = round(float(usage["amount"]) / float(state["initial_cash"]) * 100, 4) if state["initial_cash"] else 0
                if plan["plan_type"] == "rebalance_buy":
                    rebalance_buy_count += 1
            elif plan["plan_type"] == "hold":
                conn.execute("UPDATE trade_plans SET status='observed' WHERE id=?", (plan["id"],))
        if not plan_id:
            cash, sell_count = _execute_trailing_sells_replay(conn, today, latest_closes, cash, sell_count, logs, trades, rules, trailing_symbols_before)
        conn.execute("UPDATE sim_state SET cash=?, updated_at=? WHERE id=1", (round(cash, 2), now_iso()))
        report = generate_daily_report(conn, today, logs or ["次日计划检查完成，暂无触发成交"])
        state_after = _get_sim_state(conn)
        positions_after = [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()]
        trades_after = [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT 100").fetchall()]
        return {"status": "ok", "message": f"计划执行完成，成交 {len(trades)} 笔", "logs": logs, "new_trades": trades, **state_after, "positions": positions_after, "account_summary": _account_summary(state_after, positions_after), "trades": trades_after, "latest_report": report, "plans": _list_trade_plans_locked(conn)}


def _execute_trade_plans_live_locked(conn, quotes: dict[str, dict], quote_error: str = "", plan_id: str | None = None) -> dict:
        phase = _market_phase()
        today = phase["trade_date"]
        _expire_pending_trade_plans(conn, today)
        state = _get_sim_state(conn)
        rules = state["rules"]
        logs: list[str] = []
        trades: list[dict] = []
        if not phase["is_trade_day"]:
            message = f"{today} 不是交易日，实盘执行跳过"
            return {"status": "skipped", "message": message, "logs": [message], "new_trades": [], **state, "positions": [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()], "account_summary": _account_summary(state, [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()]), "trades": [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT 100").fetchall()], "latest_report": None, "plans": _list_trade_plans_locked(conn)}
        if not phase["is_open"]:
            message = f"当前不在连续竞价交易时段，实盘执行跳过；force=true 可做历史回放"
            return {"status": "skipped", "message": message, "logs": [message], "new_trades": [], **state, "positions": [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()], "account_summary": _account_summary(state, [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()]), "trades": [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT 100").fetchall()], "latest_report": None, "plans": _list_trade_plans_locked(conn)}
        if quote_error:
            logs.append(f"实时行情获取异常：{quote_error[:120]}")

        cash = float(state["cash"])
        buy_count = 0
        sell_count = 0
        rebalance_sell_count = 0
        rebalance_buy_count = 0
        buy_usage_by_date: dict[str, dict] = {}
        if plan_id:
            rows = conn.execute(
                """
                SELECT * FROM trade_plans
                WHERE id = ? AND status='pending' AND plan_date <= ?
                ORDER BY plan_date, created_at
                """,
                (plan_id, today),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM trade_plans
                WHERE status='pending' AND plan_date <= ?
                ORDER BY plan_date, created_at
                """,
                (today,),
            ).fetchall()
        rows = sorted(rows, key=lambda row: _plan_sort_key(row, rules))
        if not rows:
            logs.append("该计划不存在、不是待执行状态或尚未进入交易池" if plan_id else f"{today} 暂无待执行计划")

        for plan in rows:
            if plan["plan_type"] == "hold":
                conn.execute("UPDATE trade_plans SET status='observed' WHERE id=?", (plan["id"],))
                continue
            data_guard = _plan_data_guard(conn, plan, quotes, today, quote_error)
            if not data_guard["allow"]:
                decision = {
                    "allow": False,
                    "status": "data_guard",
                    "label": "数据熔断",
                    "reason": data_guard["reason"],
                }
                _record_plan_decision_audit(conn, plan, decision, data_guard)
                logs.append(data_guard["reason"])
                continue
            ai_gate = _ai_execution_gate(conn, plan)
            _record_plan_decision_audit(conn, plan, ai_gate["decision"], data_guard)
            if not ai_gate["allow"]:
                if not _is_buy_plan(plan["plan_type"]):
                    conn.execute("UPDATE trade_plans SET status='skipped', reason=? WHERE id=?", (ai_gate["reason"], plan["id"]))
                logs.append(f"{plan['name']} {ai_gate['reason']}")
                continue
            current_price, missing_reason = _quote_price_for_plan(conn, plan, quotes, today)
            if current_price is None:
                logs.append(missing_reason)
                continue
            if plan["plan_type"].startswith("sell") or plan["plan_type"] == "rebalance_sell":
                if sell_count >= int(rules.get("maxDailySells", rules.get("maxDailyTrades", 5))):
                    logs.append("卖出数量达到今日上限，剩余卖出计划保留")
                    continue
                if plan["plan_type"] == "rebalance_sell" and rebalance_sell_count >= int(rules.get("maxDailyRebalances", 1)):
                    logs.append("调仓数量达到今日上限，剩余调仓计划保留")
                    continue
                pos = conn.execute("SELECT * FROM sim_positions WHERE symbol=? AND status='open' LIMIT 1", (plan["symbol"],)).fetchone()
                if not pos:
                    conn.execute("UPDATE trade_plans SET status='skipped' WHERE id=?", (plan["id"],))
                    continue
                _update_position_market_price(conn, pos, current_price, rules)
                if not _position_can_sell(pos, today):
                    logs.append(f"{pos['name']} 为当日买入或开仓日期异常，按 T+1 规则不能卖出")
                    continue
                stop_trigger = max(float(pos["stop_loss"] or 0), float(plan["stop_loss"] or 0))
                if plan["plan_type"] == "sell_stop" and current_price > stop_trigger:
                    continue
                if plan["plan_type"] == "sell_take_profit" and _position_stage(pos) > 0:
                    conn.execute("UPDATE trade_plans SET status='observed' WHERE id=?", (plan["id"],))
                    continue
                if plan["plan_type"] == "sell_take_profit" and current_price < float(pos["take_profit"]):
                    continue
                price = _execution_price(current_price, "sell", rules)
                if plan["plan_type"] == "sell_stop":
                    reason = "实时价触发止损卖出"
                elif plan["plan_type"] == "sell_take_profit":
                    reason = "实时价触发止盈卖出"
                elif plan["plan_type"] == "sell_weak":
                    reason = "实时执行弱持仓退出"
                else:
                    reason = "按调仓计划实时卖出弱持仓"
                quantity = _partial_take_profit_quantity(int(pos["quantity"]), rules) if plan["plan_type"] == "sell_take_profit" else int(pos["quantity"])
                trade = _sell_sim_position(conn, today, "实时卖出", pos, price, quantity, reason, rules)
                if not trade:
                    continue
                cash += float(trade["net_amount"])
                trades.append(trade)
                suffix = "，剩余仓位进入移动止损" if quantity < int(pos["quantity"]) else ""
                logs.append(f"{pos['name']} {reason} {quantity} 股，成交价 {price}{suffix}")
                conn.execute("UPDATE trade_plans SET status='executed' WHERE id=?", (plan["id"],))
                sell_count += 1
                if plan["plan_type"] == "rebalance_sell":
                    rebalance_sell_count += 1
            elif plan["plan_type"] in ("next_buy", "rebalance_buy", "add_buy"):
                if plan["plan_type"] == "rebalance_buy" and rebalance_buy_count >= int(rules.get("maxDailyRebalances", 1)):
                    logs.append("调仓买入数量达到今日上限，剩余调仓买入计划保留")
                    continue
                exists = conn.execute("SELECT 1 FROM sim_positions WHERE symbol=? AND status='open'", (plan["symbol"],)).fetchone()
                if plan["plan_type"] == "add_buy" and not exists:
                    conn.execute("UPDATE trade_plans SET status='skipped' WHERE id=?", (plan["id"],))
                    logs.append(f"{plan['name']} 已无可追加持仓，追加计划跳过")
                    continue
                if exists and plan["plan_type"] != "add_buy":
                    conn.execute("UPDATE trade_plans SET status='skipped' WHERE id=?", (plan["id"],))
                    continue
                open_count = conn.execute("SELECT COUNT(*) AS c FROM sim_positions WHERE status='open'").fetchone()["c"]
                if open_count >= int(rules["maxPositions"]) and plan["plan_type"] != "add_buy":
                    logs.append(f"{plan['name']} 仓位已满，买入计划保留")
                    continue
                limit_price = float(plan["trigger_price"]) * (1 + float(rules.get("buyPriceTolerancePct", 2)) / 100)
                if current_price > limit_price:
                    continue
                price = _execution_price(current_price, "buy", rules)
                profile = _dynamic_buy_profile(rules, _market_state_for_trade_date(conn, plan["trade_date"]))
                usage = buy_usage_by_date.setdefault(today, _daily_buy_usage(conn, today, float(state["initial_cash"])))
                if usage["count"] >= int(profile["max_buys"]):
                    logs.append(f"{plan['name']} 动态买入额度达到上限（{profile['label']}最多 {profile['max_buys']} 笔 / {profile['max_buy_pct']}%），计划保留")
                    continue
                remaining_budget = float(state["initial_cash"]) * float(profile["max_buy_pct"]) / 100 - float(usage["amount"])
                quantity = min(int(plan["quantity"]), _max_quantity_for_buy_budget(price, remaining_budget, rules))
                if quantity <= 0:
                    logs.append(f"{plan['name']} 动态买入资金额度不足（{profile['label']}上限 {profile['max_buy_pct']}%），未买入")
                    continue
                cost = _trade_costs(price, quantity, "buy", rules)["net_amount"]
                if quantity <= 0 or cost > cash:
                    logs.append(f"{plan['name']} 资金不足或数量无效，未买入")
                    continue
                cash -= cost
                if plan["plan_type"] == "add_buy" and exists:
                    pos = conn.execute("SELECT * FROM sim_positions WHERE symbol=? AND status='open' LIMIT 1", (plan["symbol"],)).fetchone()
                    old_qty = int(pos["quantity"])
                    new_qty = old_qty + quantity
                    avg_price = round((float(pos["buy_price"]) * old_qty + price * quantity) / new_qty, 2)
                    peak = max(_position_peak(pos, price), price)
                    conn.execute(
                        "UPDATE sim_positions SET buy_price=?, quantity=?, current_price=?, stop_loss=?, take_profit=?, peak_price=? WHERE id=?",
                        (avg_price, new_qty, price, plan["stop_loss"], plan["take_profit"], peak, pos["id"]),
                    )
                    action = "实时追加"
                    reason = "按盈利追加计划实时执行"
                else:
                    position_id = str(uuid.uuid4())
                    conn.execute(
                        """
                        INSERT INTO sim_positions(
                            id, symbol, name, buy_price, quantity, current_price, stop_loss, take_profit,
                            take_profit_stage, peak_price, status, exit_price, exit_reason, opened_at, closed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'open', 0, '', ?, '')
                        """,
                        (position_id, plan["symbol"], plan["name"], price, quantity, price, plan["stop_loss"], plan["take_profit"], price, today),
                    )
                    action = "实时买入" if plan["plan_type"] == "next_buy" else "实时调仓买入"
                    reason = "交易池实时价符合次日买入计划" if plan["plan_type"] == "next_buy" else "按调仓计划实时买入强候选"
                trade = _insert_trade(conn, today, action, plan["symbol"], plan["name"], price, quantity, reason, rules, "buy")
                trades.append(trade)
                logs.append(f"{plan['name']} {reason} {quantity} 股，成交价 {price}")
                conn.execute("UPDATE trade_plans SET status='executed' WHERE id=?", (plan["id"],))
                buy_count += 1
                usage["count"] += 1
                usage["amount"] += cost
                usage["pct"] = round(float(usage["amount"]) / float(state["initial_cash"]) * 100, 4) if state["initial_cash"] else 0
                if plan["plan_type"] == "rebalance_buy":
                    rebalance_buy_count += 1

        if not plan_id:
            cash, sell_count = _execute_trailing_sells_live(conn, quotes, today, cash, sell_count, logs, trades, rules)
        conn.execute("UPDATE sim_state SET cash=?, updated_at=? WHERE id=1", (round(cash, 2), now_iso()))
        if trades:
            report = generate_daily_report(conn, today, logs, report_kind="live_execution")
        else:
            report = None
        state_after = _get_sim_state(conn)
        positions_after = [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()]
        trades_after = [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT 100").fetchall()]
        message = f"{'单计划' if plan_id else '实盘时序'}执行完成，成交 {len(trades)} 笔"
        return {"status": "ok", "message": message, "logs": logs or ["实盘时序检查完成，暂无触发成交"], "new_trades": trades, **state_after, "positions": positions_after, "account_summary": _account_summary(state_after, positions_after), "trades": trades_after, "latest_report": report, "plans": _list_trade_plans_locked(conn)}


def _automation_dict(row) -> dict:
    keys = row.keys()
    mode = row["mode"] if "mode" in keys else ("managed" if row["enabled"] else "manual")
    execution_time = row["execution_time"] if "execution_time" in keys else "09:30"
    planning_time = row["planning_time"] if "planning_time" in keys else row["run_time"]
    return {
        "enabled": bool(row["enabled"]),
        "mode": mode,
        "run_time": row["run_time"],
        "execution_time": execution_time,
        "planning_time": planning_time,
        "sync_before_run": bool(row["sync_before_run"]),
        "last_run_date": row["last_run_date"],
        "last_execution_date": row["last_execution_date"] if "last_execution_date" in keys else "",
        "last_plan_date": row["last_plan_date"] if "last_plan_date" in keys else row["last_run_date"],
        "last_status": row["last_status"],
        "last_message": row["last_message"],
        "last_check_at": row["last_check_at"],
        "updated_at": row["updated_at"],
    }


def _get_automation_state(conn) -> dict:
    row = conn.execute("SELECT * FROM automation_state WHERE id = 1").fetchone()
    return _automation_dict(row)


def get_automation_state() -> dict:
    with get_conn() as conn:
        return _get_automation_state(conn)


def update_automation_state(
    enabled: bool | None = None,
    run_time: str | None = None,
    sync_before_run: bool | None = None,
    mode: str | None = None,
    execution_time: str | None = None,
    planning_time: str | None = None,
) -> dict:
    with get_conn() as conn:
        current = _get_automation_state(conn)
        next_mode = current.get("mode") or "assist" if mode is None else mode
        if next_mode == "manual":
            next_enabled = False if enabled is None else enabled
        else:
            next_enabled = True if enabled is None else enabled
        next_execution_time = current["execution_time"] if execution_time is None else execution_time
        next_planning_time = current["planning_time"] if planning_time is None else planning_time
        next_run_time = next_planning_time if run_time is None else run_time
        next_sync = current["sync_before_run"] if sync_before_run is None else sync_before_run
        conn.execute(
            """
            UPDATE automation_state
            SET enabled=?, mode=?, run_time=?, execution_time=?, planning_time=?, sync_before_run=?, updated_at=?
            WHERE id=1
            """,
            (1 if next_enabled else 0, next_mode, next_run_time, next_execution_time, next_planning_time, 1 if next_sync else 0, now_iso()),
        )
        return _get_automation_state(conn)


def _record_automation_result(conn, status: str, message: str, run_date: str | None = None, phase: str = "plan") -> dict:
    current = _get_automation_state(conn)
    next_run_date = current["last_run_date"] if run_date is None else run_date
    next_execution_date = current.get("last_execution_date", "")
    next_plan_date = current.get("last_plan_date", "")
    if run_date is not None and phase == "execution":
        next_execution_date = run_date
    if run_date is not None and phase == "plan":
        next_plan_date = run_date
    stamp = now_iso()
    conn.execute(
        """
        UPDATE automation_state
        SET last_run_date=?, last_execution_date=?, last_plan_date=?,
            last_status=?, last_message=?, last_check_at=?, updated_at=?
        WHERE id=1
        """,
        (next_run_date, next_execution_date, next_plan_date, status, message, stamp, stamp),
    )
    return _get_automation_state(conn)


def record_automation_result(status: str, message: str, run_date: str | None = None, phase: str = "plan") -> dict:
    with _DATA_LOCK:
        with get_conn() as conn:
            return _record_automation_result(conn, status, message, run_date, phase)


def run_auto_simulation(force: bool = False) -> dict:
    with _DATA_LOCK:
        with get_conn() as conn:
            return _run_auto_simulation_locked(conn, force)


def _run_auto_simulation_locked(conn, force: bool = False) -> dict:
        latest = _latest_signal_trade_date(conn)
        if not latest:
            state = _get_sim_state(conn)
            _record_automation_result(conn, "skipped", "暂无候选信号")
            return {"status": "skipped", "message": "暂无候选信号", **state, "positions": [], "account_summary": _account_summary(state, []), "trades": [], "latest_report": None}

        state = _get_sim_state(conn)
        if state["last_run_trade_date"] == latest and not force:
            _refresh_open_positions_from_bars(conn)
            message = f"{latest} 已执行过自动模拟"
            automation = _record_automation_result(conn, "skipped", message, datetime.now().strftime("%Y-%m-%d"))
            positions = [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()]
            trades = [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT 100").fetchall()]
            report = conn.execute("SELECT * FROM daily_reports ORDER BY created_at DESC LIMIT 1").fetchone()
            return {"status": "skipped", "message": message, **state, "positions": positions, "account_summary": _account_summary(state, positions), "trades": trades, "latest_report": _daily_report_dict(report), "automation": automation}

        rules = state["rules"]
        cash = float(state["cash"])
        trades: list[dict] = []
        logs: list[str] = []
        sell_count = 0

        candidates = _list_candidates_locked(conn, latest)
        latest_by_symbol = {c["symbol"]: c for c in candidates}
        latest_closes = _latest_close_map(conn)
        trailing_symbols_before = {
            row["symbol"]
            for row in conn.execute("SELECT symbol FROM sim_positions WHERE status='open' AND take_profit_stage > 0").fetchall()
        }

        open_positions = conn.execute("SELECT * FROM sim_positions WHERE status = 'open'").fetchall()
        for p in open_positions:
            signal = latest_by_symbol.get(p["symbol"])
            close = latest_closes.get(p["symbol"])
            current_price = float(close["close"] if close else signal["entry_price"] if signal else p["current_price"])
            price_date = close["trade_date"] if close else latest
            if sell_count >= int(rules.get("maxDailySells", rules.get("maxDailyTrades", 5))):
                _update_position_market_price(conn, p, current_price, rules)
                continue
            _update_position_market_price(conn, p, current_price, rules)
            if p["stop_loss"] and current_price <= p["stop_loss"]:
                sell_price = _execution_price(current_price, "sell", rules)
                trade = _sell_sim_position(conn, price_date, "自动卖出", p, sell_price, int(p["quantity"]), "触发止损", rules)
                if not trade:
                    continue
                cash += float(trade["net_amount"])
                trades.append(trade)
                sell_count += 1
                logs.append(f"{p['name']} 触发止损，卖出 {p['quantity']} 股")
            elif _position_stage(p) == 0 and p["take_profit"] and current_price >= p["take_profit"]:
                sell_price = _execution_price(current_price, "sell", rules)
                quantity = _partial_take_profit_quantity(int(p["quantity"]), rules)
                trade = _sell_sim_position(conn, price_date, "自动卖出", p, sell_price, quantity, "触发第一止盈", rules)
                if not trade:
                    continue
                cash += float(trade["net_amount"])
                trades.append(trade)
                sell_count += 1
                suffix = "，剩余仓位进入移动止损" if quantity < int(p["quantity"]) else ""
                logs.append(f"{p['name']} 触发第一止盈，卖出 {quantity} 股{suffix}")

        cash, sell_count = _execute_trailing_sells_replay(conn, latest, latest_closes, cash, sell_count, logs, trades, rules, trailing_symbols_before)

        open_symbols = {
            row["symbol"]
            for row in conn.execute("SELECT symbol FROM sim_positions WHERE status = 'open'").fetchall()
        }
        buy_slots = max(0, int(rules["maxPositions"]) - len(open_symbols))
        trade_slots = int(rules.get("maxDailyBuys", rules.get("maxDailyTrades", 2)))
        picks = [
            c for c in candidates
            if c["score"] >= float(rules["minScore"]) and c["symbol"] not in open_symbols
        ][: min(buy_slots, trade_slots)]

        for c in picks:
            target_pct = min(float(rules["maxSinglePct"]), float(c["position_pct"]))
            budget = min(cash, float(state["initial_cash"]) * target_pct / 100)
            quantity = int(budget / c["entry_price"] / 100) * 100
            if quantity <= 0:
                continue
            buy_price = _execution_price(float(c["entry_price"]), "buy", rules)
            cost = _trade_costs(buy_price, quantity, "buy", rules)["net_amount"]
            if cost > cash:
                continue
            cash -= cost
            position_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO sim_positions(
                    id, symbol, name, buy_price, quantity, current_price, stop_loss, take_profit,
                    take_profit_stage, peak_price, status, exit_price, exit_reason, opened_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'open', 0, '', ?, '')
                """,
                (position_id, c["symbol"], c["name"], buy_price, quantity, buy_price, c["stop_loss"], c["take_profit_1"], buy_price, latest),
            )
            strategy_note = "、".join(c.get("strategy_tags") or ["综合策略"])
            trade = _insert_trade(conn, latest, "自动买入", c["symbol"], c["name"], buy_price, quantity, f"{strategy_note} 评分 {c['score']}，{c.get('market_note', '')}", rules, "buy")
            trades.append(trade)
            logs.append(f"{c['name']} {strategy_note} 评分 {c['score']}，买入 {quantity} 股")

        conn.execute(
            "UPDATE sim_state SET cash=?, last_run_trade_date=?, updated_at=? WHERE id=1",
            (round(cash, 2), latest, now_iso()),
        )

        report = generate_daily_report(conn, latest, logs)
        message = f"{latest} 自动模拟完成，成交 {len(trades)} 笔"
        automation = _record_automation_result(conn, "ok", message, datetime.now().strftime("%Y-%m-%d"))
        state_after = _get_sim_state(conn)
        positions_after = [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()]
        trades_after = [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT 100").fetchall()]
        return {"status": "ok", "message": "自动模拟完成", "trade_date": latest, "logs": logs, "new_trades": trades, "report": report, **state_after, "positions": positions_after, "account_summary": _account_summary(state_after, positions_after), "trades": trades_after, "latest_report": report, "automation": automation}


def generate_daily_report(conn, trade_date: str, logs: list[str] | None = None, report_kind: str = "daily") -> dict:
    logs = logs or []
    existing = conn.execute(
        """
        SELECT * FROM daily_reports
        WHERE report_date=? AND trade_date=? AND json_extract(snapshot_json, '$.report_kind')=?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (datetime.now().strftime("%Y-%m-%d"), trade_date, report_kind),
    ).fetchone()
    if existing and report_kind != "daily":
        return _daily_report_dict(existing)
    positions = conn.execute("SELECT * FROM sim_positions").fetchall()
    state = _get_sim_state(conn)
    cash = state["cash"]
    open_positions = [p for p in positions if p["status"] == "open"]
    closed_positions = [p for p in positions if p["status"] == "closed"]
    market = sum(float(p["current_price"]) * int(p["quantity"]) for p in open_positions)
    cost = sum(float(p["buy_price"]) * int(p["quantity"]) for p in open_positions)
    float_pnl = market - cost
    realized = sum((float(p["exit_price"]) - float(p["buy_price"])) * int(p["quantity"]) for p in closed_positions if p["exit_price"])
    today_trades = conn.execute("SELECT COUNT(*) FROM sim_trades WHERE trade_date=?", (trade_date,)).fetchone()[0]
    metrics = {
        "cash": round(cash, 2),
        "market_value": round(market, 2),
        "total_assets": round(cash + market, 2),
        "open_positions": len(open_positions),
        "float_pnl": round(float_pnl, 2),
        "realized_pnl": round(realized, 2),
        "today_trades": today_trades,
    }
    summary = (
        f"{trade_date} 自动模拟日报：当前总资产 {metrics['total_assets']}，"
        f"持仓 {metrics['open_positions']} 只，今日成交 {today_trades} 笔，"
        f"浮动盈亏 {metrics['float_pnl']}，已实现盈亏 {metrics['realized_pnl']}。"
    )
    if logs:
        summary += " 操作：" + "；".join(logs)
    candidates = _list_candidates_locked(conn, trade_date, 30)
    plans = [
        _plan_dict(row)
        for row in conn.execute(
            """
            SELECT * FROM trade_plans
            WHERE trade_date = ? OR plan_date = ?
            ORDER BY plan_date DESC, created_at DESC
            LIMIT 80
            """,
            (trade_date, trade_date),
        ).fetchall()
    ]
    trades = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM sim_trades WHERE trade_date=? ORDER BY created_at DESC",
            (trade_date,),
        ).fetchall()
    ]
    snapshot = {
        "report_kind": report_kind,
        "rules": state["rules"],
        "logs": logs,
        "candidates": candidates,
        "positions": [_position_dict(row) for row in positions],
        "trade_plans": plans,
        "trades": trades,
        "market_snapshot": _market_snapshot(conn),
    }
    evaluations = _record_stock_evaluations(conn, trade_date, candidates, plans)
    snapshot["stock_evaluations"] = evaluations
    created_at = now_iso()
    report_date = datetime.now().strftime("%Y-%m-%d")
    conn.execute(
        """
        INSERT INTO daily_reports(report_date, trade_date, summary, metrics_json, snapshot_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (report_date, trade_date, summary, json.dumps(metrics, ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False), created_at),
    )
    report_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    return {"id": report_id, "report_date": report_date, "trade_date": trade_date, "summary": summary, "metrics": metrics, "snapshot": snapshot, "created_at": created_at}


def get_daily_reports() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM daily_reports ORDER BY created_at DESC LIMIT 60").fetchall()
        return [_daily_report_dict(row) for row in rows]
