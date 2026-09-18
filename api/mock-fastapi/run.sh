#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# app.py loads the optional .env; the caller installs dependencies once.
exec python3 -m uvicorn app:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}"
