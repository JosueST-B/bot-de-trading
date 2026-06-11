# Control del bot desde celular

## Estado actual recomendado

- El bot corre en la PC.
- El celular solo controla, revisa dashboard y recibe alertas.
- Si la PC se apaga, el bot se detiene. Para 24/7 real necesitas VPS, mini PC encendida, o una PC que no se apague.

## Dashboard desde el celular

La PC debe estar encendida y el celular conectado al mismo Wi-Fi.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_dashboard_lan.ps1 -Port 8767
```

Luego abre en el celular:

```text
http://IP_DE_TU_PC:8767
```

En esta PC la IP detectada fue:

```text
http://192.168.68.106:8767
```

Si no abre, Windows Firewall puede estar bloqueando el puerto.

## Control por SSH desde celular

En Android puedes usar Termux, JuiceSSH o ConnectBot.

Primero, en Windows abre PowerShell como Administrador y ejecuta:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows_ssh_admin.ps1
```

Desde el celular:

```bash
ssh TU_USUARIO@IP_DE_TU_PC
```

Comandos utiles dentro de la carpeta del bot:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\mobile_status.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_live_guarded.ps1 -Cycles 0 -SleepSeconds 300
powershell -ExecutionPolicy Bypass -File .\scripts\stop_bot.ps1 -Live
```

## Telegram

Para alertas necesitas:

```env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=token_de_botfather
TELEGRAM_CHAT_ID=tu_chat_id
```

Probar:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_telegram.ps1 -Token "PEGA_AQUI_EL_TOKEN" -RestartLive
```

Tambien hay una guia especifica en `TELEGRAM.md`.

## VPS gratis

La alternativa gratis mas viable es Oracle Cloud Always Free. Oracle mantiene una capa Always Free para recursos de computo, pero puede haber escasez de capacidad por region y reglas de inactividad.

AWS y Google Cloud suelen servir como pruebas gratuitas o free tier limitado, pero no son tan buenos para un bot 24/7 permanente sin riesgo de cobros.

Si usas Oracle, conviene:

- Crear una VM Ubuntu Always Free.
- Configurar limites/budget de facturacion.
- Instalar Python y dependencias.
- Copiar el bot.
- Correrlo con `systemd`.

## Celular Android como mini-servidor

Si tienes un celular que no usas, puedes correr el bot directamente en Android usando Termux. Es gratis, pero menos estable que un VPS porque Android puede dormir o matar procesos.

Guia completa:

```text
TERMUX.md
```

Scripts incluidos:

```bash
./scripts/termux_start_live.sh
./scripts/termux_start_dashboard.sh
./scripts/termux_status.sh
./scripts/termux_stop.sh
```
