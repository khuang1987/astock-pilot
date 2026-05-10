from __future__ import annotations

import json
import uuid
from datetime import datetime

import pandas as pd

from .data_provider import fetch_daily_bars, load_stock_universe, normalize_symbol
from .db import get_conn, now_iso
from .schemas import BacktestRequest, SimStateUpdate, SyncRequest
from .strategy import build_historical_signals, build_signal, simulate_backtest


def sync_data(req: SyncRequest) -> dict:
    errors: list[str] = []
    total_bars = 0
    signal_count = 0

    with get_conn() as conn:
        if req.symbols:
            universe = []
            for raw in req.symbols:
                symbol = normalize_symbol(raw)
                found = conn.execute("SELECT name FROM stocks WHERE symbol = ?", (symbol,)).fetchone()
                universe.append((symbol, found["name"] if found else symbol))
        else:
            universe = load_stock_universe(req.symbol_limit)
        for symbol, name in universe:
            try:
                df = fetch_daily_bars(symbol, req.days)
                if df.empty:
                    errors.append(f"{symbol}: no bars")
                    continue
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
                total_bars += len(df)
                signal = build_signal(symbol, df)
                if signal:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO signals(
                            symbol, trade_date, score, trend_score, volume_score, risk_score,
                            entry_price, stop_loss, take_profit_1, take_profit_2, position_pct, reasons, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (*signal.to_db_tuple(), now_iso()),
                    )
                    signal_count += 1
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
    return {"synced_symbols": len(universe) - len(errors), "bars": total_bars, "signals": signal_count, "errors": errors[:20]}


def _candidate_from_row(row) -> dict:
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
    }


def list_candidates(date: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if not date:
            row = conn.execute("SELECT MAX(trade_date) AS d FROM signals").fetchone()
            date = row["d"] if row else None
        if not date:
            return []
        rows = conn.execute(
            """
            SELECT s.*, st.name FROM signals s
            JOIN stocks st ON st.symbol = s.symbol
            WHERE s.trade_date = ?
            ORDER BY s.score DESC
            LIMIT 50
            """,
            (date,),
        ).fetchall()
        return [_candidate_from_row(row) for row in rows]


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
        return {
            "symbol": symbol,
            "name": stock["name"],
            "bars": [dict(row) for row in reversed(bars)],
            "latest_signal": _candidate_from_row(signal_row) if signal_row else None,
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
            SELECT w.*, COALESCE(st.name, w.symbol) AS name
            FROM watchlist w LEFT JOIN stocks st ON st.symbol = w.symbol
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
            out.append(
                {
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "note": row["note"],
                    "created_at": row["created_at"],
                    "latest_signal": _candidate_from_row(signal) if signal else None,
                }
            )
        return out


def add_watch(symbol: str, note: str) -> dict:
    symbol = normalize_symbol(symbol)
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO watchlist(symbol, note, created_at) VALUES (?, ?, COALESCE((SELECT created_at FROM watchlist WHERE symbol = ?), ?))",
            (symbol, note, symbol, now_iso()),
        )
    return {"ok": True}


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
    return {
        "cash": row["cash"],
        "initial_cash": row["initial_cash"],
        "rules": json.loads(row["rules_json"]),
        "last_run_trade_date": row["last_run_trade_date"],
        "updated_at": row["updated_at"],
    }


def _position_dict(row) -> dict:
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "name": row["name"],
        "buyPrice": row["buy_price"],
        "quantity": row["quantity"],
        "currentPrice": row["current_price"],
        "stopLoss": row["stop_loss"],
        "takeProfit": row["take_profit"],
        "status": row["status"],
        "sellPrice": row["exit_price"],
        "exitReason": row["exit_reason"],
        "createdAt": row["opened_at"],
        "closedAt": row["closed_at"],
    }


def get_simulation_state() -> dict:
    with get_conn() as conn:
        state = _get_sim_state(conn)
        positions = [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()]
        trades = [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT 100").fetchall()]
        report = conn.execute("SELECT * FROM daily_reports ORDER BY created_at DESC LIMIT 1").fetchone()
        automation = _get_automation_state(conn)
        return {
            **state,
            "positions": positions,
            "trades": trades,
            "latest_report": dict(report) if report else None,
            "automation": automation,
        }


def update_simulation_state(req: SimStateUpdate) -> dict:
    with get_conn() as conn:
        current = _get_sim_state(conn)
        cash = current["cash"] if req.cash is None else req.cash
        initial_cash = current["initial_cash"] if req.initial_cash is None else req.initial_cash
        rules = current["rules"] if req.rules is None else req.rules.model_dump()
        conn.execute(
            """
            UPDATE sim_state SET cash = ?, initial_cash = ?, rules_json = ?, updated_at = ? WHERE id = 1
            """,
            (cash, initial_cash, json.dumps(rules, ensure_ascii=False), now_iso()),
        )
    return get_simulation_state()


def _insert_trade(conn, trade_date: str, action: str, symbol: str, name: str, price: float, quantity: int, reason: str) -> dict:
    amount = round(price * quantity, 2)
    trade = {
        "id": str(uuid.uuid4()),
        "trade_date": trade_date,
        "action": action,
        "symbol": symbol,
        "name": name,
        "price": round(price, 2),
        "quantity": quantity,
        "amount": amount,
        "reason": reason,
        "created_at": now_iso(),
    }
    conn.execute(
        """
        INSERT INTO sim_trades(id, trade_date, action, symbol, name, price, quantity, amount, reason, created_at)
        VALUES (:id, :trade_date, :action, :symbol, :name, :price, :quantity, :amount, :reason, :created_at)
        """,
        trade,
    )
    return trade


def _automation_dict(row) -> dict:
    return {
        "enabled": bool(row["enabled"]),
        "run_time": row["run_time"],
        "sync_before_run": bool(row["sync_before_run"]),
        "last_run_date": row["last_run_date"],
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


def update_automation_state(enabled: bool | None = None, run_time: str | None = None, sync_before_run: bool | None = None) -> dict:
    with get_conn() as conn:
        current = _get_automation_state(conn)
        next_enabled = current["enabled"] if enabled is None else enabled
        next_run_time = current["run_time"] if run_time is None else run_time
        next_sync = current["sync_before_run"] if sync_before_run is None else sync_before_run
        conn.execute(
            """
            UPDATE automation_state
            SET enabled=?, run_time=?, sync_before_run=?, updated_at=?
            WHERE id=1
            """,
            (1 if next_enabled else 0, next_run_time, 1 if next_sync else 0, now_iso()),
        )
        return _get_automation_state(conn)


def _record_automation_result(conn, status: str, message: str, run_date: str | None = None) -> dict:
    current = _get_automation_state(conn)
    next_run_date = current["last_run_date"] if run_date is None else run_date
    stamp = now_iso()
    conn.execute(
        """
        UPDATE automation_state
        SET last_run_date=?, last_status=?, last_message=?, last_check_at=?, updated_at=?
        WHERE id=1
        """,
        (next_run_date, status, message, stamp, stamp),
    )
    return _get_automation_state(conn)


def record_automation_result(status: str, message: str, run_date: str | None = None) -> dict:
    with get_conn() as conn:
        return _record_automation_result(conn, status, message, run_date)


def run_auto_simulation(force: bool = False) -> dict:
    with get_conn() as conn:
        latest = conn.execute("SELECT MAX(trade_date) AS d FROM signals").fetchone()["d"]
        if not latest:
            state = _get_sim_state(conn)
            _record_automation_result(conn, "skipped", "暂无候选信号")
            return {"status": "skipped", "message": "暂无候选信号", **state, "positions": [], "trades": [], "latest_report": None}

        state = _get_sim_state(conn)
        if state["last_run_trade_date"] == latest and not force:
            message = f"{latest} 已执行过自动模拟"
            automation = _record_automation_result(conn, "skipped", message, datetime.now().strftime("%Y-%m-%d"))
            positions = [_position_dict(row) for row in conn.execute("SELECT * FROM sim_positions ORDER BY opened_at DESC, name").fetchall()]
            trades = [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY created_at DESC LIMIT 100").fetchall()]
            report = conn.execute("SELECT * FROM daily_reports ORDER BY created_at DESC LIMIT 1").fetchone()
            return {"status": "skipped", "message": message, **state, "positions": positions, "trades": trades, "latest_report": dict(report) if report else None, "automation": automation}

        rules = state["rules"]
        cash = float(state["cash"])
        trades: list[dict] = []
        logs: list[str] = []
        trade_count = 0

        candidates = list_candidates(latest)
        latest_by_symbol = {c["symbol"]: c for c in candidates}

        open_positions = conn.execute("SELECT * FROM sim_positions WHERE status = 'open'").fetchall()
        for p in open_positions:
            signal = latest_by_symbol.get(p["symbol"])
            current_price = float(signal["entry_price"] if signal else p["current_price"])
            if p["stop_loss"] and current_price <= p["stop_loss"]:
                proceeds = round(current_price * p["quantity"], 2)
                cash += proceeds
                trade = _insert_trade(conn, latest, "自动卖出", p["symbol"], p["name"], current_price, p["quantity"], "触发止损")
                trades.append(trade)
                trade_count += 1
                logs.append(f"{p['name']} 触发止损，卖出 {p['quantity']} 股")
                conn.execute(
                    "UPDATE sim_positions SET status='closed', current_price=?, exit_price=?, exit_reason='止损', closed_at=? WHERE id=?",
                    (current_price, current_price, latest, p["id"]),
                )
            elif p["take_profit"] and current_price >= p["take_profit"]:
                proceeds = round(current_price * p["quantity"], 2)
                cash += proceeds
                trade = _insert_trade(conn, latest, "自动卖出", p["symbol"], p["name"], current_price, p["quantity"], "触发止盈")
                trades.append(trade)
                trade_count += 1
                logs.append(f"{p['name']} 触发止盈，卖出 {p['quantity']} 股")
                conn.execute(
                    "UPDATE sim_positions SET status='closed', current_price=?, exit_price=?, exit_reason='止盈', closed_at=? WHERE id=?",
                    (current_price, current_price, latest, p["id"]),
                )
            else:
                conn.execute("UPDATE sim_positions SET current_price=? WHERE id=?", (current_price, p["id"]))

        open_symbols = {
            row["symbol"]
            for row in conn.execute("SELECT symbol FROM sim_positions WHERE status = 'open'").fetchall()
        }
        buy_slots = max(0, int(rules["maxPositions"]) - len(open_symbols))
        trade_slots = max(0, int(rules["maxDailyTrades"]) - trade_count)
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
            cost = round(quantity * c["entry_price"], 2)
            if cost > cash:
                continue
            cash -= cost
            position_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO sim_positions(
                    id, symbol, name, buy_price, quantity, current_price, stop_loss, take_profit,
                    status, exit_price, exit_reason, opened_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, '', ?, '')
                """,
                (position_id, c["symbol"], c["name"], c["entry_price"], quantity, c["entry_price"], c["stop_loss"], c["take_profit_1"], latest),
            )
            trade = _insert_trade(conn, latest, "自动买入", c["symbol"], c["name"], c["entry_price"], quantity, f"评分 {c['score']}")
            trades.append(trade)
            logs.append(f"{c['name']} 评分 {c['score']}，买入 {quantity} 股")

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
        return {"status": "ok", "message": "自动模拟完成", "trade_date": latest, "logs": logs, "new_trades": trades, "report": report, **state_after, "positions": positions_after, "trades": trades_after, "latest_report": report, "automation": automation}


def generate_daily_report(conn, trade_date: str, logs: list[str] | None = None) -> dict:
    positions = conn.execute("SELECT * FROM sim_positions").fetchall()
    cash = conn.execute("SELECT cash FROM sim_state WHERE id=1").fetchone()["cash"]
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
    conn.execute(
        """
        INSERT OR REPLACE INTO daily_reports(report_date, trade_date, summary, metrics_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (datetime.now().strftime("%Y-%m-%d"), trade_date, summary, json.dumps(metrics, ensure_ascii=False), now_iso()),
    )
    return {"report_date": datetime.now().strftime("%Y-%m-%d"), "trade_date": trade_date, "summary": summary, "metrics": metrics}


def get_daily_reports() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM daily_reports ORDER BY created_at DESC LIMIT 30").fetchall()
        return [
            {
                "report_date": row["report_date"],
                "trade_date": row["trade_date"],
                "summary": row["summary"],
                "metrics": json.loads(row["metrics_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
