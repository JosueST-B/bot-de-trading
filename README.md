# Bot de Trading para Binance (Base Profesional)

Este proyecto crea una base seria para un bot cuantitativo en Binance con tres etapas:
1. Backtesting
2. Paper trading
3. Live trading (protegido por flags de seguridad)

## Enfoque realista de ROI

No existe forma responsable de prometer ROI alto constante. Lo que si se puede construir es:
- Estrategia disciplinada y medible
- Gestión de riesgo estricta
- Proceso de mejora continua con datos

Si quieres rendimiento sostenible, la prioridad es **controlar perdidas** antes que maximizar ganancias.

## Caracteristicas incluidas

- Arquitectura modular en Python
- Motor de estrategias por regimen (`auto`, tendencia, breakout, pullback en tendencia, reversion a la media)
- Indicadores de calidad de entrada: ADX, VWAP rolling, Donchian, Bollinger, MACD slope, estructura de minimos y cierre dentro de vela
- Entry Quality Score para bloquear entradas con bajo contexto operativo
- Filtro macro BTC en temporalidad mayor para evitar longs contra mercado debil
- Filtro de regimen de mercado (tendencia, rango, alta volatilidad)
- Confirmacion multi-timeframe (ej. entrada en `15m` con contexto `1h`)
- Filtros de mercado para evitar entradas de baja calidad
- Stop-loss, take-profit y trailing stop por ATR
- Tamano de posicion por riesgo fijo
- Limites de drawdown diario y racha de perdidas
- Cooldown entre entradas y limite diario de trades por regimen
- Validacion de filtros Binance en live (`LOT_SIZE`, `STEP_SIZE`, `MIN_NOTIONAL`, `PRICE_FILTER`)
- Logging persistente, base SQLite de eventos, alertas Telegram y reporte diario
- Dashboard local para revisar eventos, PnL y resultados
- Cliente de datos Binance (REST publico)
- Modo backtest, paper y live (live desactivado por defecto)

## Estructura

```text
bot/
  backtester.py
  binance_client.py
  config.py
  dashboard.py
  indicators.py
  main.py
  models.py
  regime.py
  telemetry.py
  paper.py
  risk.py
  strategy.py
```

## Requisitos

1. Python 3.11+ instalado y accesible en PATH
2. Instalar dependencias:

```bash
pip install -r requirements.txt
```

## Configuracion

Crear `.env` a partir de `.env.example` y ajustar parametros.

Variables importantes:
- `SYMBOL` (ej. `BTCUSDT`)
- `INTERVAL` (ej. `15m`)
- `STRATEGY_MODE` (`auto`, `trend`, `breakout`, `pullback_trend`, `mean_reversion`)
- `RISK_PER_TRADE` (ej. `0.01` = 1%)
- `MAX_DAILY_DRAWDOWN` (ej. `0.05` = 5%)
- `MAX_TRADES_PER_DAY` (ej. `4`)
- `WEAK_REGIME_MAX_TRADES_PER_DAY` (ej. `2`)
- `ENTRY_COOLDOWN_CANDLES` (ej. `2`)
- `USE_REGIME_FILTER` (`true` por defecto)
- `USE_MULTI_TIMEFRAME` (`true` por defecto)
- `HIGHER_INTERVAL` (ej. `1h`)
- `MIN_ENTRY_QUALITY` (ej. `0.72`)
- `USE_BTC_MACRO_FILTER` (`true` por defecto)
- `MACRO_SYMBOL` (ej. `BTCUSDT`)
- `MACRO_INTERVAL` (ej. `4h`)
- `LIVE_ENABLED` (`false` por defecto)
- `USE_TESTNET` (`true` por defecto)
- `ALLOW_REAL_TRADING` (`false` por defecto; debe ser `true` para permitir dinero real)
- `TELEGRAM_ENABLED` (`false` por defecto)
- `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` para alertas

## Uso

Todos los comandos pueden ejecutarse desde la entrada raiz:

```bash
python main.py <modo>
```

Tambien funciona la forma equivalente `python -m bot.main <modo>`.

### 1) Backtest

```bash
python main.py backtest
```

Con argumentos:

```bash
python main.py backtest --symbol BTCUSDT --interval 15m --lookback 1200
```

### 2) Paper trading

```bash
python main.py paper --cycles 30 --sleep-seconds 60
```

### 3) Busqueda de parametros (optimizacion inicial)

```bash
python main.py optimize --symbol BTCUSDT --interval 15m --lookback 1200 --top 5
```

### 4) Validacion Walk-Forward (recomendada antes de live)

```bash
python main.py walkforward --symbol BTCUSDT --interval 15m --lookback 900 --train-size 360 --test-size 180 --step-size 180 --top 1
```

Esto optimiza en cada bloque de entrenamiento y evalua en el bloque siguiente fuera de muestra.
Por defecto usa una grilla rapida; para una busqueda mas amplia agrega `--full-grid`.

### 5) Comparar modos de estrategia

```bash
python main.py compare --symbols BTCUSDT,ETHUSDT --intervals 15m,1h --lookback 1000 --min-trades 5
```

Prueba `auto`, `trend`, `breakout` y `mean_reversion`, con/sin filtro de regimen y con/sin multi-timeframe.

### 6) Afinar una candidata

```bash
python main.py tune --symbol ETHUSDT --interval 15m --strategy-mode breakout --use-regime-filter true --use-multi-timeframe false --lookback 1000 --top 10
```

Para una busqueda mas amplia:

```bash
python main.py tune --symbol ETHUSDT --interval 15m --strategy-mode breakout --use-regime-filter true --use-multi-timeframe false --lookback 1000 --top 10 --full-grid
```

### 7) Validar parametros fijos

```bash
python main.py validate --symbol SOLUSDT --interval 1h --strategy-mode breakout --use-regime-filter false --use-multi-timeframe false --stop-atr-mult 1.6 --take-profit-rr 0.8 --trailing-atr-mult 1.0 --min-confidence 0.35 --min-trend-strength 0.0005 --risk-per-trade 0.005 --lookback 1000
```

### 8) Research automatico

```bash
python main.py research --symbols BTCUSDT,ETHUSDT,SOLUSDT,AVAXUSDT --intervals 1h,4h --lookback 3000 --min-trades 8
```

Este modo ejecuta el flujo completo:
1. Compara simbolos, temporalidades, modos y filtros.
2. Afina las mejores candidatas con una grilla compacta.
3. Valida fuera de muestra con walk-forward.
4. Devuelve `passed` solo si una candidata supera los quality gates.

Para una busqueda mas pesada agrega `--full-grid`.

### 9) Live trading (solo cuando estes listo)

Por seguridad, requiere dos condiciones:
1. `LIVE_ENABLED=true` en `.env`
2. Confirmacion explicita en CLI

```bash
python main.py live --confirm-live I_UNDERSTAND_LIVE_RISK
```

Para ejecutar un loop live protegido:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_live_guarded.ps1 -Cycles 0 -SleepSeconds 300
```

Antes de usar dinero real, prueba con:

```env
LIVE_ENABLED=true
USE_TESTNET=true
ALLOW_REAL_TRADING=false
LIVE_MAX_QUOTE_PER_TRADE=25
LIVE_BLOCK_UNKNOWN_POSITION=true
```

Para dinero real, ademas de la confirmacion por CLI, `USE_TESTNET=false` exige
`ALLOW_REAL_TRADING=true`. No actives esa combinacion hasta validar el bot en
testnet/paper durante suficiente tiempo.

Para detener el live loop:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_bot.ps1 -Live
```

### 10) Inspeccionar regimen actual

```bash
python main.py regime --symbol BTCUSDT --interval 15m --higher-interval 1h
```

### 11) Reporte diario

```bash
python main.py report
```

Para una fecha UTC concreta:

```bash
python main.py report --day 2026-04-15
```

Para enviarlo por Telegram:

```bash
python main.py report --send
```

### Configurar Telegram

1. Crea un bot con `@BotFather` en Telegram y copia el token.
2. Ejecuta:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_telegram.ps1 -Token "PEGA_AQUI_EL_TOKEN" -RestartLive
```

3. Cuando el script lo pida, envia `/start` al bot en Telegram.
4. El script detecta el `chat_id`, actualiza `.env`, envia una prueba y reinicia `live-loop`.

Prueba manual:

```powershell
.\venv\Scripts\python.exe main.py telegram-test
```

### 12) Dashboard local

```bash
python main.py dashboard --host 127.0.0.1 --port 8765
```

Luego abre:

```text
http://127.0.0.1:8765
```

### 13) Healthcheck operativo

```bash
python main.py healthcheck
```

Esto devuelve un resumen JSON con:
- estado del dashboard
- estado del watchdog
- frescura del estado paper
- ultimo evento paper
- cantidad de fallos recientes (`data_error`, `paper_error`, reinicios)

En Windows tambien puedes usar:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\status_bot.ps1
```

### 14) Reporte de paper trading

```bash
python main.py paper-report
```

Devuelve:
- balance y ROI acumulado
- ventanas `1d`, `7d`, `30d` y `all`
- win rate
- profit factor
- expectancy
- mejor y peor trade
- fallos recientes de operacion

## Flujo recomendado para mejorar rendimiento

1. Backtest en varios activos y periodos.
2. Ajustar pocos parametros cada vez (evitar sobreoptimizacion).
3. Validar en paper al menos 2-4 semanas.
4. Empezar live con monto pequeno y limites estrictos.
5. Revisar metricas semanalmente (ROI, max drawdown, win rate, profit factor).

## Nota legal y de riesgo

Este software es educativo y no es asesoria financiera. Operar cripto implica riesgo alto, incluida la perdida total del capital.
