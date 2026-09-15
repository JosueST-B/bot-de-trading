# Despliegue en nube con Supabase

## Punto clave

Supabase aporta PostgreSQL, autenticacion, almacenamiento y funciones cortas. No mantiene por si solo este proceso Python `live-loop` ejecutandose 24/7.

Para operar el bot necesitas dos piezas:

1. Supabase como base PostgreSQL persistente.
2. Un servicio de ejecucion continuo como Render Worker, Railway Service, Fly.io, VPS o similar.

El `Procfile` de este proyecto sirve para una plataforma de ejecucion compatible. Subir el repositorio solamente a Supabase no inicia el bot.

## Variables obligatorias en el servicio de ejecucion

Configura estas variables en Render/Railway/VPS, no solo en Supabase:

```text
DATABASE_URL=<Supabase connection pooler URL>
LIVE_ENABLED=true
USE_TESTNET=true
ALLOW_REAL_TRADING=false
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Para la primera prueba en nube usa Testnet. Cambia a real solo despues de verificar logs, Telegram y bloqueo de instancia.

Usa preferiblemente la URL del connection pooler de Supabase, con SSL habilitado. No publiques `.env`.

## Comprobacion segura

En la plataforma:

```bash
python main.py cloud-check --require-postgres
```

Debe mostrar:

```json
{
  "status": "ok",
  "database_dialect": "postgresql",
  "database_url_set": true
}
```

Si muestra `sqlite`, la plataforma no recibio `DATABASE_URL`. En servicios efimeros eso provoca perdida de estado al reiniciar.

## Arranque

El servicio web usa:

```text
web: bash scripts/start_cloud.sh
```

El script:

- valida PostgreSQL antes de iniciar;
- inicia el dashboard en `$PORT`;
- mantiene el `live-loop` en primer plano;
- detiene el dashboard cuando el servicio termina.

## Una sola instancia

El bot usa un bloqueo distribuido guardado en PostgreSQL. Una segunda instancia `live-loop` debe fallar con:

```text
Otra instancia live-loop posee el bloqueo distribuido.
```

No ejecutes simultaneamente el live real en PC y nube. Durante la migracion:

1. Despliega en Testnet.
2. Comprueba `cloud-check`, dashboard y Telegram.
3. Deten el live de la PC.
4. Activa una sola instancia real en nube.

## Sobre `duplicate_candle`

No siempre es un error. El loop corre cada 5 minutos, pero el intervalo dinamico puede cambiar a `4h`. Hasta que cierre una vela nueva, el bot devuelve `duplicate_candle` para evitar operar varias veces la misma vela.
