from __future__ import annotations

import asyncio
from datetime import datetime
import traceback

from .config import get_settings
from .schemas import SyncRequest
from .services import execute_trade_plans, generate_trade_plan, get_automation_state, record_automation_result, refresh_position_quotes, run_ai_review, sync_data


_task: asyncio.Task | None = None
_quote_task: asyncio.Task | None = None

_EXECUTION_TIMEOUT_SECONDS = 180
_SYNC_TIMEOUT_SECONDS = 900
_AI_TIMEOUT_SECONDS = 180
_QUOTE_TIMEOUT_SECONDS = 120


def _time_reached(run_time: str, now: datetime) -> bool:
    hour, minute = [int(part) for part in run_time.split(":", 1)]
    return (now.hour, now.minute) >= (hour, minute)


def _in_trading_session(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    current = now.time()
    morning_start = now.replace(hour=9, minute=30, second=0, microsecond=0).time()
    morning_end = now.replace(hour=11, minute=30, second=0, microsecond=0).time()
    afternoon_start = now.replace(hour=13, minute=0, second=0, microsecond=0).time()
    afternoon_end = now.replace(hour=14, minute=57, second=0, microsecond=0).time()
    return (morning_start <= current <= morning_end) or (afternoon_start <= current <= afternoon_end)


def _is_trade_day(now: datetime) -> bool:
    return now.weekday() < 5


def _execution_message(execution: dict, today: str) -> str:
    message = execution.get("message", "交易池检查完成")
    new_trades = execution.get("new_trades") or []
    if new_trades:
        return message

    trades = execution.get("trades") or []
    today_trades = [trade for trade in trades if str(trade.get("trade_date") or "") == today]
    if today_trades:
        return f"交易池检查完成，今日已成交 {len(today_trades)} 笔，本轮新增 0 笔"
    return message


async def _to_thread_with_timeout(label: str, timeout: int, fn, *args):
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)
    except TimeoutError as exc:
        raise TimeoutError(f"{label} 超过 {timeout} 秒未返回") from exc


async def automation_loop() -> None:
    while True:
        try:
            state = get_automation_state()
            mode = state.get("mode") or ("managed" if state.get("enabled") else "manual")
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            if state["enabled"] and mode == "managed" and _time_reached(state.get("execution_time") or "09:30", now) and _in_trading_session(now):
                execution = await _to_thread_with_timeout("交易池执行", _EXECUTION_TIMEOUT_SECONDS, execute_trade_plans, False)
                record_automation_result(execution.get("status", "ok"), _execution_message(execution, today), today, "execution")
            if state["enabled"] and mode != "manual" and _is_trade_day(now) and state.get("last_plan_date") != today and _time_reached(state.get("planning_time") or state["run_time"], now):
                if state["sync_before_run"]:
                    settings = get_settings()
                    await _to_thread_with_timeout("收盘数据同步", _SYNC_TIMEOUT_SECONDS, sync_data, SyncRequest(days=settings.default_sync_days, symbol_limit=settings.default_symbol_limit))
                plan = await _to_thread_with_timeout("交易计划生成", _EXECUTION_TIMEOUT_SECONDS, generate_trade_plan)
                ai = await _to_thread_with_timeout("AI 审核", _AI_TIMEOUT_SECONDS, run_ai_review)
                ai_text = ai.get("message", "AI 最终审核完成")
                message = f"{plan.get('message', '收盘计划生成完成')}；{ai_text}"
                record_automation_result(plan.get("status", "ok"), message, today, "plan")
        except Exception as exc:
            print(f"[automation] 自动任务失败：{exc}", flush=True)
            traceback.print_exc()
            record_automation_result("error", f"自动任务失败：{exc}")
        await asyncio.sleep(60)


async def quote_refresh_loop() -> None:
    settings = get_settings()
    while True:
        await asyncio.sleep(max(5, settings.quote_refresh_minutes) * 60)
        try:
            if settings.quote_refresh_enabled:
                await _to_thread_with_timeout("行情刷新", _QUOTE_TIMEOUT_SECONDS, refresh_position_quotes)
        except Exception as exc:
            print(f"[automation] 行情刷新失败：{exc}", flush=True)


def start_automation() -> None:
    global _task, _quote_task
    if _task is None or _task.done():
        _task = asyncio.create_task(automation_loop())
    if _quote_task is None or _quote_task.done():
        _quote_task = asyncio.create_task(quote_refresh_loop())


def stop_automation() -> None:
    global _task, _quote_task
    if _task and not _task.done():
        _task.cancel()
    _task = None
    if _quote_task and not _quote_task.done():
        _quote_task.cancel()
    _quote_task = None
