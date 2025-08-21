#!/usr/bin/env bash
set -e
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
# load .env if present
export $(grep -v '^#' .env 2>/dev/null | xargs -d '\n' -r)
uvicorn app:app --host 0.0.0.0 --port 8000
