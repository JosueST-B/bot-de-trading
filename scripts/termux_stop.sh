#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

stop_pid_file() {
  local name="$1"
  local pid_file="$2"

  if [ ! -f "$pid_file" ]; then
    echo "$name no tiene PID file."
    return
  fi

  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [ -z "$pid" ]; then
    rm -f "$pid_file"
    echo "$name tenia PID file vacio; eliminado."
    return
  fi

  if kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    sleep 2
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
    echo "$name detenido. PID $pid"
  else
    echo "$name no estaba corriendo. PID file obsoleto."
  fi

  rm -f "$pid_file"
}

stop_pid_file "Live bot" "$ROOT/live.pid"
stop_pid_file "Dashboard" "$ROOT/dashboard.pid"

if command -v termux-wake-unlock >/dev/null 2>&1; then
  termux-wake-unlock || true
fi
