#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/venv/bin/python"
PID_FILE="$ROOT/dashboard.pid"
OUT_FILE="$ROOT/dashboard.out.log"
ERR_FILE="$ROOT/dashboard.err.log"
PORT="${PORT:-8767}"

cd "$ROOT"

if [ ! -x "$PYTHON" ]; then
  echo "No existe $PYTHON. Ejecuta la instalacion de TERMUX.md primero."
  exit 1
fi

if [ -f "$PID_FILE" ]; then
  existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$existing_pid" ] && kill -0 "$existing_pid" >/dev/null 2>&1; then
    echo "Dashboard ya esta corriendo con PID $existing_pid"
    exit 0
  fi
fi

nohup "$PYTHON" main.py dashboard --host 0.0.0.0 --port "$PORT" \
  >"$OUT_FILE" 2>"$ERR_FILE" &

pid="$!"
echo "$pid" > "$PID_FILE"
echo "Dashboard iniciado en Termux con PID $pid"
echo "Abre http://IP_DEL_CELULAR:$PORT desde otro dispositivo en el mismo Wi-Fi."
