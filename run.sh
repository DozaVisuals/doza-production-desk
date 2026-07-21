#!/bin/zsh
# Start the Doza Dashboard server. Safe to run repeatedly; used by launchd later.
set -e
DIR="${0:A:h}"
cd "$DIR"

if [ ! -x "venv/bin/python" ]; then
  python3 -m venv venv
  ./venv/bin/pip install --quiet --upgrade pip
  ./venv/bin/pip install --quiet -r requirements.txt
fi

exec ./venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 5175
