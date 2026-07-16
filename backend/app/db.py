from __future__ import annotations

import sqlite3
import threading
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
    strategy_tags TEXT NOT NULL DEFAULT '[]',
    strategy_scores TEXT NOT NULL DEFAULT '{}',
    market_state TEXT NOT NULL DEFAULT 'neutral',
    market_note TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT 'buy',
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
    added_price REAL NOT NULL DEFAULT 0,
    added_price_source TEXT NOT NULL DEFAULT '',
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
    take_profit_stage INTEGER NOT NULL DEFAULT 0,
    peak_price REAL NOT NULL DEFAULT 0,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    summary TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_evaluations (
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    name TEXT NOT NULL,
    groups_json TEXT NOT NULL DEFAULT '[]',
    score REAL,
    decision TEXT NOT NULL,
    summary TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    signal_json TEXT NOT NULL DEFAULT '{}',
    position_json TEXT NOT NULL DEFAULT '{}',
    plan_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS ai_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    provider TEXT NOT NULL DEFAULT 'openai',
    model TEXT NOT NULL DEFAULT 'gpt-4o-mini',
    base_url TEXT NOT NULL DEFAULT 'https://api.openai.com/v1',
    api_key TEXT NOT NULL DEFAULT '',
    daily_review_enabled INTEGER NOT NULL DEFAULT 1,
    plan_review_enabled INTEGER NOT NULL DEFAULT 1,
    stock_review_enabled INTEGER NOT NULL DEFAULT 1,
    block_trade_enabled INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_daily_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    summary TEXT NOT NULL,
    market_view TEXT NOT NULL DEFAULT '',
    candidate_view TEXT NOT NULL DEFAULT '',
    position_view TEXT NOT NULL DEFAULT '',
    action_suggestion TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_plan_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    plan_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    plan_type TEXT NOT NULL,
    verdict TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    reason TEXT NOT NULL,
    suggestion TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_stock_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    review_kind TEXT NOT NULL DEFAULT 'daily',
    decision TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    summary TEXT NOT NULL,
    suggestion TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(trade_date, symbol, review_kind)
);

CREATE TABLE IF NOT EXISTS realtime_quotes (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    pct_chg REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    groups_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_plans (
    id TEXT PRIMARY KEY,
    plan_date TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    plan_type TEXT NOT NULL,
    action TEXT NOT NULL,
    trigger_price REAL NOT NULL,
    stop_loss REAL NOT NULL DEFAULT 0,
    take_profit REAL NOT NULL DEFAULT 0,
    quantity INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(plan_date, symbol, plan_type)
);

CREATE TABLE IF NOT EXISTS plan_decision_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    plan_date TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    plan_type TEXT NOT NULL,
    decision_status TEXT NOT NULL,
    decision_label TEXT NOT NULL,
    allow_execute INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    ai_review_id INTEGER NOT NULL DEFAULT 0,
    data_guard_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 1,
    run_time TEXT NOT NULL DEFAULT '15:30',
    execution_time TEXT NOT NULL DEFAULT '09:30',
    planning_time TEXT NOT NULL DEFAULT '15:30',
    sync_before_run INTEGER NOT NULL DEFAULT 1,
    last_run_date TEXT NOT NULL DEFAULT '',
    last_execution_date TEXT NOT NULL DEFAULT '',
    last_plan_date TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT '',
    last_message TEXT NOT NULL DEFAULT '',
    last_check_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""

_INIT_LOCK = threading.Lock()
_INITIALIZED = False


def _configure_conn(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")


def db_path() -> Path:
    return get_settings().resolved_database_path


def init_db() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        with sqlite3.connect(db_path(), timeout=30) as conn:
            _configure_conn(conn)
            _init_db_locked(conn)
        _INITIALIZED = True


def _init_db_locked(conn: sqlite3.Connection) -> None:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "signals", "strategy_tags", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "signals", "strategy_scores", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "signals", "market_state", "TEXT NOT NULL DEFAULT 'neutral'")
        _ensure_column(conn, "signals", "market_note", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "signals", "decision", "TEXT NOT NULL DEFAULT 'buy'")
        _ensure_column(conn, "watchlist", "added_price", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "watchlist", "added_price_source", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "trade_plans", "status", "TEXT NOT NULL DEFAULT 'pending'")
        _ensure_column(conn, "sim_positions", "take_profit_stage", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "sim_positions", "peak_price", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "sim_trades", "fee", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "sim_trades", "tax", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "sim_trades", "net_amount", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "realtime_quotes", "source", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "realtime_quotes", "groups_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "automation_state", "mode", "TEXT NOT NULL DEFAULT 'assist'")
        _ensure_column(conn, "automation_state", "execution_time", "TEXT NOT NULL DEFAULT '09:30'")
        _ensure_column(conn, "automation_state", "planning_time", "TEXT NOT NULL DEFAULT '15:30'")
        _ensure_column(conn, "automation_state", "last_execution_date", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "automation_state", "last_plan_date", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "ai_settings", "base_url", "TEXT NOT NULL DEFAULT 'https://api.openai.com/v1'")
        _migrate_daily_reports(conn)
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
        version_count = conn.execute("SELECT COUNT(*) FROM strategy_versions WHERE version = 'v1.1'").fetchone()[0]
        if version_count == 0:
            conn.execute(
                """
                INSERT INTO strategy_versions(version, status, timeframe, params_json, change_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "v1.1",
                    "active",
                    "daily",
                    '{"strategies":["trend_breakout","strong_pullback","volume_launch"],"scheduler":"market_regime_gated","risk_off":"no_new_buy","weak_market":"only_high_score_half_position"}',
                    "加入多策略调度器：趋势突破、强势回调、放量启动独立打分；市场环境统一裁决，策略只提案，执行层统一买卖。",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        else:
            conn.execute(
                """
                UPDATE strategy_versions
                SET params_json=?, change_note=?, status='active'
                WHERE version='v1.6'
                """,
                (
                    '{"score_model":"multi_factor_evidence","weights":{"technical_evidence":0.72,"fund_flow":0.10,"theme":0.08,"message_risk":0.10},"data_sources":["ths_stock_fund_flow","ths_industry_fund_flow","ths_concept_fund_flow","cninfo_disclosure","eastmoney_research","eastmoney_news_optional"],"gates":{"min_amount":80000000,"min_actionable_score":62,"candidate_message_review":true,"ai_final_review":true},"exits":{"weak_exit_plan":true,"weak_hold_days":5,"weak_return_pct":-2.5,"weak_score_exit":78}}',
                    "评分选股升级为 v1.6 多维证据模型：资金面、题材面和消息风险接入候选评分；盘后自动完成同步、评分、计划生成、弱持仓退出和 AI 最终审核。",
                ),
            )
        version_count = conn.execute("SELECT COUNT(*) FROM strategy_versions WHERE version = 'v1.2'").fetchone()[0]
        if version_count == 0:
            conn.execute(
                """
                INSERT INTO strategy_versions(version, status, timeframe, params_json, change_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "v1.2",
                    "active",
                    "daily",
                    '{"execution":{"minScore":82,"maxPositions":4,"maxSinglePct":8,"maxDailyBuys":1,"maxDailySells":4,"buyPriceTolerancePct":0.8},"costs":{"commissionRate":0.0003,"minCommission":5,"stampTaxRate":0.0005,"transferFeeRate":0.00001,"slippagePct":0.15}}',
                    "根据最新候选数量偏少、账户已满仓的情况，降低换手和追高：最大持股4只、每日最多买1只、单票8%，并把佣金、最低佣金、过户费、卖出印花税和滑点纳入模拟成交。",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        version_count = conn.execute("SELECT COUNT(*) FROM strategy_versions WHERE version = 'v1.3'").fetchone()[0]
        if version_count == 0:
            conn.execute(
                """
                INSERT INTO strategy_versions(version, status, timeframe, params_json, change_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "v1.3",
                    "active",
                    "daily",
                    '{"rebalance":{"enabled":true,"minNewScore":88,"minScoreGap":8,"maxDailyRebalances":1,"weakHoldDays":5,"weakReturnPct":-2.5,"weakScoreExit":78},"add":{"enabled":false,"minProfitPct":4,"minScore":88,"maxAddsPerSymbol":1,"addPct":3}}',
                    "新增调仓和追加规则：满仓时允许用高分新候选替换弱持仓，每天最多一组；追加买入默认关闭，仅在持仓盈利且信号高分时启用。",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        version_count = conn.execute("SELECT COUNT(*) FROM strategy_versions WHERE version = 'v1.4'").fetchone()[0]
        if version_count == 0:
            conn.execute(
                """
                INSERT INTO strategy_versions(version, status, timeframe, params_json, change_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "v1.4",
                    "active",
                    "daily",
                    '{"sell":{"stopLoss":"full","takeProfitSellPct":50,"trailingStopPct":4,"minRemainLot":100},"rebalance":{"sell":"full"},"add":{"requiresOpenPosition":true}}',
                    "卖出执行改为趋势/动量风格：止损全卖，第一止盈默认卖出一半，剩余仓位进入移动止损；调仓卖出仍全卖，追加只允许已有持仓。",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        version_count = conn.execute("SELECT COUNT(*) FROM strategy_versions WHERE version = 'v1.5'").fetchone()[0]
        if version_count == 0:
            conn.execute(
                """
                INSERT INTO strategy_versions(version, status, timeframe, params_json, change_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "v1.5",
                    "active",
                    "daily",
                    '{"score_model":"evidence_score","weights":{"actionable_technical":0.50,"volume_launch":0.12,"liquidity_quality":0.13,"relative_strength":0.12,"risk_control":0.08,"market_score":0.05},"strategies":["trend_breakout","strong_pullback","volume_launch","relative_strength","liquidity_quality"],"gates":{"min_amount":80000000,"min_actionable_score":62,"risk_off":"no_new_buy"}}',
                    "选股评分升级为证据评分：在原趋势/回调/放量基础上加入相对强势、流动性质量、风险控制和市场环境，先用现有日线数据形成更丰富的候选排序。",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        version_count = conn.execute("SELECT COUNT(*) FROM strategy_versions WHERE version = 'v1.6'").fetchone()[0]
        if version_count == 0:
            conn.execute(
                """
                INSERT INTO strategy_versions(version, status, timeframe, params_json, change_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "v1.6",
                    "active",
                    "daily",
                    '{"score_model":"multi_factor_evidence","weights":{"technical_evidence":0.72,"fund_flow":0.10,"theme":0.08,"message_risk":0.10},"data_sources":["ths_stock_fund_flow","ths_industry_fund_flow","ths_concept_fund_flow","cninfo_disclosure","eastmoney_research","eastmoney_news_optional"],"gates":{"min_amount":80000000,"min_actionable_score":62,"candidate_message_review":true,"ai_final_review":true},"exits":{"weak_exit_plan":true,"weak_hold_days":5,"weak_return_pct":-2.5,"weak_score_exit":78}}',
                    "评分选股升级为 v1.6 多维证据模型：资金面、题材面和消息风险接入候选评分；盘后自动完成同步、评分、计划生成、弱持仓退出和 AI 最终审核。",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        version_count = conn.execute("SELECT COUNT(*) FROM strategy_versions WHERE version = 'v1.7'").fetchone()[0]
        if version_count == 0:
            conn.execute(
                """
                INSERT INTO strategy_versions(version, status, timeframe, params_json, change_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "v1.7",
                    "active",
                    "daily",
                    '{"execution":{"dynamicBuyEnabled":true,"weak":{"maxDailyBuys":1,"maxDailyBuyPct":5},"range":{"maxDailyBuys":1,"maxDailyBuyPct":8},"strong":{"maxDailyBuys":2,"maxDailyBuyPct":15},"unifiedBuyQuota":true},"risk":{"sellPriority":true,"riskExitBypassBuyQuota":true}}',
                    "买入执行升级为动态额度：按市场状态控制每日买入次数和新增买入资金占比；调仓买入和追加买入统一占用买入额度，风控卖出不受影响。",
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
                    '{"minScore":82,"maxPositions":4,"maxSinglePct":8,"maxDailyTrades":5,"maxDailyBuys":1,"maxDailySells":4,"sellPriority":true,"dynamicBuyEnabled":true,"weakMaxDailyBuys":1,"weakMaxDailyBuyPct":5,"rangeMaxDailyBuys":1,"rangeMaxDailyBuyPct":8,"strongMaxDailyBuys":2,"strongMaxDailyBuyPct":15,"buyPriceTolerancePct":0.8,"commissionRate":0.0003,"minCommission":5,"stampTaxRate":0.0005,"transferFeeRate":0.00001,"slippagePct":0.15,"rebalanceEnabled":true,"rebalanceMinNewScore":88,"rebalanceMinScoreGap":8,"maxDailyRebalances":1,"weakHoldDays":5,"weakReturnPct":-2.5,"weakScoreExit":78,"addPositionEnabled":false,"addMinProfitPct":4,"addMinScore":88,"maxAddsPerSymbol":1,"addPositionPct":3,"takeProfitSellPct":50,"trailingStopPct":4,"minRemainLot":100}',
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        conn.execute("UPDATE sim_positions SET peak_price = current_price WHERE peak_price = 0")
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
        ai_count = conn.execute("SELECT COUNT(*) FROM ai_settings").fetchone()[0]
        if ai_count == 0:
            conn.execute(
                """
                INSERT INTO ai_settings(
                    id, enabled, provider, model, base_url, api_key, daily_review_enabled,
                    plan_review_enabled, stock_review_enabled, block_trade_enabled, updated_at
                )
                VALUES (1, 0, 'openai', 'gpt-4o-mini', 'https://api.openai.com/v1', '', 1, 1, 1, 0, ?)
                """,
                (datetime.now().isoformat(timespec="seconds"),),
            )


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    init_db()
    conn = sqlite3.connect(db_path(), timeout=30)
    _configure_conn(conn)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_daily_reports(conn: sqlite3.Connection) -> None:
    columns = conn.execute("PRAGMA table_info(daily_reports)").fetchall()
    column_names = [row[1] for row in columns]
    if "id" in column_names:
        _ensure_column(conn, "daily_reports", "snapshot_json", "TEXT NOT NULL DEFAULT '{}'")
        return
    conn.execute("ALTER TABLE daily_reports RENAME TO daily_reports_legacy")
    conn.execute(
        """
        CREATE TABLE daily_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            summary TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO daily_reports(report_date, trade_date, summary, metrics_json, snapshot_json, created_at)
        SELECT report_date, trade_date, summary, metrics_json, '{}', created_at FROM daily_reports_legacy
        """
    )
    conn.execute("DROP TABLE daily_reports_legacy")
