# Configurar Telegram

## 1) Crear el bot

En Telegram:

1. Abre `@BotFather`.
2. Envia `/newbot`.
3. Elige nombre y usuario del bot.
4. Copia el token.

## 2) Configurar automaticamente

Desde la carpeta del bot:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_telegram.ps1 -Token "PEGA_AQUI_EL_TOKEN" -RestartLive
```

El script te pedira enviar `/start` al bot. Luego detecta el `chat_id`, actualiza `.env`, manda un mensaje de prueba y reinicia `live-loop` para que el bot cargue Telegram.

## 3) Probar luego

```powershell
.\venv\Scripts\python.exe main.py telegram-test
```

O:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test_telegram.ps1
```

## 4) Alertas que envia el bot

El bot envia alertas cuando hay:

- `live_buy`
- `live_sell`
- `risk_pause`
- `live_guard`
- errores live capturados por el loop

Tambien puedes enviar reporte diario:

```powershell
.\venv\Scripts\python.exe main.py report --send
```
