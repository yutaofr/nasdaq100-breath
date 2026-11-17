#!/bin/bash
# 每日自动更新: 重跑 ndx_breadth.py，数据有更新时把结果发到 Discord；失败时发失败提醒
cd "$(dirname "$0")" || exit 1
PY=/Users/weizhang/miniconda3/bin/python3
LOG=run.log

asof() { head -1 ndx_breadth_futu.py 2>/dev/null | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}'; }

before=$(asof)
echo "=== $(date '+%F %T') ===" >> "$LOG"
if "$PY" ndx_breadth.py >> "$LOG" 2>&1; then
  after=$(asof)
  if [ "$after" != "$before" ]; then
    "$PY" notify_discord.py >> "$LOG" 2>&1
  fi
else
  "$PY" notify_discord.py --error "取数失败，旧数据已保留，详见 run.log" >> "$LOG" 2>&1
fi
