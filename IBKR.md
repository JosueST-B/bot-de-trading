# Interactive Brokers (Fase 12)

El bot puede operar acciones en IBKR reutilizando el mismo motor de
estrategias, filtros de regimen y gestion de riesgo que usa en Binance.

## Requisitos (una sola vez)

1. **Instalar IB Gateway** (mas liviano que TWS): descargalo desde el sitio
   de Interactive Brokers > Trading > API Software.
2. Iniciar sesion en Gateway con tu cuenta **paper** primero
   (usuario paper, no el real).
3. Habilitar la API: `Configure > Settings > API > Settings`:
   - Marcar "Enable ActiveX and Socket Clients"
   - Desmarcar "Read-Only API"
   - Socket port: **4002** (paper) — el default del bot
   - Agregar `127.0.0.1` a Trusted IPs
4. En `Configure > Lock and Exit`, configurar **Auto restart** para que el
   Gateway no se cierre solo cada noche.

## Puertos

| Programa   | Paper | Real |
|------------|-------|------|
| IB Gateway | 4002  | 4001 |
| TWS        | 7497  | 7496 |

El bot se conecta por defecto a `4002` (paper). Para dinero real hay que
cambiar `IBKR_PORT=4001`, poner `IBKR_ALLOW_REAL_TRADING=true` y ejecutar
con `--confirm-live I_UNDERSTAND_LIVE_RISK`. Tres candados, igual que Binance.

## Uso

```powershell
# instalar dependencia nueva
.\venv\Scripts\pip.exe install ib_async

# loop de acciones (paper) - IB Gateway debe estar abierto y logueado
.\venv\Scripts\python.exe main.py ibkr --sleep-seconds 300
```

Configuracion en `.env`: `IBKR_SYMBOLS` (ej. `SPY,QQQ,AAPL`), `IBKR_INTERVAL`
(`15m`, `1h`, `1d`), `IBKR_MAX_QUOTE_PER_TRADE` (tope USD por entrada).

## Como opera

- Solo en horario regular de NYSE/NASDAQ (9:30-16:00 NY, lun-vie); fuera de
  ese horario el loop espera.
- Cada vela cerrada evalua la estrategia (`STRATEGY_MODE`) con el filtro de
  regimen. Sin filtro macro BTC (no aplica a acciones).
- Las entradas usan **bracket orders nativas**: limit de entrada + take profit
  + stop loss viven en los servidores de IB, no en tu PC. Si el bot se cae,
  la proteccion sigue activa.
- Acciones enteras (sin fraccionales por API); si el tope por trade no alcanza
  para 1 accion, el bot lo reporta como `qty_below_one_share`.
- Telemetria y alertas Telegram integradas (eventos `ibkr_buy`, `ibkr_error`).

## Datos de mercado

Sin suscripcion de market data, el bot usa datos **retrasados 15 min**
(`IBKR_USE_DELAYED_DATA=true`). Para velas de 15m/1h esto es tolerable pero
no ideal; si vas en serio, suscribe el paquete basico de US equities
(unos USD 1.50-4.50/mes, gratis si generas comisiones minimas).

## 24/7 en Windows

```powershell
# registra tareas que arrancan al encender la PC y se reinician si fallan
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows_autostart.ps1 -IncludeIBKR
```

Esto tambien desactiva la suspension del equipo con corriente. Ten en cuenta:
si la PC se apaga (luz, actualizaciones de Windows), el bot muere hasta que
vuelva a encender. Para robustez real, el paso siguiente es un VPS (CLOUD.md).

## Nota de riesgo

Paper primero, siempre. Valida al menos 2-4 semanas de operacion simulada
antes de considerar el puerto real, igual que hiciste con Binance. Acciones
con datos retrasados + spreads pueden comportarse distinto al backtest cripto.
