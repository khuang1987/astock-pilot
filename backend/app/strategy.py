from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(float(min(high, max(low, value))), 2)


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
    out["ma60"] = out["close"].rolling(60).mean()
    out["vol_ma20"] = out["volume"].rolling(20).mean()
    out["vol_std20"] = out["volume"].rolling(20).std() / out["vol_ma20"].replace(0, np.nan)
    out["amount_ma20"] = out["amount"].rolling(20).mean()
    out["amount_ma60"] = out["amount"].rolling(60).mean()
    out["ret_5"] = out["close"].pct_change(5)
    out["ret_10"] = out["close"].pct_change(10)
    out["ret_20"] = out["close"].pct_change(20)
    out["ret_60"] = out["close"].pct_change(60)
    out["high_20"] = out["high"].rolling(20).max()
    out["high_60"] = out["high"].rolling(60).max()
    out["low_20"] = out["low"].rolling(20).min()
    out["low_60"] = out["low"].rolling(60).min()
    out["amplitude_20"] = ((out["high"] - out["low"]) / out["close"]).rolling(20).mean()
    out["drawdown_20"] = out["close"] / out["high_20"].replace(0, np.nan) - 1
    out["drawdown_60"] = out["close"] / out["high_60"].replace(0, np.nan) - 1
    out["range_pos_20"] = (out["close"] - out["low_20"]) / (out["high_20"] - out["low_20"]).replace(0, np.nan)
    return out


def _market_context_from_rows(rows: list[pd.Series]) -> dict:
    valid = [r for r in rows if not pd.isna(r.get("ma20")) and not pd.isna(r.get("ret_20"))]
    if len(valid) < 10:
        return {
            "state": "neutral",
            "multiplier": 1.0,
            "max_position_pct": 10.0,
            "allow_new_buy": True,
            "note": "样本不足，按中性环境处理",
            "avg_ret20": 0.0,
            "avg_ret60": 0.0,
            "breadth": 0.5,
            "market_score": 70.0,
        }

    above_ma20 = sum(1 for r in valid if float(r["close"]) > float(r["ma20"])) / len(valid)
    avg_ret20 = float(np.mean([float(r["ret_20"]) for r in valid]))
    avg_ret60 = float(np.mean([float(r.get("ret_60") or 0) for r in valid if not pd.isna(r.get("ret_60"))])) if valid else 0
    rising_5 = sum(1 for r in valid if not pd.isna(r.get("ret_5")) and float(r["ret_5"]) > 0) / len(valid)
    if above_ma20 >= 0.6 and avg_ret20 > 0.02:
        return {"state": "strong", "multiplier": 1.08, "max_position_pct": 10.0, "allow_new_buy": True, "note": "市场偏强，允许趋势和启动策略", "avg_ret20": avg_ret20, "avg_ret60": avg_ret60, "breadth": above_ma20, "market_score": 92.0}
    if above_ma20 >= 0.45 and avg_ret20 > -0.02:
        return {"state": "range", "multiplier": 1.0, "max_position_pct": 8.0, "allow_new_buy": True, "note": "市场震荡，优先强势回调和高质量启动", "avg_ret20": avg_ret20, "avg_ret60": avg_ret60, "breadth": above_ma20, "market_score": 78.0}
    if above_ma20 >= 0.32 or rising_5 >= 0.45:
        return {"state": "weak", "multiplier": 0.88, "max_position_pct": 5.0, "allow_new_buy": True, "note": "市场偏弱，只允许高分小仓位试探", "avg_ret20": avg_ret20, "avg_ret60": avg_ret60, "breadth": above_ma20, "market_score": 58.0}
    return {"state": "risk_off", "multiplier": 0.0, "max_position_pct": 0.0, "allow_new_buy": False, "note": "市场风险较高，禁止新增买入", "avg_ret20": avg_ret20, "avg_ret60": avg_ret60, "breadth": above_ma20, "market_score": 20.0}


def _market_context(data_by_symbol: dict[str, pd.DataFrame], trade_date: str) -> dict:
    rows = []
    for df in data_by_symbol.values():
        matched = df[df["trade_date"] == trade_date]
        if not matched.empty:
            rows.append(matched.iloc[-1])
    return _market_context_from_rows(rows)


def _strategy_scores(row: pd.Series, prev: pd.Series, market: dict | None = None, factors: dict | None = None) -> tuple[dict[str, float], list[str], list[str]]:
    market = market or {"avg_ret20": 0.0, "avg_ret60": 0.0, "market_score": 70.0}
    factors = factors or {}
    close = float(row["close"])
    ma5 = float(row["ma5"])
    ma10 = float(row["ma10"])
    ma20 = float(row["ma20"])
    ma60 = float(row["ma60"])
    ret5 = float(row["ret_5"])
    ret10 = float(row["ret_10"])
    ret20 = float(row["ret_20"])
    ret60 = float(row["ret_60"])
    volume_ratio = float(row["volume"]) / max(float(row["vol_ma20"]), 1)
    amount = float(row["amount"])
    amount_ma20 = float(row["amount_ma20"])
    amount_ma60 = float(row["amount_ma60"])
    amp = float(row["amplitude_20"])
    drawdown20 = float(row["drawdown_20"])
    drawdown60 = float(row["drawdown_60"])
    range_pos20 = float(row["range_pos_20"])
    vol_std20 = float(row["vol_std20"]) if not pd.isna(row["vol_std20"]) else 1.0

    scores: dict[str, float] = {}
    tags: list[str] = []
    reasons: list[str] = []

    trend = 0.0
    if close > ma20:
        trend += 22
    if ma5 > ma20:
        trend += 18
    if ma20 > ma60:
        trend += 18
    if prev["ma5"] <= prev["ma20"] < ma5:
        trend += 14
    if ret20 > 0:
        trend += min(16, ret20 * 110)
    if ret60 > 0:
        trend += min(12, ret60 * 55)
    if 1.05 <= volume_ratio <= 2.8:
        trend += 8
    scores["trend_breakout"] = round(min(100, trend), 2)
    if scores["trend_breakout"] >= 62:
        tags.append("趋势突破")
        reasons.append("趋势突破：均线结构转强，价格站上中长期均线")

    pullback = 0.0
    if ma5 >= ma10 >= ma20:
        pullback += 28
    if ma20 >= ma60:
        pullback += 14
    if close >= ma20 and close <= ma10 * 1.04:
        pullback += 22
    if ret20 > 0.03:
        pullback += 14
    if 0.65 <= volume_ratio <= 1.25:
        pullback += 14
    if amp < 0.06:
        pullback += 8
    if -0.08 <= drawdown20 <= -0.005:
        pullback += 9
    if range_pos20 >= 0.35:
        pullback += 7
    scores["strong_pullback"] = round(min(100, pullback), 2)
    if scores["strong_pullback"] >= 62:
        tags.append("强势回调")
        reasons.append("强势回调：中期趋势未破，回撤深度和波动可控")

    launch = 0.0
    if close > ma20:
        launch += 18
    if close > ma60:
        launch += 10
    if prev["close"] <= prev["ma20"] < close or prev["ma5"] <= prev["ma20"] < ma5:
        launch += 22
    if 1.4 <= volume_ratio <= 3.2:
        launch += 24
    if 0 <= ret20 <= 0.12:
        launch += 12
    if amp < 0.07:
        launch += 8
    if close >= float(row["high_20"]) * 0.97:
        launch += 12
    if ret5 >= 0 and ret10 >= 0:
        launch += 6
    scores["volume_launch"] = round(min(100, launch), 2)
    if scores["volume_launch"] >= 62:
        tags.append("放量启动")
        reasons.append("放量启动：价格接近阶段高位并出现有效量能")

    relative = 0.0
    if ret20 > float(market.get("avg_ret20", 0)) + 0.02:
        relative += 28
    if ret60 > float(market.get("avg_ret60", 0)) + 0.03:
        relative += 22
    if close >= float(row["high_60"]) * 0.9:
        relative += 16
    if ret5 > 0:
        relative += min(12, ret5 * 180)
    if ret10 > 0:
        relative += min(10, ret10 * 120)
    if close > ma20:
        relative += 12
    if volume_ratio >= 0.8:
        relative += 8
    scores["relative_strength"] = _clip(relative)
    if scores["relative_strength"] >= 62:
        tags.append("相对强势")
        reasons.append("相对强势：阶段收益和位置强于当前股票池均值")

    liquidity = 0.0
    if amount >= 300_000_000:
        liquidity += 30
    elif amount >= 150_000_000:
        liquidity += 22
    elif amount >= 80_000_000:
        liquidity += 14
    if amount_ma20 >= 200_000_000:
        liquidity += 24
    elif amount_ma20 >= 100_000_000:
        liquidity += 16
    if amount >= amount_ma20 * 0.8:
        liquidity += 14
    if amount_ma20 >= amount_ma60 * 0.8:
        liquidity += 10
    if 0.7 <= volume_ratio <= 2.8:
        liquidity += 12
    if vol_std20 <= 0.75:
        liquidity += 10
    scores["liquidity_quality"] = _clip(liquidity)
    if scores["liquidity_quality"] >= 72:
        tags.append("流动性健康")
        reasons.append("流动性健康：成交额和量能稳定性满足短线执行要求")

    risk = 0.0
    if amp <= 0.045:
        risk += 28
    elif amp <= 0.065:
        risk += 20
    elif amp <= 0.09:
        risk += 12
    if drawdown20 >= -0.08:
        risk += 22
    elif drawdown20 >= -0.14:
        risk += 14
    if drawdown60 >= -0.16:
        risk += 18
    elif drawdown60 >= -0.24:
        risk += 10
    if close >= ma20:
        risk += 18
    if 0.15 <= range_pos20 <= 0.92:
        risk += 14
    scores["risk_control"] = _clip(risk)

    actionable = max(scores["trend_breakout"], scores["strong_pullback"], scores["volume_launch"], scores["relative_strength"])
    consensus_bonus = min(8.0, max(0, len([tag for tag in tags if tag != "流动性健康"]) - 1) * 4.0)
    evidence = (
        actionable * 0.50
        + scores["volume_launch"] * 0.12
        + scores["liquidity_quality"] * 0.13
        + scores["relative_strength"] * 0.12
        + scores["risk_control"] * 0.08
        + float(market.get("market_score", 70.0)) * 0.05
        + consensus_bonus
    )
    scores["evidence_score_v15"] = _clip(evidence)
    scores["fund_flow_score"] = _clip(float(factors.get("fund_flow_score", 50.0)))
    scores["theme_score"] = _clip(float(factors.get("theme_score", 50.0)))
    scores["message_risk_score"] = _clip(float(factors.get("message_risk_score", 58.0)))
    scores["factor_score"] = _clip(
        scores["fund_flow_score"] * 0.36
        + scores["theme_score"] * 0.24
        + scores["message_risk_score"] * 0.40
    )
    scores["evidence_score"] = _clip(
        scores["evidence_score_v15"] * 0.72
        + scores["fund_flow_score"] * 0.10
        + scores["theme_score"] * 0.08
        + scores["message_risk_score"] * 0.10
    )
    if scores["fund_flow_score"] >= 65:
        tags.append("资金流入")
        reasons.append("资金面：个股资金流和资金排名支持当前信号")
    elif scores["fund_flow_score"] <= 38:
        reasons.append("资金面：资金流偏弱，降低技术信号置信度")
    if scores["theme_score"] >= 65:
        tags.append("题材活跃")
        reasons.append("题材面：行业/概念热度或研报主题支持")
    if scores["message_risk_score"] <= 45:
        tags.append("消息风险")
        reasons.append(f"风险面：{factors.get('message_risk_note', '消息面存在明显风险')}")
    elif scores["message_risk_score"] >= 78:
        tags.append("消息健康")
        reasons.append(f"风险面：{factors.get('message_risk_note', '消息面未见明显负面')}")
    scores["fund_net_ratio"] = round(float(factors.get("fund_net_ratio", 0.0) or 0.0), 2)
    scores["fund_net_amount"] = round(float(factors.get("fund_net_amount", 0.0) or 0.0), 2)
    scores["fund_turnover"] = round(float(factors.get("fund_turnover", 0.0) or 0.0), 2)
    scores["fund_rank_score"] = round(float(factors.get("fund_rank_score", 0.0) or 0.0), 2)
    scores["message_negative_count"] = float(factors.get("message_negative_count", 0) or 0)
    scores["message_positive_count"] = float(factors.get("message_positive_count", 0) or 0)
    if factors.get("message_risk_note"):
        scores["message_risk_note"] = str(factors.get("message_risk_note"))
    if factors.get("message_risk_hint"):
        scores["message_risk_hint"] = str(factors.get("message_risk_hint"))
    if factors.get("theme_industry"):
        scores["theme_industry"] = str(factors.get("theme_industry"))
    if factors.get("industry_heat_score") is not None:
        scores["industry_heat_score"] = round(float(factors.get("industry_heat_score") or 0.0), 2)
    if factors.get("concept_heat_score") is not None:
        scores["concept_heat_score"] = round(float(factors.get("concept_heat_score") or 0.0), 2)
    if factors.get("theme_hits"):
        scores["theme_hits"] = list(factors.get("theme_hits") or [])[:6]

    return scores, tags, reasons


def _signal_from_rows(symbol: str, row: pd.Series, prev: pd.Series, market: dict | None = None, factors: dict | None = None) -> Signal | None:
    required = ["ma5", "ma10", "ma20", "ma60", "vol_ma20", "amount_ma20", "amount_ma60", "ret_5", "ret_10", "ret_20", "ret_60", "amplitude_20", "drawdown_20", "drawdown_60", "range_pos_20"]
    if row[required].isna().any():
        return None
    if row["close"] < 3 or row["amount"] < 80_000_000:
        return None

    market = market or {"state": "neutral", "multiplier": 1.0, "max_position_pct": 8.0, "allow_new_buy": True, "note": "中性环境"}
    if not market["allow_new_buy"]:
        return None
    factors = factors or {}
    strategy_scores, strategy_tags, strategy_reasons = _strategy_scores(row, prev, market, factors)
    actionable_score = max(
        strategy_scores.get("trend_breakout", 0.0),
        strategy_scores.get("strong_pullback", 0.0),
        strategy_scores.get("volume_launch", 0.0),
        strategy_scores.get("relative_strength", 0.0),
    )
    if actionable_score < 62:
        return None
    score = round(min(100.0, strategy_scores.get("evidence_score", 0.0) * float(market["multiplier"])), 2)
    if strategy_scores.get("message_risk_score", 58.0) <= 35:
        score = round(score * 0.82, 2)
    if score < 62:
        return None

    close = float(row["close"])
    ma20 = float(row["ma20"])
    stop_loss = round(max(close * 0.95, ma20 * 0.985), 2)
    position_pct = 10.0 if score >= 88 else 8.0 if score >= 82 else 6.0
    position_pct = min(position_pct, float(market["max_position_pct"]))
    reasons = strategy_reasons + [
        f"证据评分v1.6：技术 {strategy_scores.get('evidence_score_v15', 0.0)}，资金 {strategy_scores.get('fund_flow_score', 0.0)}，题材 {strategy_scores.get('theme_score', 0.0)}，消息风险 {strategy_scores.get('message_risk_score', 0.0)}，综合 {strategy_scores.get('evidence_score', 0.0)}",
        market["note"],
    ]
    return Signal(
        symbol=symbol,
        trade_date=str(row["trade_date"]),
        score=score,
        trend_score=max(strategy_scores.get("trend_breakout", 0.0), strategy_scores.get("relative_strength", 0.0)),
        volume_score=max(strategy_scores.get("volume_launch", 0.0), strategy_scores.get("liquidity_quality", 0.0)),
        risk_score=strategy_scores.get("risk_control", 0.0),
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


def build_signal(symbol: str, df: pd.DataFrame, factors: dict | None = None) -> Signal | None:
    if len(df) < 120:
        return None
    data = enrich_bars(df)
    return _signal_from_rows(symbol, data.iloc[-1], data.iloc[-2], factors=factors)


def build_latest_signals(bars_by_symbol: dict[str, pd.DataFrame], factors_by_symbol: dict[str, dict] | None = None) -> list[Signal]:
    factors_by_symbol = factors_by_symbol or {}
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
        signal = _signal_from_rows(symbol, data.iloc[-1], data.iloc[-2], market, factors_by_symbol.get(symbol))
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
    if data_by_symbol:
        market_source = pd.concat(data_by_symbol.values(), ignore_index=True)
        market_source = market_source[(market_source["trade_date"] >= start_date) & (market_source["trade_date"] <= end_date)]
        for trade_date, day_rows in market_source.groupby("trade_date", sort=False):
            market_by_date[str(trade_date)] = _market_context_from_rows([row for _, row in day_rows.iterrows()])
    for symbol, data in data_by_symbol.items():
        for idx in range(119, len(data)):
            row = data.iloc[idx]
            trade_date = str(row["trade_date"])
            if trade_date < start_date or trade_date > end_date:
                continue
            market = market_by_date.get(trade_date) or _market_context(data_by_symbol, trade_date)
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


def _bt_trade_costs(price: float, quantity: int, side: str, fee_rate: float) -> dict:
    gross = round(float(price) * int(quantity), 2)
    commission = max(5.0, gross * float(fee_rate or 0))
    transfer = gross * 0.00001
    tax = gross * 0.0005 if side == "sell" else 0.0
    fee = round(commission + transfer, 2)
    tax = round(tax, 2)
    net = round(gross + fee if side == "buy" else gross - fee - tax, 2)
    return {"amount": gross, "fee": fee, "tax": tax, "net_amount": net}


def _bt_execution_price(price: float, side: str, slippage_pct: float = 0.15) -> float:
    slip = float(slippage_pct or 0) / 100
    adjusted = float(price) * (1 + slip if side == "buy" else 1 - slip)
    return round(adjusted, 2)


def _bt_lot_quantity(budget: float, price: float) -> int:
    if price <= 0 or budget <= 0:
        return 0
    return int(float(budget) / float(price) / 100) * 100


def _bt_partial_quantity(quantity: int, pct: float = 50.0) -> int:
    total = int(quantity or 0)
    if total <= 0:
        return 0
    sell_qty = int(total * float(pct or 50.0) / 100 / 100) * 100
    if sell_qty <= 0:
        sell_qty = min(100, total)
    if total - sell_qty < 100:
        return total
    return sell_qty


def _bt_json_list(value) -> list:
    if isinstance(value, list):
        return value
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


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
        return {
            "total_return_pct": 0,
            "win_rate_pct": 0,
            "max_drawdown_pct": 0,
            "trade_count": 0,
            "avg_return_pct": 0,
            "final_assets": round(initial_cash, 2),
            "cash": round(initial_cash, 2),
            "market_value": 0,
            "open_positions": 0,
            "signal_count": int(len(signals)) if signals is not None else 0,
        }, [], []

    bars = bars.sort_values(["symbol", "trade_date"]).copy()
    bars["trade_date"] = bars["trade_date"].astype(str)
    for col in ("open", "high", "low", "close", "amount", "volume"):
        if col in bars:
            bars[col] = pd.to_numeric(bars[col], errors="coerce")
    bars = bars.dropna(subset=["symbol", "trade_date", "open", "high", "low", "close"])
    by_symbol = {symbol: df.reset_index(drop=True) for symbol, df in bars.groupby("symbol")}
    sigs = signals[(signals["trade_date"] >= start_date) & (signals["trade_date"] <= end_date)].copy()
    if sigs.empty:
        return {
            "total_return_pct": 0,
            "win_rate_pct": 0,
            "max_drawdown_pct": 0,
            "trade_count": 0,
            "avg_return_pct": 0,
            "final_assets": round(initial_cash, 2),
            "cash": round(initial_cash, 2),
            "market_value": 0,
            "open_positions": 0,
            "signal_count": 0,
        }, [], []
    sigs["trade_date"] = sigs["trade_date"].astype(str)
    sigs["score"] = pd.to_numeric(sigs["score"], errors="coerce").fillna(0)
    sigs = sigs.sort_values(["trade_date", "score"], ascending=[True, False])
    signals_by_date = {date: group.copy() for date, group in sigs.groupby("trade_date")}
    dates = sorted(d for d in bars["trade_date"].unique().tolist() if start_date <= str(d) <= end_date)
    bar_lookup = {
        (str(row["symbol"]), str(row["trade_date"])): row
        for _, row in bars.iterrows()
    }

    trades: list[dict] = []
    cash = float(initial_cash)
    equity_curve: list[dict] = []
    positions: dict[str, dict] = {}
    skipped = {"cash": 0, "slot": 0, "price": 0, "lot": 0, "duplicate": 0, "missing_bar": 0}
    max_daily_buys = max(1, min(int(max_positions), 2))
    max_daily_sells = max(1, int(max_positions))
    buy_tolerance_pct = 0.8
    take_profit_sell_pct = 50.0
    trailing_stop_pct = 4.0

    def market_value(date: str) -> float:
        value = 0.0
        for symbol, pos in positions.items():
            row = bar_lookup.get((symbol, date))
            if row is not None:
                pos["last_price"] = float(row["close"])
            value += float(pos.get("last_price", pos["entry_price"])) * int(pos["quantity"])
        return round(value, 2)

    def sell_position(symbol: str, pos: dict, date: str, price: float, quantity: int, reason: str) -> None:
        nonlocal cash
        quantity = min(int(quantity), int(pos["quantity"]))
        if quantity <= 0:
            return
        costs = _bt_trade_costs(price, quantity, "sell", fee_rate)
        old_qty = int(pos["quantity"])
        cost_basis = float(pos["cost"]) * quantity / old_qty
        cash += float(costs["net_amount"])
        pos["quantity"] = old_qty - quantity
        pos["cost"] = round(float(pos["cost"]) - cost_basis, 2)
        pnl = round(float(costs["net_amount"]) - cost_basis, 2)
        return_pct = round(pnl / cost_basis * 100, 2) if cost_basis else 0.0
        trades.append({
            "symbol": symbol,
            "entry_date": pos["entry_date"],
            "exit_date": date,
            "entry_price": round(float(pos["entry_price"]), 2),
            "exit_price": round(float(price), 2),
            "quantity": quantity,
            "return_pct": return_pct,
            "pnl": pnl,
            "reason": reason,
            "strategy_tags": pos.get("strategy_tags", []),
            "market_state": pos.get("market_state", "neutral"),
            "score": pos.get("score", 0),
            "fee": costs["fee"],
            "tax": costs["tax"],
        })
        if pos["quantity"] <= 0:
            positions.pop(symbol, None)

    for idx, trade_date in enumerate(dates):
        sell_count = 0
        buy_count = 0

        for symbol, pos in list(positions.items()):
            row = bar_lookup.get((symbol, trade_date))
            if row is None:
                continue
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])
            pos["last_price"] = close
            pos["peak"] = max(float(pos.get("peak", close)), high)
            if pos["entry_date"] == trade_date:
                continue
            if sell_count >= max_daily_sells:
                continue

            stop = float(pos["stop_loss"])
            if int(pos.get("stage", 0)) > 0:
                stop = max(stop, float(pos["peak"]) * (1 - trailing_stop_pct / 100))
            if low <= stop:
                sell_position(symbol, pos, trade_date, _bt_execution_price(stop, "sell"), int(pos["quantity"]), "移动止损" if int(pos.get("stage", 0)) > 0 else "止损")
                sell_count += 1
                continue
            if symbol not in positions:
                continue

            pos = positions[symbol]
            if int(pos.get("stage", 0)) == 0 and high >= float(pos["take_profit"]):
                sell_qty = _bt_partial_quantity(int(pos["quantity"]), take_profit_sell_pct)
                sell_position(symbol, pos, trade_date, _bt_execution_price(float(pos["take_profit"]), "sell"), sell_qty, "第一止盈")
                sell_count += 1
                if symbol in positions:
                    positions[symbol]["stage"] = 1
                    positions[symbol]["stop_loss"] = max(float(positions[symbol]["stop_loss"]), float(positions[symbol]["peak"]) * (1 - trailing_stop_pct / 100))
                continue
            if symbol not in positions:
                continue

            pos = positions[symbol]
            pos["bars_held"] = int(pos.get("bars_held", 0)) + 1
            if hold_days > 0 and int(pos["bars_held"]) >= int(hold_days):
                sell_position(symbol, pos, trade_date, _bt_execution_price(close, "sell"), int(pos["quantity"]), "持有到期")
                sell_count += 1

        prev_date = dates[idx - 1] if idx > 0 else None
        day_sigs = signals_by_date.get(prev_date) if prev_date else None
        if day_sigs is not None and not day_sigs.empty:
            for _, sig in day_sigs.iterrows():
                if buy_count >= max_daily_buys:
                    break
                if len(positions) >= int(max_positions):
                    skipped["slot"] += 1
                    break
                symbol = str(sig["symbol"])
                if symbol in positions:
                    skipped["duplicate"] += 1
                    continue
                row = bar_lookup.get((symbol, trade_date))
                if row is None:
                    skipped["missing_bar"] += 1
                    continue
                trigger = float(sig["entry_price"])
                limit_price = trigger * (1 + buy_tolerance_pct / 100)
                if float(row["open"]) > limit_price or float(row["low"]) > limit_price:
                    skipped["price"] += 1
                    continue
                entry_price = _bt_execution_price(min(float(row["open"]), limit_price), "buy")
                position_pct = min(float(sig.get("position_pct", 6.0) or 6.0), 100.0)
                budget = min(cash, float(initial_cash) * position_pct / 100)
                quantity = _bt_lot_quantity(budget, entry_price)
                if quantity <= 0:
                    skipped["lot"] += 1
                    continue
                costs = _bt_trade_costs(entry_price, quantity, "buy", fee_rate)
                if float(costs["net_amount"]) > cash:
                    skipped["cash"] += 1
                    continue
                cash -= float(costs["net_amount"])
                positions[symbol] = {
                    "symbol": symbol,
                    "entry_date": trade_date,
                    "entry_price": entry_price,
                    "quantity": quantity,
                    "cost": float(costs["net_amount"]),
                    "stop_loss": float(sig["stop_loss"]),
                    "take_profit": float(sig["take_profit_1"]),
                    "stage": 0,
                    "peak": max(float(row["high"]), entry_price),
                    "last_price": float(row["close"]),
                    "bars_held": 0,
                    "strategy_tags": _bt_json_list(sig.get("strategy_tags")),
                    "market_state": sig.get("market_state", "neutral") if "market_state" in sig else "neutral",
                    "score": round(float(sig.get("score", 0) or 0), 2),
                }
                buy_count += 1

        mv = market_value(trade_date)
        equity_curve.append({
            "date": str(trade_date),
            "equity": round(cash + mv, 2),
            "cash": round(cash, 2),
            "market_value": mv,
            "positions": len(positions),
        })

    wins = [t for t in trades if t["return_pct"] > 0]
    returns = [t["return_pct"] for t in trades]
    final_equity = equity_curve[-1]["equity"] if equity_curve else round(initial_cash, 2)
    final_market_value = equity_curve[-1]["market_value"] if equity_curve else 0
    summary = {
        "total_return_pct": round((final_equity / initial_cash - 1) * 100, 2),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "max_drawdown_pct": max_drawdown([x["equity"] for x in equity_curve]),
        "trade_count": len(trades),
        "avg_return_pct": round(float(np.mean(returns)), 2) if returns else 0,
        "final_assets": round(final_equity, 2),
        "cash": round(cash, 2),
        "market_value": round(final_market_value, 2),
        "open_positions": len(positions),
        "signal_count": int(len(sigs)),
        "skipped": skipped,
    }
    return summary, trades, equity_curve
