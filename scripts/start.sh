#!/usr/bin/env bash
set -euo pipefail

exec .venv/bin/uvicorn imax_api.main:app --app-dir apps/api/src --host 127.0.0.1 --port 8000
