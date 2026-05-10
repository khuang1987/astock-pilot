$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$venv = Join-Path $backend ".venv"

if (-not (Test-Path $venv)) {
    python -m venv $venv
}

& "$venv\Scripts\python.exe" -m pip install --upgrade pip
& "$venv\Scripts\pip.exe" install -r "$backend\requirements.txt"

if (-not (Test-Path (Join-Path $backend ".env"))) {
    Copy-Item (Join-Path $backend ".env.example") (Join-Path $backend ".env")
}

Push-Location $frontend
if (-not (Test-Path "node_modules")) {
    npm install
}
Pop-Location

$backendCmd = "cd /d `"$backend`" && `"$venv\Scripts\python.exe`" -m uvicorn app.main:app --host 0.0.0.0 --port 3019 --reload"
$frontendCmd = "cd /d `"$frontend`" && npm run dev -- --host 0.0.0.0 --port 3009"

Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", $backendCmd) -WindowStyle Normal
Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", $frontendCmd) -WindowStyle Normal

"AStockPilot backend:  http://127.0.0.1:3019"
"AStockPilot frontend: http://127.0.0.1:3009"
