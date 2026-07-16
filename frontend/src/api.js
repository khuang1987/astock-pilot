const API_BASE =
  import.meta.env.VITE_API_BASE ||
  window.location.origin;

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response.json();
}

export const api = {
  health: () => request("/api/health"),
  dataStatus: () => request("/api/data/status"),
  sync: (payload) => request("/api/data/sync", { method: "POST", body: JSON.stringify(payload) }),
  dailyWorkflow: (payload) => request("/api/daily-workflow", { method: "POST", body: JSON.stringify(payload) }),
  candidates: (date) => request(`/api/stocks/candidates${date ? `?date=${date}` : ""}`),
  stock: (symbol) => request(`/api/stocks/${symbol}`),
  backtest: (payload) => request("/api/backtests", { method: "POST", body: JSON.stringify(payload) }),
  watchlist: () => request("/api/watchlist"),
  addWatch: (payload) => request("/api/watchlist", { method: "POST", body: JSON.stringify(payload) }),
  deleteWatch: (symbol) => request(`/api/watchlist/${symbol}`, { method: "DELETE" }),
  strategyVersions: () => request("/api/strategy/versions"),
  simulationState: () => request("/api/simulation/state"),
  updateSimulationState: (payload) => request("/api/simulation/state", { method: "POST", body: JSON.stringify(payload) }),
  autoRunSimulation: (force = false) => request(`/api/simulation/auto-run?force=${force}`, { method: "POST" }),
  executePlans: (force = false) => request(`/api/simulation/execute-plans?force=${force}`, { method: "POST" }),
  refreshQuotes: () => request("/api/simulation/refresh-quotes", { method: "POST" }),
  tradePlans: () => request("/api/trade-plans"),
  executePlan: (planId, force = false) => request(`/api/trade-plans/${planId}/execute?force=${force}`, { method: "POST" }),
  aiReviewPlan: (planId) => request(`/api/trade-plans/${planId}/ai-review`, { method: "POST" }),
  automationState: () => request("/api/automation/state"),
  updateAutomationState: (payload) => request("/api/automation/state", { method: "POST", body: JSON.stringify(payload) }),
  dailyReports: () => request("/api/reports/daily"),
  aiSettings: () => request("/api/ai/settings"),
  updateAiSettings: (payload) => request("/api/ai/settings", { method: "POST", body: JSON.stringify(payload) }),
  runAiReview: () => request("/api/ai/review", { method: "POST" }),
  qmtStatus: () => request("/api/broker/qmt/status"),
  qmtAccount: () => request("/api/broker/qmt/account"),
  qmtLiveOrderGuard: () => request("/api/broker/qmt/live-order-guard", { method: "POST" }),
};
