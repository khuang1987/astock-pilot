from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Signal:
    symbol: str
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

    def to_db_tuple(self) -> tuple:
        return (
            self.symbol,
            self.trade_date,
            self.score,
            self.trend_score,
            self.volume_score,
            self.risk_score,
            self.entry_price,
            self.stop_loss,
            self.take_profit_1,
            self.take_profit_2,
            self.position_pct,
            json.dumps(self.reasons, ensure_ascii=False),
        )


def enrich_bars(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values("trade_date").copy()
    out["ma5"] = out["close"].rolling(5).mean()
    out["ma10"] = out["close"].rolling(10).mean()
    out["ma20"] = out["close"].rolling(20).mean()
    out["vol_ma20"] = out["volume"].rolling(20).mean()
    out["ret_20"] = out["close"].pct_change(20)
    out["amplitude_20"] = ((out["high"] - out["low"]) / out["close"]).rolling(20).mean()
    return out


def build_signal(symbol: str, df: pd.DataFrame) -> Signal | None:
    if len(df) < 120:
        return None
    data = enrich_bars(df)
    row = data.iloc[-1]
    prev = data.iloc[-2]
    if row[["ma5", "ma10", "ma20", "vol_ma20", "ret_20", "amplitude_20"]].isna().any():
        return None
    if row["close"] < 3 or row["amount"] < 80_000_000:
        return None

    reasons: list[str] = []
    trend_score = 0.0
    if row["close"] > row["ma20"]:
        trend_score += 25
        reasons.append("收盘价站上MA20")
    if row["ma5"] > row["ma20"]:
        trend_score += 20
        reasons.append("MA5强于MA20")
    if prev["ma5"] <= prev["ma20"] < row["ma5"]:
        trend_score += 15
        reasons.append("MA5上穿MA20")
    if row["ret_20"] > 0:
        trend_score += min(15, row["ret_20"] * 100)
        reasons.append("20日趋势为正")

    volume_ratio = row["volume"] / max(row["vol_ma20"], 1)
    volume_score = 0.0
    if 1.05 <= volume_ratio <= 2.5:
        volume_score = 20
        reasons.append("成交量温和放大")
    elif 0.8 <= volume_ratio < 1.05:
        volume_score = 10
        reasons.append("成交量稳定")

    amp = float(row["amplitude_20"])
    risk_score = max(0.0, 20 - amp * 220)
    if amp < 0.055:
        reasons.append("近期波动可控")

    score = round(min(100.0, trend_score + volume_score + risk_score), 2)
    if score < 62:
        return None

    close = float(row["close"])
    ma20 = float(row["ma20"])
    stop_loss = round(max(close * 0.95, ma20 * 0.985), 2)
    return Signal(
        symbol=symbol,
        trade_date=str(row["trade_date"]),
        score=score,
        trend_score=round(trend_score, 2),
        volume_score=round(volume_score, 2),
        risk_score=round(risk_score, 2),
        entry_price=round(close * 1.01, 2),
        stop_loss=stop_loss,
        take_profit_1=round(close * 1.06, 2),
        take_profit_2=round(close * 1.10, 2),
        position_pct=10.0 if score >= 75 else 6.0,
        reasons=reasons,
    )


def max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            worst = min(worst, (value - peak) / peak)
    return round(worst * 100, 2)


def simulate_backtest(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    start_date: str,
    end_date: str,
    initial_cash: float,
    max_positions: int,
    fee_rate: float,
    hold_days: int,
) -> tuple[dict, list[dict], list[dict]]:
    if bars.empty or signals.empty:
        return {"total_return_pct": 0, "win_rate_pct": 0, "max_drawdown_pct": 0, "trade_count": 0}, [], []

    bars = bars.sort_values(["symbol", "trade_date"]).copy()
    by_symbol = {symbol: df.reset_index(drop=True) for symbol, df in bars.groupby("symbol")}
    sigs = signals[(signals["trade_date"] >= start_date) & (signals["trade_date"] <= end_date)].copy()
    sigs = sigs.sort_values(["trade_date", "score"], ascending=[True, False])

    trades: list[dict] = []
    equity = initial_cash
    equity_curve: list[dict] = []
    open_until: dict[str, str] = {}

    for trade_date, day_sigs in sigs.groupby("trade_date"):
        candidates = day_sigs.head(max_positions)
        for _, sig in candidates.iterrows():
            symbol = sig["symbol"]
            if symbol in open_until and open_until[symbol] >= trade_date:
                continue
            df = by_symbol.get(symbol)
            if df is None:
                continue
            idxs = df.index[df["trade_date"] == trade_date].tolist()
            if not idxs or idxs[0] + 1 >= len(df):
                continue
            entry_idx = idxs[0] + 1
            exit_idx = min(entry_idx + hold_days - 1, len(df) - 1)
            entry = float(df.loc[entry_idx, "open"])
            stop = float(sig["stop_loss"])
            target = float(sig["take_profit_1"])
            exit_price = float(df.loc[exit_idx, "close"])
            exit_date = str(df.loc[exit_idx, "trade_date"])
            reason = "到期"
            for i in range(entry_idx, exit_idx + 1):
                low = float(df.loc[i, "low"])
                high = float(df.loc[i, "high"])
                if low <= stop:
                    exit_price = stop
                    exit_date = str(df.loc[i, "trade_date"])
                    reason = "止损"
                    break
                if high >= target:
                    exit_price = target
                    exit_date = str(df.loc[i, "trade_date"])
                    reason = "止盈1"
                    break
            gross = (exit_price - entry) / entry
            net = gross - fee_rate * 2
            alloc = equity / max_positions
            pnl = alloc * net
            equity += pnl
            open_until[symbol] = exit_date
            trades.append(
                {
                    "symbol": symbol,
                    "entry_date": str(df.loc[entry_idx, "trade_date"]),
                    "exit_date": exit_date,
                    "entry_price": round(entry, 2),
                    "exit_price": round(exit_price, 2),
                    "return_pct": round(net * 100, 2),
                    "pnl": round(pnl, 2),
                    "reason": reason,
                }
            )
        equity_curve.append({"date": str(trade_date), "equity": round(equity, 2)})

    wins = [t for t in trades if t["return_pct"] > 0]
    returns = [t["return_pct"] for t in trades]
    summary = {
        "total_return_pct": round((equity / initial_cash - 1) * 100, 2),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "max_drawdown_pct": max_drawdown([x["equity"] for x in equity_curve]),
        "trade_count": len(trades),
        "avg_return_pct": round(float(np.mean(returns)), 2) if returns else 0,
    }
    return summary, trades, equity_curve
