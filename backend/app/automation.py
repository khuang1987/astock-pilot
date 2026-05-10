from __future__ import annotations

import asyncio
from datetime import datetime

from .config import get_settings
from .schemas import SyncRequest
from .services import get_automation_state, record_automation_result, run_auto_simulation, sync_data


_task: asyncio.Task | None = None


def _time_reached(run_time: str, now: datetime) -> bool:
    hour, minute = [int(part) for part in run_time.split(":", 1)]
    return (now.hour, now.minute) >= (hour, minute)


async def automation_loop() -> None:
    while True:
        try:
            state = get_automation_state()
            today = datetime.now().strftime("%Y-%m-%d")
            if state["enabled"] and state["last_run_date"] != today and _time_reached(state["run_time"], datetime.now()):
                if state["sync_before_run"]:
                    settings = get_settings()
                    await asyncio.to_thread(sync_data, SyncRequest(days=settings.default_sync_days, symbol_limit=settings.default_symbol_limit))
                result = await asyncio.to_thread(run_auto_simulation, False)
                record_automation_result(result.get("status", "ok"), result.get("message", "自动执行完成"), today)
        except Exception as exc:
            record_automation_result("error", f"自动执行失败：{exc}", datetime.now().strftime("%Y-%m-%d"))
        await asyncio.sleep(60)


def start_automation() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(automation_loop())


def stop_automation() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
