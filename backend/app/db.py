from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .config import get_settings


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS stocks (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    amount REAL NOT NULL,
    pct_chg REAL NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS signals (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    score REAL NOT NULL,
    trend_score REAL NOT NULL,
    volume_score REAL NOT NULL,
    risk_score REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit_1 REAL NOT NULL,
    take_profit_2 REAL NOT NULL,
    position_pct REAL NOT NULL,
    reasons TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    params_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    trades_json TEXT NOT NULL,
    equity_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    timeframe TEXT NOT NULL DEFAULT 'daily',
    params_json TEXT NOT NULL,
    change_note TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,
    initial_cash REAL NOT NULL,
    rules_json TEXT NOT NULL,
    last_run_trade_date TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_positions (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    buy_price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    current_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    status TEXT NOT NULL,
    exit_price REAL NOT NULL DEFAULT 0,
    exit_reason TEXT NOT NULL DEFAULT '',
    opened_at TEXT NOT NULL,
    closed_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sim_trades (
    id TEXT PRIMARY KEY,
    trade_date TEXT NOT NULL,
    action TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    amount REAL NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_reports (
    report_date TEXT PRIMARY KEY,
    trade_date TEXT NOT NULL,
    summary TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 1,
    run_time TEXT NOT NULL DEFAULT '15:30',
    sync_before_run INTEGER NOT NULL DEFAULT 1,
    last_run_date TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT '',
    last_message TEXT NOT NULL DEFAULT '',
    last_check_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""


def db_path() -> Path:
    return get_settings().resolved_database_path


def init_db() -> None:
    with sqlite3.connect(db_path()) as conn:
        conn.executescript(SCHEMA)
        count = conn.execute("SELECT COUNT(*) FROM strategy_versions").fetchone()[0]
        if count == 0:
            conn.execute(
                """
                INSERT INTO strategy_versions(version, status, timeframe, params_json, change_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "v1.0",
                    "active",
                    "daily",
                    '{"min_history_days":120,"min_price":3,"min_amount":80000000,"min_score":62,"ma_short":5,"ma_mid":10,"ma_long":20,"volume_ratio_min":1.05,"volume_ratio_max":2.5,"stop_loss_pct":5,"take_profit_1_pct":6,"take_profit_2_pct":10,"position_pct_high":10,"position_pct_normal":6}',
                    "初版稳健短线策略：日线趋势、量能、波动、流动性过滤；预留小时级数据接入。",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        state_count = conn.execute("SELECT COUNT(*) FROM sim_state").fetchone()[0]
        if state_count == 0:
            conn.execute(
                """
                INSERT INTO sim_state(id, cash, initial_cash, rules_json, last_run_trade_date, updated_at)
                VALUES (1, ?, ?, ?, '', ?)
                """,
                (
                    100000,
                    100000,
                    '{"minScore":75,"maxPositions":5,"maxSinglePct":10,"maxDailyTrades":5}',
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        automation_count = conn.execute("SELECT COUNT(*) FROM automation_state").fetchone()[0]
        if automation_count == 0:
            settings = get_settings()
            conn.execute(
                """
                INSERT INTO automation_state(
                    id, enabled, run_time, sync_before_run, last_run_date,
                    last_status, last_message, last_check_at, updated_at
                )
                VALUES (1, ?, ?, 1, '', '', '', '', ?)
                """,
                (
                    1 if settings.auto_sim_enabled else 0,
                    settings.auto_sim_run_time,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    init_db()
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
