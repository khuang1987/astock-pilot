import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import * as echarts from "echarts";
import { Activity, BarChart3, Bell, Calculator, Database, GitBranch, LayoutDashboard, RefreshCw, Settings, ShieldAlert, Star, TrendingUp, WalletCards } from "lucide-react";
import { api } from "./api";
import "./styles.css";

function useAsync(fn, deps = []) {
  const [state, setState] = useState({ loading: true, error: "", data: null });
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: "" }));
    fn()
      .then((data) => alive && setState({ loading: false, error: "", data }))
      .catch((error) => alive && setState({ loading: false, error: error.message, data: null }));
    return () => {
      alive = false;
    };
  }, deps);
  return state;
}

function useStoredNumber(key, initialValue) {
  const [value, setValue] = useState(() => {
    const stored = window.localStorage.getItem(key);
    return stored ? Number(stored) : initialValue;
  });
  useEffect(() => {
    window.localStorage.setItem(key, String(value));
  }, [key, value]);
  return [value, setValue];
}

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: "" };
  }

  static getDerivedStateFromError(error) {
    return { error: error?.message || "页面渲染失败" };
  }

  render() {
    if (this.state.error) {
      return <main><div className="empty">页面渲染失败：{this.state.error}</div></main>;
    }
    return this.props.children;
  }
}

function KChart({ bars }) {
  const ref = React.useRef(null);
  useEffect(() => {
    if (!ref.current || !bars?.length) return;
    const chart = echarts.init(ref.current);
    const dates = bars.map((b) => b.trade_date);
    const candles = bars.map((b) => [b.open, b.close, b.low, b.high]);
    chart.setOption({
      animation: false,
      grid: [{ left: 44, right: 16, top: 16, height: 190, containLabel: true }, { left: 44, right: 16, top: 245, height: 58, containLabel: true }],
      xAxis: [{ type: "category", data: dates }, { type: "category", data: dates, gridIndex: 1 }],
      yAxis: [{ scale: true }, { gridIndex: 1 }],
      dataZoom: [{ type: "inside", xAxisIndex: [0, 1], start: 45, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true }],
      tooltip: { trigger: "axis" },
      series: [
        { type: "candlestick", data: candles, itemStyle: { color: "#d64545", color0: "#1d8b70", borderColor: "#d64545", borderColor0: "#1d8b70" } },
        { type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: bars.map((b) => b.volume), itemStyle: { color: "#7895b2" } },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [bars]);
  return <div className="chart-shell"><div className="chart" ref={ref} /></div>;
}

function CandidateTable({ candidates, onSelect, onSimulate, onWatch }) {
  if (!candidates?.length) {
    return <div className="empty">暂无候选。先点击“同步数据”，或等待盘后数据更新。</div>;
  }
  return (
    <div className="table">
      <div className="thead">
        <span>股票</span><span>评分</span><span>策略</span><span>观察价</span><span>止损</span><span>止盈/仓位</span>
      </div>
      {candidates.map((item) => (
        <div className="row" key={item.symbol} role="button" tabIndex={0} onClick={() => onSelect(item.symbol)} onKeyDown={(e) => { if (e.key === "Enter") onSelect(item.symbol); }}>
          <span><b>{item.name}</b><small>{item.symbol}</small></span>
          <span className="score">{item.score}</span>
          <span><b>{item.strategy_tags?.join("、") || "综合"}</b><small>{item.market_state || "neutral"}</small></span>
          <span>{item.entry_price}</span>
          <span className="risk">{item.stop_loss}</span>
          <span className="row-actions">
            <b>{item.take_profit_1} / {item.position_pct}%</b>
            <button type="button" title="模拟买入" onClick={(e) => { e.stopPropagation(); onSimulate(item); }}><WalletCards size={15} /></button>
            <button type="button" title="加入自选" onClick={(e) => { e.stopPropagation(); onWatch(item.symbol); }}><Star size={15} /></button>
          </span>
        </div>
      ))}
    </div>
  );
}

function DetailPanel({ symbol, onWatch }) {
  const detail = useAsync(() => (symbol ? api.stock(symbol) : Promise.resolve(null)), [symbol]);
  if (!symbol) return <section className="panel"><h2>股票详情</h2><div className="empty">选择一只候选股票查看 K 线和信号。</div></section>;
  if (detail.loading) return <section className="panel"><h2>股票详情</h2><div className="empty">加载中...</div></section>;
  if (detail.error) return <section className="panel"><h2>股票详情</h2><div className="empty">{detail.error}</div></section>;
  if (!detail.data) return <section className="panel"><h2>股票详情</h2><div className="empty">暂无详情数据。</div></section>;
  const signal = detail.data.latest_signal;
  return (
    <section className="panel">
      <div className="section-head">
        <h2>{detail.data.name} <small>{detail.data.symbol}</small></h2>
        <button className="icon-btn" onClick={() => onWatch(detail.data.symbol)} title="加入自选"><Star size={18} /></button>
      </div>
      <KChart bars={detail.data.bars} />
      {signal && (
        <div className="signal-box">
          <div><b>{signal.trade_date}</b> 信号评分 <b>{signal.score}</b></div>
          <div className="tags">{signal.strategy_tags?.map((r) => <span key={r}>{r}</span>)}<span>{signal.market_state}</span></div>
          <div className="tags">{signal.reasons.map((r) => <span key={r}>{r}</span>)}</div>
        </div>
      )}
    </section>
  );
}

function StockDetailPage({ symbol, onBack, onWatch }) {
  return (
    <section>
      <button className="ghost" onClick={onBack}>返回列表</button>
      <DetailPanel symbol={symbol} onWatch={onWatch} />
    </section>
  );
}

function CollapsibleSection({ title, meta, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="fold-section">
      <button className="fold-head" onClick={() => setOpen((v) => !v)}>
        <span><b>{title}</b>{meta && <small>{meta}</small>}</span>
        <span>{open ? "收起" : "展开"}</span>
      </button>
      {open && <div className="fold-body">{children}</div>}
    </section>
  );
}

function BacktestPanel() {
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ start_date: "2024-01-01", end_date: today, initial_cash: 100000, max_positions: 5, hold_days: 5, fee_rate: 0.0003 });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const run = async () => {
    setLoading(true);
    setError("");
    try {
      setResult(await api.backtest(form));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };
  return (
    <section>
      <div className="section-head"><h2>历史验证</h2><BarChart3 size={20} /></div>
      <div className="empty compact">用历史行情动态生成历史信号，再模拟策略过去会怎么交易。主要看胜率、回撤和收益，不代表未来一定赚钱。</div>
      <div className="form-grid">
        {["start_date", "end_date", "initial_cash", "max_positions", "hold_days"].map((key) => (
          <label key={key}>{key}<input value={form[key]} onChange={(e) => setForm({ ...form, [key]: ["initial_cash", "max_positions", "hold_days"].includes(key) ? Number(e.target.value) : e.target.value })} /></label>
        ))}
      </div>
      <button className="primary" onClick={run} disabled={loading}>{loading ? "回测中..." : "运行回测"}</button>
      {error && <div className="empty compact">{error}</div>}
      {result && (
        <>
          <div className="metrics">
            <div><b>{result.summary.total_return_pct}%</b><span>总收益</span></div>
            <div><b>{result.summary.win_rate_pct}%</b><span>胜率</span></div>
            <div><b>{result.summary.max_drawdown_pct}%</b><span>最大回撤</span></div>
            <div><b>{result.summary.trade_count}</b><span>交易数</span></div>
          </div>
          {result.trades.length === 0 && <div className="empty compact">当前区间没有生成交易。可以先同步更多股票，或放宽策略阈值后再验证。</div>}
          <div className="trade-list">{result.trades.slice(0, 12).map((t, i) => <div key={i}>{t.symbol} {t.entry_date} {"->"} {t.exit_date} <b>{t.return_pct}%</b> {t.reason} {(t.strategy_tags || []).join("、")} {t.market_state}</div>)}</div>
        </>
      )}
    </section>
  );
}

function SimulatorPanel({ seed, candidates = [], onSelectStock }) {
  const state = useAsync(api.simulationState, []);
  const [sim, setSim] = useState(null);
  const [autoLog, setAutoLog] = useState("");
  const [saving, setSaving] = useState(false);
  const [selectedPositionId, setSelectedPositionId] = useState("");
  const [form, setForm] = useState({ symbol: "", name: "", buyPrice: "", quantity: "100", currentPrice: "" });

  useEffect(() => {
    if (state.data) setSim(state.data);
  }, [state.data]);

  useEffect(() => {
    if (seed) {
      setForm({ symbol: seed.symbol, name: seed.name, buyPrice: String(seed.entry_price || ""), quantity: "100", currentPrice: String(seed.entry_price || "") });
    }
  }, [seed]);

  const positions = sim?.positions || [];
  const trades = sim?.trades || [];
  const rules = sim?.rules || { minScore: 75, maxPositions: 5, maxSinglePct: 10, maxDailyTrades: 5 };
  const automation = sim?.automation || { enabled: true, run_time: "15:30", sync_before_run: true, last_run_date: "", last_status: "", last_message: "", last_check_at: "" };
  const cash = Number(sim?.cash || 0);
  const openPositions = positions.filter((p) => p.status !== "closed");
  useEffect(() => {
    if (positions.length && (!selectedPositionId || !positions.some((p) => p.id === selectedPositionId))) {
      setSelectedPositionId(positions[0].id);
    }
  }, [positions, selectedPositionId]);
  const selectedPosition = positions.find((p) => p.id === selectedPositionId) || positions[0] || null;
  const selectedTrades = selectedPosition ? trades.filter((t) => t.symbol === selectedPosition.symbol) : [];
  const selectedSignal = selectedPosition ? candidates.find((c) => c.symbol === selectedPosition.symbol) : null;
  const totals = positions.reduce((acc, p) => {
    const cost = Number(p.buyPrice) * Number(p.quantity);
    const market = Number(p.currentPrice || p.buyPrice) * Number(p.quantity);
    const realized = p.status === "closed" && p.sellPrice ? (Number(p.sellPrice) - Number(p.buyPrice)) * Number(p.quantity) : 0;
    if (p.status !== "closed") {
      acc.cost += cost;
      acc.market += market;
      acc.floatPnl += market - cost;
    }
    acc.realized += realized;
    return acc;
  }, { cost: 0, market: 0, floatPnl: 0, realized: 0 });
  const totalAssets = cash + totals.market;
  const calcPosition = (p) => {
    const buy = Number(p.buyPrice || 0);
    const current = Number(p.currentPrice || p.buyPrice || 0);
    const qty = Number(p.quantity || 0);
    const market = current * qty;
    const cost = buy * qty;
    const pnl = p.status === "closed" && p.sellPrice ? (Number(p.sellPrice) - buy) * qty : market - cost;
    const pnlPct = buy ? ((p.status === "closed" && p.sellPrice ? Number(p.sellPrice) : current) / buy - 1) * 100 : 0;
    return { buy, current, qty, market, cost, pnl, pnlPct };
  };

  const updateRules = async (patch) => {
    const nextRules = { ...rules, ...patch };
    setSim({ ...sim, rules: nextRules });
    setSaving(true);
    try {
      const next = await api.updateSimulationState({ cash, rules: nextRules });
      setSim(next);
    } finally {
      setSaving(false);
    }
  };

  const updateCash = async (value) => {
    const nextCash = Number(value);
    setSim({ ...sim, cash: nextCash });
    setSaving(true);
    try {
      const next = await api.updateSimulationState({ cash: nextCash, rules });
      setSim(next);
    } finally {
      setSaving(false);
    }
  };

  const autoRun = async (force = false) => {
    setSaving(true);
    try {
      const result = await api.autoRunSimulation(force);
      setSim(result);
      setAutoLog(result.logs?.length ? result.logs.join("；") : result.message);
    } finally {
      setSaving(false);
    }
  };

  const updateAutomation = async (patch) => {
    const nextAutomation = { ...automation, ...patch };
    setSim({ ...sim, automation: nextAutomation });
    setSaving(true);
    try {
      const next = await api.updateAutomationState({
        enabled: nextAutomation.enabled,
        run_time: nextAutomation.run_time,
        sync_before_run: nextAutomation.sync_before_run,
      });
      setSim({ ...sim, automation: next });
    } finally {
      setSaving(false);
    }
  };

  const addManual = () => {
    setAutoLog("手动补录已保留为后续功能；当前建议用自动模拟执行，避免绕过交易限制和日报统计。");
  };

  if (state.loading || !sim) return <section className="panel"><h2>模拟账户</h2><div className="empty">加载中...</div></section>;
  if (state.error) return <section className="panel"><h2>模拟账户</h2><div className="empty">{state.error}</div></section>;

  return (
    <section className="panel wide-panel">
      <div className="section-head"><h2>模拟账户</h2><WalletCards size={20} /></div>
      <div className="empty compact">系统按日线策略执行，默认每个交易日最多自动执行一次；不会盘中频繁调仓。需要重跑当天策略时可用“强制重跑”。</div>
      <div className="metrics account-metrics">
        <div><b>{totalAssets.toFixed(2)}</b><span>总资产</span></div>
        <div><b>{cash.toFixed(2)}</b><span>可用现金</span></div>
        <div><b>{openPositions.length}</b><span>持仓股票</span></div>
        <div><b className={totals.floatPnl >= 0 ? "profit" : "loss"}>{totals.floatPnl.toFixed(2)}</b><span>浮动盈亏</span></div>
        <div><b className={totals.realized >= 0 ? "profit" : "loss"}>{totals.realized.toFixed(2)}</b><span>已实现盈亏</span></div>
      </div>
      <CollapsibleSection title="自动执行与日报" meta={automation.enabled ? `已启用 · ${automation.run_time}` : "已暂停"} defaultOpen>
        {sim.latest_report && <div className="auto-log">日报提醒：{sim.latest_report.summary}</div>}
        <div className="switch-grid">
          <label className="check-line"><input type="checkbox" checked={automation.enabled} onChange={(e) => updateAutomation({ enabled: e.target.checked })} />启用每日自动执行</label>
          <label className="check-line"><input type="checkbox" checked={automation.sync_before_run} onChange={(e) => updateAutomation({ sync_before_run: e.target.checked })} />执行前自动同步数据</label>
          <label>执行时间<input type="time" value={automation.run_time} onChange={(e) => updateAutomation({ run_time: e.target.value })} /></label>
        </div>
        <div className="auto-log">
          状态：{automation.enabled ? "已启用" : "已暂停"}；每日 {automation.run_time} 后检查一次。
          上次检查：{automation.last_check_at || "暂无"}；上次结果：{automation.last_message || "暂无"}。
        </div>
      </CollapsibleSection>
      <CollapsibleSection title="自动交易规则" meta={`评分 ${rules.minScore} · 持仓 ${rules.maxPositions} · 每日 ${rules.maxDailyTrades} 笔`} defaultOpen>
        <div className="form-grid simulator-form">
          <label>可用现金<input type="number" value={cash} onChange={(e) => updateCash(e.target.value)} /></label>
          <label>最低评分<input type="number" value={rules.minScore} onChange={(e) => updateRules({ minScore: Number(e.target.value) })} /></label>
          <label>最大持仓<input type="number" value={rules.maxPositions} onChange={(e) => updateRules({ maxPositions: Number(e.target.value) })} /></label>
          <label>单票上限%<input type="number" value={rules.maxSinglePct} onChange={(e) => updateRules({ maxSinglePct: Number(e.target.value) })} /></label>
          <label>每日交易上限<input type="number" value={rules.maxDailyTrades} onChange={(e) => updateRules({ maxDailyTrades: Number(e.target.value) })} /></label>
        </div>
        <div className="button-row">
          <button className="primary" onClick={() => autoRun(false)} disabled={saving}>{saving ? "执行中..." : "执行今日自动模拟"}</button>
          <button className="ghost inline" onClick={() => autoRun(true)} disabled={saving}>强制重跑</button>
        </div>
        <div className="auto-log">上次执行交易日：{sim.last_run_trade_date || "未执行"}。调仓频次：每日最多一次，买入/卖出合计不超过每日交易上限。</div>
        {autoLog && <div className="auto-log">{autoLog}</div>}
      </CollapsibleSection>
      <CollapsibleSection title="手动补录" meta="当前仅说明，后续接入补录保存">
        <div className="form-grid simulator-form">
          <label>代码<input value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} /></label>
          <label>名称<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>买入价<input type="number" value={form.buyPrice} onChange={(e) => setForm({ ...form, buyPrice: e.target.value })} /></label>
          <label>数量<input type="number" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></label>
          <label>当前价<input type="number" value={form.currentPrice} onChange={(e) => setForm({ ...form, currentPrice: e.target.value })} /></label>
        </div>
        <button className="primary" onClick={addManual}>手动补录说明</button>
      </CollapsibleSection>
      <CollapsibleSection title="持仓股票" meta={`${positions.length} 只记录`} defaultOpen>
      {positions.length === 0 ? <div className="empty compact">暂无持仓。点“执行今日自动模拟”，系统会从今日候选里自动买入。</div> : (
        <div className="position-workbench">
          <div className="position-table">
            <div className="position-thead">
              <span>股票</span><span>状态</span><span>数量</span><span>成本/现价</span><span>市值</span><span>盈亏</span><span>风控</span>
            </div>
            {positions.map((p) => {
              const stat = calcPosition(p);
              return (
                <button className={`position-row ${p.id === selectedPosition?.id ? "active" : ""} ${p.status === "closed" ? "closed" : ""}`} key={p.id} onClick={() => setSelectedPositionId(p.id)}>
                  <span><b>{p.name}</b><small>{p.symbol} · {p.createdAt}</small></span>
                  <span>{p.status === "closed" ? `已平仓${p.exitReason ? `/${p.exitReason}` : ""}` : "持仓中"}</span>
                  <span>{p.quantity}股</span>
                  <span>{Number(p.buyPrice).toFixed(2)} / {Number(p.currentPrice || p.buyPrice).toFixed(2)}</span>
                  <span>{stat.market.toFixed(2)}</span>
                  <span className={stat.pnl >= 0 ? "profit" : "loss"}>{stat.pnl.toFixed(2)}<small>{stat.pnlPct.toFixed(2)}%</small></span>
                  <span>损 {p.stopLoss || "-"} / 盈 {p.takeProfit || "-"}</span>
                </button>
              );
            })}
          </div>
          {selectedPosition && (() => {
            const stat = calcPosition(selectedPosition);
            const buyTrade = selectedTrades.find((t) => t.action.includes("买入"));
            const sellTrade = selectedTrades.find((t) => t.action.includes("卖出"));
            return (
              <div className="position-detail">
                <div className="section-head">
                  <h3>{selectedPosition.name} <small>{selectedPosition.symbol}</small></h3>
                  <button className="ghost inline" onClick={() => onSelectStock?.(selectedPosition.symbol)}>查看K线</button>
                </div>
                <div className="detail-grid">
                  <div><span>买入点</span><b>{Number(selectedPosition.buyPrice).toFixed(2)}</b><small>{buyTrade?.reason || "自动策略买入"}</small></div>
                  <div><span>当前/卖出点</span><b>{selectedPosition.status === "closed" && selectedPosition.sellPrice ? Number(selectedPosition.sellPrice).toFixed(2) : Number(selectedPosition.currentPrice || selectedPosition.buyPrice).toFixed(2)}</b><small>{sellTrade?.reason || "未触发卖出"}</small></div>
                  <div><span>止损/止盈</span><b>{selectedPosition.stopLoss} / {selectedPosition.takeProfit}</b><small>触发后自动模拟卖出</small></div>
                  <div><span>盈亏</span><b className={stat.pnl >= 0 ? "profit" : "loss"}>{stat.pnl.toFixed(2)}</b><small>{stat.pnlPct.toFixed(2)}%</small></div>
                </div>
                <div className="strategy-match">
                  <b>匹配交易策略</b>
                  <div className="tags">
                    <span>最低评分 {rules.minScore}</span>
                    <span>最大持仓 {rules.maxPositions}</span>
                    <span>单票上限 {rules.maxSinglePct}%</span>
                    <span>每日上限 {rules.maxDailyTrades} 笔</span>
                    {selectedSignal ? <span>候选评分 {selectedSignal.score}</span> : <span>历史持仓，当前候选未命中</span>}
                    {selectedSignal?.strategy_tags?.map((tag) => <span key={tag}>{tag}</span>)}
                    {selectedSignal?.market_state && <span>{selectedSignal.market_state}</span>}
                  </div>
                  {selectedSignal?.reasons?.length ? <p>{selectedSignal.reasons.join("；")}</p> : <p>该持仓来自历史自动模拟交易，当前最新候选列表中没有对应信号。</p>}
                </div>
                <h3>本股交易历史</h3>
                <div className="trade-list compact-list">
                  {selectedTrades.length === 0 && <div className="empty compact">暂无本股成交记录。</div>}
                  {selectedTrades.map((t) => (
                    <div key={t.id}>{t.trade_date} {t.action} {t.quantity}股 @ {t.price}，金额 {Number(t.amount).toFixed(2)}，{t.reason}</div>
                  ))}
                </div>
              </div>
            );
          })()}
        </div>
      )}
      </CollapsibleSection>
      <CollapsibleSection title="全部成交记录" meta={`${trades.length} 笔`}>
      <div className="trade-list no-top">
        {trades.length === 0 && <div className="empty compact">暂无成交记录。</div>}
        {trades.slice(0, 30).map((t) => (
          <div key={t.id}>{t.trade_date} {t.action} {t.name} {t.symbol} {t.quantity}股 @ {t.price}，金额 {Number(t.amount).toFixed(2)}，{t.reason}</div>
        ))}
      </div>
      </CollapsibleSection>
    </section>
  );
}

function CalculatorPanel() {
  const [calc, setCalc] = useState({ cash: 100000, price: 20, stop: 19, riskPct: 1, feeRate: 0.0003 });
  const riskAmount = Number(calc.cash) * Number(calc.riskPct) / 100;
  const perShareRisk = Math.max(0, Number(calc.price) - Number(calc.stop));
  const riskShares = perShareRisk ? Math.floor(riskAmount / perShareRisk / 100) * 100 : 0;
  const maxShares = Math.floor((Number(calc.cash) * 0.1) / Number(calc.price) / 100) * 100;
  const shares = Math.max(0, Math.min(riskShares, maxShares));
  const cost = shares * Number(calc.price);
  const fee = cost * Number(calc.feeRate);
  return (
    <section className="panel wide-panel">
      <div className="section-head"><h2>仓位计算</h2><Calculator size={20} /></div>
      <div className="empty compact">输入资金、买入价和止损价，系统按单笔风险帮你算可以买多少股。</div>
      <div className="form-grid">
        {[
          ["cash", "账户资金"],
          ["price", "计划买入价"],
          ["stop", "止损价"],
          ["riskPct", "单笔风险%"],
          ["feeRate", "费率"],
        ].map(([key, label]) => (
          <label key={key}>{label}<input type="number" value={calc[key]} onChange={(e) => setCalc({ ...calc, [key]: Number(e.target.value) })} /></label>
        ))}
      </div>
      <div className="metrics">
        <div><b>{shares}</b><span>建议股数</span></div>
        <div><b>{cost.toFixed(2)}</b><span>占用资金</span></div>
        <div><b>{riskAmount.toFixed(2)}</b><span>允许亏损</span></div>
        <div><b>{fee.toFixed(2)}</b><span>预估单边费用</span></div>
      </div>
    </section>
  );
}

function WatchlistPanel({ refreshKey, onSelect }) {
  const list = useAsync(api.watchlist, [refreshKey]);
  const [symbol, setSymbol] = useState("");
  const add = async () => {
    if (!symbol.trim()) return;
    await api.addWatch({ symbol, note: "" });
    setSymbol("");
    window.dispatchEvent(new Event("watch-refresh"));
  };
  return (
    <section className="panel">
      <div className="section-head"><h2>自选观察</h2><Bell size={20} /></div>
      <div className="inline-form"><input placeholder="输入代码，如 600519" value={symbol} onChange={(e) => setSymbol(e.target.value)} /><button onClick={add}>加入</button></div>
      {list.loading ? <div className="empty">加载中...</div> : list.data?.map((item) => (
        <div className="watch clickable" key={item.symbol} onClick={() => onSelect(item.symbol)}>
          <span><b>{item.name}</b><small>{item.symbol}</small></span>
          <span>{item.latest_signal ? `评分 ${item.latest_signal.score}` : "暂无信号"}</span>
          <button onClick={(e) => { e.stopPropagation(); api.deleteWatch(item.symbol).then(() => window.dispatchEvent(new Event("watch-refresh"))); }}>移除</button>
        </div>
      ))}
    </section>
  );
}

function StrategyPanel() {
  const versions = useAsync(api.strategyVersions, []);
  return (
    <section>
      <div className="section-head"><h2>策略版本</h2><GitBranch size={20} /></div>
      <div className="empty">当前为日线多策略调度：趋势突破、强势回调、放量启动只负责提案；市场环境负责降权或禁止新增买入；模拟执行层统一买卖，避免策略互相重复下单。</div>
      {versions.loading ? <div className="empty">加载中...</div> : versions.data?.map((item) => (
        <div className="version-card" key={item.id}>
          <div className="version-head"><b>{item.version}</b><span>{item.status}</span><span>{item.timeframe}</span><small>{item.created_at}</small></div>
          <p>{item.change_note}</p>
          <div className="param-grid">
            {Object.entries(item.params).map(([key, value]) => <span key={key}>{key}: <b>{String(value)}</b></span>)}
          </div>
        </div>
      ))}
    </section>
  );
}

function DisplaySettings({ fontSize, onFontSizeChange }) {
  return (
    <section>
      <div className="section-head"><h2>显示设置</h2><Settings size={20} /></div>
      <div className="display-settings">
        <label>全局字体大小
          <input type="range" min="12" max="16" step="1" value={fontSize} onChange={(e) => onFontSizeChange(Number(e.target.value))} />
        </label>
        <label>字号
          <input type="number" min="12" max="16" value={fontSize} onChange={(e) => onFontSizeChange(Number(e.target.value))} />
        </label>
        <button className="ghost inline" onClick={() => onFontSizeChange(14)}>恢复默认</button>
      </div>
      <div className="empty compact">字号越小，同一屏显示的表格和持仓内容越多。当前：{fontSize}px。</div>
    </section>
  );
}

function ConfigPanel({ fontSize, onFontSizeChange }) {
  return (
    <section className="panel wide-panel">
      <div className="section-head"><h2>配置</h2><Settings size={20} /></div>
      <CollapsibleSection title="显示设置" meta={`全局 ${fontSize}px`} defaultOpen>
        <DisplaySettings fontSize={fontSize} onFontSizeChange={onFontSizeChange} />
      </CollapsibleSection>
      <CollapsibleSection title="历史回测" meta="验证策略过去表现" defaultOpen>
        <BacktestPanel />
      </CollapsibleSection>
      <CollapsibleSection title="策略版本" meta="记录参数和规则调整" defaultOpen>
        <StrategyPanel />
      </CollapsibleSection>
    </section>
  );
}

function App() {
  const tabs = [
    ["dashboard", "量化分析", LayoutDashboard],
    ["watch", "自选股", Star],
    ["sim", "模拟交易", WalletCards],
    ["calc", "仓位", Calculator],
    ["config", "配置", Settings],
  ];
  const [activeTab, setActiveTab] = useState("dashboard");
  const [detailSymbol, setDetailSymbol] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [simSeed, setSimSeed] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState("");
  const [fontSize, setFontSize] = useStoredNumber("astockpilot-font-size", 14);
  useEffect(() => {
    const next = Math.min(16, Math.max(12, Number(fontSize) || 14));
    document.documentElement.style.setProperty("--app-font-size", `${next}px`);
  }, [fontSize]);
  const candidates = useAsync(api.candidates, [refresh]);
  const [watchRefresh, setWatchRefresh] = useState(0);
  useEffect(() => {
    const fn = () => setWatchRefresh((v) => v + 1);
    window.addEventListener("watch-refresh", fn);
    return () => window.removeEventListener("watch-refresh", fn);
  }, []);
  const topDate = useMemo(() => candidates.data?.[0]?.trade_date || "暂无数据", [candidates.data]);
  const sync = async () => {
    setSyncing(true);
    setSyncMsg("");
    try {
      const res = await api.sync({ days: 730, symbol_limit: 80 });
      setSyncMsg(`同步 ${res.synced_symbols} 只，${res.bars} 条日线，${res.signals} 个信号`);
      setRefresh((v) => v + 1);
    } catch (err) {
      setSyncMsg(err.message);
    } finally {
      setSyncing(false);
    }
  };
  const addWatch = async (symbol) => {
    await api.addWatch({ symbol, note: "" });
    setWatchRefresh((v) => v + 1);
  };
  const simulateCandidate = (item) => {
    setSimSeed(item);
    setActiveTab("sim");
  };
  const openDetail = (symbol) => {
    setDetailSymbol(symbol);
    setActiveTab("detail");
  };
  return (
    <main>
      {activeTab === "dashboard" && (
        <>
          <header>
            <div>
              <h1>量化分析</h1>
              <p>A股短线候选跟踪，按策略信号、风控和模拟结果做决策。</p>
            </div>
            <button className="primary" onClick={sync} disabled={syncing}><Database size={18} />{syncing ? "同步中..." : "同步数据"}</button>
          </header>
          <div className="status">
            <span><Activity size={16} /> 数据日期 {topDate}</span>
            <span><ShieldAlert size={16} /> 不构成投资建议</span>
            {syncMsg && <span><RefreshCw size={16} /> {syncMsg}</span>}
          </div>
        </>
      )}
      <nav className="tabs bottom-tabs">
        {tabs.map(([key, label, Icon]) => (
          <button key={key} className={activeTab === key ? "active" : ""} onClick={() => setActiveTab(key)}><Icon size={16} />{label}</button>
        ))}
      </nav>
      {activeTab === "dashboard" && (
          <section className="panel main-panel">
            <div className="section-head"><h2>今日候选</h2><TrendingUp size={20} /></div>
            {candidates.loading ? <div className="empty">加载中...</div> : candidates.error ? <div className="empty">{candidates.error}</div> : <CandidateTable candidates={candidates.data} onSelect={openDetail} onSimulate={simulateCandidate} onWatch={addWatch} />}
          </section>
      )}
      {activeTab === "detail" && <StockDetailPage symbol={detailSymbol} onBack={() => setActiveTab("dashboard")} onWatch={addWatch} />}
      {activeTab === "watch" && <WatchlistPanel refreshKey={watchRefresh} onSelect={openDetail} />}
      {activeTab === "sim" && <SimulatorPanel seed={simSeed} candidates={candidates.data || []} onSelectStock={openDetail} />}
      {activeTab === "calc" && <CalculatorPanel />}
      {activeTab === "config" && <ConfigPanel fontSize={fontSize} onFontSizeChange={setFontSize} />}
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>
);
