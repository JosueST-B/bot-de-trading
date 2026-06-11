#!/bin/bash

# Script de despliegue automatizado para el Bot de Trading en un VPS Linux (Ubuntu)
set -e

echo "=== INICIANDO DESPLIEGUE EN VPS LINUX ==="

# 1. Actualizar el sistema e instalar dependencias
echo "-> Actualizando paquetes e instalando Python 3, virtualenv y dependencias..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv git curl ufw

# 2. Definir rutas de instalación
APP_DIR=$(pwd)
echo "-> Directorio de la aplicación detectado: $APP_DIR"

# 3. Crear entorno virtual si no existe
if [ ! -d "$APP_DIR/venv" ]; then
    echo "-> Creando entorno virtual de Python..."
    python3 -m venv "$APP_DIR/venv"
fi

# 4. Activar entorno virtual e instalar requerimientos
echo "-> Instalando dependencias de Python..."
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# 5. Configurar permisos de scripts
chmod +x "$APP_DIR/scripts/deploy_vps.sh" 2>/dev/null || true

# 6. Configurar servicios Systemd
echo "-> Configurando servicios Systemd para ejecución 24/7..."

# Obtener el nombre del usuario actual
CURRENT_USER=$(whoami)

# Crear archivos de servicio modificados con las rutas correctas
cat <<EOF > /tmp/bot-trading.service
[Unit]
Description=Binance Trading Bot Live Loop
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python main.py live-loop --confirm-live I_UNDERSTAND_LIVE_RISK --sleep-seconds 300
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat <<EOF > /tmp/bot-dashboard.service
[Unit]
Description=Binance Trading Bot Dashboard
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python main.py dashboard
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Mover archivos de servicio a systemd y habilitarlos
echo "-> Instalando servicios en systemd..."
sudo mv /tmp/bot-trading.service /etc/systemd/system/bot-trading.service
sudo mv /tmp/bot-dashboard.service /etc/systemd/system/bot-dashboard.service

# Recargar systemd
sudo systemctl daemon-reload

# Habilitar servicios para que arranquen con el sistema
echo "-> Habilitando servicios para arranque automático..."
sudo systemctl enable bot-trading.service
sudo systemctl enable bot-dashboard.service

# Iniciar servicios
echo "-> Iniciando servicios..."
sudo systemctl restart bot-trading.service
sudo systemctl restart bot-dashboard.service

# 7. Configuración básica de Firewall
echo "-> Configurando reglas de puerto para el Firewall de Linux (UFW)..."
sudo ufw allow 22/tcp comment 'SSH'
sudo ufw allow 8765/tcp comment 'Bot Dashboard'

echo "=== DESPLIEGUE FINALIZADO CON ÉXITO ==="
echo "Para verificar el estado de los servicios ejecuta:"
echo "  sudo systemctl status bot-trading"
echo "  sudo systemctl status bot-dashboard"
echo "Para ver logs en tiempo real:"
echo "  journalctl -u bot-trading -f"
echo "  journalctl -u bot-dashboard -f"
