from __future__ import annotations

from datetime import datetime, timedelta

import akshare as ak
import pandas as pd


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
        return FALLBACK_SYMBOLS[:limit]


def fetch_daily_bars(symbol: str, days: int) -> pd.DataFrame:
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 90)).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist(symbol=normalize_symbol(symbol), period="daily", start_date=start, end_date=end, adjust="qfq")
    if df.empty:
        return pd.DataFrame()

    rename = {
        "日期": "trade_date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=rename)
    cols = ["trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
    df = df[cols].copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    for col in cols[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().tail(days)
    return df
