from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SyncRequest(BaseModel):
    days: int = Field(default=730, ge=60, le=3000)
    symbol_limit: int = Field(default=80, ge=10, le=5000)
    symbols: list[str] | None = None


class SyncResult(BaseModel):
    synced_symbols: int
    bars: int
    signals: int
    errors: list[str]


class Candidate(BaseModel):
    symbol: str
    name: str
    trade_date: str
    score: float
    trend_score: float
    volume_score: float
    risk_score: float
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    position_pct: float
    reasons: list[str]


class StockDetail(BaseModel):
    symbol: str
    name: str
    bars: list[dict[str, Any]]
    latest_signal: Candidate | None = None


class BacktestRequest(BaseModel):
    start_date: str
    end_date: str
    max_positions: int = Field(default=5, ge=1, le=20)
    initial_cash: float = Field(default=100000, gt=1000)
    fee_rate: float = Field(default=0.0003, ge=0, le=0.01)
    hold_days: int = Field(default=5, ge=1, le=20)


class BacktestResult(BaseModel):
    id: int
    summary: dict[str, Any]
    trades: list[dict[str, Any]]
    equity: list[dict[str, Any]]


class WatchItemIn(BaseModel):
    symbol: str
    note: str = ""


class WatchItem(BaseModel):
    symbol: str
    name: str
    note: str
    created_at: str
    latest_signal: Candidate | None = None


class SimRules(BaseModel):
    minScore: float = Field(default=82, ge=0, le=100)
    maxPositions: int = Field(default=4, ge=1, le=50)
    maxSinglePct: float = Field(default=8, ge=1, le=100)
    maxDailyTrades: int = Field(default=5, ge=1, le=100)
    maxDailyBuys: int = Field(default=1, ge=0, le=50)
    maxDailySells: int = Field(default=4, ge=0, le=50)
    sellPriority: bool = True
    dynamicBuyEnabled: bool = True
    weakMaxDailyBuys: int = Field(default=1, ge=0, le=10)
    weakMaxDailyBuyPct: float = Field(default=5, ge=0, le=100)
    rangeMaxDailyBuys: int = Field(default=1, ge=0, le=10)
    rangeMaxDailyBuyPct: float = Field(default=8, ge=0, le=100)
    strongMaxDailyBuys: int = Field(default=2, ge=0, le=10)
    strongMaxDailyBuyPct: float = Field(default=15, ge=0, le=100)
    buyPriceTolerancePct: float = Field(default=0.8, ge=0, le=20)
    commissionRate: float = Field(default=0.0003, ge=0, le=0.01)
    minCommission: float = Field(default=5, ge=0, le=100)
    stampTaxRate: float = Field(default=0.0005, ge=0, le=0.01)
    transferFeeRate: float = Field(default=0.00001, ge=0, le=0.01)
    slippagePct: float = Field(default=0.15, ge=0, le=5)
    rebalanceEnabled: bool = True
    rebalanceMinNewScore: float = Field(default=88, ge=0, le=100)
    rebalanceMinScoreGap: float = Field(default=8, ge=0, le=100)
    maxDailyRebalances: int = Field(default=1, ge=0, le=20)
    weakHoldDays: int = Field(default=5, ge=1, le=60)
    weakReturnPct: float = Field(default=-2.5, ge=-50, le=50)
    weakScoreExit: float = Field(default=78, ge=0, le=100)
    addPositionEnabled: bool = False
    addMinProfitPct: float = Field(default=4, ge=0, le=100)
    addMinScore: float = Field(default=88, ge=0, le=100)
    maxAddsPerSymbol: int = Field(default=1, ge=0, le=10)
    addPositionPct: float = Field(default=3, ge=0, le=50)
    takeProfitSellPct: float = Field(default=50, ge=1, le=100)
    trailingStopPct: float = Field(default=4, ge=0.5, le=30)
    minRemainLot: int = Field(default=100, ge=0, le=10000)


class SimStateUpdate(BaseModel):
    cash: float | None = Field(default=None, ge=0)
    initial_cash: float | None = Field(default=None, ge=1000)
    rules: SimRules | None = None


class AutomationUpdate(BaseModel):
    enabled: bool | None = None
    mode: str | None = Field(default=None, pattern=r"^(manual|assist|managed)$")
    run_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    execution_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    planning_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    sync_before_run: bool | None = None


class AISettingsUpdate(BaseModel):
    enabled: bool | None = None
    provider: str | None = Field(default=None, pattern=r"^(openai|deepseek)$")
    model: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, min_length=8, max_length=200)
    api_key: str | None = Field(default=None, max_length=300)
    daily_review_enabled: bool | None = None
    plan_review_enabled: bool | None = None
    stock_review_enabled: bool | None = None
    block_trade_enabled: bool | None = None
