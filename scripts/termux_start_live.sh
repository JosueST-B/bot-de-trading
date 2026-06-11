#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/venv/bin/python"
PID_FILE="$ROOT/live.pid"
OUT_FILE="$ROOT/live.out.log"
ERR_FILE="$ROOT/live.err.log"
CYCLES="${CYCLES:-0}"
SLEEP_SECONDS="${SLEEP_SECONDS:-300}"

cd "$ROOT"

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
fi

if [ ! -x "$PYTHON" ]; then
  echo "No existe $PYTHON. Ejecuta la instalacion de TERMUX.md primero."
  exit 1
fi

if [ -f "$PID_FILE" ]; then
  existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$existing_pid" ] && kill -0 "$existing_pid" >/dev/null 2>&1; then
    echo "Live bot ya esta corriendo con PID $existing_pid"
    exit 0
  fi
fi

nohup "$PYTHON" main.py live-loop \
  --confirm-live I_UNDERSTAND_LIVE_RISK \
  --cycles "$CYCLES" \
  --sleep-seconds "$SLEEP_SECONDS" \
  >"$OUT_FILE" 2>"$ERR_FILE" &

pid="$!"
echo "$pid" > "$PID_FILE"
echo "Live bot iniciado en Termux con PID $pid"
