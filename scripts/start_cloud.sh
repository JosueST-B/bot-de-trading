#!/usr/bin/env bash
set -euo pipefail

# Si no está definida la variable PORT, usar 8765 por defecto
PORT_NUM=${PORT:-8765}

echo "=== INICIANDO BOT EN LA NUBE ==="
echo "-> Servidor Dashboard en puerto: $PORT_NUM"

# Fallar temprano si DATABASE_URL no apunta a PostgreSQL/Supabase.
python -u main.py cloud-check --require-postgres

# Lanzar el dashboard en segundo plano en 0.0.0.0
python -u main.py dashboard --host 0.0.0.0 --port "$PORT_NUM" &
DASHBOARD_PID=$!

cleanup() {
    kill "$DASHBOARD_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Esperar un par de segundos
sleep 2

if ! kill -0 "$DASHBOARD_PID" 2>/dev/null; then
    echo "ERROR: el dashboard no pudo iniciar."
    exit 1
fi

# Lanzar el bucle de trading en vivo en primer plano
echo "-> Iniciando bucle de trading en vivo..."
python -u main.py live-loop --confirm-live I_UNDERSTAND_LIVE_RISK --sleep-seconds 300
