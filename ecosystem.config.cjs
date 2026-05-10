module.exports = {
  apps: [
    {
      name: "astockpilot-backend",
      cwd: "E:/kai_project/AStockPilot/backend",
      script: ".venv/Scripts/python.exe",
      args: "-m uvicorn app.main:app --host 0.0.0.0 --port 3019",
      autorestart: true,
      max_restarts: 10,
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
    {
      name: "astockpilot-frontend",
      cwd: "E:/kai_project/AStockPilot/frontend",
      script: "node_modules/vite/bin/vite.js",
      args: "--host 0.0.0.0 --port 3009",
      autorestart: true,
      max_restarts: 10,
    },
  ],
};
