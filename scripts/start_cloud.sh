#!/bin/bash

# Si no está definida la variable PORT, usar 8765 por defecto
PORT_NUM=${PORT:-8765}

echo "=== INICIANDO BOT EN LA NUBE ==="
echo "-> Servidor Dashboard en puerto: $PORT_NUM"

# Lanzar el dashboard en segundo plano en 0.0.0.0
python main.py dashboard --host 0.0.0.0 --port $PORT_NUM &

# Esperar un par de segundos
sleep 2

# Lanzar el bucle de trading en vivo en primer plano
echo "-> Iniciando bucle de trading en vivo..."
python main.py live-loop --confirm-live I_UNDERSTAND_LIVE_RISK --sleep-seconds 300
