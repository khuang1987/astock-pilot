const API_BASE =
  import.meta.env.VITE_API_BASE ||
  `${window.location.protocol}//${window.location.hostname}:3018`;

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
  sync: (payload) => request("/api/data/sync", { method: "POST", body: JSON.stringify(payload) }),
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
  automationState: () => request("/api/automation/state"),
  updateAutomationState: (payload) => request("/api/automation/state", { method: "POST", body: JSON.stringify(payload) }),
  dailyReports: () => request("/api/reports/daily"),
};
