"""Motor de datos de mercado en tiempo real via yfinance (ArcaFid Quantitative).

Descarga y normaliza velas dinamicas (klines) para acciones y ETFs lideres
(NVDA, AAPL, MSFT, AMZN, SPY, QQQ) en temporalidades 15m, 1h y 1d sin requerir
suscripciones pagas de datos.

Esquema de velas cuantitativo garantizado:
['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time']
- open_time / close_time en UTC (pd.Timestamp / datetime)
- open, high, low, close, volume en float numerico estricto
- Cache acotado con TTL (60s por defecto) para proteccion anti-429 (rate-limit)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

import pandas as pd
import yfinance as yf

logger = logging.getLogger("yfinance_engine")

SUPPORTED_TICKERS = ("NVDA", "AAPL", "MSFT", "AMZN", "SPY", "QQQ")

QUANT_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]

INTERVAL_SECONDS = {
    "1m": 60,
    "2m": 120,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "60m": 3600,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1wk": 604800,
}


@dataclass
class _KlineCacheEntry:
    timestamp: float
    data: pd.DataFrame


@dataclass
class _QuoteCacheEntry:
    timestamp: float
    data: dict[str, Any]


class YFinanceDataEngine:
    """Motor fiduciario de datos de renta variable y ETFs con cache TTL acotado."""

    def __init__(
        self,
        ttl_seconds: float = 60.0,
        max_cache_size: int = 128,
        quote_ttl_seconds: float = 15.0,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.quote_ttl_seconds = quote_ttl_seconds
        self.max_cache_size = max_cache_size
        self._kline_cache: dict[tuple[str, str, str, int], _KlineCacheEntry] = {}
        self._quote_cache: dict[str, _QuoteCacheEntry] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Normalizacion de datos
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_dataframe(
        df_raw: pd.DataFrame, interval: str, limit: int = 500
    ) -> pd.DataFrame:
        """Estandariza un DataFrame de yfinance al esquema fiduciario en UTC."""
        if df_raw is None or df_raw.empty:
            return pd.DataFrame(columns=QUANT_COLUMNS)

        df = df_raw.copy().reset_index()

        # Detectar la columna de tiempo (yfinance suele llamarla 'Datetime' o 'Date')
        time_col = None
        for candidate in ("Datetime", "Date", "datetime", "date", "index"):
            if candidate in df.columns:
                time_col = candidate
                break
        if time_col is None:
            time_col = df.columns[0]

        # Renombrar columnas clave
        rename_map = {
            time_col: "open_time",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
        df = df.rename(columns=rename_map)

        # Forzar conversion a UTC estricto
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)

        # Calcular close_time segun duracion del intervalo
        seconds = INTERVAL_SECONDS.get(interval, 900)
        df["close_time"] = df["open_time"] + pd.Timedelta(seconds=seconds - 1)

        # Conversion numerica rigurosa
        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = 0.0

        # Depuracion: descartar filas con valores faltantes en precios
        df = df.dropna(subset=["open", "high", "low", "close"])

        # Ordenar cronologicamente y eliminar duplicados de marca de tiempo
        df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

        # Retornar exactamente el subconjunto acotado por limit con las columnas del esquema
        return df[QUANT_COLUMNS].tail(limit).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Obtencion de Klines con Cache Bounded TTL
    # ------------------------------------------------------------------
    def get_klines(
        self,
        ticker: str,
        interval: str = "15m",
        period: str | None = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """Obtiene velas dinamicas validadas con resolucion de cache anti-429."""
        ticker_clean = ticker.strip().upper()

        # Determinar periodo optimo si no se especifica explicitamente
        if period is None:
            if interval in {"1m", "2m", "5m", "15m", "30m"}:
                period = "60d" if limit > 100 else "5d"
            elif interval in {"60m", "1h", "4h"}:
                period = "730d" if limit > 150 else "1mo"
            else:  # '1d', etc.
                period = "2y" if limit > 250 else "1y"

        cache_key = (ticker_clean, interval, period, limit)
        now_mono = time.monotonic()

        with self._lock:
            cached = self._kline_cache.get(cache_key)
            if cached is not None and (now_mono - cached.timestamp) < self.ttl_seconds:
                return cached.data.copy()

        # Descarga desde yfinance con manejo fiduciario de errores
        try:
            t = yf.Ticker(ticker_clean)
            df_raw = t.history(period=period, interval=interval, auto_adjust=False)

            # Reintento con ventana mas amplia si vino vacio
            if (df_raw is None or df_raw.empty) and period not in {"60d", "2y", "max"}:
                fallback_period = "60d" if interval in {"15m", "1h"} else "2y"
                df_raw = t.history(period=fallback_period, interval=interval, auto_adjust=False)

            if df_raw is None or df_raw.empty:
                # Si fallan ambos, consultar si hay copia en cache aunque haya expirado
                with self._lock:
                    if cached is not None and not cached.data.empty:
                        logger.warning(
                            "Velas vacias para %s (%s). Retornando cache stale.",
                            ticker_clean,
                            interval,
                        )
                        return cached.data.copy()
                raise RuntimeError(
                    f"No kline data returned by yfinance for {ticker_clean} ({interval})."
                )

            df = self.normalize_dataframe(df_raw, interval=interval, limit=limit)
            if df.empty:
                with self._lock:
                    if cached is not None and not cached.data.empty:
                        return cached.data.copy()
                raise RuntimeError(
                    f"Velas normalizadas vacias para {ticker_clean} ({interval})."
                )

            # Almacenar en cache con desalojo acotado
            with self._lock:
                if len(self._kline_cache) >= self.max_cache_size:
                    oldest = min(self._kline_cache.keys(), key=lambda k: self._kline_cache[k].timestamp)
                    self._kline_cache.pop(oldest, None)
                self._kline_cache[cache_key] = _KlineCacheEntry(
                    timestamp=now_mono,
                    data=df.copy(),
                )

            return df

        except Exception as exc:
            with self._lock:
                if cached is not None and not cached.data.empty:
                    logger.warning(
                        "Excepcion yfinance para %s (%s): %s. Sirviendo cache de respaldo.",
                        ticker_clean,
                        interval,
                        exc,
                    )
                    return cached.data.copy()
            raise

    # ------------------------------------------------------------------
    # Cotizaciones instantaneas (Fast Info)
    # ------------------------------------------------------------------
    def get_latest_quote(self, ticker: str) -> dict[str, Any]:
        """Obtiene la cotizacion mas reciente de un activo con cache ultra-rapido."""
        ticker_clean = ticker.strip().upper()
        now_mono = time.monotonic()

        with self._lock:
            q_cached = self._quote_cache.get(ticker_clean)
            if q_cached is not None and (now_mono - q_cached.timestamp) < self.quote_ttl_seconds:
                return dict(q_cached.data)

        try:
            t = yf.Ticker(ticker_clean)
            fast = getattr(t, "fast_info", None)

            price = None
            prev_close = None
            open_price = None
            high = None
            low = None
            vol = None

            if fast is not None:
                try:
                    price = float(getattr(fast, "last_price", 0.0) or 0.0)
                    prev_close = float(
                        getattr(fast, "previous_close", 0.0)
                        or getattr(fast, "regular_market_previous_close", 0.0)
                        or 0.0
                    )
                    open_price = float(getattr(fast, "open", 0.0) or 0.0)
                    high = float(getattr(fast, "day_high", 0.0) or 0.0)
                    low = float(getattr(fast, "day_low", 0.0) or 0.0)
                    vol = float(getattr(fast, "last_volume", 0.0) or 0.0)
                except Exception:
                    pass

            # Fallback a history intraday si fast_info no entrego precio valido
            if not price or price <= 0:
                hist = t.history(period="1d", interval="1m")
                if hist is not None and not hist.empty:
                    last_row = hist.iloc[-1]
                    price = float(last_row["Close"])
                    if not prev_close and len(hist) > 1:
                        prev_close = float(hist.iloc[0]["Open"])
                    if not vol:
                        vol = float(hist["Volume"].sum())

            if not price or price <= 0:
                # Ultimo recurso: vela diaria
                hist_d = t.history(period="5d", interval="1d")
                if hist_d is not None and not hist_d.empty:
                    price = float(hist_d.iloc[-1]["Close"])
                    if len(hist_d) > 1:
                        prev_close = float(hist_d.iloc[-2]["Close"])

            if not price or price <= 0:
                raise RuntimeError(f"No se pudo determinar precio para {ticker_clean}.")

            prev_close = prev_close or price
            change = price - prev_close
            change_pct = (change / prev_close * 100.0) if prev_close > 0 else 0.0

            result = {
                "symbol": ticker_clean,
                "price": round(price, 4),
                "change": round(change, 4),
                "change_pct": round(change_pct, 2),
                "previous_close": round(prev_close, 4),
                "open": round(open_price or price, 4),
                "high": round(high or price, 4),
                "low": round(low or price, 4),
                "volume": round(vol or 0.0, 2),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "yfinance_realtime",
            }

            with self._lock:
                if len(self._quote_cache) >= self.max_cache_size:
                    oldest = min(self._quote_cache.keys(), key=lambda k: self._quote_cache[k].timestamp)
                    self._quote_cache.pop(oldest, None)
                self._quote_cache[ticker_clean] = _QuoteCacheEntry(timestamp=now_mono, data=result)

            return result

        except Exception as exc:
            with self._lock:
                if q_cached is not None:
                    logger.warning("Fallo al obtener quote de %s: %s. Usando cache.", ticker_clean, exc)
                    return dict(q_cached.data)
            raise

    def get_last_price(self, ticker: str) -> float:
        """Devuelve unicamente el precio flotante actual del ticker."""
        quote = self.get_latest_quote(ticker)
        return float(quote.get("price", 0.0))

    # ------------------------------------------------------------------
    # Mantenimiento y telemetria de cache
    # ------------------------------------------------------------------
    def clear_cache(self) -> None:
        """Limpia todos los registros del cache en memoria."""
        with self._lock:
            self._kline_cache.clear()
            self._quote_cache.clear()

    def cache_stats(self) -> dict[str, Any]:
        """Informa el estado de utilizacion de los caches."""
        with self._lock:
            return {
                "klines_cached": len(self._kline_cache),
                "quotes_cached": len(self._quote_cache),
                "ttl_seconds": self.ttl_seconds,
                "quote_ttl_seconds": self.quote_ttl_seconds,
                "max_cache_size": self.max_cache_size,
            }
