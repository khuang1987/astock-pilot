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
    strategy_tags: list[str]
    strategy_scores: dict[str, float]
    market_state: str
    market_note: str
    decision: str

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
            json.dumps(self.strategy_tags, ensure_ascii=False),
            json.dumps(self.strategy_scores, ensure_ascii=False),
            self.market_state,
            self.market_note,
            self.decision,
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


def _market_context(data_by_symbol: dict[str, pd.DataFrame], trade_date: str) -> dict:
    rows = []
    for df in data_by_symbol.values():
        matched = df[df["trade_date"] == trade_date]
        if not matched.empty:
            rows.append(matched.iloc[-1])
    valid = [r for r in rows if not pd.isna(r.get("ma20")) and not pd.isna(r.get("ret_20"))]
    if len(valid) < 10:
        return {"state": "neutral", "multiplier": 1.0, "max_position_pct": 10.0, "allow_new_buy": True, "note": "样本不足，按中性环境处理"}

    above_ma20 = sum(1 for r in valid if float(r["close"]) > float(r["ma20"])) / len(valid)
    avg_ret20 = float(np.mean([float(r["ret_20"]) for r in valid]))
    if above_ma20 >= 0.6 and avg_ret20 > 0.02:
        return {"state": "strong", "multiplier": 1.08, "max_position_pct": 10.0, "allow_new_buy": True, "note": "市场偏强，允许趋势和启动策略"}
    if above_ma20 >= 0.45 and avg_ret20 > -0.02:
        return {"state": "range", "multiplier": 1.0, "max_position_pct": 8.0, "allow_new_buy": True, "note": "市场震荡，优先强势回调和高质量启动"}
    if above_ma20 >= 0.32:
        return {"state": "weak", "multiplier": 0.86, "max_position_pct": 5.0, "allow_new_buy": True, "note": "市场偏弱，只允许高分小仓位试探"}
    return {"state": "risk_off", "multiplier": 0.0, "max_position_pct": 0.0, "allow_new_buy": False, "note": "市场风险较高，禁止新增买入"}


def _strategy_scores(row: pd.Series, prev: pd.Series) -> tuple[dict[str, float], list[str], list[str]]:
    close = float(row["close"])
    ma5 = float(row["ma5"])
    ma10 = float(row["ma10"])
    ma20 = float(row["ma20"])
    ret20 = float(row["ret_20"])
    volume_ratio = float(row["volume"]) / max(float(row["vol_ma20"]), 1)
    amp = float(row["amplitude_20"])

    scores: dict[str, float] = {}
    tags: list[str] = []
    reasons: list[str] = []

    trend = 0.0
    if close > ma20:
        trend += 28
    if ma5 > ma20:
        trend += 22
    if prev["ma5"] <= prev["ma20"] < ma5:
        trend += 18
    if ret20 > 0:
        trend += min(18, ret20 * 120)
    if 1.05 <= volume_ratio <= 2.8:
        trend += 14
    scores["trend_breakout"] = round(min(100, trend), 2)
    if scores["trend_breakout"] >= 62:
        tags.append("趋势突破")
        reasons.append("趋势突破：均线转强且价格站上中期均线")

    pullback = 0.0
    if ma5 > ma10 > ma20:
        pullback += 35
    if close >= ma20 and close <= ma10 * 1.035:
        pullback += 25
    if ret20 > 0.03:
        pullback += 18
    if 0.65 <= volume_ratio <= 1.25:
        pullback += 15
    if amp < 0.06:
        pullback += 7
    scores["strong_pullback"] = round(min(100, pullback), 2)
    if scores["strong_pullback"] >= 62:
        tags.append("强势回调")
        reasons.append("强势回调：中期趋势未破，回踩后波动可控")

    launch = 0.0
    if close > ma20:
        launch += 24
    if prev["close"] <= prev["ma20"] < close or prev["ma5"] <= prev["ma20"] < ma5:
        launch += 24
    if 1.4 <= volume_ratio <= 3.2:
        launch += 28
    if 0 <= ret20 <= 0.12:
        launch += 14
    if amp < 0.07:
        launch += 10
    scores["volume_launch"] = round(min(100, launch), 2)
    if scores["volume_launch"] >= 62:
        tags.append("放量启动")
        reasons.append("放量启动：价格突破并出现有效量能")

    return scores, tags, reasons


def _signal_from_rows(symbol: str, row: pd.Series, prev: pd.Series, market: dict | None = None) -> Signal | None:
    if row[["ma5", "ma10", "ma20", "vol_ma20", "ret_20", "amplitude_20"]].isna().any():
        return None
    if row["close"] < 3 or row["amount"] < 80_000_000:
        return None

    market = market or {"state": "neutral", "multiplier": 1.0, "max_position_pct": 8.0, "allow_new_buy": True, "note": "中性环境"}
    strategy_scores, strategy_tags, strategy_reasons = _strategy_scores(row, prev)
    raw_score = max(strategy_scores.values()) if strategy_scores else 0.0
    consensus_bonus = min(10.0, max(0, len(strategy_tags) - 1) * 5.0)
    risk_score = max(0.0, 20 - float(row["amplitude_20"]) * 220)
    score = round(min(100.0, (raw_score + consensus_bonus + risk_score * 0.35) * float(market["multiplier"])), 2)
    if not market["allow_new_buy"]:
        return None
    if score < 62:
        return None

    close = float(row["close"])
    ma20 = float(row["ma20"])
    stop_loss = round(max(close * 0.95, ma20 * 0.985), 2)
    position_pct = 10.0 if score >= 82 else 6.0
    position_pct = min(position_pct, float(market["max_position_pct"]))
    reasons = strategy_reasons + [market["note"]]
    return Signal(
        symbol=symbol,
        trade_date=str(row["trade_date"]),
        score=score,
        trend_score=strategy_scores.get("trend_breakout", 0.0),
        volume_score=strategy_scores.get("volume_launch", 0.0),
        risk_score=round(risk_score, 2),
        entry_price=round(close * 1.01, 2),
        stop_loss=stop_loss,
        take_profit_1=round(close * 1.06, 2),
        take_profit_2=round(close * 1.10, 2),
        position_pct=position_pct,
        reasons=reasons,
        strategy_tags=strategy_tags,
        strategy_scores=strategy_scores,
        market_state=market["state"],
        market_note=market["note"],
        decision="buy" if position_pct > 0 else "watch",
    )


def build_signal(symbol: str, df: pd.DataFrame) -> Signal | None:
    if len(df) < 120:
        return None
    data = enrich_bars(df)
    return _signal_from_rows(symbol, data.iloc[-1], data.iloc[-2])


def build_latest_signals(bars_by_symbol: dict[str, pd.DataFrame]) -> list[Signal]:
    data_by_symbol = {
        symbol: enrich_bars(df.reset_index(drop=True))
        for symbol, df in bars_by_symbol.items()
        if len(df) >= 120
    }
    if not data_by_symbol:
        return []
    latest = max(str(df.iloc[-1]["trade_date"]) for df in data_by_symbol.values())
    market = _market_context(data_by_symbol, latest)
    signals: list[Signal] = []
    for symbol, data in data_by_symbol.items():
        if str(data.iloc[-1]["trade_date"]) != latest:
            continue
        signal = _signal_from_rows(symbol, data.iloc[-1], data.iloc[-2], market)
        if signal:
            signals.append(signal)
    return sorted(signals, key=lambda item: item.score, reverse=True)


def build_historical_signals(bars: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    rows: list[dict] = []
    if bars.empty:
        return pd.DataFrame(rows)
    bars = bars.sort_values(["symbol", "trade_date"]).copy()
    data_by_symbol = {symbol: enrich_bars(df.reset_index(drop=True)) for symbol, df in bars.groupby("symbol")}
    market_by_date: dict[str, dict] = {}
    for symbol, data in data_by_symbol.items():
        for idx in range(119, len(data)):
            row = data.iloc[idx]
            trade_date = str(row["trade_date"])
            if trade_date < start_date or trade_date > end_date:
                continue
            market = market_by_date.setdefault(trade_date, _market_context(data_by_symbol, trade_date))
            signal = _signal_from_rows(symbol, row, data.iloc[idx - 1], market)
            if not signal:
                continue
            rows.append(
                {
                    "symbol": signal.symbol,
                    "trade_date": signal.trade_date,
                    "score": signal.score,
                    "trend_score": signal.trend_score,
                    "volume_score": signal.volume_score,
                    "risk_score": signal.risk_score,
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                    "take_profit_1": signal.take_profit_1,
                    "take_profit_2": signal.take_profit_2,
                    "position_pct": signal.position_pct,
                    "reasons": json.dumps(signal.reasons, ensure_ascii=False),
                    "strategy_tags": json.dumps(signal.strategy_tags, ensure_ascii=False),
                    "strategy_scores": json.dumps(signal.strategy_scores, ensure_ascii=False),
                    "market_state": signal.market_state,
                    "market_note": signal.market_note,
                    "decision": signal.decision,
                }
            )
    return pd.DataFrame(rows)


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
        return {"total_return_pct": 0, "win_rate_pct": 0, "max_drawdown_pct": 0, "trade_count": 0, "avg_return_pct": 0}, [], []

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
                    "strategy_tags": json.loads(sig["strategy_tags"]) if "strategy_tags" in sig and not pd.isna(sig["strategy_tags"]) else [],
                    "market_state": sig["market_state"] if "market_state" in sig else "neutral",
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
