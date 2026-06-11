#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/venv/bin/python"

cd "$ROOT"

show_pid() {
  local name="$1"
  local pid_file="$2"

  if [ ! -f "$pid_file" ]; then
    echo "$name: sin PID file"
    return
  fi

  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
    echo "$name: corriendo PID $pid"
  else
    echo "$name: detenido o PID obsoleto ($pid)"
  fi
}

show_pid "Live bot" "$ROOT/live.pid"
show_pid "Dashboard" "$ROOT/dashboard.pid"

if [ -x "$PYTHON" ]; then
  "$PYTHON" main.py healthcheck --dashboard-url http://127.0.0.1:8767/api/summary || true
fi

echo "--- ultimas lineas bot.log ---"
tail -n 20 "$ROOT/bot.log" 2>/dev/null || true
