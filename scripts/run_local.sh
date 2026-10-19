#!/usr/bin/env bash
# Local dev server (macOS/Linux). Creates a venv, installs deps, starts the API with reload.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
pip install -e ".[dev]"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Fill in NEBIUS_API_KEY and TAVILY_API_KEY."
fi

exec uvicorn upshift.api.main:create_app --factory --reload --port 8000
