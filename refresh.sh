#!/bin/zsh
# Hourly refresh entry point for launchd. The 7 o'clock run is the deep pass
# (re-scans the past 7 days for missed replies).
DIR="${0:A:h}"
cd "$DIR"
mkdir -p data
FLAG=""
[ "$(date +%H)" = "07" ] && FLAG="--deep"
exec ./venv/bin/python -m refresh.refresh $FLAG >> data/refresh-cron.log 2>&1
