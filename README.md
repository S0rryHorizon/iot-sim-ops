# iot-sim-ops

A small end-to-end demo to connect what I learned in the internship:
Linux + MySQL + API testing (Postman) + HTML, with simple ops (systemd & cron backup).

## What it does
- Provide SIM-card query API (e.g., monthly data margin) and lifecycle ops (activate/suspend/throttle/terminate).
- Store data in MySQL; support read/write split at the app layer (demo).
- Test with Postman collections and data-driven runs.
- Minimal HTML page for login, query, and quick recharge.

## Tech stack
Backend: Python (Flask/FastAPI TBD) · DB: MySQL · Test: Postman · Web: HTML+fetch

## Getting started (WIP)
- `db/` schema & seed SQL (coming soon)
- `backend/` minimal API (coming soon)
- `ops/` backup script & systemd unit (coming soon)

## Roadmap (milestones)
- DB schema & 10k sample SIMs
- `/v5/ec/query/sim-data-margin` API
- SIM lifecycle endpoints
- Postman Runner + CSV
- systemd + cron backup (keep last 7)
- HTML page for demo

## License
MIT

## Repository layout (skeleton)
- backend/   # Backend source (Flask/FastAPI), configs, logs/
- db/        # schema.sql, seed.sql, dumps/ (keep last 7)
- web/       # login.html, index.html
- ops/       # backup_db.sh, health_check.sh, myapp.service
- postman/   # collections & environments

## Repository layout (skeleton)
- backend/   # Backend source (Flask/FastAPI), configs, logs/
- db/        # schema.sql, seed.sql, dumps/ (keep last 7)
- web/       # login.html, index.html
- ops/       # backup_db.sh, health_check.sh, myapp.service
- postman/   # collections & environments
