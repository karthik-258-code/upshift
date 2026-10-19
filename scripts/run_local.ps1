# Local dev server (Windows). Creates a venv, installs deps, starts the API with reload.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip | Out-Null
pip install -e ".[dev]"

if (-not (Test-Path ".env")) {
  Copy-Item .env.example .env
  Write-Host "Created .env from .env.example. Fill in NEBIUS_API_KEY and TAVILY_API_KEY." -ForegroundColor Yellow
}

uvicorn upshift.api.main:create_app --factory --reload --port 8000
