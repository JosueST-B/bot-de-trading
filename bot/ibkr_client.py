"""Cliente de Interactive Brokers con Multi-Puerto y Standby Fiduciario (ArcaFid Quantitative).

Conecta con IB Gateway o TWS via ib_async (fork mantenido de ib_insync).
Incluye sondeo rapido no bloqueante de puertos (7497, 7496, 4002, 4001).
Cuando TWS o IB Gateway estan cerrados, transiciona de forma determinista y sin
errores al Modo Fiduciary Standby (STANDBY_YFINANCE), sirviendo cotizaciones y
velas en tiempo real a traves de YFinanceDataEngine.

Requisitos de Puertos:
- Gateway Paper: 4002
- TWS Paper: 7497
- Gateway Live: 4001 (exige IBKR_ALLOW_REAL_TRADING=true)
- TWS Live: 7496 (exige IBKR_ALLOW_REAL_TRADING=true)
"""

from __future__ import annotations

import logging
import socket
import time
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Sequence

import pandas as pd

from bot.config import BotConfig
from bot.yfinance_engine import YFinanceDataEngine

log = logging.getLogger("ibkr")

PAPER_PORTS = {7497, 4002}
LIVE_PORTS = {7496, 4001}
ALL_STANDARD_PORTS = [7497, 7496, 4002, 4001]

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
    """Importa ib_async con fallback a ib_insync o None si no esta instalado."""
    try:
        import ib_async as ibmod  # type: ignore
        return ibmod
    except ImportError:
        pass
    try:
        import ib_insync as ibmod  # type: ignore
        return ibmod
    except ImportError:
        return None


def probe_ibkr_port(host: str, candidate_ports: Sequence[int], timeout: float = 0.25) -> int | None:
    """Sondea puertos TCP de forma no bloqueante y retorna el primer puerto abierto."""
    for port in candidate_ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            res = s.connect_ex((host, port))
            if res == 0:
                return port
        except Exception:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass
    return None


class IBKRClient:
    """Conector institucional de IBKR con auto-probing y Fiduciary Standby Mode."""

    def __init__(
        self,
        cfg: BotConfig,
        yfinance_engine: YFinanceDataEngine | None = None,
    ) -> None:
        self.cfg = cfg
        self._ibmod = _import_ib()
        self.ib = self._ibmod.IB() if self._ibmod is not None else None

        self.yfinance_engine = yfinance_engine or YFinanceDataEngine(ttl_seconds=60.0)

        self.status: str = "STANDBY_YFINANCE"
        self.is_connected: bool = False
        self.connected_port: int | None = None
        self.active_provider: str = "yfinance"
        self.is_paper: bool = self.cfg.ibkr_port in PAPER_PORTS

    # ------------------------------------------------------------------
    # Deteccion y Conexion
    # ------------------------------------------------------------------
    def get_candidate_ports(self) -> list[int]:
        """Calcula los puertos candidatos respetando las politicas de seguridad."""
        configured = self.cfg.ibkr_port
        if self.cfg.ibkr_allow_real_trading:
            order = [configured, 4002, 7497, 4001, 7496]
        else:
            order = [configured, 4002, 7497]

        # Eliminar duplicados preservando el orden
        seen = set()
        candidates = []
        for p in order:
            if p not in seen:
                seen.add(p)
                candidates.append(p)
        return candidates

    def check_connection(self) -> tuple[bool, int | None]:
        """Verifica el estado del conector o sondea puertos para autoconectar."""
        if self.ib is not None and self.ib.isConnected():
            self.status = "CONNECTED"
            self.is_connected = True
            self.active_provider = "ib_async"
            return True, self.connected_port

        if self.ib is None:
            self.status = "STANDBY_YFINANCE"
            self.is_connected = False
            self.connected_port = None
            self.active_provider = "yfinance"
            return False, None

        candidates = self.get_candidate_ports()
        open_port = probe_ibkr_port(self.cfg.ibkr_host, candidates, timeout=0.25)

        if open_port is None:
            self.status = "STANDBY_YFINANCE"
            self.is_connected = False
            self.connected_port = None
            self.active_provider = "yfinance"
            return False, None

        try:
            self.ib.connect(
                self.cfg.ibkr_host,
                open_port,
                clientId=self.cfg.ibkr_client_id,
                timeout=4,
            )
            if self.cfg.ibkr_use_delayed_data:
                self.ib.reqMarketDataType(3)

            self.connected_port = open_port
            self.is_paper = open_port in PAPER_PORTS
            self.is_connected = True
            self.status = "CONNECTED"
            self.active_provider = "ib_async"
            log.info(
                "IBKR conectado exitosamente en %s:%s (modo %s).",
                self.cfg.ibkr_host,
                open_port,
                "PAPER" if self.is_paper else "REAL",
            )
            return True, open_port
        except Exception as exc:
            log.warning(
                "Error en handshake IBKR en puerto %s: %s. Conmutando a STANDBY_YFINANCE.",
                open_port,
                exc,
            )
            self.status = "STANDBY_YFINANCE"
            self.is_connected = False
            self.connected_port = None
            self.active_provider = "yfinance"
            return False, None

    def connect(self) -> None:
        """Intenta conectar a IBKR; si no hay software disponible, activa Standby sin crashear."""
        success, port = self.check_connection()
        if not success:
            log.info(
                "IBKR desktop cerrado/no disponible. Modo Fiduciary Standby activo (STANDBY_YFINANCE) via yfinance."
            )

    def disconnect(self) -> None:
        try:
            if self.ib is not None and self.ib.isConnected():
                self.ib.disconnect()
        except Exception:
            pass
        finally:
            self.is_connected = False
            self.status = "STANDBY_YFINANCE"
            self.active_provider = "yfinance"

    def _stock(self, symbol: str):
        if self._ibmod is not None:
            return self._ibmod.Stock(symbol.upper(), "SMART", "USD")
        return None

    # ------------------------------------------------------------------
    # Datos de Mercado (Dual Feed: ib_async + yfinance)
    # ------------------------------------------------------------------
    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        """Descarga velas dinamicas validadas (IBKR nativo o yfinance Standby)."""
        # Si esta conectado a IBKR nativo, intentar obtener barras
        if self.is_connected and self.ib is not None and self.ib.isConnected():
            try:
                bar_size = BAR_SIZES.get(interval)
                seconds = INTERVAL_SECONDS.get(interval)
                if bar_size and seconds:
                    total_seconds = seconds * max(limit, 50)
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
                    if bars:
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
            except Exception as exc:
                log.warning(
                    "Fallo al solicitar velas a IBKR para %s (%s): %s. Derivando a yfinance.",
                    symbol,
                    interval,
                    exc,
                )

        # Standby fiduciario transparente via yfinance
        return self.yfinance_engine.get_klines(symbol, interval=interval, limit=limit)

    def get_bars(self, symbol: str, interval: str = "15m") -> pd.DataFrame:
        """Alias fiduciario segun contrato de interfaz."""
        return self.get_klines(symbol, interval=interval)

    # ------------------------------------------------------------------
    # Cuenta y Posiciones
    # ------------------------------------------------------------------
    def account_cash_usd(self) -> float:
        """Retorna el efectivo disponible en USD (IBKR real o Standby)."""
        if self.is_connected and self.ib is not None and self.ib.isConnected():
            for row in self.ib.accountValues():
                if row.tag == "TotalCashValue" and row.currency == "USD":
                    try:
                        return float(row.value)
                    except (TypeError, ValueError):
                        return 0.0
        return float(getattr(self.cfg, "ibkr_standby_cash", 10000.0))

    def net_liquidation(self) -> float:
        """Retorna el valor neto de liquidacion en USD (IBKR real o Standby)."""
        if self.is_connected and self.ib is not None and self.ib.isConnected():
            for row in self.ib.accountValues():
                if row.tag == "NetLiquidation" and row.currency == "USD":
                    try:
                        return float(row.value)
                    except (TypeError, ValueError):
                        return 0.0
        return float(getattr(self.cfg, "ibkr_standby_cash", 10000.0))

    def buying_power(self) -> float:
        """Poder de compra disponible en USD."""
        if self.is_connected and self.ib is not None and self.ib.isConnected():
            for row in self.ib.accountValues():
                if row.tag == "BuyingPower" and row.currency == "USD":
                    try:
                        return float(row.value)
                    except (TypeError, ValueError):
                        return 0.0
        return self.account_cash_usd() * 2.0

    def get_account_summary(self) -> dict[str, float]:
        """Resumen financiero fiduciario de la cuenta IBKR."""
        return {
            "net_liquidation": round(self.net_liquidation(), 2),
            "total_cash": round(self.account_cash_usd(), 2),
            "buying_power": round(self.buying_power(), 2),
        }

    def position_qty(self, symbol: str) -> float:
        if self.is_connected and self.ib is not None and self.ib.isConnected():
            for pos in self.ib.positions():
                if pos.contract.symbol.upper() == symbol.upper():
                    return float(pos.position)
        return 0.0

    def has_open_orders(self, symbol: str) -> bool:
        if self.is_connected and self.ib is not None and self.ib.isConnected():
            for trade in self.ib.openTrades():
                if trade.contract.symbol.upper() == symbol.upper():
                    return True
        return False

    # ------------------------------------------------------------------
    # Ejecucion de Ordenes
    # ------------------------------------------------------------------
    def buy_bracket(
        self,
        symbol: str,
        qty: int,
        entry_ref: float,
        take_profit: float,
        stop_loss: float,
    ) -> dict[str, Any]:
        """Compra con bracket: entrada limit + TP limit + SL stop."""
        if qty <= 0:
            return {"ok": False, "reason": "qty_zero"}

        limit_price = round(entry_ref * 1.002, 2)

        # Si esta conectado en vivo a TWS/Gateway
        if self.is_connected and self.ib is not None and self.ib.isConnected():
            try:
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
                    "provider": "ib_async",
                }
            except Exception as exc:
                log.warning("Fallo al enviar bracket a IBKR: %s. Pasando a Standby simulado.", exc)

        # Fiduciary Standby Order (simulada fiduciariamente sin crashear)
        return {
            "ok": True,
            "order_id": f"STANDBY_{symbol.upper()}_{int(time.time())}",
            "status": "standby_simulated",
            "limit_price": limit_price,
            "take_profit": round(take_profit, 2),
            "stop_loss": round(stop_loss, 2),
            "qty": qty,
            "provider": "yfinance",
            "note": "IBKR Desktop cerrado; orden simulada fiduciariamente con cotizaciones yfinance.",
        }

    def market_sell(self, symbol: str, qty: float) -> dict[str, Any]:
        if qty <= 0:
            return {"ok": False, "reason": "qty_zero"}

        if self.is_connected and self.ib is not None and self.ib.isConnected():
            try:
                contract = self._stock(symbol)
                self.ib.qualifyContracts(contract)
                order = self._ibmod.MarketOrder("SELL", qty)
                trade = self.ib.placeOrder(contract, order)
                self.ib.sleep(2)
                return {
                    "ok": True,
                    "status": trade.orderStatus.status,
                    "order_id": trade.order.orderId,
                    "provider": "ib_async",
                }
            except Exception as exc:
                log.warning("Fallo al enviar market sell a IBKR: %s", exc)

        return {
            "ok": True,
            "status": "standby_simulated",
            "order_id": f"STANDBY_SELL_{symbol.upper()}_{int(time.time())}",
            "qty": qty,
            "provider": "yfinance",
            "note": "IBKR Desktop cerrado; venta simulada fiduciariamente.",
        }

    # ------------------------------------------------------------------
    # Control de bucle y Horarios
    # ------------------------------------------------------------------
    def sleep(self, seconds: float) -> None:
        """Pausa fiduciaria respetando el bucle de eventos de ib_async si esta presente."""
        if self.ib is not None:
            try:
                self.ib.sleep(seconds)
                return
            except Exception:
                pass
        time.sleep(seconds)

    @staticmethod
    def is_market_open(now_utc: datetime | None = None) -> bool:
        """Determina si la sesion regular de Wall Street (NYSE/NASDAQ) esta abierta."""
        try:
            from zoneinfo import ZoneInfo
            ny = (now_utc or datetime.now(timezone.utc)).astimezone(
                ZoneInfo("America/New_York")
            )
        except Exception:
            # Fallback sin tzdata: aproximar NY = UTC-5 (ignora DST)
            ny = (now_utc or datetime.now(timezone.utc)) - timedelta(hours=5)
        if ny.weekday() >= 5:  # Sabado/Domingo
            return False
        open_t, close_t = dtime(9, 30), dtime(16, 0)
        return open_t <= ny.time() < close_t
