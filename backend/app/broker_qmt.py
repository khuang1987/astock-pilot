from __future__ import annotations

import importlib
import random
from dataclasses import asdict, dataclass

from .config import get_settings


@dataclass
class QMTConfigStatus:
    enabled: bool
    live_order_enabled: bool
    account_id_configured: bool
    user_data_path_configured: bool
    account_type: str


def _load_xtquant():
    try:
        xttrader = importlib.import_module("xtquant.xttrader")
        xttype = importlib.import_module("xtquant.xttype")
        return xttrader, xttype, ""
    except Exception as exc:
        return None, None, str(exc)


def qmt_status() -> dict:
    settings = get_settings()
    xttrader, _xttype, import_error = _load_xtquant()
    config = QMTConfigStatus(
        enabled=bool(settings.qmt_enabled),
        live_order_enabled=bool(settings.qmt_live_order_enabled),
        account_id_configured=bool(settings.qmt_account_id),
        user_data_path_configured=bool(settings.qmt_user_data_path),
        account_type=settings.qmt_account_type or "STOCK",
    )
    ready = bool(
        config.enabled
        and xttrader
        and config.account_id_configured
        and config.user_data_path_configured
    )
    missing: list[str] = []
    if not config.enabled:
        missing.append("未启用 QMT_ENABLED")
    if not xttrader:
        missing.append("未安装或未配置 xtquant Python 库")
    if not config.user_data_path_configured:
        missing.append("未配置 QMT_USER_DATA_PATH")
    if not config.account_id_configured:
        missing.append("未配置 QMT_ACCOUNT_ID")
    return {
        "provider": "qmt",
        "ready": ready,
        "connected": False,
        "config": asdict(config),
        "missing": missing,
        "import_error": import_error,
        "message": "QMT 配置就绪，仍需 MiniQMT 已登录后才能查询账户。" if ready else "QMT 尚未就绪：" + "；".join(missing),
    }


def _connect_trader():
    settings = get_settings()
    xttrader, xttype, import_error = _load_xtquant()
    if not xttrader:
        raise RuntimeError(f"xtquant 不可用：{import_error or '未安装'}")
    if not settings.qmt_enabled:
        raise RuntimeError("QMT_ENABLED 未启用")
    if not settings.qmt_user_data_path:
        raise RuntimeError("QMT_USER_DATA_PATH 未配置")
    if not settings.qmt_account_id:
        raise RuntimeError("QMT_ACCOUNT_ID 未配置")
    session_id = random.randint(100000, 999999)
    trader = xttrader.XtQuantTrader(settings.qmt_user_data_path, session_id)
    account = xttype.StockAccount(settings.qmt_account_id, settings.qmt_account_type or "STOCK")
    trader.start()
    connect_result = trader.connect()
    if connect_result != 0:
        raise RuntimeError(f"MiniQMT 连接失败，connect 返回 {connect_result}")
    subscribe_result = trader.subscribe(account)
    if subscribe_result != 0:
        raise RuntimeError(f"资金账号订阅失败，subscribe 返回 {subscribe_result}")
    return trader, account


def qmt_account_snapshot() -> dict:
    status = qmt_status()
    if not status["ready"]:
        return {**status, "asset": None, "positions": []}
    trader = None
    try:
        trader, account = _connect_trader()
        asset = trader.query_stock_asset(account)
        positions = trader.query_stock_positions(account) or []
        return {
            **status,
            "connected": True,
            "asset": _asset_dict(asset),
            "positions": [_position_dict(item) for item in positions],
            "message": "QMT 账户读取成功",
        }
    except Exception as exc:
        return {**status, "connected": False, "asset": None, "positions": [], "message": str(exc)}
    finally:
        if trader is not None:
            try:
                trader.stop()
            except Exception:
                pass


def _asset_dict(asset) -> dict | None:
    if asset is None:
        return None
    return {
        "account_id": getattr(asset, "account_id", ""),
        "cash": float(getattr(asset, "cash", 0) or 0),
        "frozen_cash": float(getattr(asset, "frozen_cash", 0) or 0),
        "market_value": float(getattr(asset, "market_value", 0) or 0),
        "total_asset": float(getattr(asset, "total_asset", 0) or 0),
    }


def _position_dict(pos) -> dict:
    return {
        "account_id": getattr(pos, "account_id", ""),
        "stock_code": getattr(pos, "stock_code", ""),
        "volume": int(getattr(pos, "volume", 0) or 0),
        "can_use_volume": int(getattr(pos, "can_use_volume", 0) or 0),
        "open_price": float(getattr(pos, "open_price", 0) or 0),
        "market_value": float(getattr(pos, "market_value", 0) or 0),
        "frozen_volume": int(getattr(pos, "frozen_volume", 0) or 0),
        "on_road_volume": int(getattr(pos, "on_road_volume", 0) or 0),
    }


def qmt_live_order_guard() -> dict:
    settings = get_settings()
    if not settings.qmt_live_order_enabled:
        return {
            "allowed": False,
            "message": "真实下单开关 QMT_LIVE_ORDER_ENABLED 未开启。当前只允许读取账户和生成委托预案。",
        }
    return {
        "allowed": False,
        "message": "项目尚未开放 QMT 真实下单入口，需要完成小额人工确认测试后再启用。",
    }
