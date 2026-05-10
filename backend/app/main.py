from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .automation import start_automation, stop_automation
from .config import get_settings
from .db import init_db
from .schemas import AutomationUpdate, BacktestRequest, SimStateUpdate, SyncRequest, WatchItemIn
from .services import add_watch, delete_watch, get_automation_state, get_daily_reports, get_simulation_state, get_stock_detail, list_candidates, list_strategy_versions, list_watchlist, run_auto_simulation, run_backtest, sync_data, update_automation_state, update_simulation_state


settings = get_settings()
app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3009",
        "http://127.0.0.1:3009",
        "http://10.0.0.17:3009",
    ],
    allow_origin_regex=r"^https?://.*:3009$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    init_db()
    start_automation()


@app.on_event("shutdown")
def shutdown() -> None:
    stop_automation()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name}


@app.post("/api/data/sync")
def sync(req: SyncRequest | None = None) -> dict:
    payload = req or SyncRequest(days=settings.default_sync_days, symbol_limit=settings.default_symbol_limit)
    return sync_data(payload)


@app.get("/api/stocks/candidates")
def candidates(date: str | None = None) -> list[dict]:
    return list_candidates(date)


@app.get("/api/stocks/{symbol}")
def stock_detail(symbol: str) -> dict:
    detail = get_stock_detail(symbol)
    if not detail:
        raise HTTPException(status_code=404, detail="stock not found")
    return detail


@app.post("/api/backtests")
def backtest(req: BacktestRequest) -> dict:
    return run_backtest(req)


@app.get("/api/watchlist")
def watchlist() -> list[dict]:
    return list_watchlist()


@app.post("/api/watchlist")
def watchlist_add(item: WatchItemIn) -> dict:
    return add_watch(item.symbol, item.note)


@app.delete("/api/watchlist/{symbol}")
def watchlist_delete(symbol: str) -> dict:
    return delete_watch(symbol)


@app.get("/api/strategy/versions")
def strategy_versions() -> list[dict]:
    return list_strategy_versions()


@app.get("/api/simulation/state")
def simulation_state() -> dict:
    return get_simulation_state()


@app.post("/api/simulation/state")
def simulation_state_update(req: SimStateUpdate) -> dict:
    return update_simulation_state(req)


@app.post("/api/simulation/auto-run")
def simulation_auto_run(force: bool = False) -> dict:
    return run_auto_simulation(force=force)


@app.get("/api/automation/state")
def automation_state() -> dict:
    return get_automation_state()


@app.post("/api/automation/state")
def automation_state_update(req: AutomationUpdate) -> dict:
    return update_automation_state(req.enabled, req.run_time, req.sync_before_run)


@app.get("/api/reports/daily")
def reports_daily() -> list[dict]:
    return get_daily_reports()
