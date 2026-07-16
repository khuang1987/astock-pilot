from __future__ import annotations

from datetime import datetime
from functools import lru_cache

import pandas as pd

try:
    from mootdx.quotes import Quotes
except Exception:  # pragma: no cover - optional provider
    Quotes = None


_TDX_TIMEOUT_SECONDS = 2
_MAX_TDX_BARS = 800


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().split(".")[0].zfill(6)


def _number(value, default: float = 0.0) -> float:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return default
    return float(number)


def _first(row: pd.Series, names: list[str], default=None):
    for name in names:
        if name in row and not pd.isna(row[name]):
            return row[name]
    return default


@lru_cache(maxsize=1)
def _tdx_server() -> tuple[str, int] | None:
    if Quotes is None:
        return None
    try:
        client = Quotes.factory(market="std", bestip=False, timeout=_TDX_TIMEOUT_SECONDS, heartbeat=False)
        server = getattr(client, "server", None)
        close = getattr(getattr(client, "client", None), "disconnect", None)
        if callable(close):
            close()
        if not server:
            return None
        return str(server[0]), int(server[1])
    except Exception:
        return None


def _tdx_client():
    if Quotes is None:
        return None
    server = _tdx_server()
    try:
        kwargs = {"market": "std", "bestip": False, "timeout": _TDX_TIMEOUT_SECONDS, "heartbeat": False}
        if server:
            kwargs["server"] = server
        return Quotes.factory(**kwargs)
    except Exception:
        return None


def _disconnect(client) -> None:
    close = getattr(getattr(client, "client", None), "disconnect", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def fetch_daily_bars(symbol: str, days: int) -> pd.DataFrame:
    """Fetch daily bars through the a-stock-data direction provider.

    The adapter intentionally exposes the same columns as the local strategy
    engine. Any failure returns an empty DataFrame so the existing provider
    chain can continue with Eastmoney/Sina fallbacks.
    """
    symbol = normalize_symbol(symbol)
    client = _tdx_client()
    if client is None:
        return pd.DataFrame()
    try:
        count = min(max(days + 90, 120), _MAX_TDX_BARS)
        raw = client.bars(symbol=symbol, frequency=9, start=0, offset=count)
        if raw is None or raw.empty:
            return pd.DataFrame()
        df = raw.reset_index(drop=True).copy()
    except Exception:
        return pd.DataFrame()
    finally:
        _disconnect(client)

    rows: list[dict] = []
    for _, row in df.iterrows():
        trade_date = _first(row, ["trade_date", "date", "datetime", "time"])
        if trade_date is None:
            continue
        rows.append({
            "trade_date": trade_date,
            "open": _first(row, ["open", "开盘"], 0),
            "high": _first(row, ["high", "最高"], 0),
            "low": _first(row, ["low", "最低"], 0),
            "close": _first(row, ["close", "收盘", "price"], 0),
            "volume": _first(row, ["volume", "vol", "成交量"], 0),
            "amount": _first(row, ["amount", "成交额"], 0),
            "pct_chg": _first(row, ["pct_chg", "percent", "zhangfu", "涨幅"], None),
        })
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg"])
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["pct_chg"] = out["pct_chg"].fillna(out["close"].pct_change() * 100).fillna(0)
    return out.dropna(subset=["trade_date", "open", "high", "low", "close"]).tail(days)


def fetch_realtime_quotes(symbols: list[str]) -> dict[str, dict]:
    symbols = [normalize_symbol(symbol) for symbol in symbols]
    if not symbols:
        return {}
    client = _tdx_client()
    if client is None:
        return {}
    try:
        raw = client.quotes(symbol=symbols)
        if raw is None or raw.empty:
            return {}
        df = raw.reset_index(drop=True)
    except Exception:
        return {}
    finally:
        _disconnect(client)

    quotes: dict[str, dict] = {}
    for _, row in df.iterrows():
        symbol = normalize_symbol(str(_first(row, ["symbol", "code", "代码"], "")))
        if not symbol or symbol == "000000":
            continue
        price = _number(_first(row, ["price", "now", "close", "最新价", "现价"], 0))
        if price <= 0:
            continue
        pct_chg = _number(_first(row, ["pct_chg", "percent", "zhangfu", "涨幅"], 0))
        last_close = _number(_first(row, ["last_close", "昨收"], 0))
        if last_close > 0:
            pct_chg = (price / last_close - 1) * 100
        if abs(pct_chg) > 100:
            pct_chg = pct_chg / 100
        quotes[symbol] = {
            "symbol": symbol,
            "name": str(_first(row, ["name", "名称"], symbol) or symbol),
            "price": round(price, 2),
            "open": round(_number(_first(row, ["open", "开盘"], price), price), 2),
            "high": round(_number(_first(row, ["high", "最高"], price), price), 2),
            "low": round(_number(_first(row, ["low", "最低"], price), price), 2),
            "volume": _number(_first(row, ["volume", "vol", "成交量"], 0)),
            "amount": _number(_first(row, ["amount", "成交额"], 0)),
            "pct_chg": round(pct_chg, 2),
            "source": "mootdx",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    return quotes
