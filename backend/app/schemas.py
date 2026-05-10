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
    minScore: float = Field(default=75, ge=0, le=100)
    maxPositions: int = Field(default=5, ge=1, le=50)
    maxSinglePct: float = Field(default=10, ge=1, le=100)
    maxDailyTrades: int = Field(default=5, ge=1, le=100)


class SimStateUpdate(BaseModel):
    cash: float | None = Field(default=None, ge=0)
    initial_cash: float | None = Field(default=None, ge=1000)
    rules: SimRules | None = None


class AutomationUpdate(BaseModel):
    enabled: bool | None = None
    run_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    sync_before_run: bool | None = None
