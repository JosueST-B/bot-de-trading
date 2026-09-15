# Original User Request

## Initial Request — 2026-07-06T15:15:47Z

Mejorar y expandir la integración de notificaciones de Telegram en el bot de trading de Binance. El bot operará como notificador unidireccional integrado dentro del proceso del live-loop del trading bot, enviando alertas de compras/ventas, reportes diarios, notificaciones de auto-tuning y alertas críticas de sistema con un diseño premium y robusto.

Working directory: C:\Users\USUARIO\bot de trading
Integrity mode: demo

## Requirements

### R1. Formato de Mensajes Enriquecidos (HTML)
Mejorar la clase `TelegramNotifier` en `bot/telemetry.py` y las llamadas en `bot/main.py` para estructurar los mensajes con tags HTML reales (como `<b>`, `<code>`, `<i>`).
Los mensajes de Telegram deben replicar y adaptar el formato experto-institucional de Binance Square:
- **Alertas de Compra Spot:** Zona de entrada, Stop Loss, Target, estrategia técnica y sentimiento de noticias en un formato visualmente premium.
- **Alertas de Cierre/Venta Spot:** Resultados con PnL neto (`+` / `-`) en USDT y porcentaje, precio de salida y razón técnica del cierre.
- **Informe de Auto-Tuning:** Detalle ordenado y estructurado de las estrategias recalibradas.
- **Mensajes Críticos:** Tracebacks de errores de red o API en bloques monospace `<code>` legibles.

### R2. Sanitización y Escape de Caracteres HTML
Implementar una utilidad de escape de HTML robusta en la clase de notificación. Cualquier texto dinámico (como títulos de noticias, razones técnicas u tracebacks de errores) debe sanitizarse (reemplazando `<`, `>`, `&` con sus correspondientes entidades HTML `&lt;`, `&gt;`, `&amp;`) para evitar que la API de Telegram rechace los mensajes debido a errores de parseo HTML.

### R3. Configuración Fina por Variables de Entorno
Soportar las siguientes variables opcionales en el archivo `.env` y la clase `BotConfig` para permitir apagar selectivamente ciertos canales de alerta:
- `TELEGRAM_NOTIFY_BUYS` (por defecto `true`)
- `TELEGRAM_NOTIFY_SELLS` (por defecto `true`)
- `TELEGRAM_NOTIFY_AUTOTUNE` (por defecto `true`)
- `TELEGRAM_NOTIFY_ERRORS` (por defecto `true`)

## Acceptance Criteria

### Mensajería Estructural
- [ ] Las alertas de compra, venta, auto-tuning y error se envían utilizando formato HTML válido de Telegram (`parse_mode="HTML"`).
- [ ] Los mensajes de error del sistema o fallos de red se envuelven en etiquetas `<code>...</code>` para una lectura clara del traceback.

### Robustez de Parseo
- [ ] Se verifica mediante un test que los mensajes que contienen caracteres como < o > (comunes en errores de Python o tags XML de noticias) se escapan y entregan exitosamente sin provocar que la API de Telegram responda con HTTP 400 (Bad Request).

### Configuración Selectiva
- [ ] Si se define `TELEGRAM_NOTIFY_ERRORS=false` en el archivo `.env`, las alertas críticas no se envían a Telegram, pero las de compra/venta continúan funcionando normalmente.
- [ ] Si se define `TELEGRAM_NOTIFY_AUTOTUNE=false`, el reporte de recalibración diario no se envía a Telegram, pero el bot continúa ejecutando el auto-tuning en la base de datos de manera habitual.
