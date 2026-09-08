#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r backend/requirements.txt
python3 -m ipykernel install --user --name python3 --display-name "Python 3" >/dev/null 2>&1 || true

if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm install)
fi

export PYTHONPATH="$ROOT/backend:${PYTHONPATH:-}"
uvicorn app.main:app --app-dir backend --reload --reload-dir backend --timeout-graceful-shutdown 3 --host 127.0.0.1 --port 8000 &
API_PID=$!
cleanup() { kill "$API_PID" 2>/dev/null || true; }
trap cleanup EXIT

cd frontend
npm run dev
