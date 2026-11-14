#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# app.py loads the optional .env; the caller installs dependencies once.
PYTHON_EXECUTABLE="${IOT_PYTHON:-../../.venv/bin/python}"
if [[ ! -x "$PYTHON_EXECUTABLE" ]]; then
  PYTHON_EXECUTABLE=python3
fi
exec "$PYTHON_EXECUTABLE" -m uvicorn app:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}"
