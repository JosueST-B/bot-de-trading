"""Cliente de Interactive Brokers (Fase 12).

Conecta con IB Gateway o TWS via ib_async (fork mantenido de ib_insync).
Expone la misma forma de datos que BinanceDataClient (open_time, open, high,
low, close, volume, close_time en UTC) para reutilizar el motor de
estrategias, el filtro de regimen y el gestor de riesgo sin cambios.

Requisitos en la maquina:
1. IB Gateway o TWS abierto y logueado.
2. API habilitada: Configure > Settings > API > "Enable ActiveX and Socket Clients".
3. Puerto segun modo: Gateway paper 4002, Gateway live 4001,
   TWS paper 7497, TWS live 7496.

Seguridad: por defecto se conecta a puertos PAPER. Para dinero real se exige
IBKR_ALLOW_REAL_TRADING=true + confirmacion CLI, igual que en Binance.
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any

import pandas as pd

from bot.config import BotConfig

log = logging.getLogger("ibkr")

PAPER_PORTS = {7497, 4002}

# Mapeo de intervalos del bot a barSize de IB
BAR_SIZES = {
    "1m": "1 min",
    "5m": "5 mins",
    "15m": "15 mins",
    "30m": "30 mins",
    "1h": "1 hour",
    "4h": "4 hours",
    "1d": "1 day",
}

INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def _import_ib():
    """Importa ib_async (mantenido) con fallback a ib_insync (API compatible)."""
    try:
        import ib_async as ibmod  # type: ignore
        return ibmod
    except ImportError:
        pass
    try:
        import ib_insync as ibmod  # type: ignore
        return ibmod
    except ImportError as exc:
        raise RuntimeError(
            "Se requiere ib_async (pip install ib_async) para operar con IBKR."
        ) from exc


class IBKRClient:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self._ibmod = _import_ib()
        self.ib = self._ibmod.IB()
        self.is_paper = cfg.ibkr_port in PAPER_PORTS

    # ------------------------------------------------------------------
    # Conexion
    # ------------------------------------------------------------------
    def connect(self) -> None:
        if self.ib.isConnected():
            return
        self.ib.connect(
            self.cfg.ibkr_host,
            self.cfg.ibkr_port,
            clientId=self.cfg.ibkr_client_id,
            timeout=20,
        )
        if self.cfg.ibkr_use_delayed_data:
            # 3 = datos retrasados gratuitos si no hay suscripcion de mercado
            self.ib.reqMarketDataType(3)
        log.info(
            "IBKR conectado a %s:%s (modo %s).",
            self.cfg.ibkr_host,
            self.cfg.ibkr_port,
            "PAPER" if self.is_paper else "REAL",
        )

    def disconnect(self) -> None:
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
        except Exception:
            pass

    def _stock(self, symbol: str):
        return self._ibmod.Stock(symbol.upper(), "SMART", "USD")

    # ------------------------------------------------------------------
    # Datos historicos (compatibles con el motor de estrategias)
    # ------------------------------------------------------------------
    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        bar_size = BAR_SIZES.get(interval)
        seconds = INTERVAL_SECONDS.get(interval)
        if not bar_size or not seconds:
            raise ValueError(f"Intervalo no soportado para IBKR: {interval}")
        total_seconds = seconds * max(limit, 50)
        # IB exige durationStr en S o D; margen x3 por horas de mercado cerrado
        if interval == "1d":
            duration = f"{max(limit + 10, 60)} D"
        else:
            days = max(1, int(total_seconds * 3 / 86400) + 1)
            duration = f"{min(days, 360)} D"
        bars = self.ib.reqHistoricalData(
            self._stock(symbol),
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,  # UTC
        )
        if not bars:
            raise RuntimeError(f"IBKR no devolvio barras para {symbol} {interval}.")
        df = self._ibmod.util.df(bars)
        df = df.rename(columns={"date": "open_time"})
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        df["close_time"] = df["open_time"] + pd.Timedelta(seconds=seconds - 1)
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
        return df[["open_time", "open", "high", "low", "close", "volume", "close_time"]].tail(
            limit
        ).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Cuenta y posiciones
    # ------------------------------------------------------------------
    def account_cash_usd(self) -> float:
        for row in self.ib.accountValues():
            if row.tag == "TotalCashValue" and row.currency == "USD":
                try:
                    return float(row.value)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def net_liquidation(self) -> float:
        for row in self.ib.accountValues():
            if row.tag == "NetLiquidation" and row.currency == "USD":
                try:
                    return float(row.value)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def position_qty(self, symbol: str) -> float:
        for pos in self.ib.positions():
            if pos.contract.symbol.upper() == symbol.upper():
                return float(pos.position)
        return 0.0

    def has_open_orders(self, symbol: str) -> bool:
        for trade in self.ib.openTrades():
            if trade.contract.symbol.upper() == symbol.upper():
                return True
        return False

    # ------------------------------------------------------------------
    # Ordenes
    # ------------------------------------------------------------------
    def buy_bracket(
        self,
        symbol: str,
        qty: int,
        entry_ref: float,
        take_profit: float,
        stop_loss: float,
    ) -> dict[str, Any]:
        """Compra con bracket: entrada limit + TP limit + SL stop, todo nativo en IB."""
        if qty <= 0:
            return {"ok": False, "reason": "qty_zero"}
        limit_price = round(entry_ref * 1.002, 2)  # margen para asegurar fill
        bracket = self.ib.bracketOrder(
            "BUY",
            qty,
            limitPrice=limit_price,
            takeProfitPrice=round(take_profit, 2),
            stopLossPrice=round(stop_loss, 2),
        )
        contract = self._stock(symbol)
        self.ib.qualifyContracts(contract)
        trades = [self.ib.placeOrder(contract, order) for order in bracket]
        parent = trades[0]
        self.ib.sleep(2)
        return {
            "ok": True,
            "order_id": parent.order.orderId,
            "status": parent.orderStatus.status,
            "limit_price": limit_price,
            "qty": qty,
        }

    def market_sell(self, symbol: str, qty: float) -> dict[str, Any]:
        contract = self._stock(symbol)
        self.ib.qualifyContracts(contract)
        order = self._ibmod.MarketOrder("SELL", qty)
        trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(2)
        return {"ok": True, "status": trade.orderStatus.status}

    # ------------------------------------------------------------------
    # Horario de mercado (NYSE/NASDAQ regular)
    # ------------------------------------------------------------------
    @staticmethod
    def is_market_open(now_utc: datetime | None = None) -> bool:
        try:
            from zoneinfo import ZoneInfo
            ny = (now_utc or datetime.now(timezone.utc)).astimezone(
                ZoneInfo("America/New_York")
            )
        except Exception:
            # Fallback sin tzdata: aproximar NY = UTC-5 (ignora DST)
            ny = (now_utc or datetime.now(timezone.utc)) - timedelta(hours=5)
        if ny.weekday() >= 5:  # sabado/domingo
            return False
        open_t, close_t = dtime(9, 30), dtime(16, 0)
        return open_t <= ny.time() < close_t
