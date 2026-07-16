from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta
import io
import re

import akshare as ak
import pandas as pd
import requests

try:
    from . import a_stock_data_provider
except Exception:  # pragma: no cover - optional data source
    a_stock_data_provider = None

try:
    import baostock as bs
except Exception:  # pragma: no cover - optional data source
    bs = None


FALLBACK_SYMBOLS = [
    ("000001", "平安银行"),
    ("000002", "万科A"),
    ("000063", "中兴通讯"),
    ("000333", "美的集团"),
    ("000651", "格力电器"),
    ("000725", "京东方A"),
    ("000858", "五粮液"),
    ("002415", "海康威视"),
    ("002594", "比亚迪"),
    ("300059", "东方财富"),
    ("300750", "宁德时代"),
    ("600000", "浦发银行"),
    ("600030", "中信证券"),
    ("600036", "招商银行"),
    ("600050", "中国联通"),
    ("600519", "贵州茅台"),
    ("600690", "海尔智家"),
    ("601318", "中国平安"),
    ("601398", "工商银行"),
    ("601888", "中国中免"),
]


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().split(".")[0].zfill(6)


def load_stock_universe(limit: int) -> list[tuple[str, str]]:
    try:
        spot = ak.stock_zh_a_spot_em()
        code_col = "代码"
        name_col = "名称"
        df = spot[[code_col, name_col]].dropna()
        df = df[~df[name_col].astype(str).str.contains("ST|退", regex=True)]
        df[code_col] = df[code_col].astype(str).map(normalize_symbol)
        rows = list(df.head(limit).itertuples(index=False, name=None))
        return [(str(code), str(name)) for code, name in rows]
    except Exception:
        baostock_universe = _load_stock_universe_baostock(limit)
        if baostock_universe:
            return baostock_universe
        return FALLBACK_SYMBOLS[:limit]


def fetch_daily_bars(symbol: str, days: int) -> pd.DataFrame:
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 90)).strftime("%Y%m%d")
    astock = _fetch_daily_bars_a_stock_data(symbol, days)
    if not astock.empty:
        return astock
    direct = _fetch_daily_bars_eastmoney(symbol, days, start, end)
    if not direct.empty:
        return direct
    return _fetch_daily_bars_from_realtime_close(symbol)


def _fetch_daily_bars_a_stock_data(symbol: str, days: int) -> pd.DataFrame:
    if a_stock_data_provider is None:
        return pd.DataFrame()
    try:
        return a_stock_data_provider.fetch_daily_bars(symbol, days)
    except Exception:
        return pd.DataFrame()


def _fetch_daily_bars_from_realtime_close(symbol: str) -> pd.DataFrame:
    quote = _fetch_realtime_quotes_sina([symbol]).get(normalize_symbol(symbol))
    if not quote:
        return pd.DataFrame()
    quote_date = str(quote.get("updated_at") or "")[:10]
    if not quote_date:
        return pd.DataFrame()
    return pd.DataFrame([{
        "trade_date": quote_date,
        "open": quote.get("open") or quote["price"],
        "high": quote.get("high") or quote["price"],
        "low": quote.get("low") or quote["price"],
        "close": quote["price"],
        "volume": quote.get("volume") or 0,
        "amount": quote.get("amount") or 0,
        "pct_chg": quote.get("pct_chg") or 0,
    }])


def _fetch_daily_bars_eastmoney(symbol: str, days: int, start: str, end: str) -> pd.DataFrame:
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={
                "secid": _eastmoney_secid(symbol),
                "klt": "101",
                "fqt": "1",
                "beg": start,
                "end": end,
                "lmt": "1000000",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            },
            headers={
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=8,
        )
        response.raise_for_status()
        klines = ((response.json().get("data") or {}).get("klines") or [])
    except Exception:
        return pd.DataFrame()
    rows = []
    for item in klines:
        parts = str(item).split(",")
        if len(parts) < 9:
            continue
        rows.append({
            "trade_date": parts[0],
            "open": parts[1],
            "close": parts[2],
            "high": parts[3],
            "low": parts[4],
            "volume": parts[5],
            "amount": parts[6],
            "pct_chg": parts[8],
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg"])
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna().tail(days)


def _baostock_code(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    prefix = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}.{symbol}"


def _from_baostock_code(code: str) -> str:
    return normalize_symbol(code.split(".")[-1])


def _is_a_share_code(code: str) -> bool:
    symbol = _from_baostock_code(code)
    if code.startswith("sh."):
        return symbol.startswith(("600", "601", "603", "605"))
    if code.startswith("sz."):
        return symbol.startswith(("000", "001", "002", "003", "300", "301"))
    return False


def _baostock_login() -> bool:
    if bs is None:
        return False
    with redirect_stdout(io.StringIO()):
        result = bs.login()
    return getattr(result, "error_code", "1") == "0"


def _baostock_logout() -> None:
    if bs is None:
        return
    with redirect_stdout(io.StringIO()):
        bs.logout()


def _load_stock_universe_baostock(limit: int) -> list[tuple[str, str]]:
    if not _baostock_login():
        return []
    try:
        for offset in range(10):
            day = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
            rs = bs.query_all_stock(day=day)
            if getattr(rs, "error_code", "1") != "0":
                continue
            rows: list[tuple[str, str]] = []
            while rs.next():
                row = rs.get_row_data()
                if len(row) < 3:
                    continue
                code, trade_status, name = row[0], row[1], row[2]
                if trade_status != "1" or not _is_a_share_code(code):
                    continue
                if re.search(r"ST|退", name):
                    continue
                rows.append((_from_baostock_code(code), name))
                if len(rows) >= limit:
                    return rows
        return []
    finally:
        _baostock_logout()


def _fetch_daily_bars_baostock(symbol: str, days: int) -> pd.DataFrame:
    if not _baostock_login():
        return pd.DataFrame()
    try:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=days + 120)).strftime("%Y-%m-%d")
        rs = bs.query_history_k_data_plus(
            _baostock_code(symbol),
            "date,open,high,low,close,volume,amount,pctChg",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="2",
        )
        if getattr(rs, "error_code", "1") != "0":
            return pd.DataFrame()
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg"])
        for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["trade_date", "open", "high", "low", "close", "volume", "amount"]).copy()
        df["pct_chg"] = df["pct_chg"].fillna(df["close"].pct_change() * 100).fillna(0)
        return df.tail(days)
    finally:
        _baostock_logout()


def fetch_realtime_quotes(symbols: list[str]) -> dict[str, dict]:
    wanted = {normalize_symbol(symbol) for symbol in symbols}
    if not wanted:
        return {}
    astock_quotes = _fetch_realtime_quotes_a_stock_data(sorted(wanted))
    if astock_quotes:
        return astock_quotes
    sina_quotes = _fetch_realtime_quotes_sina(sorted(wanted))
    if sina_quotes:
        return sina_quotes
    return _fetch_realtime_quotes_direct(sorted(wanted))


def _fetch_realtime_quotes_a_stock_data(symbols: list[str]) -> dict[str, dict]:
    if a_stock_data_provider is None:
        return {}
    try:
        return a_stock_data_provider.fetch_realtime_quotes(symbols)
    except Exception:
        return {}


def _eastmoney_secid(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    market = "1" if symbol.startswith(("5", "6", "9")) else "0"
    return f"{market}.{symbol}"


def _scaled_price(value) -> float | None:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number) or float(number) <= 0:
        return None
    return round(float(number) / 100, 2)


def _fetch_realtime_quotes_direct(symbols: list[str]) -> dict[str, dict]:
    session = requests.Session()
    session.trust_env = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }
    hosts = [
        "https://push2.eastmoney.com/api/qt/stock/get",
        "https://82.push2.eastmoney.com/api/qt/stock/get",
        "http://push2.eastmoney.com/api/qt/stock/get",
    ]
    quotes: dict[str, dict] = {}
    for symbol in symbols:
        for url in hosts:
            try:
                response = session.get(
                    url,
                    params={
                        "secid": _eastmoney_secid(symbol),
                        "fields": "f43,f57,f58,f60,f170",
                    },
                    headers=headers,
                    timeout=2,
                )
                response.raise_for_status()
                data = response.json().get("data") or {}
                price = _scaled_price(data.get("f43"))
                if price is None:
                    continue
                pct_chg = pd.to_numeric(data.get("f170"), errors="coerce")
                quotes[symbol] = {
                    "symbol": symbol,
                    "name": str(data.get("f58") or symbol),
                    "price": price,
                    "pct_chg": 0 if pd.isna(pct_chg) else round(float(pct_chg) / 100, 2),
                    "source": "eastmoney",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
                break
            except Exception:
                continue
    return quotes


def _sina_symbol(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    prefix = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}{symbol}"


def _fetch_realtime_quotes_sina(symbols: list[str]) -> dict[str, dict]:
    session = requests.Session()
    session.trust_env = False
    codes = ",".join(_sina_symbol(symbol) for symbol in symbols)
    try:
        response = session.get(
            "https://hq.sinajs.cn/list=" + codes,
            headers={
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=8,
        )
        response.raise_for_status()
        response.encoding = "gb18030"
    except Exception:
        return {}
    quotes: dict[str, dict] = {}
    for match in re.finditer(r'var hq_str_(sh|sz)(\d{6})="([^"]*)";', response.text):
        symbol = match.group(2)
        parts = match.group(3).split(",")
        if len(parts) < 32 or not parts[0]:
            continue
        price = pd.to_numeric(parts[3], errors="coerce")
        prev_close = pd.to_numeric(parts[2], errors="coerce")
        if pd.isna(price) or float(price) <= 0:
            continue
        open_price = pd.to_numeric(parts[1], errors="coerce")
        high = pd.to_numeric(parts[4], errors="coerce")
        low = pd.to_numeric(parts[5], errors="coerce")
        volume = pd.to_numeric(parts[8], errors="coerce") if len(parts) > 8 else 0
        amount = pd.to_numeric(parts[9], errors="coerce") if len(parts) > 9 else 0
        pct_chg = 0
        if not pd.isna(prev_close) and float(prev_close) > 0:
            pct_chg = (float(price) / float(prev_close) - 1) * 100
        quote_date = parts[30] if len(parts) > 30 else datetime.now().strftime("%Y-%m-%d")
        quote_time = parts[31] if len(parts) > 31 else datetime.now().strftime("%H:%M:%S")
        quotes[symbol] = {
            "symbol": symbol,
            "name": parts[0],
            "price": round(float(price), 2),
            "open": round(float(open_price), 2) if not pd.isna(open_price) and float(open_price) > 0 else round(float(price), 2),
            "high": round(float(high), 2) if not pd.isna(high) and float(high) > 0 else round(float(price), 2),
            "low": round(float(low), 2) if not pd.isna(low) and float(low) > 0 else round(float(price), 2),
            "volume": 0 if pd.isna(volume) else float(volume),
            "amount": 0 if pd.isna(amount) else float(amount),
            "pct_chg": round(pct_chg, 2),
            "source": "sina",
            "updated_at": f"{quote_date}T{quote_time}",
        }
    return quotes
