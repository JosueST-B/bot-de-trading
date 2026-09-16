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

## Follow-up — 2026-09-15T20:25:34Z

Rediseñar la interfaz visual y estructural de la plataforma institucional hacia un minimalismo "Monochrome Terminal" de alta fidelidad (estándar Jane Street, Linear y Vercel), purgando el 100% de imágenes decorativas y saturación visual para garantizar descanso óptico, máxima transparencia fiduciaria y conversión de inversores en un flujo de 4 bloques esenciales.

Working directory: C:\Users\USUARIO\bot de trading
Integrity mode: development

## Requirements

### R1. Minimalismo "Monochrome Terminal" y Purga Total de Imágenes
Eliminar todas las imágenes fotográficas y recursos gráficos decorativos en el portal público (`institutional_portal.py`) y en la versión web estática (`index.html` y `docs/index.html`). Aplicar una paleta monocromática de ultra-precisión (zinc/carbón `#09090b`, `#111215`, `#14161b`), micro-bordes milimétricos `1px solid rgba(255,255,255,0.08)`, botón de acento blanco sólido (`#f4f4f5`), tipografía dual con cifras tabulares fijas (`Inter` + `JetBrains Mono` con `tnum`) y amplio espacio en blanco/respiro sin resplandores artificiales.

### R2. Arquitectura de Navegación en 4 Bloques Esenciales
Reestructurar el recorrido visual del inversor en 4 secciones sin fricción:
1) **Resumen Ejecutivo & Consenso Alpha:** Métricas clave auditadas (Sharpe, Sortino, Drawdown), ticker en vivo discreto y telemetría de auto-evolución continua (Hugging Face FinBERT + Bandit RL).
2) **Rendimiento Histórico & Matriz de Riesgo:** Gráfico vectorial de alta precisión con selector temporal (1M, 3M, 6M, 1Y, ALL), cursor crosshair fino y cerrojo de Drawdown fiduciario en -6.4%.
3) **Simulador Actuarial & Transparencia Fiduciaria:** Modelo de retornos compuestos con conmutador multidivisa (USDT, USD, EUR, BTC) y desglose transparente de honorarios (0% gestión, 5% Hurdle Rate anual, 20% High-Water Mark estricto).
4) **Seguridad No-Custodial & Libro Mayor Auditado:** Conexión segura de credenciales API con cifrado AES-256 en reposo, retiros protegidos por 2FA TOTP con SLA < 24h, y barra de búsqueda interactiva con exportación en PDF, CSV y JSON.

### R3. Preservación y Sincronización Multi-Plataforma
Garantizar paridad funcional total entre el servidor HTTP nativo en Python (`main.py portal` en puerto 8765), el panel unificado (puerto 8770), y el despliegue público en GitHub Pages (`https://josuest-b.github.io/bot-de-trading/`) manteniendo código de respuesta `200 OK` en todas las rutas y la suite completa de pruebas unitarias al 100%.

## Acceptance Criteria

### Estética y Cero Fatiga Visual
- [ ] Cero imágenes de stock o decorativas (`.jpg`, `.png`) presentes en el DOM de la plataforma; la interfaz se construye exclusivamente con tipografía, micro-bordes de 1px y componentes vectoriales minimalistas.
- [ ] La paleta de colores utiliza fondo zinc `#09090b`, tarjetas `#14161b`, y color semántico apagado (verde esmeralda `#10b981` y rosa coral `#f43f5e`) exclusivamente para variaciones numéricas de precios y retornos.
- [ ] Todos los números y métricas financieras emplean la propiedad CSS `font-feature-settings: "tnum" 1, "zero" 1` para evitar temblores al actualizarse en vivo.

### Flujo de 4 Bloques y Funcionalidad Interactiva
- [ ] La página presenta claramente las 4 secciones esenciales ordenadas lógicamente con navegación fluida y anclas rápidas.
- [ ] El conmutador multidivisa recalcula instantáneamente todas las cifras entre USDT, USD, EUR y BTC sin errores de renderizado.
- [ ] El botón de paso de auto-optimización por refuerzo (RL) actualiza las barras de ponderación y la generación en tiempo real.
- [ ] La barra de filtrado y búsqueda del libro mayor filtra filas por activo (`BTC, ETH, SOL, NVDA`) y texto en tiempo real.
- [ ] Las descargas de reportes en PDF, CSV y JSON funcionan con código 200 OK.

### Verificación Técnica y Despliegue
- [ ] Suite de pruebas unitarias pasando al 100% (`python -m unittest discover tests`).
- [ ] Servidor institucional local en `http://127.0.0.1:8765` responde 200 OK.
- [ ] Despliegue en GitHub Pages (`https://josuest-b.github.io/bot-de-trading/`) sincronizado y respondiendo 200 OK.
