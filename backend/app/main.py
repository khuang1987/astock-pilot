from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .automation import start_automation, stop_automation
from .broker_qmt import qmt_account_snapshot, qmt_live_order_guard, qmt_status
from .config import get_settings
from .db import init_db
from .schemas import AISettingsUpdate, AutomationUpdate, BacktestRequest, SimStateUpdate, SyncRequest, WatchItemIn
from .services import add_watch, delete_watch, execute_trade_plans, generate_trade_plan, get_ai_settings, get_automation_state, get_daily_reports, get_data_status, get_simulation_state, get_stock_detail, list_candidates, list_strategy_versions, list_trade_plans, list_watchlist, refresh_position_quotes, run_ai_review, run_auto_simulation, run_backtest, run_daily_workflow, run_plan_ai_review, sync_data, update_ai_settings, update_automation_state, update_simulation_state


settings = get_settings()
app = FastAPI(title=settings.app_name)
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

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


@app.get("/api/data/status")
def data_status() -> dict:
    return get_data_status()


@app.post("/api/daily-workflow")
def daily_workflow(req: SyncRequest | None = None) -> dict:
    payload = req or SyncRequest(days=settings.default_sync_days, symbol_limit=settings.default_symbol_limit)
    return run_daily_workflow(payload)


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
    return generate_trade_plan()


@app.post("/api/simulation/execute-plans")
def simulation_execute_plans(force: bool = False) -> dict:
    return execute_trade_plans(force=force)


@app.post("/api/simulation/refresh-quotes")
def simulation_refresh_quotes() -> dict:
    return refresh_position_quotes()


@app.get("/api/trade-plans")
def trade_plans() -> list[dict]:
    return list_trade_plans()


@app.post("/api/trade-plans/{plan_id}/execute")
def trade_plan_execute(plan_id: str, force: bool = False) -> dict:
    return execute_trade_plans(force=force, plan_id=plan_id)


@app.post("/api/trade-plans/{plan_id}/ai-review")
def trade_plan_ai_review(plan_id: str) -> dict:
    return run_plan_ai_review(plan_id)


@app.get("/api/broker/qmt/status")
def broker_qmt_status() -> dict:
    return qmt_status()


@app.get("/api/broker/qmt/account")
def broker_qmt_account() -> dict:
    return qmt_account_snapshot()


@app.post("/api/broker/qmt/live-order-guard")
def broker_qmt_live_order_guard() -> dict:
    return qmt_live_order_guard()


@app.get("/api/automation/state")
def automation_state() -> dict:
    return get_automation_state()


@app.post("/api/automation/state")
def automation_state_update(req: AutomationUpdate) -> dict:
    return update_automation_state(req.enabled, req.run_time, req.sync_before_run, req.mode, req.execution_time, req.planning_time)


@app.get("/api/reports/daily")
def reports_daily() -> list[dict]:
    return get_daily_reports()


@app.get("/api/ai/settings")
def ai_settings() -> dict:
    return get_ai_settings()


@app.post("/api/ai/settings")
def ai_settings_update(req: AISettingsUpdate) -> dict:
    return update_ai_settings(req)


@app.post("/api/ai/review")
def ai_review() -> dict:
    return run_ai_review()


if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    def frontend_index() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend_spa(path: str) -> FileResponse:
        target = FRONTEND_DIST / path
        if target.is_file():
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
