# Runs the API (port 8000) and the Vite dev server (port 5173, proxies /api) side by side.
# Usage: .\scripts\dev_ui.ps1   then open http://localhost:5173
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "web\node_modules")) {
    Push-Location web
    npm install --no-audit --no-fund
    Pop-Location
}

$api = Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "-m", "uvicorn", "upshift.api.main:create_app", "--factory", "--port", "8000", "--reload" -PassThru -NoNewWindow
try {
    Push-Location web
    npm run dev
} finally {
    Pop-Location
    if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
}
