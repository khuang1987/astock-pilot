import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import * as echarts from "echarts";
import { Activity, BarChart3, Bell, Bot, Calculator, Database, GitBranch, Globe2, HelpCircle, Info, KeyRound, LayoutDashboard, LockKeyhole, Mail, Moon, RefreshCw, Settings, ShieldCheck, SlidersHorizontal, Sparkles, Star, Sun, TrendingUp, UserRound, WalletCards, X } from "lucide-react";
import { api } from "./api";
import "./styles.css";

const DEFAULT_SIM_RULES = {
  minScore: 82,
  maxPositions: 4,
  maxSinglePct: 8,
  maxDailyTrades: 5,
  maxDailyBuys: 1,
  maxDailySells: 4,
  sellPriority: true,
  dynamicBuyEnabled: true,
  weakMaxDailyBuys: 1,
  weakMaxDailyBuyPct: 5,
  rangeMaxDailyBuys: 1,
  rangeMaxDailyBuyPct: 8,
  strongMaxDailyBuys: 2,
  strongMaxDailyBuyPct: 15,
  buyPriceTolerancePct: 0.8,
  commissionRate: 0.0003,
  minCommission: 5,
  stampTaxRate: 0.0005,
  transferFeeRate: 0.00001,
  slippagePct: 0.15,
  rebalanceEnabled: true,
  rebalanceMinNewScore: 88,
  rebalanceMinScoreGap: 8,
  maxDailyRebalances: 1,
  weakHoldDays: 5,
  weakReturnPct: -2.5,
  weakScoreExit: 78,
  addPositionEnabled: false,
  addMinProfitPct: 4,
  addMinScore: 88,
  maxAddsPerSymbol: 1,
  addPositionPct: 3,
  takeProfitSellPct: 50,
  trailingStopPct: 4,
  minRemainLot: 100,
};

function formatTime(value) {
  if (!value) return "暂无";
  const date = new Date(String(value).replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function planLabel(type) {
  const labels = {
    next_buy: "普通买入",
    sell_stop: "止损卖出",
    sell_weak: "弱势卖出",
    sell_take_profit: "止盈卖出",
    rebalance_sell: "调仓卖出",
    rebalance_buy: "调仓买入",
    add_buy: "盈利追加",
    hold: "继续持有",
  };
  return labels[type] || type || "计划";
}

function verdictLabel(value) {
  const labels = { approve: "通过", caution: "谨慎", reject: "不建议" };
  return labels[value] || value || "未审核";
}

function finalDecisionClass(decision) {
  const status = decision?.status || "unknown";
  if (["approved", "approved_caution", "risk_exit", "observe", "rule_only"].includes(status)) return "allow";
  if (["missing_ai", "stale_ai"].includes(status)) return "pending";
  return "block";
}

function shouldShowAuditNote(plan) {
  const auditStatus = plan?.latest_decision_audit?.decision_status;
  if (!auditStatus) return false;
  if (auditStatus === "data_guard") return true;
  return auditStatus === plan?.final_decision?.status;
}

function planStatusLabel(status) {
  const labels = {
    pending: "待执行",
    executed: "已成交",
    observed: "已观察",
    skipped: "已跳过",
    expired: "已过期",
  };
  return labels[status] || status || "未知";
}

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

function useStoredString(key, initialValue) {
  const [value, setValue] = useState(() => window.localStorage.getItem(key) || initialValue);
  useEffect(() => {
    window.localStorage.setItem(key, value);
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

function EvaluationTrend({ evaluations }) {
  const ref = React.useRef(null);
  useEffect(() => {
    if (!ref.current || !evaluations?.length) return;
    const items = [...evaluations].reverse();
    const chart = echarts.init(ref.current);
    chart.setOption({
      animation: false,
      grid: { left: 42, right: 16, top: 18, bottom: 30, containLabel: true },
      tooltip: { trigger: "axis" },
      legend: { top: 0, right: 8, textStyle: { color: "#60707d" } },
      xAxis: { type: "category", data: items.map((item) => item.trade_date) },
      yAxis: [
        { type: "value", name: "评分", min: 0, max: 100 },
        { type: "value", name: "收益%", scale: true },
      ],
      series: [
        {
          name: "评分",
          type: "line",
          smooth: true,
          symbolSize: 6,
          data: items.map((item) => item.score ?? null),
          color: "#1f6f8b",
        },
        {
          name: "持仓收益%",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbolSize: 6,
          data: items.map((item) => item.metrics?.pnl_pct ?? null),
          color: "#b15f2a",
        },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [evaluations]);
  if (!evaluations?.length) return <div className="empty compact">暂无评估趋势。生成每日分析后会记录评分和持仓收益变化。</div>;
  return <div className="chart-shell"><div className="mini-chart" ref={ref} /></div>;
}

function MiniSparkline({ points = [] }) {
  const values = (points || []).map((item) => Number(item.close || 0)).filter((value) => value > 0);
  if (values.length < 2) return <div className="watch-spark empty-spark" aria-hidden="true"></div>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const path = values.map((value, index) => {
    const x = (index / (values.length - 1)) * 100;
    const y = 34 - ((value - min) / span) * 28 - 3;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg className={`watch-spark-svg ${up ? "up" : "down"}`} viewBox="0 0 100 36" preserveAspectRatio="none" aria-hidden="true">
      <polyline points={path} fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function AssetCurve({ points = [], loading = false, compact = false }) {
  const values = points.map((item) => Number(item.value || 0)).filter((value) => value > 0);
  if (values.length < 2) return <div className={`asset-curve ${compact ? "compact" : ""} empty`} aria-hidden="true"><span>{loading ? "读取资产曲线" : "资产曲线"}</span></div>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = compact ? 128 : 240;
  const height = compact ? 52 : 74;
  const coords = values.map((value, index) => {
    const x = values.length === 1 ? 0 : (index / (values.length - 1)) * width;
    const y = height - 8 - ((value - min) / span) * (height - 18);
    return [x, y];
  });
  const line = coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `0,${height} ${line} ${width},${height}`;
  const up = values[values.length - 1] >= values[0];
  const change = values[values.length - 1] - values[0];
  const changePct = values[0] ? (change / values[0]) * 100 : 0;
  return (
    <div className={`asset-curve ${compact ? "compact" : ""} ${up ? "up" : "down"}`}>
      {!compact && (
        <div className="asset-curve-head">
          <span>近2周资产曲线</span>
          <b>{change >= 0 ? "+" : ""}{change.toFixed(2)} / {changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%</b>
        </div>
      )}
      <svg className="asset-curve-svg" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
        {!compact && <polygon points={area} />}
        <polyline points={line} fill="none" stroke="currentColor" strokeWidth={compact ? "2.3" : "3"} strokeLinecap="round" strokeLinejoin="round" />
        {coords.map(([x, y], index) => <circle key={`${x}-${y}-${index}`} cx={x} cy={y} r={compact ? "2.4" : "3.8"} />)}
      </svg>
      {compact && <span className={up ? "profit" : "loss"}>{change >= 0 ? "+" : ""}{changePct.toFixed(2)}%</span>}
    </div>
  );
}

function clampScore(value) {
  const next = Number(value);
  if (!Number.isFinite(next)) return 0;
  return Math.max(0, Math.min(100, next));
}

function FactorRadar({ items = [] }) {
  const center = 100;
  const radius = 68;
  const axes = items.length ? items.slice(0, 5) : [
    { label: "趋势", value: 0 },
    { label: "资金", value: 0 },
    { label: "情绪", value: 0 },
    { label: "流动性", value: 0 },
    { label: "风控", value: 0 },
  ];
  const angleFor = (index) => -90 + (360 / axes.length) * index;
  const point = (score, index, scale = 1) => {
    const angle = angleFor(index) * Math.PI / 180;
    const distance = radius * scale * (clampScore(score) / 100);
    return [center + Math.cos(angle) * distance, center + Math.sin(angle) * distance];
  };
  const ringPoint = (index, scale = 1) => {
    const angle = angleFor(index) * Math.PI / 180;
    return [center + Math.cos(angle) * radius * scale, center + Math.sin(angle) * radius * scale];
  };
  const polygon = (values) => values.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const valuePoints = axes.map((item, index) => point(item.value, index));
  const labelPoints = axes.map((item, index) => {
    const [x, y] = ringPoint(index, 1.18);
    return { ...item, x, y };
  });
  return (
    <div className="factor-radar">
      <svg viewBox="0 0 200 200" role="img" aria-label="五维评分雷达图">
        {[0.33, 0.66, 1].map((scale) => (
          <polygon key={scale} className="radar-ring" points={polygon(axes.map((_, index) => ringPoint(index, scale)))} />
        ))}
        {axes.map((_, index) => {
          const [x, y] = ringPoint(index, 1);
          return <line key={index} className="radar-axis" x1={center} y1={center} x2={x} y2={y} />;
        })}
        <polygon className="radar-area" points={polygon(valuePoints)} />
        <polyline className="radar-line" points={`${polygon(valuePoints)} ${valuePoints[0]?.[0]?.toFixed(1)},${valuePoints[0]?.[1]?.toFixed(1)}`} />
        {valuePoints.map(([x, y], index) => <circle key={index} className="radar-dot" cx={x} cy={y} r="3.6" />)}
        {labelPoints.map((item) => (
          <text className="radar-label-point" x={item.x} y={item.y} textAnchor="middle" dominantBaseline="middle" key={item.label}>
            <tspan className="radar-label-name" x={item.x} dy="-0.35em">{item.label}</tspan>
            <tspan className="radar-label-score" x={item.x} dy="1.25em">{Math.round(clampScore(item.value))}</tspan>
          </text>
        ))}
      </svg>
    </div>
  );
}

function reportAssetValue(report) {
  return Number(
    report?.metrics?.total_assets ??
    report?.snapshot?.metrics?.total_assets ??
    report?.snapshot?.account_summary?.total_assets ??
    0
  );
}

function advisorActionFrom(review, plan, candidate) {
  if (plan?.action) return plan.action;
  const verdict = String(review?.verdict || "").toLowerCase();
  if (verdict === "approve") return "按计划执行";
  if (verdict === "reject") return "暂停执行";
  if (verdict === "caution") return "谨慎观察";
  if (candidate?.decision === "buy") return "买入观察";
  return "等待审核";
}

function advisorClass(review, plan) {
  const verdict = String(review?.verdict || "").toLowerCase();
  const risk = String(review?.risk_level || "").toLowerCase();
  const type = String(plan?.plan_type || "").toLowerCase();
  if (verdict === "reject" || risk === "high" || type.includes("sell")) return "sell";
  if (verdict === "caution" || risk === "medium") return "caution";
  if (verdict === "approve") return "buy";
  return "hold";
}

function advisorConfidence(review, candidate) {
  const risk = String(review?.risk_level || "").toLowerCase();
  const verdict = String(review?.verdict || "").toLowerCase();
  if (verdict === "approve" && risk === "low") return 88;
  if (verdict === "reject" || risk === "high") return 32;
  if (verdict === "caution" || risk === "medium") return 62;
  return Math.round(Number(candidate?.score || 0));
}

function AccountAssetPanel({ totalAssets, cash, marketValue, floatPnl, realizedPnl, positionPct, openCount, maxPositions, updatedAt, reports = [] }) {
  const assetPoints = (reports || [])
    .filter((item) => reportAssetValue(item) > 0)
    .slice(0, 14)
    .reverse()
    .map((item) => ({ date: item.trade_date || item.report_date, value: reportAssetValue(item) }));
  const curve = assetPoints.length >= 2 ? assetPoints : [
    { date: "初始", value: Math.max(1, Number(totalAssets || 0) - Number(floatPnl || 0)) },
    { date: "当前", value: Number(totalAssets || 0) },
  ];
  return (
    <div className="account-asset-panel">
      <div className="account-asset-hero">
        <div className="hero-top">
          <span>模拟资产</span>
          <small>{formatTime(updatedAt)}</small>
        </div>
        <span>总资产</span>
        <b>{Number(totalAssets || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</b>
        <small>浮动盈亏 <em className={Number(floatPnl || 0) >= 0 ? "profit" : "loss"}>{Number(floatPnl || 0).toFixed(2)}</em> · 仓位 {Number(positionPct || 0).toFixed(1)}%</small>
        <AssetCurve points={curve} />
      </div>
      <div className="account-asset-grid">
        <div><span>可用现金</span><b>{Number(cash || 0).toFixed(2)}</b></div>
        <div><span>持仓市值</span><b>{Number(marketValue || 0).toFixed(2)}</b></div>
        <div><span>已实现</span><b className={Number(realizedPnl || 0) >= 0 ? "profit" : "loss"}>{Number(realizedPnl || 0).toFixed(2)}</b></div>
        <div><span>持仓</span><b>{openCount}/{maxPositions}</b></div>
      </div>
    </div>
  );
}

function PositionSummaryText({ totalAssets, cash, marketValue, floatPnl, realizedPnl, positionPct, openCount, maxPositions, updatedAt }) {
  return (
    <div className="position-summary-text">
      <div className="position-summary-head">
        <b>仓位持仓</b>
        <span>估值口径：当前持仓价 · {formatTime(updatedAt)}</span>
      </div>
      <div className="position-summary-body">
        <div className="position-summary-main">
          <span>总资产</span>
          <b>{Number(totalAssets || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</b>
          <small>浮动盈亏 <em className={Number(floatPnl || 0) >= 0 ? "profit" : "loss"}>{Number(floatPnl || 0).toFixed(2)}</em></small>
        </div>
        <div className="position-summary-grid">
          <span>可用现金 <b>{Number(cash || 0).toFixed(2)}</b></span>
          <span>持仓市值 <b>{Number(marketValue || 0).toFixed(2)}</b></span>
          <span>已实现 <b className={Number(realizedPnl || 0) >= 0 ? "profit" : "loss"}>{Number(realizedPnl || 0).toFixed(2)}</b></span>
          <span>仓位 <b>{Number(positionPct || 0).toFixed(1)}%</b></span>
          <span>持仓 <b>{openCount}/{maxPositions}</b></span>
        </div>
      </div>
    </div>
  );
}

function CandidateTable({ candidates, onSelect, onSimulate, onWatch, watchSymbols = [] }) {
  if (!candidates?.length) {
    return <div className="empty">暂无候选。先点击“同步数据”，或等待盘后数据更新。</div>;
  }
  const watched = new Set(watchSymbols);
  return (
    <div className="table">
      <div className="thead">
        <span>股票</span><span>评分</span><span>策略</span><span>观察价</span><span>止损</span><span>止盈/仓位</span>
      </div>
      {candidates.map((item) => (
        <div className={`row ${watched.has(item.symbol) ? "watched-row" : ""}`} key={item.symbol} role="button" tabIndex={0} onClick={() => onSelect(item.symbol)} onKeyDown={(e) => { if (e.key === "Enter") onSelect(item.symbol); }}>
          <span><b>{item.name}</b><small>{item.symbol}</small></span>
          <span className="score">{item.score}</span>
          <span>
            <b>{item.strategy_tags?.join("、") || "综合"}</b>
            <small>证据 {item.strategy_scores?.evidence_score ?? item.score} · 资金 {item.strategy_scores?.fund_flow_score ?? 0} · 题材 {item.strategy_scores?.theme_score ?? 0} · 消息 {item.strategy_scores?.message_risk_score ?? 0}</small>
          </span>
          <span>{item.realtime ? <><b>{Number(item.realtime.price).toFixed(2)}</b><small>{formatTime(item.realtime.updated_at)}</small></> : item.entry_price}</span>
          <span className="risk">{item.stop_loss}</span>
          <span className="row-actions">
            <b>{item.take_profit_1} / {item.position_pct}%</b>
            <button type="button" title="模拟买入" onClick={(e) => { e.stopPropagation(); onSimulate(item); }}><WalletCards size={15} /></button>
            <button type="button" className={watched.has(item.symbol) ? "active-watch" : ""} title={watched.has(item.symbol) ? "取消自选" : "加入自选"} onClick={(e) => { e.stopPropagation(); onWatch(item.symbol, item.name, watched.has(item.symbol)); }}><Star size={15} fill={watched.has(item.symbol) ? "currentColor" : "none"} /></button>
          </span>
        </div>
      ))}
    </div>
  );
}

function statusText(status) {
  if (status === "ok") return "正常";
  if (status === "warning") return "关注";
  if (status === "empty") return "暂无";
  if (status === "planned") return "待接入";
  return status || "未知";
}

function DataStatusPanel({ status }) {
  const [open, setOpen] = useState(false);
  if (!status) return null;
  const daily = status.daily || {};
  const realtime = status.realtime || {};
  const plans = status.plans || {};
  const topSource = realtime.sources?.[0];
  const latestUpdate = realtime.updated_at || status.updated_at || daily.updated_at || plans.updated_at || "";
  return (
    <section className={`panel data-status-panel ${open ? "open" : "collapsed"}`}>
      <button className="data-status-toggle" type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span>
          <b>数据链路</b>
          <small>最新更新 {formatTime(latestUpdate)}</small>
        </span>
        <span className={`health-pill ${status.status}`}>{statusText(status.status)}</span>
        <i aria-hidden="true"></i>
      </button>
      {open && (
        <div className="data-status-body">
          <div className="data-status-grid">
            <div><span>最新日线</span><b>{daily.latest_date || "暂无"}</b><small>{daily.latest_symbols || 0}/{daily.total_symbols || 0} 只 · {daily.complete_pct || 0}%</small></div>
            <div><span>行情源</span><b>{topSource?.source || "暂无"}</b><small>{realtime.count || 0} 条快照 · {formatTime(realtime.updated_at)}</small></div>
            <div><span>下一计划</span><b>{plans.pending || 0}</b><small>{plans.latest_plan_date || "暂无"} · 基于 {plans.trade_date || "暂无"}</small></div>
          </div>
          <div className="provider-strip">
            {(status.provider_order || []).map((item) => <span key={item}>{item}</span>)}
          </div>
          <p>{(status.messages || []).join("；")}</p>
        </div>
      )}
    </section>
  );
}

function HomeDashboard({ candidates, loading, error, topDate, syncMsg, dataStatus, watchSymbols = [], onSelect, onSimulate, onWatch }) {
  const list = candidates || [];
  const topItems = list.slice(0, 5);
  const sim = useAsync(api.simulationState, []);
  const reports = useAsync(api.dailyReports, []);
  const account = sim.data?.account_summary || {};
  const risk = sim.data?.risk_snapshot || {};
  const latestReport = sim.data?.latest_report || {};
  const positions = sim.data?.positions || [];
  const plans = sim.data?.plans || [];
  const aiPlanReviews = sim.data?.ai_plan_reviews || [];
  const avgScore = list.length ? (list.reduce((sum, item) => sum + Number(item.score || 0), 0) / list.length).toFixed(1) : "0.0";
  const strategyCounts = list.reduce((acc, item) => {
    (item.strategy_tags?.length ? item.strategy_tags : ["综合"]).forEach((tag) => {
      acc[tag] = (acc[tag] || 0) + 1;
    });
    return acc;
  }, {});
  const topStrategies = Object.entries(strategyCounts).sort((a, b) => b[1] - a[1]).slice(0, 3);
  const topCandidate = topItems[0];
  const topWatched = topCandidate && watchSymbols.includes(topCandidate.symbol);
  const strongCount = list.filter((item) => Number(item.score || 0) >= 80).length;
  const marketSnapshot = sim.data?.market_snapshot || {};
  const upCount = Number(marketSnapshot.up_count ?? 0);
  const downCount = Number(marketSnapshot.down_count ?? 0);
  const breadthTotal = upCount + downCount;
  const hasMarketSnapshot = breadthTotal > 0;
  const breadthPct = hasMarketSnapshot ? Math.round((upCount / breadthTotal) * 100) : null;
  const avgPctChg = hasMarketSnapshot ? Number(marketSnapshot.avg_pct_chg ?? 0) : 0;
  const marketMood = hasMarketSnapshot
    ? avgPctChg >= 0.5 ? "偏强" : avgPctChg <= -0.8 ? "偏弱" : "震荡"
    : "同步中";
  const assetPoints = (reports.data || [])
    .filter((item) => reportAssetValue(item) > 0)
    .slice(0, 7)
    .reverse()
    .map((item) => ({ date: item.trade_date || item.report_date, value: reportAssetValue(item) }));
  const assetCurve = assetPoints.length >= 2 ? assetPoints : [];
  const openPositionCount = positions.filter((p) => p.status !== "closed").length || account.open_positions || 0;
  const cashValue = Number(account.cash || 0);
  const marketValue = Number(account.market_value || 0);
  const totalAssets = Number(account.total_assets || account.cash || 0);
  const positionPct = Number(account.position_pct ?? (totalAssets ? (marketValue / totalAssets) * 100 : 0));
  const todayTradeCount = Number(latestReport.metrics?.today_trades || 0);
  const summaryText = latestReport.summary || (hasMarketSnapshot
    ? `${marketMood}，关注 ${list.length} 只候选，强信号 ${strongCount} 只。`
    : `实时行情快照同步中，当前候选 ${list.length} 只，强信号 ${strongCount} 只。`);
  const fundText = positionPct >= 60
    ? `仓位 ${positionPct.toFixed(1)}%，持仓偏高，优先看风控与卖出计划。`
    : positionPct <= 15
      ? `仓位 ${positionPct.toFixed(1)}%，现金充足，等待高确定性计划。`
      : `仓位 ${positionPct.toFixed(1)}%，资金处于中性配置。`;

  return (
    <>
      <section className="home-overview">
        <div className="asset-hero">
          <div className="hero-top">
            <span>AStockPilot</span>
            <small>{topDate}</small>
          </div>
          <div className="asset-main-line">
            <div>
              <span>总资产 (CNY)</span>
              <b>{Number(account.total_assets || account.cash || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</b>
              <small>今日收益 <em className={Number(account.float_pnl || 0) >= 0 ? "profit" : "loss"}>{Number(account.float_pnl || 0).toFixed(2)} / {Number(account.float_pnl_pct || 0).toFixed(2)}%</em></small>
            </div>
            <AssetCurve points={assetCurve} loading={reports.loading} compact />
          </div>
          <div className="asset-mini-grid">
            <div><span>累计收益</span><b className={Number(account.realized_pnl || 0) >= 0 ? "profit" : "loss"}>{Number(account.realized_pnl || 0).toFixed(2)}</b></div>
            <div><span>胜率</span><b>{Number(latestReport.metrics?.win_rate || 0).toFixed(1)}%</b></div>
            <div><span>最大回撤</span><b className="loss">{Number(latestReport.metrics?.max_drawdown || 0).toFixed(2)}%</b></div>
            <div><span>持仓</span><b>{positions.filter((p) => p.status !== "closed").length || account.open_positions || 0}/6</b></div>
          </div>
        </div>

        <div className="advisor-card">
          <div className="section-head compact-head"><h2>AI 投顾助手</h2><span>{latestReport.trade_date || topDate}</span></div>
          <div className={`market-mood-strip advisor-market ${marketMood === "偏强" ? "strong" : marketMood === "偏弱" ? "weak" : ""}`}>
            <div>
              <span>今日行情</span>
              <b>{marketMood}</b>
            </div>
            <div className="market-bar" style={{ "--mood": `${breadthPct ?? 0}%` }}><i></i></div>
            <strong>{hasMarketSnapshot ? `${breadthPct}%` : "--"}</strong>
          </div>
          <p>{summaryText}</p>
          <div className="advisor-summary-grid">
            <div><span>资金状态</span><b>{positionPct.toFixed(1)}%</b><small>{fundText}</small></div>
            <div><span>盈亏总结</span><b className={Number(account.float_pnl || 0) >= 0 ? "profit" : "loss"}>{Number(account.float_pnl || 0).toFixed(2)}</b><small>已实现 {Number(account.realized_pnl || 0).toFixed(2)}</small></div>
            <div><span>市场宽度</span><b>{hasMarketSnapshot ? `${upCount}/${breadthTotal}` : "--"}</b><small>平均涨跌 {hasMarketSnapshot ? `${avgPctChg.toFixed(2)}%` : "--"}</small></div>
            <div><span>执行概况</span><b>{todayTradeCount}</b><small>持仓 {openPositionCount} 只 · 候选 {list.length} 只</small></div>
          </div>
          <div className={`risk-health-card ${risk.guard_active ? "guarded" : Number(risk.recent_pnl || 0) < 0 ? "caution" : "stable"}`}>
            <div className="risk-health-heading"><span>策略风控</span><b>{risk.guard_label || "正常观察"}</b></div>
            <p>{risk.guard_reason || "风险阈值正常，继续观察买入质量与持仓退出。"}</p>
            <div className="risk-health-meta">
              <span>近20日 {Number(risk.recent_pnl || 0).toFixed(2)}</span>
              <span>连续亏损日 {Number(risk.loss_streak || 0)} / {Number(risk.max_loss_streak || 3)}</span>
              <span>日亏损上限 {Number(risk.max_daily_loss_pct || 1.5).toFixed(1)}%</span>
            </div>
          </div>
        </div>
      </section>

      <section className="panel main-panel">
        <div className="section-head"><h2>今日机会</h2><button className="ghost inline" type="button">查看更多</button></div>
        {syncMsg && <div className="empty compact">{syncMsg}</div>}
        {loading ? <div className="empty">加载中...</div> : error ? <div className="empty">{error}</div> : <CandidateTable candidates={list} onSelect={onSelect} onSimulate={onSimulate} onWatch={onWatch} watchSymbols={watchSymbols} />}
        {!loading && !error && !!list.length && (
          <div className="mobile-opportunity-list">
            {list.slice(0, 6).map((item) => (
              <button className="mobile-opportunity" key={`m-${item.symbol}`} type="button" onClick={() => onSelect(item.symbol)}>
                <span><b>{item.name}</b><small>{item.symbol}</small></span>
                <strong>{Number(item.score || 0).toFixed(2)}</strong>
                <em>{item.strategy_tags?.[0] || "观察"}</em>
              </button>
            ))}
          </div>
        )}
      </section>

      <DataStatusPanel status={dataStatus} />
    </>
  );
}

function DetailPanel({ symbol, onWatch, watchSymbols = [] }) {
  const detail = useAsync(() => (symbol ? api.stock(symbol) : Promise.resolve(null)), [symbol]);
  if (!symbol) return <section className="panel"><h2>股票详情</h2><div className="empty">选择一只候选股票查看 K 线和信号。</div></section>;
  if (detail.loading) return <section className="panel"><h2>股票详情</h2><div className="empty">加载中...</div></section>;
  if (detail.error) return <section className="panel"><h2>股票详情</h2><div className="empty">{detail.error}</div></section>;
  if (!detail.data) return <section className="panel"><h2>股票详情</h2><div className="empty">暂无详情数据。</div></section>;
  const signal = detail.data.latest_signal;
  const evaluations = detail.data.evaluations || [];
  const aiReviews = detail.data.ai_reviews || [];
  const latestEval = evaluations[0];
  const quote = detail.data.quote;
  const latestBar = detail.data.latest_bar;
  const displayPrice = quote?.price ?? latestBar?.close ?? signal?.entry_price ?? 0;
  const changePct = quote?.pct_chg;
  const priceSource = quote ? (quote.source || "实时行情") : latestBar ? "日线收盘" : "策略价";
  const priceUpdatedAt = quote?.updated_at || latestBar?.trade_date || signal?.trade_date;
  const latestAi = aiReviews[0];
  const latestPlan = latestEval?.plan;
  const strategyScores = signal?.strategy_scores || {};
  const watched = watchSymbols.includes(detail.data.symbol);
  const factorItems = [
    ["总分", signal?.score ?? latestEval?.score ?? "无"],
    ["技术", strategyScores.evidence_score_v15 ?? strategyScores.evidence_score ?? "无"],
    ["资金", strategyScores.fund_flow_score ?? "无"],
    ["题材", strategyScores.theme_score ?? "无"],
    ["消息", strategyScores.message_risk_score ?? "无"],
  ];
  const radarItems = [
    { label: "趋势", value: strategyScores.evidence_score_v15 ?? strategyScores.trend_breakout ?? signal?.trend_score ?? signal?.score ?? 0 },
    { label: "资金", value: strategyScores.fund_flow_score ?? 0 },
    { label: "情绪", value: strategyScores.message_risk_score ?? strategyScores.theme_score ?? 0 },
    { label: "流动性", value: strategyScores.liquidity_quality ?? signal?.volume_score ?? 0 },
    { label: "风控", value: strategyScores.risk_control ?? signal?.risk_score ?? 0 },
  ];
  const signalCards = [
    ["趋势突破", strategyScores.trend_breakout ?? signal?.trend_score ?? 0, "trend"],
    ["资金流入", strategyScores.fund_flow_score ?? 0, "fund"],
    ["流动性健康", strategyScores.liquidity_quality ?? signal?.volume_score ?? 0, "liquidity"],
    ["消息利好", strategyScores.message_risk_score ?? 0, "message"],
  ];
  return (
    <section className="panel detail-panel">
      <div className="section-head">
        <h2>{detail.data.name} <small>{detail.data.symbol}</small></h2>
        <button className={`icon-btn ${watched ? "active-watch" : ""}`} onClick={() => onWatch(detail.data.symbol, detail.data.name, watched)} title={watched ? "取消自选" : "加入自选"}><Star size={18} fill={watched ? "currentColor" : "none"} /></button>
      </div>
      <div className="analysis-hero">
        <div className="analysis-score-card">
          <b>{Number(signal?.score ?? latestEval?.score ?? 0).toFixed(2)}</b>
          <span>综合评分</span>
          <small>较昨日 <em className="profit">+{Number(changePct || 0).toFixed(2)}</em></small>
          <div className="analysis-tags">{signal?.strategy_tags?.slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}</div>
        </div>
        <div className="radar-card">
          <FactorRadar items={radarItems} />
        </div>
      </div>
      <div className="signal-strength-grid">
        {signalCards.map(([label, value, key]) => (
          <div key={key}>
            <span>{label}</span>
            <b className={Number(value) >= 70 ? "loss" : Number(value) >= 50 ? "profit" : ""}>{Number(value || 0).toFixed(0)}</b>
          </div>
        ))}
      </div>
      <div className="strategy-advice-grid">
        <div><span>操作建议</span><b>{latestEval?.decision || signal?.decision || "观察"}</b></div>
        <div><span>信心指数</span><b>{strategyScores.evidence_score ?? signal?.score ?? "--"}%</b></div>
        <div><span>建议仓位</span><b>{signal?.position_pct ?? "--"}%</b></div>
      </div>
      <div className="detail-brief">
        <div className="quote-card">
          <span>当前价</span>
          <b>{Number(displayPrice || 0).toFixed(2)}</b>
          {changePct !== undefined && changePct !== null && <strong className={Number(changePct) >= 0 ? "profit" : "loss"}>{Number(changePct).toFixed(2)}%</strong>}
          <small>{priceSource} · {quote ? formatTime(priceUpdatedAt) : priceUpdatedAt || "暂无更新时间"}</small>
        </div>
        <div className="decision-card">
          <div className="decision-head">
            <div>
              <span>当前结论</span>
              <b>{latestEval?.decision || signal?.decision || "观察"}</b>
            </div>
            {latestPlan?.plan_type && <em>{planLabel(latestPlan.plan_type)}</em>}
          </div>
          <p>{latestEval?.summary || signal?.reasons?.[0] || "暂无最新评估摘要。"}</p>
          <div className="factor-strip">
            {factorItems.map(([label, value]) => <span key={label}>{label}<b>{value}</b></span>)}
          </div>
        </div>
      </div>
      {latestAi && (
        <div className={`ai-brief ${latestAi.risk_level === "high" ? "warn" : latestAi.risk_level === "medium" ? "caution" : ""}`}>
          <b>AI {latestAi.decision} · {latestAi.risk_level}</b>
          <span>{latestAi.summary}</span>
        </div>
      )}
      <CollapsibleSection title="价格走势" meta={latestBar?.trade_date || "K线"} defaultOpen>
        <KChart bars={detail.data.bars} />
      </CollapsibleSection>
      {signal && (
        <CollapsibleSection title="策略信号" meta={`${signal.trade_date} · ${signal.score}分`} defaultOpen={false}>
          <div className="signal-box">
            <div className="tags">{signal.strategy_tags?.map((r) => <span key={r}>{r}</span>)}<span>{signal.market_state}</span></div>
            <div className="reason-list">{signal.reasons.map((r) => <p key={r}>{r}</p>)}</div>
          </div>
        </CollapsibleSection>
      )}
      <EvidencePanel evidence={detail.data.evidence} />
      <CollapsibleSection title="评估趋势" meta={`${evaluations.length} 条历史`} defaultOpen={false}>
        <EvaluationTrend evaluations={evaluations} />
      </CollapsibleSection>
      <CollapsibleSection title="最近分析记录" meta={latestEval ? `${latestEval.trade_date} · ${latestEval.decision}` : "暂无记录"} defaultOpen={false}>
        {evaluations.length === 0 ? <div className="empty compact">暂无个股评估记录。每天生成交易计划或日报后会自动写入。</div> : (
          <div className="eval-list">
            {evaluations.slice(0, 20).map((item) => (
              <div className="eval-row" key={`${item.symbol}-${item.trade_date}`}>
                <div className="eval-head">
                  <b>{item.trade_date}</b>
                  <span className={`eval-decision ${String(item.decision).includes("卖") || String(item.decision).includes("弱") ? "warn" : ""}`}>{item.decision}</span>
                </div>
                <div className="eval-metrics">
                  <span>评分 <b>{item.score ?? "无"}</b></span>
                  <span>现价 <b>{Number(item.metrics?.current_price || 0).toFixed(2)}</b></span>
                  {item.metrics?.pnl_pct !== undefined && <span>收益 <b className={Number(item.metrics.pnl_pct) >= 0 ? "profit" : "loss"}>{Number(item.metrics.pnl_pct).toFixed(2)}%</b></span>}
                  {item.plan?.plan_type && <span>计划 <b>{planLabel(item.plan.plan_type)}</b></span>}
                </div>
                <p>{item.summary}</p>
              </div>
            ))}
          </div>
        )}
      </CollapsibleSection>
      <CollapsibleSection title="AI 个股分析" meta={aiReviews[0] ? `${aiReviews[0].trade_date} · ${aiReviews[0].decision}` : "暂无记录"} defaultOpen={false}>
        {aiReviews.length === 0 ? <div className="empty compact">暂无 AI 个股分析。配置 API key 后，在模拟交易页点击“AI 审核”。</div> : (
          <div className="eval-list">
            {aiReviews.slice(0, 20).map((item) => (
              <div className="eval-row" key={`ai-${item.id}`}>
                <div className="eval-head">
                  <b>{item.trade_date}</b>
                  <span className={`eval-decision ${item.risk_level === "high" ? "warn" : ""}`}>{item.decision} · {item.risk_level}</span>
                </div>
                <p>{item.summary}</p>
                <small>{item.suggestion}</small>
              </div>
            ))}
          </div>
        )}
      </CollapsibleSection>
    </section>
  );
}

function EvidencePanel({ evidence }) {
  if (!evidence) return null;
  const technical = evidence.technical?.items || [];
  const dataItems = evidence.data_quality?.items || [];
  const extended = (evidence.extended_factors || evidence.planned_factors || []).filter((item) => item?.value !== "待接入");
  return (
    <CollapsibleSection title="决策依据" meta="技术、数据质量、扩展证据" defaultOpen={false}>
      <div className="evidence-summary">{evidence.summary || "暂无摘要"}</div>
      <div className="evidence-grid">
        <EvidenceGroup title="技术面" items={technical} />
        <EvidenceGroup title="数据质量" items={dataItems} />
        <EvidenceGroup title="扩展因子" items={extended} />
      </div>
    </CollapsibleSection>
  );
}

function EvidenceGroup({ title, items }) {
  return (
    <div className="evidence-group">
      <h3>{title}</h3>
      {(items || []).map((item) => (
        <div className="evidence-item" key={`${title}-${item.label}`}>
          <span className={`evidence-dot ${item.status || "neutral"}`}></span>
          <div>
            <b>{item.label}<em>{item.value}</em></b>
            <small>{item.note || "暂无说明"}</small>
          </div>
        </div>
      ))}
    </div>
  );
}

function StockDetailPage({ symbol, symbols = [], watchSymbols = [], onBack, onWatch, onSelectSymbol }) {
  const uniqueSymbols = [...new Set((symbols?.length ? symbols : [symbol]).filter(Boolean))];
  const index = uniqueSymbols.indexOf(symbol);
  const prevSymbol = index > 0 ? uniqueSymbols[index - 1] : uniqueSymbols[uniqueSymbols.length - 1];
  const nextSymbol = index >= 0 && uniqueSymbols.length ? uniqueSymbols[(index + 1) % uniqueSymbols.length] : "";
  return (
    <section>
      <div className="detail-nav">
        <button className="ghost inline back-icon" onClick={onBack}>返回</button>
        <div className="button-row">
          <button className="ghost inline" disabled={uniqueSymbols.length <= 1} onClick={() => onSelectSymbol(prevSymbol)}>上一只</button>
          <button className="ghost inline" disabled={uniqueSymbols.length <= 1} onClick={() => onSelectSymbol(nextSymbol)}>下一只</button>
        </div>
      </div>
      <DetailPanel symbol={symbol} onWatch={onWatch} watchSymbols={watchSymbols} />
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
  const defaultStart = new Date();
  defaultStart.setFullYear(defaultStart.getFullYear() - 1);
  const [form, setForm] = useState({
    start_date: defaultStart.toISOString().slice(0, 10),
    end_date: today,
    initial_cash: 100000,
    max_positions: 5,
    hold_days: 10,
    fee_rate: 0.0003,
  });
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
  const summary = result?.summary || {};
  const skipped = summary.skipped || {};
  const equity = result?.equity || [];
  const lastEquity = equity[equity.length - 1];
  return (
    <section>
      <div className="section-head"><h2>历史验证</h2><BarChart3 size={20} /></div>
      <div className="empty compact">按历史行情逐日生成信号，并用接近当前模拟账户的规则回测：次日条件买入、持仓上限、交易费用、止损、第一止盈和移动止损。长区间会更慢。</div>
      <div className="form-grid backtest-form">
        <label>开始日期<input type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} /></label>
        <label>结束日期<input type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} /></label>
        <label>初始资金<input type="number" value={form.initial_cash} onChange={(e) => setForm({ ...form, initial_cash: Number(e.target.value) })} /></label>
        <label>最大持仓<input type="number" value={form.max_positions} onChange={(e) => setForm({ ...form, max_positions: Number(e.target.value) })} /></label>
        <label>最长持有天数<input type="number" value={form.hold_days} onChange={(e) => setForm({ ...form, hold_days: Number(e.target.value) })} /></label>
        <label>佣金率<input type="number" step="0.0001" value={form.fee_rate} onChange={(e) => setForm({ ...form, fee_rate: Number(e.target.value) })} /></label>
      </div>
      <button className="primary" onClick={run} disabled={loading}>{loading ? "回测中..." : "运行回测"}</button>
      {error && <div className="empty compact">{error}</div>}
      {result && (
        <>
          <div className="metrics backtest-metrics">
            <div><b className={Number(summary.total_return_pct || 0) >= 0 ? "profit" : "loss"}>{Number(summary.total_return_pct || 0).toFixed(2)}%</b><span>总收益</span></div>
            <div><b>{Number(summary.final_assets || 0).toFixed(2)}</b><span>最终资产</span></div>
            <div><b>{Number(summary.win_rate_pct || 0).toFixed(2)}%</b><span>胜率</span></div>
            <div><b className="loss">{Number(summary.max_drawdown_pct || 0).toFixed(2)}%</b><span>最大回撤</span></div>
            <div><b>{summary.trade_count || 0}</b><span>卖出成交</span></div>
            <div><b>{summary.signal_count || 0}</b><span>历史信号</span></div>
            <div><b>{Number(summary.cash || 0).toFixed(2)}</b><span>期末现金</span></div>
            <div><b>{summary.open_positions || 0}</b><span>期末持仓</span></div>
          </div>
          <div className="backtest-subgrid">
            <div className="backtest-box">
              <b>资金曲线</b>
              <p>起始 {Number(form.initial_cash || 0).toFixed(2)}，期末 {Number(lastEquity?.equity || summary.final_assets || 0).toFixed(2)}，市值 {Number(summary.market_value || 0).toFixed(2)}。</p>
              <div className="equity-spark">
                {equity.slice(-12).map((item) => (
                  <span key={item.date} title={`${item.date} ${item.equity}`} style={{ height: `${Math.max(10, Math.min(100, Number(item.equity || 0) / Number(form.initial_cash || 1) * 70))}%` }} />
                ))}
              </div>
            </div>
            <div className="backtest-box">
              <b>未成交原因</b>
              <div className="skip-grid">
                <span>仓位满 <b>{skipped.slot || 0}</b></span>
                <span>价格未到 <b>{skipped.price || 0}</b></span>
                <span>重复持仓 <b>{skipped.duplicate || 0}</b></span>
                <span>资金不足 <b>{skipped.cash || 0}</b></span>
                <span>不足一手 <b>{skipped.lot || 0}</b></span>
                <span>缺少行情 <b>{skipped.missing_bar || 0}</b></span>
              </div>
            </div>
          </div>
          {result.trades.length === 0 && <div className="empty compact">当前区间没有生成交易。可以先同步更多股票，或放宽策略阈值后再验证。</div>}
          <div className="trade-list backtest-trades">
            {result.trades.slice(-40).reverse().map((t, i) => (
              <div key={`${t.symbol}-${t.exit_date}-${i}`}>
                <span><b>{t.symbol}</b><small>{t.entry_date} {"->"} {t.exit_date}</small></span>
                <span>{t.quantity || "-"} 股</span>
                <span>{Number(t.entry_price || 0).toFixed(2)} / {Number(t.exit_price || 0).toFixed(2)}</span>
                <span className={Number(t.return_pct || 0) >= 0 ? "profit" : "loss"}>{Number(t.return_pct || 0).toFixed(2)}%</span>
                <span>{t.reason}</span>
                <span>{(t.strategy_tags || []).slice(0, 3).join("、") || t.market_state}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function ReportArchive() {
  const reports = useAsync(api.dailyReports, []);
  const [openId, setOpenId] = useState("");
  if (reports.loading) return <div className="empty compact">加载归档中...</div>;
  if (reports.error) return <div className="empty compact">{reports.error}</div>;
  const items = reports.data || [];
  if (items.length === 0) return <div className="empty compact">暂无归档。生成次日计划或执行计划后会自动保存。</div>;
  return (
    <div className="archive-list">
      {items.slice(0, 10).map((item) => {
        const snapshot = item.snapshot || {};
        const candidateCount = snapshot.candidates?.length || 0;
        const planCount = snapshot.trade_plans?.length || 0;
        const tradeCount = snapshot.trades?.length || item.metrics?.today_trades || 0;
        const topCandidates = (snapshot.candidates || []).slice(0, 3).map((c) => c.name).join("、");
        const archiveId = String(item.id || item.created_at || item.trade_date);
        const expanded = openId === archiveId;
        const plans = snapshot.trade_plans || [];
        const trades = snapshot.trades || [];
        const candidates = snapshot.candidates || [];
        return (
          <div className="archive-row" key={archiveId}>
            <button className="archive-toggle" type="button" onClick={() => setOpenId(expanded ? "" : archiveId)}>
              <span>
                <b>{item.trade_date}</b>
                <small>{formatTime(item.created_at)} · 候选 {candidateCount} · 计划 {planCount}</small>
              </span>
              <em>{expanded ? "收起" : "展开"}</em>
            </button>
            <div className="archive-metrics">
              <span>总资产 <b>{Number(item.metrics?.total_assets || 0).toFixed(2)}</b></span>
              <span>持仓 <b>{item.metrics?.open_positions ?? 0}</b></span>
              <span>成交 <b>{tradeCount}</b></span>
              <span>浮盈亏 <b className={(item.metrics?.float_pnl || 0) >= 0 ? "profit" : "loss"}>{Number(item.metrics?.float_pnl || 0).toFixed(2)}</b></span>
            </div>
            <p>{topCandidates ? `重点候选：${topCandidates}` : item.summary}</p>
            {expanded && (
              <div className="archive-detail">
                <div className="archive-detail-block">
                  <b>执行摘要</b>
                  <p>{item.summary || "暂无摘要。"}</p>
                </div>
                <div className="archive-detail-grid">
                  <div>
                    <b>交易计划</b>
                    {plans.length ? plans.slice(0, 12).map((plan) => (
                      <button className="archive-plan-line" key={plan.id || `${plan.symbol}-${plan.plan_type}`} type="button">
                        <span>{planLabel(plan.plan_type)} · {plan.name} {plan.symbol}</span>
                        <small>{planStatusLabel(plan.status)} · {plan.plan_date} · {Number(plan.trigger_price || 0).toFixed(2)}</small>
                      </button>
                    )) : <small>暂无计划。</small>}
                  </div>
                  <div>
                    <b>成交记录</b>
                    {trades.length ? trades.slice(0, 12).map((trade) => (
                      <div className="archive-trade-line" key={trade.id || `${trade.symbol}-${trade.created_at}`}>
                        <span>{trade.action} · {trade.name || trade.symbol}</span>
                        <small>{trade.trade_date} · {trade.quantity} 股 · {Number(trade.price || 0).toFixed(2)}</small>
                      </div>
                    )) : <small>暂无成交。</small>}
                  </div>
                </div>
                {candidates.length > 0 && (
                  <div className="archive-detail-block">
                    <b>重点候选</b>
                    <p>{candidates.slice(0, 5).map((candidate) => `${candidate.name} ${candidate.score}`).join("、")}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function SimulatorPanel({ onSync, onDailyWorkflow, syncing = false, workflowing = false, syncMsg = "" }) {
  const state = useAsync(api.simulationState, []);
  const planState = useAsync(api.tradePlans, []);
  const [sim, setSim] = useState(null);
  const [plans, setPlans] = useState([]);
  const [autoLog, setAutoLog] = useState("");
  const [saving, setSaving] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [aiLog, setAiLog] = useState("");
  const [selectedPlan, setSelectedPlan] = useState(null);
  const [planActionLog, setPlanActionLog] = useState("");

  useEffect(() => {
    if (state.data) setSim(state.data);
  }, [state.data]);
  useEffect(() => {
    if (planState.data) setPlans(planState.data);
  }, [planState.data]);

  const positions = sim?.positions || [];
  const rules = sim?.rules || DEFAULT_SIM_RULES;
  const automation = sim?.automation || { enabled: true, mode: "assist", run_time: "15:30", execution_time: "09:30", planning_time: "15:30", sync_before_run: true, last_run_date: "", last_execution_date: "", last_plan_date: "", last_status: "", last_message: "", last_check_at: "" };
  const automationMode = automation.mode || (automation.enabled ? "managed" : "manual");
  const cash = Number(sim?.cash || 0);
  const openPositions = positions.filter((p) => p.status !== "closed");
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
  const pendingPlans = plans.filter((p) => p.status === "pending");
  const latestPlanDateValue = plans.reduce((latest, p) => (!latest || String(p.plan_date || "") > latest ? String(p.plan_date || "") : latest), "");
  const currentPlans = latestPlanDateValue ? plans.filter((p) => p.plan_date === latestPlanDateValue) : [];
  const currentPendingPlans = currentPlans.filter((p) => p.status === "pending");
  const currentHandledPlans = currentPlans.filter((p) => p.status !== "pending");
  const buyPlans = currentPendingPlans.filter((p) => p.plan_type === "next_buy");
  const sellPlans = currentPendingPlans.filter((p) => String(p.plan_type).startsWith("sell"));
  const rebalancePlans = currentPendingPlans.filter((p) => String(p.plan_type).startsWith("rebalance"));
  const addPlans = currentPendingPlans.filter((p) => p.plan_type === "add_buy");
  const holdPlans = currentPendingPlans.filter((p) => p.plan_type === "hold");
  const latestPlanDate = latestPlanDateValue || "暂无";
  const marketSnapshot = sim?.market_snapshot || {};
  const latestAiReport = sim?.latest_ai_report;
  const aiReviewByPlan = (sim?.ai_plan_reviews || []).reduce((acc, item) => {
    acc[item.plan_id] = item;
    return acc;
  }, {});
  const decisionWarnings = currentPendingPlans.filter((p) => ["missing_ai", "stale_ai", "rejected", "high_risk", "blocked"].includes(p.final_decision?.status) || p.latest_decision_audit?.decision_status === "data_guard");

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
      if (result.positions) setSim(result);
      if (result.plans) setPlans(result.plans);
      setAutoLog(result.logs?.length ? result.logs.join("；") : result.message);
    } finally {
      setSaving(false);
    }
  };

  const executePlans = async (force = false) => {
    setSaving(true);
    try {
      const result = await api.executePlans(force);
      setSim(result);
      if (result.plans) setPlans(result.plans);
      setAutoLog(result.logs?.length ? result.logs.join("；") : result.message);
    } finally {
      setSaving(false);
    }
  };

  const syncPlanDetail = (nextPlans, planId) => {
    const nextSelected = (nextPlans || []).find((item) => item.id === planId);
    if (nextSelected) setSelectedPlan(nextSelected);
  };

  const executeSelectedPlan = async () => {
    if (!selectedPlan) return;
    setSaving(true);
    setPlanActionLog("");
    try {
      const result = await api.executePlan(selectedPlan.id);
      setSim(result);
      if (result.plans) {
        setPlans(result.plans);
        syncPlanDetail(result.plans, selectedPlan.id);
      }
      setPlanActionLog(result.logs?.length ? result.logs.join("；") : result.message);
    } catch (err) {
      setPlanActionLog(err.message);
    } finally {
      setSaving(false);
    }
  };

  const reviewSelectedPlan = async () => {
    if (!selectedPlan) return;
    setSaving(true);
    setPlanActionLog("");
    try {
      const result = await api.aiReviewPlan(selectedPlan.id);
      if (result.status !== "ok") {
        setPlanActionLog(result.message || "AI 分析未完成");
      } else {
        const [nextSim, nextPlans] = await Promise.all([api.simulationState(), api.tradePlans()]);
        setSim(nextSim);
        setPlans(nextPlans);
        syncPlanDetail(nextPlans, selectedPlan.id);
        setPlanActionLog(result.message || "单股计划 AI 分析完成");
      }
    } catch (err) {
      setPlanActionLog(err.message);
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
        mode: nextAutomation.mode,
        run_time: nextAutomation.run_time,
        execution_time: nextAutomation.execution_time,
        planning_time: nextAutomation.planning_time,
        sync_before_run: nextAutomation.sync_before_run,
      });
      setSim({ ...sim, automation: next });
    } finally {
      setSaving(false);
    }
  };

  const runAi = async () => {
    setSaving(true);
    setAiLog("");
    try {
      const result = await api.runAiReview();
      if (result.status !== "ok") {
        setAiLog(result.message || "AI 审核未完成");
      } else {
        setAiLog(result.message);
        const next = await api.simulationState();
        setSim(next);
      }
    } catch (err) {
      setAiLog(err.message);
    } finally {
      setSaving(false);
    }
  };

  const runDailyWorkflow = async () => {
    if (onDailyWorkflow) {
      await onDailyWorkflow();
    }
  };

  const renderPlanRows = (items, limit = 8) => (
    <div className="plan-list">
      {items.slice(0, limit).map((p) => (
        <button className="plan-row plan-row-button" key={p.id} type="button" onClick={() => { setSelectedPlan(p); setPlanActionLog(""); }}>
          <span className={`plan-type ${p.plan_type}`}>{planLabel(p.plan_type)}</span>
          <div>
            <b>{p.name} <small>{p.symbol}</small></b>
            <p>{p.plan_date} · 触发 {Number(p.trigger_price).toFixed(2)}{p.quantity ? ` · ${p.quantity}股` : ""}</p>
            <small className="plan-analysis-line">技术：{p.reason || "暂无技术分析结论"}</small>
            {aiReviewByPlan[p.id] && (
              <small className={`ai-plan-note ${aiReviewByPlan[p.id].verdict}`}>
                AI方向：{verdictLabel(aiReviewByPlan[p.id].verdict)} · 风险 {aiReviewByPlan[p.id].risk_level} · {aiReviewByPlan[p.id].reason || aiReviewByPlan[p.id].suggestion}
              </small>
            )}
            <small className={`final-decision ${finalDecisionClass(p.final_decision)}`}>
              执行守门：{p.final_decision?.label || (aiReviewByPlan[p.id] ? `AI ${verdictLabel(aiReviewByPlan[p.id].verdict)}` : "待决策")}
            </small>
            {shouldShowAuditNote(p) && (
              <small className={`audit-note ${p.latest_decision_audit.allow_execute ? "allow" : "block"}`}>
                上次执行检查：{p.latest_decision_audit.decision_label || p.latest_decision_audit.decision_status}
              </small>
            )}
          </div>
          <em className={`plan-status ${p.status}`}>{planStatusLabel(p.status)}</em>
        </button>
      ))}
    </div>
  );

  if (state.loading || !sim) return <section className="panel"><h2>模拟账户</h2><div className="empty">加载中...</div></section>;
  if (state.error) return <section className="panel"><h2>模拟账户</h2><div className="empty">{state.error}</div></section>;

  return (
    <section className="panel wide-panel trade-workbench">
      <div className="trade-plan-hero">
        <div>
          <span>交易计划</span>
          <b>{latestPlanDate}</b>
          <small>执行以 AI 最终决策为准，满足交易池条件才成交。</small>
        </div>
        <div className="trade-plan-stats">
          <span>待执行 <b>{currentPendingPlans.length}</b></span>
          <span>买入 <b>{buyPlans.length}</b></span>
          <span>卖出 <b>{sellPlans.length}</b></span>
          <span>调仓 <b>{rebalancePlans.length}</b></span>
        </div>
      </div>

      <div className="sim-grid">
        <section className="sim-card plan-first-card">
          <div className="sim-card-head">
            <div><h3>即将执行</h3><small>{currentPendingPlans.length} 条待执行计划</small></div>
            <span className="state-pill">{buyPlans.length} 买 / {sellPlans.length} 卖 / {rebalancePlans.length} 调 / {addPlans.length} 追 / {holdPlans.length} 持</span>
          </div>
          {plans.length === 0 ? <div className="empty compact">暂无计划。盘后点击“生成次日计划”。</div> : (
            <>
              {decisionWarnings.length > 0 && (
                <div className="decision-warning">
                  {decisionWarnings.length} 条计划未获执行守门放行，执行时会跳过；点击单条计划可查看原因并重新 AI 分析。
                </div>
              )}
              <div className="plan-group-stack">
                <div className="plan-group current">
                  <div className="plan-group-head">
                    <div>
                      <b>{latestPlanDate} 交易池</b>
                      <span>{currentPendingPlans.length} 条待执行</span>
                    </div>
                  </div>
                  {currentPendingPlans.length ? renderPlanRows(currentPendingPlans, 12) : <div className="empty compact">当前计划日暂无待执行计划。</div>}
                </div>
                {currentHandledPlans.length > 0 && (
                  <div className="plan-group">
                    <div className="plan-group-head">
                      <b>{latestPlanDate} 已处理</b>
                      <span>{currentHandledPlans.length} 条</span>
                    </div>
                    {renderPlanRows(currentHandledPlans, 6)}
                  </div>
                )}
              </div>
            </>
          )}
          <div className="plan-action-row">
            <button className="primary" onClick={() => executePlans(false)} disabled={saving || currentPendingPlans.length === 0}>执行满足条件计划</button>
          </div>
          <div className="plan-action-help">审核顺序：技术计划生成交易池，AI 给出方向结论，执行守门检查审核时效、风险等级、行情数据和触发价；AI 通过且非 high 风险才进入成交检查，风控卖出仍优先执行。</div>
          {autoLog && <div className="auto-log compact-log">{autoLog}</div>}
        </section>

        <section className="sim-card">
          <div className="sim-card-head">
            <div><h3>分析与审核</h3><small>日报 {sim.latest_report?.trade_date || "暂无交易日"} · 生成 {formatTime(sim.latest_report?.created_at)}</small></div>
            <span className={automationMode === "managed" ? "state-pill on" : "state-pill"}>{automationMode === "managed" ? "全托管" : automationMode === "assist" ? "自动计划" : "手动"}</span>
          </div>
          {sim.latest_report ? (
            <div className="report-box compact-report">
              <div className="report-metrics">
                <span>成交 <b>{sim.latest_report.metrics?.today_trades ?? 0}</b></span>
                <span>候选 <b>{marketSnapshot.candidate_count ?? 0}</b></span>
                <span>上涨 <b>{marketSnapshot.up_count ?? 0}</b></span>
                <span>平均涨跌 <b className={(marketSnapshot.avg_pct_chg ?? 0) >= 0 ? "profit" : "loss"}>{Number(marketSnapshot.avg_pct_chg ?? 0).toFixed(2)}%</b></span>
              </div>
              <p>{sim.latest_report.summary}</p>
            </div>
          ) : <div className="empty compact">暂无日报。生成次日计划或执行计划后会出现分析结果。</div>}
          {latestAiReport && (
            <div className="ai-report-box">
              <div className="eval-head">
                <b>AI 总结：{latestAiReport.trade_date || sim.latest_report?.trade_date || "暂无日期"} · {latestAiReport.risk_level}</b>
                <small>生成 {formatTime(latestAiReport.created_at)}</small>
              </div>
              <p>{latestAiReport.summary}</p>
              <small>{latestAiReport.action_suggestion || latestAiReport.market_view || "暂无执行建议。"}</small>
            </div>
          )}
          <div className="sim-action-grid">
            <button className="primary" onClick={runDailyWorkflow} disabled={saving || workflowing}>{workflowing ? "生成中..." : "生成计划与审核"}</button>
            <button className="ghost inline" onClick={runAi} disabled={saving}>AI 复核</button>
          </div>
          {syncMsg && <div className="auto-log compact-log">{syncMsg}</div>}
          {aiLog && <div className="auto-log compact-log">{aiLog}</div>}
        </section>
      </div>

      {selectedPlan && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="rule-modal plan-detail-modal">
            <div className="section-head">
              <h2>{selectedPlan.name} <small>{selectedPlan.symbol} · {planLabel(selectedPlan.plan_type)}</small></h2>
              <button className="icon-btn" type="button" onClick={() => setSelectedPlan(null)} title="关闭"><X size={18} /></button>
            </div>
            <div className="plan-detail-actions">
              <button className="primary" type="button" onClick={reviewSelectedPlan} disabled={saving}>AI 分析</button>
              <button className="ghost inline" type="button" onClick={executeSelectedPlan} disabled={saving || selectedPlan.status !== "pending" || selectedPlan.final_decision?.allow === false}>执行该计划</button>
              <span>{selectedPlan.status === "pending" ? (selectedPlan.final_decision?.reason || "按交易池条件检查，满足才成交。") : "当前计划不是待执行状态。"}</span>
            </div>
            {planActionLog && <div className="auto-log compact-log">{planActionLog}</div>}
            <div className="plan-detail-grid">
              <section className="plan-detail-block">
                <h3>策略结论</h3>
                <div className="detail-grid">
                  <div><span>计划日</span><b>{selectedPlan.plan_date}</b></div>
                  <div><span>状态</span><b>{selectedPlan.status}</b></div>
                  <div><span>执行守门</span><b>{selectedPlan.final_decision?.label || "待决策"}</b></div>
                  <div><span>动作</span><b>{selectedPlan.action}</b></div>
                  <div><span>数量</span><b>{selectedPlan.quantity || 0} 股</b></div>
                  <div><span>触发价</span><b>{Number(selectedPlan.trigger_price || 0).toFixed(2)}</b></div>
                  <div><span>止损/止盈</span><b>{Number(selectedPlan.stop_loss || 0).toFixed(2)} / {Number(selectedPlan.take_profit || 0).toFixed(2)}</b></div>
                </div>
                <div className="plan-full-text">
                  <b>策略理由</b>
                  <p>{selectedPlan.reason || "暂无策略理由。"}</p>
                  {selectedPlan.latest_decision_audit && (
                    <>
                      <b>最近执行审计</b>
                      <p>{selectedPlan.latest_decision_audit.reason || "暂无审计原因。"}</p>
                      {selectedPlan.latest_decision_audit.data_guard?.status && selectedPlan.latest_decision_audit.data_guard.status !== "not_required" && (
                        <p>
                          数据检查：{selectedPlan.latest_decision_audit.data_guard.label || selectedPlan.latest_decision_audit.data_guard.status}
                          {selectedPlan.latest_decision_audit.data_guard.current_price ? ` · 实时价 ${selectedPlan.latest_decision_audit.data_guard.current_price}` : ""}
                          {selectedPlan.latest_decision_audit.data_guard.deviation_pct ? ` · 偏离 ${selectedPlan.latest_decision_audit.data_guard.deviation_pct}%` : ""}
                        </p>
                      )}
                    </>
                  )}
                </div>
              </section>
              <section className="plan-detail-block">
                <h3>AI 审核结论</h3>
                {aiReviewByPlan[selectedPlan.id] ? (
                  <>
                    <div className="detail-grid">
                      <div><span>AI方向</span><b>{verdictLabel(aiReviewByPlan[selectedPlan.id].verdict)}</b></div>
                      <div><span>风险等级</span><b>{aiReviewByPlan[selectedPlan.id].risk_level}</b></div>
                    </div>
                    <div className="plan-full-text">
                      <b>AI 理由</b>
                      <p>{aiReviewByPlan[selectedPlan.id].reason}</p>
                      <b>AI 建议</b>
                      <p>{aiReviewByPlan[selectedPlan.id].suggestion}</p>
                    </div>
                  </>
                ) : <div className="empty compact">暂无 AI 审核。点击“AI 审核”后会生成并保存。</div>}
              </section>
            </div>
          </div>
        </div>
      )}

      <CollapsibleSection title="历史归档" meta="日报、候选、计划和持仓快照">
        <ReportArchive />
      </CollapsibleSection>

      <CollapsibleSection title="AI 审核依据" meta="提示词和输入摘要">
        {!latestAiReport?.raw ? <div className="empty compact">暂无 AI 审核依据。点击“AI 审核”后会保存本次提示词和输入摘要。</div> : (
          <div className="ai-debug">
            <h3>系统提示词</h3>
            <pre>{latestAiReport.raw._prompt || "暂无"}</pre>
            <h3>输入摘要</h3>
            <pre>{JSON.stringify(latestAiReport.raw._input_summary || {}, null, 2)}</pre>
          </div>
        )}
      </CollapsibleSection>

      <CollapsibleSection title="自动与规则" meta={`${automationMode === "managed" ? "全托管" : automationMode === "assist" ? "自动计划" : "手动"} · 交易池 ${automation.execution_time || "09:30"} / 收盘 ${automation.planning_time || automation.run_time} · 评分 ${rules.minScore}`}>
        <div className="mode-switch">
          {[
            ["manual", "手动", "只在页面点击时同步、生成计划或执行计划。"],
            ["assist", "自动计划", "收盘后同步数据并生成下一交易日计划，不自动成交。"],
            ["managed", "全托管", "交易池开始后，盘中持续检查买入、止损、止盈条件，收盘后生成下一交易日计划。"],
          ].map(([mode, label, text]) => (
            <button
              key={mode}
              type="button"
              className={automationMode === mode ? "active" : ""}
              onClick={() => updateAutomation({ mode, enabled: mode !== "manual" })}
              disabled={saving}
            >
              <b>{label}</b>
              <small>{text}</small>
            </button>
          ))}
        </div>
        <div className="switch-grid">
          <label className="check-line"><input type="checkbox" checked={automation.sync_before_run} onChange={(e) => updateAutomation({ sync_before_run: e.target.checked })} />收盘生成计划前同步数据</label>
          <label>交易池开始<input type="time" value={automation.execution_time || "09:30"} onChange={(e) => updateAutomation({ execution_time: e.target.value })} /></label>
          <label>收盘生成<input type="time" value={automation.planning_time || automation.run_time} onChange={(e) => updateAutomation({ planning_time: e.target.value, run_time: e.target.value })} /></label>
        </div>
        <div className="auto-log">
          上次检查：{automation.last_check_at || "暂无"}；交易池：{automation.last_execution_date || "暂无"}；收盘计划：{automation.last_plan_date || automation.last_run_date || "暂无"}；上次结果：{automation.last_message || "暂无"}。
        </div>
        <button className="strategy-entry-card compact-strategy" type="button" onClick={() => setRulesOpen(true)}>
          <span><SlidersHorizontal size={20} />策略与交易规则</span>
          <b>最多 {rules.maxPositions} 只 · 单票 {rules.maxSinglePct}% · 弱/震/强 {rules.weakMaxDailyBuys ?? 1}/{rules.rangeMaxDailyBuys ?? 1}/{rules.strongMaxDailyBuys ?? 2} 买</b>
        </button>
      </CollapsibleSection>
      {rulesOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="rule-modal">
            <div className="section-head">
              <h2>策略与交易规则</h2>
              <button className="icon-btn" type="button" onClick={() => setRulesOpen(false)} title="关闭"><X size={18} /></button>
            </div>
            <div className="rule-summary-grid">
              <div><span>最大持股</span><b>{rules.maxPositions} 只</b><small>已持仓达到上限时不再新增买入</small></div>
              <div><span>买入门槛</span><b>{rules.minScore} 分</b><small>候选评分低于门槛不会买</small></div>
              <div><span>单票预算</span><b>{rules.maxSinglePct}%</b><small>按初始资金计算，再取整到100股</small></div>
              <div><span>动态买入</span><b>{rules.dynamicBuyEnabled !== false ? "开启" : "关闭"}</b><small>按市场强弱控制买入次数和新增仓位</small></div>
              <div><span>弱市新增</span><b>{rules.allowWeakMarketBuy ? `${rules.weakMinScore ?? 88} 分起` : "默认暂停"}</b><small>避免在弱势环境反复接飞刀</small></div>
              <div><span>弱市买入</span><b>{rules.weakMaxDailyBuys ?? 1} 笔 / {rules.weakMaxDailyBuyPct ?? 5}%</b><small>开启后仍限制为小仓位</small></div>
              <div><span>震荡买入</span><b>{rules.rangeMaxDailyBuys ?? 1} 笔 / {rules.rangeMaxDailyBuyPct ?? 8}%</b><small>维持默认节奏</small></div>
              <div><span>强市买入</span><b>{rules.strongMaxDailyBuys ?? 2} 笔 / {rules.strongMaxDailyBuyPct ?? 15}%</b><small>高分机会可适度放宽</small></div>
              <div><span>状态门槛</span><b>{rules.weakMinScore ?? 88}/{rules.rangeMinScore ?? 85}/{rules.strongMinScore ?? 82}</b><small>弱市 / 震荡 / 强市</small></div>
              <div><span>亏损保护</span><b>{rules.maxDailyLossPct ?? 1.5}% / {rules.maxLossStreak ?? 3}日</b><small>达到阈值暂停新增买入</small></div>
              <div><span>每日卖出</span><b>{rules.maxDailySells ?? 5} 笔</b><small>风控优先处理，不被买入挤占</small></div>
              <div><span>买入容忍</span><b>{rules.buyPriceTolerancePct ?? 2}%</b><small>高于观察价过多不追买</small></div>
              <div><span>第一止盈</span><b>{rules.takeProfitSellPct ?? 50}%</b><small>先兑现一部分利润</small></div>
              <div><span>移动止损</span><b>{rules.trailingStopPct ?? 4}%</b><small>剩余仓位按阶段高点回撤退出</small></div>
              <div><span>调仓</span><b>{rules.rebalanceEnabled ? `每日 ${rules.maxDailyRebalances ?? 1} 组` : "关闭"}</b><small>满仓时弱换强</small></div>
              <div><span>追加</span><b>{rules.addPositionEnabled ? "开启" : "关闭"}</b><small>盈利后小仓位加码</small></div>
            </div>
            <div className="rule-explain">
              <b>当前买入条件</b>
              <p>盘后只生成次日买入计划；系统会先按弱市、震荡、强市使用不同评分门槛。弱市默认暂停新增买入，次日价格不明显高于观察价、当前没有持仓、还有最大持仓名额、当天动态买入次数和新增仓位未超限，并且现金足够覆盖 100 股和交易费用时才模拟买入。</p>
              <b>当前卖出条件</b>
              <p>盘后对持仓生成止损、止盈或继续持有计划；止损和调仓卖出全仓处理，第一止盈默认卖出一半，剩余仓位按阶段最高价移动止损。模拟成交会计入佣金、过户费、卖出印花税和滑点。</p>
              <b>调仓与追加</b>
              <p>满仓时，如果新候选评分足够高，且现有持仓信号变弱、浮亏或持有多日不走强，系统会生成一组“调仓卖出 + 调仓买入”。追加默认关闭，开启后只在已有持仓盈利且评分继续高时小仓位加码。若单日亏损达到上限，或连续亏损交易日达到保护值，系统只执行风险卖出，不新增买入。</p>
            </div>
            <div className="form-grid simulator-form">
              <label>可用现金<input type="number" value={cash} onChange={(e) => updateCash(e.target.value)} /></label>
              <label>最低评分<input type="number" value={rules.minScore} onChange={(e) => updateRules({ minScore: Number(e.target.value) })} /></label>
              <label>弱市最低评分<input type="number" value={rules.weakMinScore ?? 88} onChange={(e) => updateRules({ weakMinScore: Number(e.target.value) })} /></label>
              <label>震荡最低评分<input type="number" value={rules.rangeMinScore ?? 85} onChange={(e) => updateRules({ rangeMinScore: Number(e.target.value) })} /></label>
              <label>强市最低评分<input type="number" value={rules.strongMinScore ?? 82} onChange={(e) => updateRules({ strongMinScore: Number(e.target.value) })} /></label>
              <label>日亏损上限%<input type="number" step="0.1" value={rules.maxDailyLossPct ?? 1.5} onChange={(e) => updateRules({ maxDailyLossPct: Number(e.target.value) })} /></label>
              <label>连续亏损保护<input type="number" value={rules.maxLossStreak ?? 3} onChange={(e) => updateRules({ maxLossStreak: Number(e.target.value) })} /></label>
              <label className="check-line"><input type="checkbox" checked={rules.allowWeakMarketBuy === true} onChange={(e) => updateRules({ allowWeakMarketBuy: e.target.checked })} />允许弱市高分试探</label>
              <label>最大持仓<input type="number" value={rules.maxPositions} onChange={(e) => updateRules({ maxPositions: Number(e.target.value) })} /></label>
              <label>单票上限%<input type="number" value={rules.maxSinglePct} onChange={(e) => updateRules({ maxSinglePct: Number(e.target.value) })} /></label>
              <label>固定买入上限<input type="number" value={rules.maxDailyBuys ?? 1} onChange={(e) => updateRules({ maxDailyBuys: Number(e.target.value) })} /></label>
              <label>弱市买入笔数<input type="number" value={rules.weakMaxDailyBuys ?? 1} onChange={(e) => updateRules({ weakMaxDailyBuys: Number(e.target.value) })} /></label>
              <label>弱市新增仓位%<input type="number" step="0.5" value={rules.weakMaxDailyBuyPct ?? 5} onChange={(e) => updateRules({ weakMaxDailyBuyPct: Number(e.target.value) })} /></label>
              <label>震荡买入笔数<input type="number" value={rules.rangeMaxDailyBuys ?? 1} onChange={(e) => updateRules({ rangeMaxDailyBuys: Number(e.target.value) })} /></label>
              <label>震荡新增仓位%<input type="number" step="0.5" value={rules.rangeMaxDailyBuyPct ?? 8} onChange={(e) => updateRules({ rangeMaxDailyBuyPct: Number(e.target.value) })} /></label>
              <label>强市买入笔数<input type="number" value={rules.strongMaxDailyBuys ?? 2} onChange={(e) => updateRules({ strongMaxDailyBuys: Number(e.target.value) })} /></label>
              <label>强市新增仓位%<input type="number" step="0.5" value={rules.strongMaxDailyBuyPct ?? 15} onChange={(e) => updateRules({ strongMaxDailyBuyPct: Number(e.target.value) })} /></label>
              <label>每日卖出上限<input type="number" value={rules.maxDailySells ?? 5} onChange={(e) => updateRules({ maxDailySells: Number(e.target.value) })} /></label>
              <label>买入容忍涨幅%<input type="number" value={rules.buyPriceTolerancePct ?? 2} onChange={(e) => updateRules({ buyPriceTolerancePct: Number(e.target.value) })} /></label>
              <label>止盈卖出比例%<input type="number" value={rules.takeProfitSellPct ?? 50} onChange={(e) => updateRules({ takeProfitSellPct: Number(e.target.value) })} /></label>
              <label>移动止损回撤%<input type="number" step="0.5" value={rules.trailingStopPct ?? 4} onChange={(e) => updateRules({ trailingStopPct: Number(e.target.value) })} /></label>
              <label>最小剩余股数<input type="number" step="100" value={rules.minRemainLot ?? 100} onChange={(e) => updateRules({ minRemainLot: Number(e.target.value) })} /></label>
              <label>佣金率<input type="number" step="0.0001" value={rules.commissionRate ?? 0.0003} onChange={(e) => updateRules({ commissionRate: Number(e.target.value) })} /></label>
              <label>最低佣金<input type="number" value={rules.minCommission ?? 5} onChange={(e) => updateRules({ minCommission: Number(e.target.value) })} /></label>
              <label>印花税率<input type="number" step="0.0001" value={rules.stampTaxRate ?? 0.0005} onChange={(e) => updateRules({ stampTaxRate: Number(e.target.value) })} /></label>
              <label>滑点%<input type="number" step="0.05" value={rules.slippagePct ?? 0.15} onChange={(e) => updateRules({ slippagePct: Number(e.target.value) })} /></label>
              <label>新候选调仓分<input type="number" value={rules.rebalanceMinNewScore ?? 88} onChange={(e) => updateRules({ rebalanceMinNewScore: Number(e.target.value) })} /></label>
              <label>调仓分差<input type="number" value={rules.rebalanceMinScoreGap ?? 8} onChange={(e) => updateRules({ rebalanceMinScoreGap: Number(e.target.value) })} /></label>
              <label>每日调仓组数<input type="number" value={rules.maxDailyRebalances ?? 1} onChange={(e) => updateRules({ maxDailyRebalances: Number(e.target.value) })} /></label>
              <label>弱势持有天数<input type="number" value={rules.weakHoldDays ?? 5} onChange={(e) => updateRules({ weakHoldDays: Number(e.target.value) })} /></label>
              <label>弱势收益%<input type="number" step="0.5" value={rules.weakReturnPct ?? -2.5} onChange={(e) => updateRules({ weakReturnPct: Number(e.target.value) })} /></label>
              <label>追加盈利%<input type="number" step="0.5" value={rules.addMinProfitPct ?? 4} onChange={(e) => updateRules({ addMinProfitPct: Number(e.target.value) })} /></label>
              <label>追加评分<input type="number" value={rules.addMinScore ?? 88} onChange={(e) => updateRules({ addMinScore: Number(e.target.value) })} /></label>
              <label>追加仓位%<input type="number" step="0.5" value={rules.addPositionPct ?? 3} onChange={(e) => updateRules({ addPositionPct: Number(e.target.value) })} /></label>
              <label className="check-line"><input type="checkbox" checked={rules.rebalanceEnabled !== false} onChange={(e) => updateRules({ rebalanceEnabled: e.target.checked })} />启用调仓</label>
              <label className="check-line"><input type="checkbox" checked={rules.addPositionEnabled === true} onChange={(e) => updateRules({ addPositionEnabled: e.target.checked })} />启用追加</label>
              <label className="check-line"><input type="checkbox" checked={rules.dynamicBuyEnabled !== false} onChange={(e) => updateRules({ dynamicBuyEnabled: e.target.checked })} />启用动态买入</label>
              <label className="check-line"><input type="checkbox" checked={rules.sellPriority !== false} onChange={(e) => updateRules({ sellPriority: e.target.checked })} />卖出优先</label>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function CalculatorPanel({ seed, candidates = [], onSelectStock }) {
  const state = useAsync(api.simulationState, []);
  const [sim, setSim] = useState(null);
  const [manualMsg, setManualMsg] = useState("");
  const [modal, setModal] = useState("");
  const [refreshingQuotes, setRefreshingQuotes] = useState(false);
  const [form, setForm] = useState({ symbol: "", name: "", buyPrice: "", quantity: "100", currentPrice: "" });
  const [calc, setCalc] = useState({ cash: 100000, price: 20, stop: 19, riskPct: 1, feeRate: 0.0003 });

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
  const rules = sim?.rules || DEFAULT_SIM_RULES;
  const cash = Number(sim?.cash || 0);
  const openPositions = positions.filter((p) => p.status !== "closed");
  const closedPositions = positions.filter((p) => p.status === "closed");

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

  const totals = positions.reduce((acc, p) => {
    const stat = calcPosition(p);
    if (p.status !== "closed") {
      acc.cost += stat.cost;
      acc.market += stat.market;
      acc.floatPnl += stat.pnl;
    } else {
      acc.realized += stat.pnl;
    }
    return acc;
  }, { cost: 0, market: 0, floatPnl: 0, realized: 0 });
  const summary = sim?.account_summary || {};
  const totalAssets = Number(summary.total_assets ?? (cash + totals.market));
  const marketValue = Number(summary.market_value ?? totals.market);
  const floatPnl = Number(summary.float_pnl ?? totals.floatPnl);
  const realizedPnl = Number(summary.realized_pnl ?? totals.realized);
  const positionPct = Number(summary.position_pct ?? (totalAssets ? (marketValue / totalAssets) * 100 : 0));
  const positionSymbols = openPositions.map((p) => p.symbol);
  const closedSymbols = closedPositions.map((p) => p.symbol);

  const addManual = () => {
    setManualMsg("手动补录已放在仓位页，当前版本先用于记录计划；后续会接入保存、影响现金和交易流水。");
  };

  const refreshQuotes = async () => {
    setRefreshingQuotes(true);
    setManualMsg("");
    try {
      const next = await api.refreshQuotes();
      setSim(next);
      setManualMsg(`${next.message || "实时价格已刷新"} · 更新时间 ${formatTime(next.updated_at)}`);
    } catch (err) {
      setManualMsg(err.message || "实时价格刷新失败");
    } finally {
      setRefreshingQuotes(false);
    }
  };

  const riskAmount = Number(calc.cash) * Number(calc.riskPct) / 100;
  const perShareRisk = Math.max(0, Number(calc.price) - Number(calc.stop));
  const riskShares = perShareRisk ? Math.floor(riskAmount / perShareRisk / 100) * 100 : 0;
  const maxShares = Math.floor((Number(calc.cash) * 0.1) / Number(calc.price) / 100) * 100;
  const shares = Math.max(0, Math.min(riskShares, maxShares));
  const cost = shares * Number(calc.price);
  const fee = cost * Number(calc.feeRate);

  if (state.loading || !sim) return <section className="panel"><h2>仓位持仓</h2><div className="empty">加载中...</div></section>;
  if (state.error) return <section className="panel"><h2>仓位持仓</h2><div className="empty">{state.error}</div></section>;

  return (
    <section className="panel wide-panel">
      <PositionSummaryText
        totalAssets={totalAssets}
        cash={Number(summary.cash ?? cash)}
        marketValue={marketValue}
        floatPnl={floatPnl}
        realizedPnl={realizedPnl}
        positionPct={positionPct}
        openCount={openPositions.length}
        maxPositions={rules.maxPositions}
        updatedAt={summary.updated_at || sim.updated_at}
      />

      <div className="broker-actions">
        <button type="button" onClick={() => setModal("manual")}>补录</button>
        <button type="button" onClick={() => setModal("calc")}>计算</button>
        <button type="button" onClick={() => setModal("trades")}>成交</button>
        <button type="button" onClick={refreshQuotes} disabled={refreshingQuotes}><RefreshCw size={15} />{refreshingQuotes ? "刷新中" : "刷新"}</button>
      </div>
      {manualMsg && <div className="auto-log compact-log">{manualMsg}</div>}

      <CollapsibleSection title="持仓股票" meta={`${openPositions.length} 只持仓`} defaultOpen>
        {openPositions.length === 0 ? <div className="empty compact">暂无持仓。在“模拟交易”执行今日自动模拟后，系统会按候选和规则生成持仓。</div> : (
          <div className="holding-table">
            <div className="holding-head">
              <span>股票</span><span>市值/盈亏</span><span>持仓/可用</span><span>成本/现价</span>
            </div>
            {openPositions.map((p) => {
              const stat = calcPosition(p);
              return (
                <button className="holding-row" key={p.id} onClick={() => onSelectStock?.(p.symbol, positionSymbols, "calc")}>
                  <span><b>{p.name}</b><small>{p.symbol} · 持仓中</small></span>
                  <span><b>{stat.market.toFixed(2)}</b><small className={stat.pnl >= 0 ? "profit" : "loss"}>{stat.pnl.toFixed(2)} / {stat.pnlPct.toFixed(2)}%</small></span>
                  <span><b>{p.quantity}</b><small>可用 {p.quantity}</small></span>
                  <span><b>{Number(p.buyPrice).toFixed(2)}</b><small>{Number(p.currentPrice || p.buyPrice).toFixed(2)}</small></span>
                </button>
              );
            })}
          </div>
        )}
      </CollapsibleSection>

      <CollapsibleSection title="清仓记录" meta={`${closedPositions.length} 只已清仓 · 已实现 ${realizedPnl.toFixed(2)}`} defaultOpen={false}>
        {closedPositions.length === 0 ? <div className="empty compact">暂无清仓记录。</div> : (
          <div className="holding-table closed-holding-table">
            <div className="holding-head closed-holding-head">
              <span>股票</span><span>实现盈亏</span><span>买入/卖出</span><span>日期/原因</span>
            </div>
            {closedPositions.map((p) => {
              const stat = calcPosition(p);
              const sellPrice = Number(p.sellPrice || p.currentPrice || p.buyPrice || 0);
              return (
                <button className="holding-row closed" key={p.id} onClick={() => onSelectStock?.(p.symbol, closedSymbols, "calc")}>
                  <span><b>{p.name}</b><small>{p.symbol} · {p.quantity}股</small></span>
                  <span><b className={stat.pnl >= 0 ? "profit" : "loss"}>{stat.pnl.toFixed(2)}</b><small className={stat.pnlPct >= 0 ? "profit" : "loss"}>{stat.pnlPct.toFixed(2)}%</small></span>
                  <span><b>{Number(p.buyPrice).toFixed(2)} / {sellPrice.toFixed(2)}</b><small>成本 / 清仓</small></span>
                  <span><b>{p.closedAt || "未记录"}</b><small>{p.exitReason || "已清仓"} · 开仓 {p.createdAt || "未知"}</small></span>
                </button>
              );
            })}
          </div>
        )}
      </CollapsibleSection>

      {modal && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="rule-modal">
            <div className="section-head">
              <h2>{modal === "manual" ? "手动补录" : modal === "calc" ? "仓位计算" : modal === "trades" ? "成交记录" : "交易规则"}</h2>
              <button className="icon-btn" type="button" onClick={() => setModal("")} title="关闭"><X size={18} /></button>
            </div>
            {modal === "manual" && (
              <>
                <div className="form-grid simulator-form">
                  <label>代码<input value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} /></label>
                  <label>名称<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
                  <label>买入价<input type="number" value={form.buyPrice} onChange={(e) => setForm({ ...form, buyPrice: e.target.value })} /></label>
                  <label>数量<input type="number" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></label>
                  <label>当前价<input type="number" value={form.currentPrice} onChange={(e) => setForm({ ...form, currentPrice: e.target.value })} /></label>
                </div>
                <button className="primary" onClick={addManual}>补录说明</button>
                {manualMsg && <div className="auto-log">{manualMsg}</div>}
              </>
            )}
            {modal === "calc" && (
              <>
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
              </>
            )}
            {modal === "trades" && (
              <div className="trade-list no-top">
                {trades.length === 0 && <div className="empty compact">暂无成交记录。</div>}
                {trades.slice(0, 50).map((t) => (
                  <div key={t.id}>{t.trade_date} {t.action} {t.name} {t.symbol} {t.quantity}股 @ {t.price}，成交 {Number(t.amount).toFixed(2)}，费用 {Number(t.fee || 0).toFixed(2)}，税 {Number(t.tax || 0).toFixed(2)}，净额 {Number(t.net_amount || t.amount).toFixed(2)}，{t.reason}</div>
                ))}
              </div>
            )}
            {modal === "rules" && (
              <div className="rule-summary-grid">
                <div><span>最低评分</span><b>{rules.minScore}</b><small>低于门槛不买入</small></div>
                <div><span>最大持仓</span><b>{rules.maxPositions} 只</b><small>达到上限不新增</small></div>
                <div><span>单票上限</span><b>{rules.maxSinglePct}%</b><small>按初始资金控制</small></div>
                <div><span>动态买入</span><b>{rules.dynamicBuyEnabled !== false ? "开启" : "关闭"}</b><small>按市场状态调整开仓频次</small></div>
                <div><span>弱/震/强</span><b>{rules.weakMaxDailyBuys ?? 1}/{rules.rangeMaxDailyBuys ?? 1}/{rules.strongMaxDailyBuys ?? 2} 笔</b><small>新增仓位 {rules.weakMaxDailyBuyPct ?? 5}%/{rules.rangeMaxDailyBuyPct ?? 8}%/{rules.strongMaxDailyBuyPct ?? 15}%</small></div>
                <div><span>每日卖出</span><b>{rules.maxDailySells ?? 5} 笔</b><small>风控优先</small></div>
                <div><span>买入容忍</span><b>{rules.buyPriceTolerancePct ?? 2}%</b><small>高开过多不追</small></div>
                <div><span>第一止盈</span><b>{rules.takeProfitSellPct ?? 50}%</b><small>先兑现一部分利润</small></div>
                <div><span>移动止损</span><b>{rules.trailingStopPct ?? 4}%</b><small>剩余仓位跟踪阶段高点</small></div>
                <div><span>调仓</span><b>{rules.rebalanceEnabled ? `每日 ${rules.maxDailyRebalances ?? 1} 组` : "关闭"}</b><small>弱换强</small></div>
                <div><span>追加</span><b>{rules.addPositionEnabled ? "开启" : "关闭"}</b><small>盈利后加码</small></div>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function WatchlistPanel({ refreshKey, onSelect }) {
  const list = useAsync(api.watchlist, [refreshKey]);
  const [symbol, setSymbol] = useState("");
  const items = list.data || [];
  const holdingCount = items.filter((item) => item.latest_signal?.decision === "hold").length;
  const add = async () => {
    if (!symbol.trim()) return;
    await api.addWatch({ symbol, note: "" });
    setSymbol("");
    window.dispatchEvent(new Event("watch-refresh"));
  };
  return (
    <section className="panel watch-page">
      <div className="watch-toolbar">
        <div className="watch-search"><span>⌕</span><input placeholder="搜索股票" value={symbol} onChange={(e) => setSymbol(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") add(); }} /></div>
        <button className="ghost inline" onClick={add}>编辑</button>
      </div>
      <div className="watch-tabs">
        <span className="active">全部({items.length})</span>
        <span>观察中({Math.max(0, items.length - holdingCount)})</span>
        <span>持仓({holdingCount})</span>
      </div>
      {list.loading ? <div className="empty">加载中...</div> : !items.length ? <div className="empty">暂无自选股。</div> : (
        <div className="watch-list">
          {items.map((item) => {
            const price = item.realtime?.price ?? item.latest_close ?? 0;
            const pct = Number(item.realtime?.pct_chg ?? 0);
            const watchPct = item.watch_return_pct;
            const score = item.latest_signal?.score;
            return (
              <div className="watch-line" key={item.symbol} onClick={() => onSelect(item.symbol)}>
                <div className="watch-line-main">
                  <b>{item.name}</b>
                  <small>{item.symbol}</small>
                </div>
                <MiniSparkline points={item.sparkline || []} />
                <div className="watch-price"><b>{price ? Number(price).toFixed(2) : "--"}</b><small className={pct >= 0 ? "profit" : "loss"}>{item.realtime ? `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%` : "--"}</small></div>
                <div className="watch-score"><b>{score ?? "--"}</b><small>{item.latest_signal?.strategy_tags?.[0] || "观察"}</small></div>
                <button className="watch-star active-watch" onClick={(e) => { e.stopPropagation(); api.deleteWatch(item.symbol).then(() => window.dispatchEvent(new Event("watch-refresh"))); }} title="移除自选"><Star size={17} fill="currentColor" /></button>
                <div className="watch-extra">
                  <span>自选收益 <b className={Number(watchPct || 0) >= 0 ? "profit" : "loss"}>{watchPct === null || watchPct === undefined ? "--" : `${Number(watchPct).toFixed(2)}%`}</b></span>
                  <span>加入价 <b>{Number(item.added_price || 0) > 0 ? Number(item.added_price).toFixed(2) : "--"}</b></span>
                  <span>自选时间 <b>{(item.created_at || "").slice(0, 10)}</b></span>
                  <span>更新 <b>{formatTime(item.realtime?.updated_at || item.latest_bar_date || "")}</b></span>
                </div>
              </div>
            );
          })}
        </div>
      )}
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

function DisplaySettings({ fontSize, onFontSizeChange, themeMode, onThemeModeChange }) {
  return (
    <section>
      <div className="section-head"><h2>显示设置</h2><Settings size={20} /></div>
      <div className="display-settings">
        <label>显示模式
          <select value={themeMode} onChange={(e) => onThemeModeChange(e.target.value)}>
            <option value="dark">夜间模式</option>
            <option value="light">白天模式</option>
          </select>
        </label>
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

function AISettingsPanel() {
  const state = useAsync(api.aiSettings, []);
  const [settings, setSettings] = useState(null);
  const [apiKey, setApiKey] = useState("");
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);
  const modelOptions = settings?.provider === "deepseek"
    ? [
      ["deepseek-v4-pro", "DeepSeek V4 Pro · 1M"],
      ["deepseek-v4-flash", "DeepSeek V4 Flash · 1M"],
      ["deepseek-chat", "DeepSeek Chat · 旧版"],
    ]
    : [
      ["gpt-4o-mini", "gpt-4o-mini"],
      ["gpt-4o", "gpt-4o"],
    ];

  useEffect(() => {
    if (state.data) setSettings(state.data);
  }, [state.data]);

  const update = async (patch) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    setSaving(true);
    setMsg("");
    try {
      const payload = { ...patch };
      if (Object.prototype.hasOwnProperty.call(patch, "api_key")) payload.api_key = patch.api_key;
      const saved = await api.updateAiSettings(payload);
      setSettings(saved);
      setApiKey("");
      setMsg("AI 设置已保存");
    } catch (err) {
      setMsg(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (state.loading || !settings) return <div className="empty compact">加载 AI 设置...</div>;
  if (state.error) return <div className="empty compact">{state.error}</div>;

  return (
    <section>
      <div className="ai-settings-head">
        <div>
          <b>{settings.enabled ? "AI 审核已启用" : "AI 审核未启用"}</b>
          <small>Key 状态：{settings.has_api_key ? "已配置" : "未配置"} · {settings.provider} · {settings.model}</small>
        </div>
        <label className="check-line"><input type="checkbox" checked={settings.enabled} onChange={(e) => update({ enabled: e.target.checked })} disabled={saving} />启用 AI</label>
      </div>
      <div className="form-grid ai-form">
        <label>服务商
          <select value={settings.provider} onChange={(e) => {
            const provider = e.target.value;
            update({
              provider,
              model: provider === "deepseek" ? "deepseek-v4-pro" : "gpt-4o-mini",
              base_url: provider === "deepseek" ? "https://api.deepseek.com" : "https://api.openai.com/v1",
            });
          }}>
            <option value="deepseek">DeepSeek</option>
            <option value="openai">OpenAI</option>
          </select>
        </label>
        <label>API Key
          <input type="password" placeholder={settings.has_api_key ? "已保存，输入新 key 可覆盖" : "粘贴 API key"} value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        </label>
        <div className="key-mask">
          <span>{settings.masked_key || "未配置"}</span>
          <button className="ghost inline" type="button" disabled={saving || !settings.has_api_key} onClick={() => update({ api_key: "" })}>清空</button>
        </div>
        <label>模型
          <select value={settings.model} onChange={(e) => update({ model: e.target.value })}>
            {modelOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label>Base URL
          <input value={settings.base_url || ""} onChange={(e) => setSettings({ ...settings, base_url: e.target.value })} onBlur={(e) => update({ base_url: e.target.value })} />
        </label>
        <button className="primary" type="button" disabled={saving || !apiKey.trim()} onClick={() => update({ api_key: apiKey, enabled: true })}>保存 Key</button>
      </div>
      <div className="switch-grid">
        <label className="check-line"><input type="checkbox" checked={settings.daily_review_enabled} onChange={(e) => update({ daily_review_enabled: e.target.checked })} disabled={saving} />每日总结</label>
        <label className="check-line"><input type="checkbox" checked={settings.plan_review_enabled} onChange={(e) => update({ plan_review_enabled: e.target.checked })} disabled={saving} />计划审核</label>
        <label className="check-line"><input type="checkbox" checked={settings.stock_review_enabled} onChange={(e) => update({ stock_review_enabled: e.target.checked })} disabled={saving} />个股分析</label>
      </div>
      <label className="check-line"><input type="checkbox" checked={settings.block_trade_enabled} onChange={(e) => update({ block_trade_enabled: e.target.checked })} disabled={saving} />AI 最终决策</label>
        <div className="empty compact">API key 只保存在后端数据库，前端不会回显。开启后，非风险退出计划以 AI 方向为准：approve 可进入执行守门，medium 只标记谨慎，caution 进入条件执行，最终仍由价格、额度和风控规则触发；high/reject 阻断，止损和弱势退出仍按风控规则优先执行。</div>
      {msg && <div className="auto-log compact-log">{msg}</div>}
    </section>
  );
}

function QMTBrokerPanel() {
  const state = useAsync(api.qmtStatus, []);
  const [snapshot, setSnapshot] = useState(null);
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const status = snapshot || state.data;

  const refreshAccount = async () => {
    setLoading(true);
    setMsg("");
    try {
      const next = await api.qmtAccount();
      setSnapshot(next);
      setMsg(next.message || "QMT 状态已刷新");
    } catch (err) {
      setMsg(err.message || "QMT 读取失败");
    } finally {
      setLoading(false);
    }
  };

  const checkLiveGuard = async () => {
    setLoading(true);
    setMsg("");
    try {
      const result = await api.qmtLiveOrderGuard();
      setMsg(result.message || "真实下单保护已检查");
    } catch (err) {
      setMsg(err.message || "真实下单保护检查失败");
    } finally {
      setLoading(false);
    }
  };

  if (state.loading && !status) return <div className="empty compact">加载 QMT 状态...</div>;
  if (state.error) return <div className="empty compact">{state.error}</div>;

  const config = status?.config || {};
  const asset = status?.asset;
  const positions = status?.positions || [];
  return (
    <section>
      <div className="broker-status-head">
        <div>
          <b>QMT / miniQMT</b>
          <small>{status?.ready ? "配置就绪" : "尚未就绪"} · {status?.connected ? "已连接账户" : "未连接账户"}</small>
        </div>
        <span className={status?.ready ? "state-pill on" : "state-pill"}>{status?.ready ? "ready" : "setup"}</span>
      </div>
      <div className="broker-check-grid">
        <div><span>QMT 开关</span><b>{config.enabled ? "已启用" : "未启用"}</b><small>QMT_ENABLED</small></div>
        <div><span>账号</span><b>{config.account_id_configured ? "已配置" : "未配置"}</b><small>QMT_ACCOUNT_ID</small></div>
        <div><span>UserData</span><b>{config.user_data_path_configured ? "已配置" : "未配置"}</b><small>QMT_USER_DATA_PATH</small></div>
        <div><span>真实下单</span><b>{config.live_order_enabled ? "开关已开" : "关闭"}</b><small>默认保护</small></div>
      </div>
      {status?.missing?.length > 0 && (
        <div className="decision-warning">
          {status.missing.join("；")}
          {status.import_error ? `；${status.import_error}` : ""}
        </div>
      )}
      {asset && (
        <div className="broker-asset-grid">
          <div><span>总资产</span><b>{Number(asset.total_asset || 0).toFixed(2)}</b></div>
          <div><span>可用现金</span><b>{Number(asset.cash || 0).toFixed(2)}</b></div>
          <div><span>持仓市值</span><b>{Number(asset.market_value || 0).toFixed(2)}</b></div>
          <div><span>冻结资金</span><b>{Number(asset.frozen_cash || 0).toFixed(2)}</b></div>
        </div>
      )}
      {positions.length > 0 && (
        <div className="broker-position-list">
          {positions.slice(0, 8).map((item) => (
            <div key={item.stock_code}>
              <b>{item.stock_code}</b>
              <span>{item.volume} 股 · 可用 {item.can_use_volume} · 市值 {Number(item.market_value || 0).toFixed(2)}</span>
            </div>
          ))}
        </div>
      )}
      <div className="button-row">
        <button className="primary" type="button" onClick={refreshAccount} disabled={loading}>读取 QMT 账户</button>
        <button className="ghost inline" type="button" onClick={checkLiveGuard} disabled={loading}>检查真实下单保护</button>
      </div>
      <div className="empty compact">先启动并登录 MiniQMT，再读取账户。当前版本只做只读接入和真实下单保护，不会自动提交委托。</div>
      {msg && <div className="auto-log compact-log">{msg}</div>}
    </section>
  );
}

function ConfigPanel({ fontSize, onFontSizeChange, themeMode, onThemeModeChange }) {
  const [modal, setModal] = useState("");
  const modeText = themeMode === "light" ? "浅色模式" : "深色模式";
  const modalTitle = {
    account: "账户信息",
    password: "修改密码",
    security: "安全设置",
    display: "主题模式",
    notification: "消息通知",
    refresh: "数据刷新频率",
    language: "语言设置",
    strategy: "策略参数配置",
    signal: "信号权重设置",
    risk: "风险控制设置",
    ai: "AI 审核配置",
    qmt: "QMT 实盘接入",
    backtest: "历史回测",
    help: "帮助中心",
    feedback: "意见反馈",
    about: "关于 AStockPilot",
  }[modal];
  const open = (key) => setModal(key);
  const item = (key, Icon, label, value = "") => (
    <button className="settings-item" type="button" onClick={() => open(key)}>
      <span className="settings-icon"><Icon size={20} /></span>
      <span className="settings-label">{label}</span>
      {value && <em>{value}</em>}
      <b>›</b>
    </button>
  );
  return (
    <section className="settings-page">
      <div className="settings-title">设置 / 配置</div>
      <div className="settings-group">
        <h3>账户与安全</h3>
        {item("account", UserRound, "账户信息")}
        {item("password", LockKeyhole, "修改密码")}
        {item("security", ShieldCheck, "安全设置")}
      </div>
      <div className="settings-group">
        <h3>系统设置</h3>
        {item("display", Moon, "主题模式", modeText)}
        {item("notification", Bell, "消息通知")}
        {item("refresh", RefreshCw, "数据刷新频率", "实时")}
        {item("language", Globe2, "语言设置", "简体中文")}
      </div>
      <div className="settings-group">
        <h3>量化策略</h3>
        {item("strategy", SlidersHorizontal, "策略参数配置")}
        {item("signal", Sparkles, "信号权重设置")}
        {item("risk", ShieldCheck, "风险控制设置")}
        {item("ai", Bot, "AI 审核配置")}
        {item("qmt", KeyRound, "QMT 实盘接入")}
        {item("backtest", BarChart3, "历史回测")}
      </div>
      <div className="settings-group">
        <h3>关于我们</h3>
        {item("help", HelpCircle, "帮助中心")}
        {item("feedback", Mail, "意见反馈")}
        {item("about", Info, "关于 AStockPilot", "v2.1.0")}
      </div>
      {modal && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="rule-modal settings-modal">
            <div className="section-head">
              <h2>{modalTitle}</h2>
              <button className="icon-btn" type="button" onClick={() => setModal("")} title="关闭"><X size={18} /></button>
            </div>
            {["account", "password", "security"].includes(modal) && (
              <div className="placeholder-panel">
                <UserRound size={34} />
                <b>{modalTitle}</b>
                <p>账号体系暂未实装。后续接入登录、权限、密码和安全审计后，这里会显示真实账户信息。</p>
              </div>
            )}
            {modal === "display" && <DisplaySettings fontSize={fontSize} onFontSizeChange={onFontSizeChange} themeMode={themeMode} onThemeModeChange={onThemeModeChange} />}
            {modal === "notification" && <div className="placeholder-panel"><Bell size={34} /><b>消息通知</b><p>暂按系统默认提示。后续可配置交易计划、AI 审核、风控触发和数据同步失败提醒。</p></div>}
            {modal === "refresh" && <div className="placeholder-panel"><RefreshCw size={34} /><b>实时刷新</b><p>交易时间内行情和持仓按当前自动化任务刷新；手动刷新入口保留在持仓页。</p></div>}
            {modal === "language" && <div className="placeholder-panel"><Globe2 size={34} /><b>简体中文</b><p>当前仅启用简体中文界面。</p></div>}
            {modal === "strategy" && <StrategyPanel />}
            {modal === "signal" && <StrategyPanel />}
            {modal === "risk" && <StrategyPanel />}
            {modal === "ai" && <AISettingsPanel />}
            {modal === "qmt" && <QMTBrokerPanel />}
            {modal === "backtest" && <BacktestPanel />}
            {modal === "help" && <div className="placeholder-panel"><HelpCircle size={34} /><b>帮助中心</b><p>常见问题和操作说明后续整合到这里。</p></div>}
            {modal === "feedback" && <div className="placeholder-panel"><Mail size={34} /><b>意见反馈</b><p>反馈通道暂未接入，可先在当前会话中直接描述问题。</p></div>}
            {modal === "about" && <div className="placeholder-panel"><Info size={34} /><b>AStockPilot v2.1.0</b><p>A 股短线候选、模拟交易、AI 审核和实盘接入工作台。</p></div>}
          </div>
        </div>
      )}
    </section>
  );
}

function App() {
  const tabs = [
    ["dashboard", "首页", LayoutDashboard],
    ["watch", "自选", Star],
    ["sim", "交易", WalletCards],
    ["calc", "仓位", Calculator],
    ["config", "设置", Settings],
  ];
  const [activeTab, setActiveTab] = useState("dashboard");
  const [detailSymbol, setDetailSymbol] = useState("");
  const [detailSymbols, setDetailSymbols] = useState([]);
  const [detailBackTab, setDetailBackTab] = useState("dashboard");
  const [refresh, setRefresh] = useState(0);
  const [simSeed, setSimSeed] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [workflowing, setWorkflowing] = useState(false);
  const [syncMsg, setSyncMsg] = useState("");
  const [fontSize, setFontSize] = useStoredNumber("astockpilot-font-size", 14);
  const [themeMode, setThemeMode] = useStoredString("astockpilot-theme", "light");
  useEffect(() => {
    const next = Math.min(16, Math.max(12, Number(fontSize) || 14));
    document.documentElement.style.setProperty("--app-font-size", `${next}px`);
  }, [fontSize]);
  useEffect(() => {
    document.documentElement.dataset.theme = themeMode === "light" ? "light" : "dark";
  }, [themeMode]);
  const candidates = useAsync(api.candidates, [refresh]);
  const dataStatus = useAsync(api.dataStatus, [refresh]);
  const [watchRefresh, setWatchRefresh] = useState(0);
  useEffect(() => {
    const fn = () => setWatchRefresh((v) => v + 1);
    window.addEventListener("watch-refresh", fn);
    return () => window.removeEventListener("watch-refresh", fn);
  }, []);
  const watchlist = useAsync(api.watchlist, [watchRefresh]);
  const watchSymbols = useMemo(() => (watchlist.data || []).map((item) => item.symbol), [watchlist.data]);
  const topDate = useMemo(() => candidates.data?.[0]?.trade_date || dataStatus.data?.daily?.latest_date || "暂无数据", [candidates.data, dataStatus.data]);
  const sync = async () => {
    setSyncing(true);
    setSyncMsg("");
    try {
      const res = await api.sync({ days: 730, symbol_limit: 80 });
      const refreshed = res.positions_refreshed?.updated ? `，刷新持仓 ${res.positions_refreshed.updated} 只` : "";
      const realtime = res.realtime_refreshed?.updated
        ? `，实时 ${res.realtime_refreshed.updated} 只`
        : res.realtime_refreshed?.status === "skipped"
          ? "，实时价单独刷新"
          : res.realtime_refreshed?.status === "error" ? "，实时失败" : "";
      const requested = res.requested_symbols ? `请求 ${res.requested_symbols} 只，` : "";
      const failed = res.failed_symbols ? `，失败 ${res.failed_symbols} 只` : "";
      const latest = res.daily_status?.latest_date ? `，库内最新 ${res.daily_status.latest_date}/${res.daily_status.latest_symbols || 0} 只` : "";
      const firstError = res.errors?.length ? `；首个错误：${shortSyncError(res.errors[0])}` : "";
      setSyncMsg(`${requested}同步 ${res.synced_symbols} 只，${res.bars} 条日线，${res.signals} 个信号${failed}${latest}${refreshed}${realtime}，更新时间 ${formatTime(res.updated_at)}${firstError}`);
      setRefresh((v) => v + 1);
    } catch (err) {
      setSyncMsg(err.message);
    } finally {
      setSyncing(false);
    }
  };
  const toggleWatch = async (symbol, name = "", isWatched = false) => {
    if (!symbol) return;
    try {
      if (isWatched) {
        await api.deleteWatch(symbol);
        setSyncMsg(`${name || symbol} 已取消自选`);
      } else {
        await api.addWatch({ symbol, note: "" });
        setSyncMsg(`${name || symbol} 已加入自选`);
      }
      setWatchRefresh((v) => v + 1);
    } catch (err) {
      setSyncMsg(`${isWatched ? "取消" : "加入"}自选失败：${err.message}`);
    }
  };
  const simulateCandidate = (item) => {
    setSimSeed(item);
    setActiveTab("calc");
  };
  const dailyWorkflow = async () => {
    setWorkflowing(true);
    setSyncMsg("");
    try {
      const res = await api.dailyWorkflow({ days: 730, symbol_limit: 80 });
      const syncPart = res.sync || {};
      const planPart = res.plan || {};
      const aiPart = res.ai || {};
      const latest = syncPart.daily_status?.latest_date ? `库内最新 ${syncPart.daily_status.latest_date}/${syncPart.daily_status.latest_symbols || 0} 只` : "库内最新 暂无";
      const planText = planPart.status === "skipped" ? `计划跳过：${planPart.message}` : `生成计划 ${planPart.plans?.length ?? 0} 条`;
      const aiText = aiPart.status === "ok" ? "AI 审核完成" : `AI ${aiPart.message || "未完成"}`;
      setSyncMsg(`每日分析完成：同步 ${syncPart.synced_symbols ?? 0} 只，${syncPart.signals ?? 0} 个信号，${latest}；${planText}；${aiText}`);
      setRefresh((v) => v + 1);
    } catch (err) {
      setSyncMsg(err.message);
    } finally {
      setWorkflowing(false);
    }
  };
  const openDetail = (symbol, symbols = [], backTab = activeTab) => {
    setDetailSymbol(symbol);
    setDetailSymbols(symbols?.length ? symbols : (candidates.data || []).map((item) => item.symbol));
    setDetailBackTab(backTab || activeTab || "dashboard");
    setActiveTab("detail");
  };
  return (
    <main>
      <nav className="tabs bottom-tabs">
        {tabs.map(([key, label, Icon]) => (
          <button key={key} className={activeTab === key ? "active" : ""} onClick={() => setActiveTab(key)}><Icon size={16} />{label}</button>
        ))}
      </nav>
      {activeTab === "dashboard" && (
        <HomeDashboard
          candidates={candidates.data}
          loading={candidates.loading}
          error={candidates.error}
          topDate={topDate}
          syncMsg={syncMsg}
          dataStatus={dataStatus.data}
          watchSymbols={watchSymbols}
          onSelect={openDetail}
          onSimulate={simulateCandidate}
          onWatch={toggleWatch}
        />
      )}
      {activeTab === "detail" && <StockDetailPage symbol={detailSymbol} symbols={detailSymbols} watchSymbols={watchSymbols} onBack={() => setActiveTab(detailBackTab)} onWatch={toggleWatch} onSelectSymbol={setDetailSymbol} />}
      {activeTab === "watch" && <WatchlistPanel refreshKey={watchRefresh} onSelect={openDetail} />}
      {activeTab === "sim" && <SimulatorPanel onSync={sync} onDailyWorkflow={dailyWorkflow} syncing={syncing} workflowing={workflowing} syncMsg={syncMsg} />}
      {activeTab === "calc" && <CalculatorPanel seed={simSeed} candidates={candidates.data || []} onSelectStock={openDetail} />}
      {activeTab === "config" && <ConfigPanel fontSize={fontSize} onFontSizeChange={setFontSize} themeMode={themeMode} onThemeModeChange={setThemeMode} />}
    </main>
  );
}

function shortSyncError(message = "") {
  if (!message) return "";
  const symbol = message.split(":", 1)[0];
  if (message.includes("ProxyError") || message.includes("RemoteDisconnected") || message.includes("HTTPSConnectionPool")) {
    return `${symbol}: 日线源连接失败，已尝试备用源`;
  }
  return message.length > 80 ? `${message.slice(0, 80)}...` : message;
}

createRoot(document.getElementById("root")).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>
);
