#!/usr/bin/env bash
set -euo pipefail

cleanup() {
  if [[ -n "${API_PID:-}" ]]; then
    kill "$API_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

.venv/bin/uvicorn imax_api.main:app --app-dir apps/api/src --reload --host 127.0.0.1 --port 8000 &
API_PID=$!

npm --prefix apps/web run dev -- --host 127.0.0.1
