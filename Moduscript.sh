#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  PY="$(command -v python)"
fi

export MCMOD_DEPLOY_INTEGRATED=1
export PYTHONUNBUFFERED=1

echo "========================================"
echo " Moduscript - Web Server + Deploy Receiver"
echo " Web:    http://127.0.0.1:8000"
echo " Deploy: receiver 127.0.0.1:9001"
echo " Local dev: use scripts/run.ps1 or server/main.py"
echo "========================================"
echo

"$PY" -u "$ROOT/deploy/receiver.py" &
RECV_PID=$!
sleep 1
echo "[deploy] receiver started (PID $RECV_PID, integrated mode)"
echo

while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting server..."
  (cd "$ROOT/server" && exec "$PY" -u main.py) || true
  RC=$?
  echo
  echo "[MCmodAgent] Server stopped (exit $RC). Restarting in 2s..."
  sleep 2
done
