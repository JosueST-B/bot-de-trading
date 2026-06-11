# Ejecutar el bot en un celular Android con Termux

Esta es la alternativa gratis si no quieres pagar VPS. Funciona, pero no tiene la misma estabilidad que una nube: Android puede matar procesos, cortar datos o reiniciar apps para ahorrar bateria.

## Requisitos

- Celular Android dedicado.
- Cargador conectado 24/7.
- Wi-Fi estable o datos moviles confiables.
- Termux instalado desde F-Droid.
- Termux:API instalado si quieres usar `termux-wake-lock`.

## Preparar Android

En ajustes del telefono:

- Desactiva optimizacion de bateria para Termux.
- Permite actividad en segundo plano para Termux.
- Manten el celular cargando.
- Si puedes, usa Wi-Fi fijo y evita modo ahorro de energia.
- Activa reinicio automatico solo si tienes forma de relanzar Termux despues.

## Instalar dependencias

En Termux:

```bash
pkg update && pkg upgrade -y
pkg install -y python git clang make libcrypt openssl rust termux-api
termux-wake-lock
```

## Copiar el bot al celular

Opcion simple:

- Copia la carpeta del bot desde la PC al celular por USB.
- En Termux, deja el proyecto en:

```bash
~/bot-trading
```

Si lo copiaste a Descargas:

```bash
termux-setup-storage
cp -r ~/storage/downloads/bot-trading ~/bot-trading
cd ~/bot-trading
```

No subas `.env` a lugares publicos. Ese archivo contiene API keys.

## Crear entorno Python

```bash
cd ~/bot-trading
python -m venv venv
. venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

Si `pandas` o `numpy` fallan al instalar, prueba:

```bash
pkg install -y python-numpy
. venv/bin/activate
python -m pip install -r requirements.txt --no-build-isolation
```

## Verificar configuracion

```bash
cd ~/bot-trading
. venv/bin/activate
python main.py telegram-test --message "Termux conectado al bot."
python main.py live --confirm-live I_UNDERSTAND_LIVE_RISK
```

Si el comando `live` hace una evaluacion sin error, ya puede correr el loop.

## Arrancar en real

```bash
cd ~/bot-trading
chmod +x scripts/termux_*.sh
./scripts/termux_start_live.sh
```

Por defecto corre con:

```text
sleep-seconds = 300
cycles = 0
```

Para cambiarlo:

```bash
SLEEP_SECONDS=180 ./scripts/termux_start_live.sh
```

## Dashboard en el celular

```bash
cd ~/bot-trading
./scripts/termux_start_dashboard.sh
```

Desde el navegador del mismo celular:

```text
http://127.0.0.1:8767
```

Desde otro dispositivo en el mismo Wi-Fi, usa la IP del celular.

## Estado y apagado

```bash
cd ~/bot-trading
./scripts/termux_status.sh
./scripts/termux_stop.sh
```

## Mantenerlo vivo

Lo minimo:

```bash
termux-wake-lock
./scripts/termux_start_live.sh
```

Mas robusto:

- Instala Termux:Boot.
- Crea el archivo `~/.termux/boot/start-bot.sh`.
- Pon dentro:

```bash
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
cd "$HOME/bot-trading"
./scripts/termux_start_live.sh
```

Luego:

```bash
chmod +x ~/.termux/boot/start-bot.sh
```

## Mi recomendacion

Para operar real con bajo riesgo operativo:

- Usa el celular solo si acepta estar conectado 24/7.
- Mantén `LIVE_MAX_QUOTE_PER_TRADE` bajo al principio.
- Mantén Telegram activo.
- Revisa `./scripts/termux_status.sh` al menos una vez al dia.
- Si Android mata Termux, no es fallo del bot: es gestion de bateria del sistema.
