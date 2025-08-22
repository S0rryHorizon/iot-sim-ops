#!/usr/bin/env bash
set -euo pipefail
ROOT="/home/fenrir/iot-sim-ops"
UNIT="iot-sim-ops-api"   # 如果你启用的是 @fenrir，则改成 iot-sim-ops-api@fenrir
LOGDIR="$ROOT/logs"
mkdir -p "$LOGDIR"

# 首次测试可临时导出更长时间：sudo systemctl start iot-sim-ops-logdump.service DURATION="1 hour ago"
DURATION="${DURATION:-10 min ago}"

ts=$(date +'%Y%m%d_%H%M')
outfile="$LOGDIR/app-$ts.log"

# 导出过去一段时间（默认10分钟）的 journald 日志到文件
journalctl -u "$UNIT" --since "$DURATION" --no-pager -o short-iso > "$outfile" || true

# 只保留最近10份
ls -1t "$LOGDIR"/app-*.log 2>/dev/null | tail -n +11 | xargs -r rm -f
