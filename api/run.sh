#!/usr/bin/env bash
# Start the API from the repository root. Override with IOT_PYTHON, API_HOST, API_PORT.
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${IOT_PYTHON:-.venv/bin/python}" -m uvicorn api.app:app \
  --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}"
