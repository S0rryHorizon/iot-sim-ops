#!/usr/bin/env bash
# 把 API 服务最近一段时间的 journald 日志导出到 logs/，只保留最近 10 份。
# 可用环境变量覆盖：UNIT（服务名）、DURATION（journalctl --since 的值）、LOGDIR。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT="${UNIT:-iot-sim-ops-api}"
DURATION="${DURATION:-10 min ago}"
LOGDIR="${LOGDIR:-$ROOT/logs}"

mkdir -p "$LOGDIR"
outfile="$LOGDIR/app-$(date +'%Y%m%d_%H%M').log"
journalctl -u "$UNIT" --since "$DURATION" --no-pager -o short-iso > "$outfile"

ls -1t "$LOGDIR"/app-*.log | tail -n +11 | xargs -r rm -f
