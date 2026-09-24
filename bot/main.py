from __future__ import annotations

import argparse
import html
import json
import logging
import os
import signal
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from bot.backtester import Backtester
from bot.binance_client import BinanceDataClient, BinanceExecutionClient, SymbolFilters
from bot.config import BotConfig
from bot.dashboard import run_dashboard
from bot.indicators import atr
from bot.models import Position, Trade
from bot.optimizer import optimize_config
from bot.paper import run_paper
from bot.regime import classify_market
from bot.risk import RiskManager, CircuitBreakerStatus
from bot.strategy import HybridStrategy
from bot.telemetry import build_paper_report, build_telemetry
from bot.watchdog import run_watchdog
from bot.walkforward import run_fixed_walkforward, run_walkforward
from bot.db import DBPosition, DBTrade, DBBotState, get_db_session, OrderState, generate_client_order_id
from bot.earn_manager import EarnManager, build_earn_manager
from bot.news_sentiment import NewsSentimentAnalyzer
from enum import Enum


class MarketSession(str, Enum):
    CRYPTO_24_7 = "CRYPTO_24_7"
    US_PRE_MARKET = "US_PRE_MARKET"   # 04:00 <= ET < 09:30 (Mon-Fri)
    US_REGULAR = "US_REGULAR"         # 09:30 <= ET < 16:00 (Mon-Fri)
    US_POST_MARKET = "US_POST_MARKET" # 16:00 <= ET < 20:00 (Mon-Fri)
    US_CLOSED = "US_CLOSED"           # 20:00 <= ET < 04:00 or Weekends/Holidays


def get_market_session(now_utc: datetime | None = None) -> MarketSession:
    """Returns the current US market session for equities."""
    from datetime import time as dtime
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        et = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        et = now - timedelta(hours=5)

    if et.weekday() >= 5:
        return MarketSession.US_CLOSED

    t = et.time()
    if dtime(4, 0) <= t < dtime(9, 30):
        return MarketSession.US_PRE_MARKET
    elif dtime(9, 30) <= t < dtime(16, 0):
        return MarketSession.US_REGULAR
    elif dtime(16, 0) <= t < dtime(20, 0):
        return MarketSession.US_POST_MARKET
    else:
        return MarketSession.US_CLOSED


def _to_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def calculate_vwap_fills(fills: list[dict[str, Any]] | None, fallback_price: float) -> float:
    """Computes exact Volume-Weighted Average Price across all fill slices."""
    if not fills:
        return fallback_price
    total_qty = 0.0
    total_quote = 0.0
    for f in fills:
        q = _to_float(f.get("qty"), 0.0)
        p = _to_float(f.get("price"), 0.0)
        total_qty += q
        total_quote += (q * p)
    if total_qty <= 0:
        return fallback_price
    return total_quote / total_qty



class LiveTrader:
    def __init__(self, cfg: BotConfig, state_store=None) -> None:
        self.cfg = cfg
        self.data = BinanceDataClient()
        self.exec = BinanceExecutionClient(cfg)
        self.symbol_filters: SymbolFilters = self.exec.get_symbol_filters(cfg.symbol)
        self.base_asset, self.quote_asset = self.exec.split_symbol(cfg.symbol)
        self.strategy = HybridStrategy(cfg)
        self.risk = RiskManager(cfg)
        self.state_store = state_store
        self.state_key = f"live:{cfg.symbol}:{cfg.interval}"
        self.db_session = get_db_session(cfg.event_db_path)
        self.news_analyzer = NewsSentimentAnalyzer(cfg.event_db_path)
        from bot.ml_filter import MLFilter
        self.ml_filter = MLFilter()
        self.cash = cfg.initial_balance
        # Earn: solo en cuenta real (los endpoints /sapi no existen en testnet)
        self.earn = (
            build_earn_manager(cfg, self.exec.client)
            if not cfg.use_testnet
            else None
        )


        self.position: Position | None = None
        self.entry_fee_paid = 0.0
        self.trades: list[Trade] = []
        self.last_processed_close_time: datetime | None = None
        self.load_state()

    def close(self) -> None:
        if hasattr(self, "db_session") and self.db_session is not None:
            try:
                self.db_session.close()
            except Exception:
                pass
        if hasattr(self, "data") and hasattr(self.data, "session"):
            try:
                self.data.session.close()
            except Exception:
                pass

    def __enter__(self) -> LiveTrader:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(str(value))

    @staticmethod
    def _position_to_dict(position: Position | None) -> dict[str, Any] | None:
        if position is None:
            return None
        return {
            "entry_time": position.entry_time.isoformat(),
            "entry_price": position.entry_price,
            "quantity": position.quantity,
            "stop_price": position.stop_price,
            "take_profit_price": position.take_profit_price,
        }

    @classmethod
    def _position_from_dict(cls, payload: dict[str, Any] | None) -> Position | None:
        if payload is None:
            return None
        entry_time = cls._parse_dt(str(payload["entry_time"]))
        if entry_time is None:
            return None
        return Position(
            entry_time=entry_time,
            entry_price=float(payload["entry_price"]),
            quantity=float(payload["quantity"]),
            stop_price=float(payload["stop_price"]),
            take_profit_price=float(payload["take_profit_price"]),
        )

    @staticmethod
    def _trade_to_dict(trade: Trade) -> dict[str, Any]:
        return {
            "entry_time": trade.entry_time.isoformat(),
            "exit_time": trade.exit_time.isoformat(),
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "quantity": trade.quantity,
            "pnl": trade.pnl,
            "pnl_pct": trade.pnl_pct,
            "reason": trade.reason,
        }

    @classmethod
    def _trade_from_dict(cls, payload: dict[str, Any]) -> Trade:
        entry_time = cls._parse_dt(str(payload["entry_time"]))
        exit_time = cls._parse_dt(str(payload["exit_time"]))
        if entry_time is None or exit_time is None:
            raise ValueError("Invalid live trade timestamps in state.")
        return Trade(
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=float(payload["entry_price"]),
            exit_price=float(payload["exit_price"]),
            quantity=float(payload["quantity"]),
            pnl=float(payload["pnl"]),
            pnl_pct=float(payload["pnl_pct"]),
            reason=str(payload["reason"]),
        )

    def load_state(self) -> None:
        self.trades = []
        # Cargar trades de la base de datos ORM
        try:
            db_trades = self.db_session.query(DBTrade).filter(
                DBTrade.symbol == self.cfg.symbol
            ).order_by(DBTrade.exit_time.desc()).limit(200).all()
            self.trades = [
                Trade(
                    entry_time=t.entry_time.replace(tzinfo=timezone.utc) if t.entry_time.tzinfo is None else t.entry_time,
                    exit_time=t.exit_time.replace(tzinfo=timezone.utc) if t.exit_time.tzinfo is None else t.exit_time,
                    entry_price=t.entry_price,
                    exit_price=t.exit_price,
                    quantity=t.quantity,
                    pnl=t.pnl,
                    pnl_pct=t.pnl_pct,
                    reason=t.reason
                ) for t in reversed(db_trades)
            ]
        except Exception as e:
            logging.error(f"Error al cargar historial de trades desde DB: {e}")

        # Cargar estado de riesgo
        try:
            state_record = self.db_session.query(DBBotState).filter(
                DBBotState.key == f"risk_state:{self.cfg.symbol}"
            ).first()
            if state_record:
                risk_state = json.loads(state_record.value_json)
                self.risk.state.day_anchor = self._parse_dt(risk_state.get("day_anchor"))
                self.risk.state.day_start_equity = float(risk_state.get("day_start_equity", 0.0))
                self.risk.state.consecutive_losses = int(risk_state.get("consecutive_losses", 0))
                self.risk.state.daily_trade_count = int(risk_state.get("daily_trade_count", 0))
                self.risk.state.last_entry_time = self._parse_dt(risk_state.get("last_entry_time"))
                self.risk.state.last_exit_time = self._parse_dt(risk_state.get("last_exit_time"))
                cb_val = risk_state.get("circuit_breaker_status")
                if cb_val:
                    try:
                        self.risk.state.circuit_breaker_status = CircuitBreakerStatus(cb_val)
                    except Exception:
                        pass
                self.risk.state.circuit_breaker_paused = bool(risk_state.get("circuit_breaker_paused", False))
        except Exception as e:
            logging.error(f"Error al cargar estado de riesgo desde DB: {e}")

        # Cargar y reconciliar posición activa / órdenes pendientes / huérfanas
        self.position = None
        try:
            self.reconcile_startup_state()
        except Exception as e:
            logging.error(f"Error al cargar/reconciliar posición desde DB: {e}")

        # Consultar balances reales en Binance
        try:
            self._balances()
        except Exception as e:
            logging.error(f"Error al consultar balances iniciales en Binance: {e}")

        # Cargar configuración auto-optimizada si existe
        self.load_optimal_config()

    def load_optimal_config(self) -> None:
        """Carga parámetros auto-optimizados desde la DB si existen."""
        if not self.cfg.auto_tune_enabled:
            return
        try:
            from bot.db import DBBotState
            key = f"optimal_config:{self.cfg.symbol}"
            state_record = self.db_session.query(DBBotState).filter(DBBotState.key == key).first()
            if state_record:
                payload = json.loads(state_record.value_json)
                params = payload.get("params", {})
                if params and _autotune_candidate_is_acceptable(payload):
                    from dataclasses import replace
                    self.cfg = replace(self.cfg, **params)
                    self.strategy = HybridStrategy(self.cfg)
                    self.risk = RiskManager(self.cfg)
                    logging.info(f"[{self.cfg.symbol}] Configuración auto-optimizada cargada con éxito: {params}")
                elif params:
                    logging.warning(
                        "[%s] Configuracion auto-optimizada rechazada por metricas no rentables.",
                        self.cfg.symbol,
                    )
        except Exception as e:
            logging.error(f"[{self.cfg.symbol}] Error al cargar configuración auto-optimizada para {self.cfg.symbol}: {e}")

    def _determine_dynamic_interval(self, symbol: str) -> str:
        """Determina el intervalo de operación según el régimen de mercado."""
        if not self.cfg.dynamic_timeframe_enabled:
            return self.cfg.interval

        try:
            higher_interval = self.cfg.higher_interval
            regime_df = self.data.get_klines(symbol, higher_interval, self.cfg.higher_lookback)
            if regime_df is not None and len(regime_df) >= 80:
                regime = classify_market(regime_df, self.cfg)
                if regime.name == "bullish_trend":
                    logging.info(f"[{symbol}] Régimen de mercado: bullish_trend. Ajustando a intervalo dinámico: 15m")
                    return "15m"
                elif regime.name == "range":
                    logging.info(f"[{symbol}] Régimen de mercado: range. Ajustando a intervalo dinámico: 1h")
                    return "1h"
                elif regime.name in {"bearish_trend", "high_volatility"}:
                    logging.info(f"[{symbol}] Régimen de mercado: {regime.name}. Ajustando a intervalo dinámico: 4h")
                    return "4h"
        except Exception as e:
            logging.error(f"[{symbol}] Error al determinar intervalo dinámico: {e}")
            
        return self.cfg.interval

    def _get_approx_price(self) -> float:
        try:
            res = self.data.get_klines(self.cfg.symbol, self.cfg.interval, limit=1)
            if res is not None and not res.empty:
                return float(res["close"].iloc[-1])
        except Exception:
            pass
        return 1.0

    def reconcile_startup_state(self) -> None:
        """
        Reconciliador atómico de estado al reiniciar:
        1. Sincroniza órdenes pendientes (PENDING_SUBMIT) con Binance por client_order_id.
           - Si la orden se ejecutó en Binance, se transiciona a FILLED.
           - Si fue cancelada, expirada o rechazada (o no existe), se purga el registro fantasma.
        2. Consulta balances reales y órdenes abiertas en Binance.
        3. Si existe una posición activa en DB:
           - Verifica que el SL protector siga abierto en Binance; si falta, lo re-adjunta.
           - Si la posición fue cerrada en Binance, detecta el precio real de salida y la cierra.
        4. Si no existe registro en DB pero Binance tiene balance positivo (posición huérfana tras crash):
           - Adopta la posición, reconstruye precio de entrada y adjunta orden Stop Loss de inmediato.
        """
        logging.info(f"[{self.cfg.symbol}] Iniciando reconciliación atómica de estado en arranque...")

        # 1. Reconciliar órdenes PENDING_SUBMIT
        try:
            pending_positions = self.db_session.query(DBPosition).filter(
                DBPosition.symbol == self.cfg.symbol,
                DBPosition.is_active == True,
                DBPosition.state == OrderState.PENDING_SUBMIT.value
            ).all()

            for pos in pending_positions:
                cid = pos.client_order_id
                if not cid:
                    continue
                try:
                    ex_order = self.exec.get_order_status(self.cfg.symbol, origClientOrderId=cid)
                    st = ex_order.get("status")
                    if st in ("FILLED", "PARTIALLY_FILLED"):
                        pos.state = OrderState.FILLED.value
                        exec_qty = _to_float(ex_order.get("executedQty"), pos.quantity)
                        if exec_qty > 0:
                            pos.quantity = exec_qty
                            c_quote = _to_float(ex_order.get("cummulativeQuoteQty"), 0.0)
                            if c_quote > 0:
                                pos.entry_price = c_quote / exec_qty
                        self.db_session.commit()
                        logging.info(f"Orden pendiente {cid} confirmada como FILLED en Binance.")
                    elif st in ("CANCELED", "REJECTED", "EXPIRED"):
                        pos.state = OrderState.CANCELLED.value
                        pos.is_active = False
                        self.db_session.commit()
                        logging.info(f"Purgada orden pendiente {cid} ({st}) en Binance.")
                except Exception as ex_err:
                    err_text = str(ex_err).lower()
                    if "-2013" in err_text or "not exist" in err_text:
                        pos.state = OrderState.CANCELLED.value
                        pos.is_active = False
                        self.db_session.commit()
                        logging.info(f"Purgado registro fantasma {cid} no encontrado en Binance.")
                    else:
                        logging.warning(f"No se pudo consultar orden pendiente {cid}: {ex_err}")
        except Exception as e:
            logging.error(f"Error al reconciliar órdenes pendientes: {e}")

        # 2. Consultar balances reales en Binance
        quote_free, quote_locked, base_free, base_locked = self._balances()
        total_base = base_free + base_locked
        filters = self.symbol_filters

        # 3. Reconciliar posición activa en DB si existe
        db_pos = self.db_session.query(DBPosition).filter(
            DBPosition.symbol == self.cfg.symbol,
            DBPosition.is_active == True
        ).first()

        if db_pos:
            self.position = self.reconcile_position_with_binance(db_pos)
            return

        # 4. Detección y adopción de posición huérfana en Binance
        approx_price = self._get_approx_price()
        if total_base > 0 and self.exec.quantity_is_valid(total_base, filters) and (total_base * approx_price) >= filters.min_notional:
            logging.warning(
                f"[{self.cfg.symbol}] POSICIÓN HUÉRFANA DETECTADA en arranque. "
                f"Base={total_base}, valor aprox={total_base * approx_price:.2f}. Protegiendo..."
            )
            entry_price = approx_price
            entry_time = datetime.utcnow()
            try:
                my_trades = self.exec._call_signed(self.exec.client.get_my_trades, symbol=self.cfg.symbol, limit=10)
                if my_trades:
                    buy_trades = [t for t in my_trades if t.get("isBuyer")]
                    if buy_trades:
                        last_buy = buy_trades[-1]
                        entry_price = _to_float(last_buy.get("price"), entry_price)
                        entry_time = datetime.fromtimestamp(last_buy.get("time") / 1000, tz=timezone.utc).replace(tzinfo=None)
            except Exception as trade_err:
                logging.warning(f"No se pudo consultar historial de trades para posición huérfana: {trade_err}")

            stop_loss_pct = getattr(self.cfg, "stop_loss", getattr(self.cfg, "sl_pct", 0.02))
            take_profit_pct = getattr(self.cfg, "take_profit", getattr(self.cfg, "tp_pct", 0.04))
            stop_price = entry_price * (1 - stop_loss_pct)
            take_profit_price = entry_price * (1 + take_profit_pct)

            open_orders = []
            try:
                open_orders = self.exec.get_open_orders(self.cfg.symbol)
            except Exception as oerr:
                logging.warning(f"Error al consultar órdenes abiertas para posición huérfana: {oerr}")

            sl_order_id = None
            for o in open_orders:
                if o.get("type") in ("STOP_LOSS_LIMIT", "STOP_LOSS"):
                    sl_order_id = str(o.get("orderId"))
                    stop_price = _to_float(o.get("stopPrice") or o.get("price"), stop_price)
                    break

            if not sl_order_id and base_free > 0 and self.exec.quantity_is_valid(base_free, filters):
                try:
                    sl_cid = generate_client_order_id(self.cfg.symbol, "OSL", int(time.time() * 1000))
                    limit_sl_price = stop_price * (1 - self.cfg.slippage)
                    new_sl = self.exec.create_stop_loss_limit(
                        self.cfg.symbol,
                        min(total_base, base_free),
                        stop_price,
                        limit_sl_price,
                        filters,
                        newClientOrderId=sl_cid
                    )
                    sl_order_id = str(new_sl.get("orderId"))
                    logging.info(f"SL protector colocado para posición huérfana. ID: {sl_order_id}")
                except Exception as sl_err:
                    logging.error(f"Fallo al colocar SL de emergencia para posición huérfana: {sl_err}")

            orphan_state = OrderState.FILLED.value if sl_order_id else OrderState.UNHEDGED_CRITICAL.value
            db_pos = DBPosition(
                symbol=self.cfg.symbol,
                entry_time=entry_time,
                entry_price=entry_price,
                quantity=total_base,
                stop_price=stop_price,
                take_profit_price=take_profit_price,
                stop_loss_order_id=sl_order_id,
                take_profit_order_id=None,
                is_active=True,
                state=orphan_state,
                entry_reason="reconciled_orphan_startup"
            )
            self.db_session.add(db_pos)
            self.db_session.commit()

            self.position = Position(
                entry_time=entry_time.replace(tzinfo=timezone.utc) if entry_time.tzinfo is None else entry_time,
                entry_price=entry_price,
                quantity=total_base,
                stop_price=stop_price,
                take_profit_price=take_profit_price,
                entry_reason="reconciled_orphan_startup"
            )

    def reconcile_position_with_binance(self, db_pos: DBPosition) -> Position | None:
        """
        Sincroniza y reconcilia la posición activa local con Binance API.
        Retorna la Position si sigue activa, o None si fue cerrada.
        """
        logging.info("Sincronizando estado de posición con Binance...")
        try:
            quote_free, quote_locked, base_free, base_locked = self._balances()
            total_base = base_free + base_locked
            filters = self.symbol_filters

            if not self.exec.quantity_is_valid(total_base, filters) or (total_base * db_pos.entry_price) < filters.min_notional:
                logging.warning("No hay suficiente balance de la moneda base en Binance. La posición fue cerrada externamente.")
                actual_exit_price = db_pos.stop_price
                actual_exit_time = datetime.utcnow()
                try:
                    my_trades = self.exec._call_signed(self.exec.client.get_my_trades, symbol=self.cfg.symbol, limit=5)
                    if my_trades:
                        last_trade = my_trades[-1]
                        if not last_trade.get("isBuyer"):
                            actual_exit_price = _to_float(last_trade.get("price"), db_pos.stop_price)
                            actual_exit_time = datetime.fromtimestamp(last_trade.get("time") / 1000, tz=timezone.utc).replace(tzinfo=None)
                except Exception:
                    pass
                self._close_db_position(db_pos, actual_exit_price, actual_exit_time, "external_close_no_balance")
                return None

            sl_filled = False
            tp_filled = False
            exit_price = db_pos.entry_price
            exit_time = datetime.utcnow()
            close_reason = "reconciled_close"

            if db_pos.stop_loss_order_id:
                try:
                    sl_order = self.exec.get_order_status(self.cfg.symbol, db_pos.stop_loss_order_id)
                    if sl_order.get("status") == "FILLED":
                        sl_filled = True
                        exit_price = float(sl_order.get("price") or sl_order.get("avgPrice") or db_pos.stop_price)
                        update_time_ms = sl_order.get("updateTime")
                        if update_time_ms:
                            exit_time = datetime.fromtimestamp(update_time_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
                        close_reason = "stop_or_trailing"
                        logging.info(f"El Stop Loss nativo ({db_pos.stop_loss_order_id}) se ejecutó en Binance.")
                except Exception as e:
                    logging.warning(f"No se pudo consultar orden SL {db_pos.stop_loss_order_id}: {e}")

            if not sl_filled and db_pos.take_profit_order_id:
                try:
                    tp_order = self.exec.get_order_status(self.cfg.symbol, db_pos.take_profit_order_id)
                    if tp_order.get("status") == "FILLED":
                        tp_filled = True
                        exit_price = float(tp_order.get("price") or tp_order.get("avgPrice") or db_pos.take_profit_price)
                        update_time_ms = tp_order.get("updateTime")
                        if update_time_ms:
                            exit_time = datetime.fromtimestamp(update_time_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
                        close_reason = "take_profit"
                        logging.info(f"El Take Profit nativo ({db_pos.take_profit_order_id}) se ejecutó en Binance.")
                except Exception as e:
                    logging.warning(f"No se pudo consultar orden TP {db_pos.take_profit_order_id}: {e}")

            if sl_filled or tp_filled:
                other_order_id = db_pos.take_profit_order_id if sl_filled else db_pos.stop_loss_order_id
                if other_order_id:
                    try:
                        self.exec.cancel_order(self.cfg.symbol, other_order_id)
                        logging.info(f"Cancelada orden contraria {other_order_id} tras reconciliación.")
                    except Exception as e:
                        logging.warning(f"No se pudo cancelar orden contraria {other_order_id}: {e}")

                self._close_db_position(db_pos, exit_price, exit_time, close_reason)
                return None

            # Si la posición sigue abierta, verificar si el SL está activo en Binance
            open_orders = []
            try:
                open_orders = self.exec.get_open_orders(self.cfg.symbol)
            except Exception as e:
                logging.warning(f"Error al consultar órdenes abiertas en Binance: {e}")

            open_order_ids = {str(o.get("orderId")) for o in open_orders}
            sl_is_open = db_pos.stop_loss_order_id and (str(db_pos.stop_loss_order_id) in open_order_ids)

            if not sl_is_open and base_free > 0 and self.exec.quantity_is_valid(base_free, filters):
                logging.warning(f"[{self.cfg.symbol}] Posición activa sin SL en Binance. Adjuntando SL protector...")
                try:
                    sl_cid = generate_client_order_id(self.cfg.symbol, "RSL", int(time.time() * 1000))
                    limit_sl_price = db_pos.stop_price * (1 - self.cfg.slippage)
                    new_sl = self.exec.create_stop_loss_limit(
                        self.cfg.symbol,
                        min(db_pos.quantity, base_free),
                        db_pos.stop_price,
                        limit_sl_price,
                        filters,
                        newClientOrderId=sl_cid
                    )
                    db_pos.stop_loss_order_id = str(new_sl.get("orderId"))
                    db_pos.state = OrderState.FILLED.value
                    self.db_session.commit()
                    logging.info(f"SL protector re-adjuntado exitosamente. ID: {db_pos.stop_loss_order_id}")
                except Exception as ex:
                    logging.error(f"Fallo al adjuntar nuevo SL en reconciliación: {ex}")
                    db_pos.state = OrderState.UNHEDGED_CRITICAL.value
                    self.db_session.commit()

            logging.info("La posición sigue activa en Binance.")
            return Position(
                entry_time=db_pos.entry_time.replace(tzinfo=timezone.utc) if db_pos.entry_time.tzinfo is None else db_pos.entry_time,
                entry_price=db_pos.entry_price,
                quantity=db_pos.quantity,
                stop_price=db_pos.stop_price,
                take_profit_price=db_pos.take_profit_price,
                entry_reason=getattr(db_pos, "entry_reason", "") or "",
            )

        except Exception as e:
            logging.error(f"Error durante la reconciliación con Binance: {e}")
            return Position(
                entry_time=db_pos.entry_time.replace(tzinfo=timezone.utc) if db_pos.entry_time.tzinfo is None else db_pos.entry_time,
                entry_price=db_pos.entry_price,
                quantity=db_pos.quantity,
                stop_price=db_pos.stop_price,
                take_profit_price=db_pos.take_profit_price,
                entry_reason=getattr(db_pos, "entry_reason", "") or "",
            )

    def _close_db_position(self, db_pos: DBPosition, exit_price: float, exit_time: datetime, reason: str):
        db_pos.is_active = False
        db_pos.state = OrderState.FILLED.value
        gross = db_pos.quantity * exit_price
        exit_fee = gross * self.cfg.fee_rate
        entry_cost = db_pos.quantity * db_pos.entry_price
        entry_fee = entry_cost * self.cfg.fee_rate
        pnl = gross - entry_cost - entry_fee - exit_fee
        pnl_pct = pnl / entry_cost if entry_cost > 0 else 0.0

        
        db_trade = DBTrade(
            symbol=self.cfg.symbol,
            entry_time=db_pos.entry_time,
            exit_time=exit_time,
            entry_price=db_pos.entry_price,
            exit_price=exit_price,
            quantity=db_pos.quantity,
            pnl=pnl,
            pnl_pct=pnl_pct * 100,
            reason=reason
        )
        self.db_session.add(db_trade)
        self.db_session.commit()
        
        # Recargar en la lista en memoria
        new_trade = Trade(
            entry_time=db_pos.entry_time.replace(tzinfo=timezone.utc) if db_pos.entry_time.tzinfo is None else db_pos.entry_time,
            exit_time=exit_time.replace(tzinfo=timezone.utc) if exit_time.tzinfo is None else exit_time,
            entry_price=db_pos.entry_price,
            exit_price=exit_price,
            quantity=db_pos.quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            reason=reason
        )
        self.trades.append(new_trade)
        self.risk.register_trade_result(pnl, now=exit_time.replace(tzinfo=timezone.utc) if exit_time.tzinfo is None else exit_time)
        logging.info(f"Reconciliado: Posición cerrada en DB local. PnL: {pnl:.4f} ({pnl_pct*100:.2f}%)")

    def _close_db_position_as_lost(self, db_pos: DBPosition, reason: str):
        self._close_db_position(db_pos, db_pos.stop_price, datetime.utcnow(), reason)

    def save_state(self) -> None:
        try:
            risk_payload = {
                "day_anchor": self.risk.state.day_anchor.isoformat() if self.risk.state.day_anchor else None,
                "day_start_equity": self.risk.state.day_start_equity,
                "consecutive_losses": self.risk.state.consecutive_losses,
                "daily_trade_count": self.risk.state.daily_trade_count,
                "last_entry_time": self.risk.state.last_entry_time.isoformat() if self.risk.state.last_entry_time else None,
                "last_exit_time": self.risk.state.last_exit_time.isoformat() if self.risk.state.last_exit_time else None,
                "circuit_breaker_status": self.risk.state.circuit_breaker_status.value,
                "circuit_breaker_paused": self.risk.state.circuit_breaker_paused,
            }
            
            state_record = self.db_session.query(DBBotState).filter(
                DBBotState.key == f"risk_state:{self.cfg.symbol}"
            ).first()
            
            if not state_record:
                state_record = DBBotState(
                    key=f"risk_state:{self.cfg.symbol}",
                    value_json=json.dumps(risk_payload)
                )
                self.db_session.add(state_record)
            else:
                state_record.value_json = json.dumps(risk_payload)
                state_record.updated_ts = datetime.utcnow()
                
            self.db_session.commit()
        except Exception as e:
            logging.error(f"Error al guardar estado en DB: {e}")


    def _equity(self, mark: float) -> float:
        return self.cash + ((self.position.quantity * mark) if self.position else 0.0)

    def _balances(self) -> tuple[float, float, float, float]:
        quote_free, quote_locked = 0.0, 0.0
        base_free, base_locked = 0.0, 0.0
        try:
            val_q = self.exec.get_asset_balance_values(self.quote_asset)
            if isinstance(val_q, (tuple, list)) and len(val_q) == 2:
                quote_free = float(val_q[0])
                quote_locked = float(val_q[1])
        except Exception:
            pass

        try:
            val_b = self.exec.get_asset_balance_values(self.base_asset)
            if isinstance(val_b, (tuple, list)) and len(val_b) == 2:
                base_free = float(val_b[0])
                base_locked = float(val_b[1])
        except Exception:
            pass

        # Fallback to get_account if get_asset_balance_values didn't yield balances or was mocked via get_account
        if quote_free == 0.0 and quote_locked == 0.0 and base_free == 0.0 and base_locked == 0.0:
            if hasattr(self.exec, "get_account"):
                try:
                    acct = self.exec.get_account()
                    if isinstance(acct, dict) and "balances" in acct:
                        for b in acct.get("balances", []):
                            asset = b.get("asset")
                            if asset == self.quote_asset:
                                quote_free = float(b.get("free", 0.0))
                                quote_locked = float(b.get("locked", 0.0))
                            elif asset == self.base_asset:
                                base_free = float(b.get("free", 0.0))
                                base_locked = float(b.get("locked", 0.0))
                except Exception:
                    pass

        if quote_free > 0 or not self.cash:
            self.cash = quote_free
        return quote_free, quote_locked, base_free, base_locked

    @staticmethod
    def _closed_candles(df):
        if len(df) < 2:
            return df.iloc[0:0].copy()
        return df.iloc[:-1].copy()

    def _prepare_live_qty(self, raw_qty: float, ref_price: float) -> tuple[float, str]:
        qty = self.exec.normalize_quantity(raw_qty, self.symbol_filters)
        if not self.exec.quantity_is_valid(qty, self.symbol_filters):
            return 0.0, "qty_filter_rejected"
        if not self.exec.notional_is_valid(qty, ref_price, self.symbol_filters):
            return 0.0, "min_notional_rejected"
        return qty, "ok"

    def step(self, order_book: dict[str, Any] | None = None) -> dict[str, Any]:
        # Sincronizar reloj en cada paso para evitar desvíos temporales (APIError -1021)
        try:
            self.exec.sync_clock()
        except Exception as e:
            logging.warning(f"[{self.cfg.symbol}] No se pudo sincronizar el reloj en step(): {e}")

        # Cargar configuración auto-optimizada si existe
        self.load_optimal_config()

        # Ajuste dinámico de intervalo de operación
        if self.cfg.dynamic_timeframe_enabled:
            dynamic_interval = self._determine_dynamic_interval(self.cfg.symbol)
            if dynamic_interval != self.cfg.interval:
                from dataclasses import replace
                self.cfg = replace(self.cfg, interval=dynamic_interval)
                self.state_key = f"live:{self.cfg.symbol}:{self.cfg.interval}"

        df = self.data.get_klines(self.cfg.symbol, self.cfg.interval, self.cfg.lookback)
        higher_df = None
        if self.cfg.use_multi_timeframe:
            higher_df = self.data.get_klines(
                self.cfg.symbol, self.cfg.higher_interval, self.cfg.higher_lookback
            )
        macro_df = None
        if self.cfg.use_btc_macro_filter:
            macro_df = self.data.get_klines(
                self.cfg.macro_symbol, self.cfg.macro_interval, self.cfg.macro_lookback
            )
        analysis_df = self._closed_candles(df)
        if analysis_df.empty:
            return {
                "time": datetime.now(timezone.utc).isoformat(),
                "event": "hold",
                "reason": "waiting_closed_candle",
            }

        if not self.ml_filter.is_trained:
            self.ml_filter.train(analysis_df)

        higher_analysis_df = None
        if higher_df is not None:
            higher_analysis_df = self._closed_candles(higher_df)
            if higher_analysis_df.empty:
                higher_analysis_df = None
        macro_analysis_df = None
        if macro_df is not None:
            macro_analysis_df = self._closed_candles(macro_df)
            if macro_analysis_df.empty:
                macro_analysis_df = None

        row = analysis_df.iloc[-1]
        now: datetime = row["close_time"].to_pydatetime()
        if (
            self.last_processed_close_time is not None
            and now <= self.last_processed_close_time
        ):
            return {"time": now.isoformat(), "event": "hold", "reason": "duplicate_candle"}
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        quote_free, quote_locked, base_free, base_locked = self._balances()
        atr_value = float(
            atr(analysis_df["high"], analysis_df["low"], analysis_df["close"], 14).iloc[-1]
        )

        # Monitorear señales VIP activas contra precios de la vela actual
        try:
            from bot.vip_signal_bot import VIPSignalTracker
            tracker = VIPSignalTracker(self.cfg.event_db_path)
            try:
                tracker.check_price(self.cfg.symbol, high=high, low=low, close=close)
            finally:
                tracker.close()
        except Exception as track_exc:
            logging.debug("Error al verificar tracker VIP: %s", track_exc)

        signal = self.strategy.generate(
            analysis_df,
            higher_analysis_df,
            macro_df=macro_analysis_df,
            in_position=self.position is not None,
            entry_reason=self.position.entry_reason if self.position is not None else None,
        )
        regime_source = higher_analysis_df if higher_analysis_df is not None else analysis_df
        regime = classify_market(regime_source, self.cfg)

        # Si tenemos una posición abierta, primero verificamos las órdenes nativas en Binance
        if self.position is not None:
            db_pos = self.db_session.query(DBPosition).filter(
                DBPosition.symbol == self.cfg.symbol,
                DBPosition.is_active == True
            ).first()

            if db_pos:
                sl_filled = False
                tp_filled = False
                exit_price = close
                exit_time = datetime.utcnow()
                close_reason = None

                # Consultar órdenes en Binance
                if db_pos.stop_loss_order_id:
                    try:
                        sl_order = self.exec.get_order_status(self.cfg.symbol, db_pos.stop_loss_order_id)
                        if sl_order.get("status") == "FILLED":
                            sl_filled = True
                            exit_price = float(sl_order.get("price") or sl_order.get("avgPrice") or db_pos.stop_price)
                            update_time_ms = sl_order.get("updateTime")
                            if update_time_ms:
                                exit_time = datetime.fromtimestamp(update_time_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
                            close_reason = "stop_or_trailing"
                    except Exception as e:
                        logging.error(f"Error al verificar orden de Stop Loss {db_pos.stop_loss_order_id}: {e}")

                if not sl_filled and db_pos.take_profit_order_id:
                    try:
                        tp_order = self.exec.get_order_status(self.cfg.symbol, db_pos.take_profit_order_id)
                        if tp_order.get("status") == "FILLED":
                            tp_filled = True
                            exit_price = float(tp_order.get("price") or tp_order.get("avgPrice") or db_pos.take_profit_price)
                            update_time_ms = tp_order.get("updateTime")
                            if update_time_ms:
                                exit_time = datetime.fromtimestamp(update_time_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
                            close_reason = "take_profit"
                    except Exception as e:
                        logging.error(f"Error al verificar orden de Take Profit {db_pos.take_profit_order_id}: {e}")

                if sl_filled or tp_filled:
                    other_order_id = db_pos.take_profit_order_id if sl_filled else db_pos.stop_loss_order_id
                    if other_order_id:
                        try:
                            self.exec.cancel_order(self.cfg.symbol, other_order_id)
                            logging.info(f"Cancelada orden contraria activa {other_order_id}")
                        except Exception as e:
                            logging.warning(f"No se pudo cancelar orden contraria {other_order_id}: {e}")

                    self._close_db_position(db_pos, exit_price, exit_time, close_reason)
                    self.position = None
                    self.entry_fee_paid = 0.0
                    self.last_processed_close_time = now
                    pnl = (exit_price - db_pos.entry_price) * db_pos.quantity
                    pnl_pct = (pnl / (db_pos.entry_price * db_pos.quantity)) * 100 if db_pos.entry_price > 0 else 0.0
                    return {
                        "time": now.isoformat(),
                        "event": "live_sell",
                        "price": exit_price,
                        "qty": db_pos.quantity,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "reason": close_reason,
                        "strategy_mode": self.cfg.strategy_mode,
                    }

                # Trailing stop dinámico
                new_stop = close - (atr_value * self.cfg.trailing_atr_mult)
                if new_stop > self.position.stop_price:
                    logging.info(f"Actualizando Trailing Stop de {self.position.stop_price} a {new_stop}...")
                    if db_pos.stop_loss_order_id:
                        try:
                            self.exec.cancel_order(self.cfg.symbol, db_pos.stop_loss_order_id)
                        except Exception as e:
                            logging.warning(f"Error al cancelar SL anterior {db_pos.stop_loss_order_id}: {e}")
                    try:
                        limit_price = new_stop * (1 - self.cfg.slippage)
                        sl_order = self.exec.create_stop_loss_limit(
                            self.cfg.symbol,
                            self.position.quantity,
                            new_stop,
                            limit_price,
                            self.symbol_filters
                        )
                        sl_order_id = str(sl_order.get("orderId"))
                        self.position.stop_price = new_stop
                        db_pos.stop_price = new_stop
                        db_pos.stop_loss_order_id = sl_order_id
                        self.db_session.commit()
                        logging.info(f"Nuevo Trailing Stop colocado en Binance. Orden ID: {sl_order_id}")
                    except Exception as e:
                        logging.error(f"Error al colocar nuevo Trailing Stop en Binance: {e}")

                # Verificar si la estrategia indica salida
                if signal.action == "exit":
                    logging.info(f"Señal de salida detectada por estrategia: {signal.reason}. Cancelando órdenes nativas...")
                    if db_pos.stop_loss_order_id:
                        try:
                            self.exec.cancel_order(self.cfg.symbol, db_pos.stop_loss_order_id)
                        except Exception as e:
                            logging.warning(f"Error al cancelar SL {db_pos.stop_loss_order_id}: {e}")
                    if db_pos.take_profit_order_id:
                        try:
                            self.exec.cancel_order(self.cfg.symbol, db_pos.take_profit_order_id)
                        except Exception as e:
                            logging.warning(f"Error al cancelar TP {db_pos.take_profit_order_id}: {e}")

                    # Proceder con la venta a mercado
                    qty_to_sell = min(self.position.quantity, base_free)
                    qty_to_sell, qty_reason = self._prepare_live_qty(qty_to_sell, close)
                    if qty_to_sell <= 0:
                        self.last_processed_close_time = now
                        return {
                            "time": now.isoformat(),
                            "event": "live_position_open",
                            "reason": f"No se pudo vender: {qty_reason}",
                            "position_qty": self.position.quantity,
                        }

                    sell_cid = generate_client_order_id(self.cfg.symbol, "SELL", int(time.time() * 1000))
                    order = self.exec.create_market_sell(self.cfg.symbol, qty_to_sell, newClientOrderId=sell_cid)
                    fills = order.get("fills", []) if isinstance(order, dict) else []
                    if fills:
                        exit_price = calculate_vwap_fills(fills, close)
                    else:
                        quote_qty = _to_float(order.get("cummulativeQuoteQty"), 0.0)
                        exec_qty = _to_float(order.get("executedQty"), qty_to_sell)
                        exit_price = quote_qty / exec_qty if exec_qty > 0 else close

                    executed_qty = _to_float(order.get("executedQty"), qty_to_sell)
                    if executed_qty <= 0:
                        executed_qty = qty_to_sell

                    self._close_db_position(db_pos, exit_price, datetime.utcnow(), signal.reason)
                    self.position = None
                    self.entry_fee_paid = 0.0
                    self.last_processed_close_time = now
                    pnl = (exit_price - db_pos.entry_price) * executed_qty
                    pnl_pct = (pnl / (db_pos.entry_price * executed_qty)) * 100 if db_pos.entry_price > 0 else 0.0
                    return {
                        "time": now.isoformat(),
                        "event": "live_sell",
                        "price": exit_price,
                        "qty": executed_qty,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "reason": signal.reason,
                        "strategy_mode": self.cfg.strategy_mode,
                    }

            self.last_processed_close_time = now
            return {
                "time": now.isoformat(),
                "event": "live_position_open",
                "stop": self.position.stop_price,
                "take_profit": self.position.take_profit_price,
            }

        # Si no tenemos una posición, buscamos señal de compra
        base_notional = (base_free + base_locked) * close
        if (
            self.cfg.live_block_unknown_position
            and self.exec.notional_is_valid(base_free + base_locked, close, self.symbol_filters)
        ):
            self.last_processed_close_time = now
            return {
                "time": now.isoformat(),
                "event": "live_guard",
                "reason": "unknown_existing_position",
                "base_asset": self.base_asset,
                "base_total": base_free + base_locked,
                "estimated_notional": base_notional,
            }

        allowed, reason = self.risk.can_trade(
            now,
            self._equity(close),
            regime_name=regime.name,
            interval=self.cfg.interval,
        )
        if not allowed:
            self.last_processed_close_time = now
            return {"time": now.isoformat(), "event": "risk_pause", "reason": reason}

        if signal.action != "buy":
            self.last_processed_close_time = now
            return {"time": now.isoformat(), "event": "hold", "reason": signal.reason}

        # Validar señal de compra con el filtro predictivo de Machine Learning
        try:
            ml_prob = self.ml_filter.predict_probability(analysis_df)
            logging.info(f"[{self.cfg.symbol}] Probabilidad de éxito estimada por ML: {ml_prob:.2%}")
            if ml_prob < self.cfg.min_confidence:
                self.last_processed_close_time = now
                return {
                    "time": now.isoformat(),
                    "event": "hold",
                    "reason": f"ml_low_probability: {ml_prob:.2%} (< {self.cfg.min_confidence:.2%})"
                }
        except Exception as e:
            logging.error(f"Error al evaluar predicción de ML: {e}")

        # Consultar sentimiento de noticias antes de comprar
        try:
            sentiment_score, _ = self.news_analyzer.get_sentiment()
            logging.info(f"Índice de sentimiento de noticias: {sentiment_score:.2f}")
            if sentiment_score < -0.3:
                self.last_processed_close_time = now
                return {
                    "time": now.isoformat(),
                    "event": "hold",
                    "reason": f"bearish_news_sentiment: {sentiment_score:.2f}"
                }
        except Exception as e:
            logging.error(f"Error al analizar sentimiento de noticias en el ciclo: {e}")

        # Evaluación simétrica de 5 factores cuantitativos (S_composite >= 0.72)
        try:
            from bot.quant_engine import QuantEngine
            factor_breakdown = QuantEngine.evaluate_setup(
                symbol=self.cfg.symbol,
                df=analysis_df,
                ml_filter=self.ml_filter,
                news_analyzer=self.news_analyzer,
                is_crypto=True,
            )
            logging.info(
                f"[{self.cfg.symbol}] Composite 5-Factor Score: {factor_breakdown.composite:.3f} "
                f"(Trend={factor_breakdown.trend:.2f}, Mom={factor_breakdown.momentum:.2f}, "
                f"Vol={factor_breakdown.volatility:.2f}, ML={factor_breakdown.ml:.2f}, "
                f"Sent={factor_breakdown.sentiment:.2f})"
            )
            if not factor_breakdown.is_buy_authorized:
                self.last_processed_close_time = now
                return {
                    "time": now.isoformat(),
                    "event": "hold",
                    "reason": f"score_below_hurdle: {factor_breakdown.composite:.3f} < {factor_breakdown.details.get('hurdle', 0.72)}",
                    "composite_score": factor_breakdown.composite,
                }
        except Exception as q_err:
            logging.warning("[%s] Error al evaluar 5-factor scoring: %s", self.cfg.symbol, q_err)

        entry_ref = close * (1 + self.cfg.slippage)
        stop = entry_ref - (atr_value * self.cfg.stop_atr_mult)
        take = entry_ref + (entry_ref - stop) * self.cfg.take_profit_rr

        # Salvaguarda de volatilidad extrema (rejection si ATR > 3x baseline)
        vol_allowed, vol_ratio, vol_reason = self.risk.validate_volatility_regime(analysis_df)
        if not vol_allowed:
            self.last_processed_close_time = now
            return {
                "time": now.isoformat(),
                "event": "risk_pause",
                "reason": vol_reason,
                "volatility_ratio": vol_ratio,
            }

        # Salvaguarda de circuit breaker global y cerrojo fiduciario (-6.4%)
        cb_allowed, cb_reason = self.risk.check_global_circuit_breaker(self._equity(close))
        if not cb_allowed:
            self.last_processed_close_time = now
            return {
                "time": now.isoformat(),
                "event": "risk_pause",
                "reason": cb_reason,
            }

        # Salvaguarda de slippage pre-trade contra profundidad del libro de órdenes
        current_ob = order_book
        if current_ob is None:
            try:
                if hasattr(self.exec, "get_order_book"):
                    current_ob = self.exec.get_order_book(self.cfg.symbol)
                elif hasattr(self.exec, "client") and hasattr(self.exec.client, "get_order_book"):
                    current_ob = self.exec.client.get_order_book(symbol=self.cfg.symbol, limit=20)
                elif hasattr(self.data, "get_order_book"):
                    current_ob = self.data.get_order_book(self.cfg.symbol)
            except Exception as ob_err:
                logging.debug("No se pudo obtener order book para pre-trade slippage: %s", ob_err)

        if current_ob is not None:
            slip_allowed, projected_slip, slip_reason = self.risk.validate_pre_trade_slippage(
                current_ob, expected_price=entry_ref, max_slippage=self.cfg.slippage
            )
            if not slip_allowed:
                self.last_processed_close_time = now
                return {
                    "time": now.isoformat(),
                    "event": "slippage_guard_rejected",
                    "reason": slip_reason,
                    "projected_slippage": projected_slip,
                }

        # Redimir de Simple Earn Flexible si falta liquidez para esta entrada
        if self.earn is not None and quote_free < self.cfg.live_max_quote_per_trade:
            try:
                redeemed = self.earn.ensure_quote_available(
                    self.quote_asset, self.cfg.live_max_quote_per_trade
                )
                if redeemed > 0:
                    logging.info(
                        f"[{self.cfg.symbol}] Earn: redimidos {redeemed:.4f} "
                        f"{self.quote_asset} de flexible para la entrada."
                    )
                    time.sleep(2)  # esperar acreditacion de la redencion rapida
                    quote_free, quote_locked, base_free, base_locked = self._balances()
            except Exception as exc:
                logging.warning(f"[{self.cfg.symbol}] Earn: redencion fallo: {exc}")

        if quote_free < max(self.symbol_filters.min_notional, 1.0):
            self.last_processed_close_time = now
            return {
                "time": now.isoformat(),
                "event": "hold",
                "reason": "insufficient_quote_balance",
                "quote_asset": self.quote_asset,
                "quote_free": quote_free,
            }

        qty = self.risk.position_size(
            equity=self.cash,
            entry_price=entry_ref,
            stop_price=stop,
            fee_rate=self.cfg.fee_rate,
        )
        quote_cap = min(self.cash, self.cfg.live_max_quote_per_trade)
        if quote_free > 0:
            quote_cap = min(quote_cap, quote_free)
        qty = min(qty, quote_cap / (entry_ref * (1 + self.cfg.fee_rate)))
        qty, qty_reason = self._prepare_live_qty(qty, entry_ref)
        if qty <= 0:
            self.last_processed_close_time = now
            return {"time": now.isoformat(), "event": "hold", "reason": qty_reason}

        # Registrar y emitir señal VIP
        try:
            from bot.vip_signal_bot import VIPSignalTracker
            tracker = VIPSignalTracker(self.cfg.event_db_path)
            tracker.register_signal(
                self.cfg.symbol,
                "BUY",
                entry_ref,
                stop,
                reason=signal.reason,
                strategy_mode=self.cfg.strategy_mode,
            )
        except Exception as texc:
            logging.warning("Error al registrar señal VIP: %s", texc)

        # Generar client order id determinista
        client_order_id = generate_client_order_id(self.cfg.symbol, "BUY", int(time.time() * 1000))

        # Fase 1: Registrar intención de compra en DB (Two-Phase Commit)
        db_pos = DBPosition(
            symbol=self.cfg.symbol,
            entry_time=now,
            entry_price=entry_ref,
            quantity=qty,
            stop_price=stop,
            take_profit_price=take,
            stop_loss_order_id=None,
            take_profit_order_id=None,
            client_order_id=client_order_id,
            state=OrderState.PENDING_SUBMIT.value,
            is_active=True,
            entry_reason=signal.reason
        )
        self.db_session.add(db_pos)
        self.db_session.commit()

        # Fase 2: Ejecutar compra a mercado en Binance pasando newClientOrderId
        try:
            order = self.exec.create_market_buy(self.cfg.symbol, qty, newClientOrderId=client_order_id)
        except Exception as e:
            err_msg = str(e)
            if "-2015" in err_msg or "permissions" in err_msg or getattr(self.cfg, "signal_provider_mode", False):
                logging.info(f"[{self.cfg.symbol}] Modo Proveedor de Señales: Señal VIP emitida a Telegram (API Key en modo lectura).")
                db_pos.state = OrderState.CANCELLED.value
                db_pos.is_active = False
                self.db_session.commit()
                self.last_processed_close_time = now
                return {
                    "time": now.isoformat(),
                    "event": "vip_signal_published",
                    "price": entry_ref,
                    "stop": stop,
                    "take": take,
                    "qty": qty,
                    "reason": signal.reason,
                    "strategy_mode": self.cfg.strategy_mode,
                    "confidence": getattr(signal, "confidence", 0.85),
                    "news_sentiment": getattr(self, "news_sentiment_score", 0.0),
                }
            is_timeout = (
                "timeout" in err_msg.lower()
                or "timed out" in err_msg.lower()
                or "connection" in err_msg.lower()
                or "reset" in err_msg.lower()
            )
            if is_timeout:
                resolved_order = None
                try:
                    resolved_order = self.exec.get_order_status(self.cfg.symbol, origClientOrderId=client_order_id)
                except Exception as status_err:
                    logging.warning(f"No se pudo consultar estado de orden en Binance para {client_order_id}: {status_err}")

                if resolved_order and resolved_order.get("status") in ("FILLED", "PARTIALLY_FILLED"):
                    order = resolved_order
                    logging.info(f"[{self.cfg.symbol}] Recuperada orden ejecutada {client_order_id} tras timeout")
                else:
                    db_pos.state = OrderState.PENDING_SUBMIT.value
                    db_pos.error_details = f"Timeout during dispatch: {e}"
                    db_pos.is_active = True
                    self.db_session.commit()
                    raise
            else:
                db_pos.state = OrderState.FAILED.value
                db_pos.error_details = str(e)
                db_pos.is_active = False
                self.db_session.commit()
                raise

        fills = order.get("fills", []) if isinstance(order, dict) else []
        if fills:
            entry_price = calculate_vwap_fills(fills, entry_ref)
        else:
            quote_qty = _to_float(order.get("cummulativeQuoteQty"), 0.0)
            exec_qty = _to_float(order.get("executedQty"), qty)
            entry_price = quote_qty / exec_qty if exec_qty > 0 else entry_ref

        executed_qty = _to_float(order.get("executedQty"), qty)
        if executed_qty <= 0:
            executed_qty = qty

        # Auditoría post-fill de slippage
        audit_ok, actual_slip, audit_reason = self.risk.audit_post_fill_slippage(
            executed_price=entry_price,
            expected_price=entry_ref,
            max_slippage=self.cfg.slippage,
            multiplier=2.0,
        )
        if not audit_ok:
            logging.warning(
                f"[{self.cfg.symbol}] ALERTA POST-FILL SLIPPAGE: Desviación excesiva "
                f"({actual_slip:.4%} vs {self.cfg.slippage:.4%}). {audit_reason}"
            )
            self.risk.trigger_slippage_pause(actual_slip, audit_reason)

        # Actualizar datos de ejecución real en db_pos
        db_pos.entry_price = entry_price
        db_pos.quantity = executed_qty
        db_pos.state = OrderState.FILLED.value

        # Calcular precios de SL y TP basados en el precio de entrada real VWAP
        stop = entry_price - (atr_value * self.cfg.stop_atr_mult)
        take = entry_price + (entry_price - stop) * self.cfg.take_profit_rr
        db_pos.stop_price = stop
        db_pos.take_profit_price = take
        self.db_session.commit()

        sl_order_id = None
        tp_order_id = None
        sl_placed = False

        # Colocar órdenes nativas de SL en Binance con reintento (Anti-Orphan Pipeline)
        logging.info("Colocando órdenes de protección SL nativas en Binance...")
        for sl_attempt in range(2):
            try:
                sl_cid = generate_client_order_id(self.cfg.symbol, "STOP", int(time.time() * 1000))
                limit_sl_price = stop * (1 - self.cfg.slippage)
                sl_order = self.exec.create_stop_loss_limit(
                    self.cfg.symbol,
                    executed_qty,
                    stop,
                    limit_sl_price,
                    self.symbol_filters,
                    newClientOrderId=sl_cid
                )
                sl_order_id = str(sl_order.get("orderId"))
                db_pos.stop_loss_order_id = sl_order_id
                self.db_session.commit()
                sl_placed = True
                logging.info(f"Orden de Stop Loss colocada. ID: {sl_order_id}")
                break
            except Exception as e:
                logging.warning(f"Intento {sl_attempt+1}/2 falló al colocar Stop Loss: {e}")
                time.sleep(0.3)

        if not sl_placed:
            logging.error("FALLO CRÍTICO al colocar Stop Loss en Binance. Activando fallback anti-huérfano...")
            sold = False
            try:
                fsell_cid = generate_client_order_id(self.cfg.symbol, "FSEL", int(time.time() * 1000))
                sell_order = self.exec.create_market_sell(self.cfg.symbol, executed_qty, newClientOrderId=fsell_cid)
                sell_fills = sell_order.get("fills", []) if isinstance(sell_order, dict) else []
                sell_exit_price = calculate_vwap_fills(sell_fills, stop)
                self._close_db_position(db_pos, sell_exit_price, datetime.utcnow(), "anti_orphan_emergency_sell")
                self.position = None
                sold = True
                logging.info("Posición huérfana liquidada exitosamente por seguridad.")
                self.last_processed_close_time = now
                return {"time": now.isoformat(), "event": "anti_orphan_liquidated", "reason": "emergency_market_sell_executed"}
            except Exception as ex:
                logging.critical(f"Fallo al vender posición tras fallo de SL: {ex}")

            if not sold:
                db_pos.state = OrderState.UNHEDGED_CRITICAL.value
                db_pos.error_details = "Stop Loss and fallback market sell failed"
                db_pos.is_active = True
                self.db_session.commit()
                self.position = Position(
                    entry_time=now,
                    entry_price=entry_price,
                    quantity=executed_qty,
                    stop_price=stop,
                    take_profit_price=take,
                    entry_reason=signal.reason
                )
                self.last_processed_close_time = now
                return {"time": now.isoformat(), "event": "live_error", "reason": "UNHEDGED_CRITICAL: sl_and_emergency_sell_failed"}

        # Si el SL se colocó con éxito, intentar colocar Take Profit
        try:
            tp_cid = generate_client_order_id(self.cfg.symbol, "TAKE", int(time.time() * 1000))
            tp_order = self.exec.create_take_profit_limit(
                self.cfg.symbol,
                executed_qty,
                take,
                take,
                self.symbol_filters,
                newClientOrderId=tp_cid
            )
            tp_order_id = str(tp_order.get("orderId"))
            db_pos.take_profit_order_id = tp_order_id
            logging.info(f"Orden de Take Profit colocada. ID: {tp_order_id}")
        except Exception as e:
            logging.warning(f"Error al colocar Take Profit en Binance: {e}. Se gestionará de forma manual si falla.")

        db_pos.state = OrderState.FILLED.value
        self.db_session.commit()

        self.position = Position(
            entry_time=now,
            entry_price=entry_price,
            quantity=executed_qty,
            stop_price=stop,
            take_profit_price=take,
            entry_reason=signal.reason
        )

        self.risk.register_entry(now)
        self.last_processed_close_time = now
        return {
            "time": now.isoformat(),
            "event": "live_buy",
            "price": entry_price,
            "qty": executed_qty,
            "stop": stop,
            "take": take,
            "signal_confidence": signal.confidence,
            "strategy_mode": self.cfg.strategy_mode,
            "reason": signal.reason,
        }



def run_backtest(cfg: BotConfig, symbol: str | None, interval: str | None, lookback: int | None):
    local_cfg = replace(
        cfg,
        symbol=symbol or cfg.symbol,
        interval=interval or cfg.interval,
        lookback=lookback or cfg.lookback,
    )

    data = BinanceDataClient()
    df = data.get_klines(local_cfg.symbol, local_cfg.interval, local_cfg.lookback)
    strategy = HybridStrategy(local_cfg)
    risk = RiskManager(local_cfg)
    backtester = Backtester(local_cfg, strategy, risk)
    result = backtester.run(df)
    telemetry = build_telemetry(local_cfg)
    telemetry.record("backtest", local_cfg.symbol, "backtest_metrics", result["metrics"])
    print(json.dumps(result["metrics"], indent=2, default=str))


def run_optimize(cfg: BotConfig, symbol: str | None, interval: str | None, lookback: int | None, top: int):
    local_cfg = replace(
        cfg,
        symbol=symbol or cfg.symbol,
        interval=interval or cfg.interval,
        lookback=lookback or cfg.lookback,
    )
    data = BinanceDataClient()
    df = data.get_klines(local_cfg.symbol, local_cfg.interval, local_cfg.lookback)
    best = optimize_config(local_cfg, df, top_n=top)
    telemetry = build_telemetry(local_cfg)
    telemetry.record(
        "optimize",
        local_cfg.symbol,
        "optimize_result",
        {"top": top, "candidates": best},
    )
    print(json.dumps(best, indent=2, default=str))


def run_walkforward_cmd(
    cfg: BotConfig,
    symbol: str | None,
    interval: str | None,
    lookback: int | None,
    train_size: int,
    test_size: int,
    step_size: int,
    top: int,
    full_grid: bool,
) -> None:
    local_cfg = replace(
        cfg,
        symbol=symbol or cfg.symbol,
        interval=interval or cfg.interval,
        lookback=lookback or cfg.lookback,
    )
    data = BinanceDataClient()
    df = data.get_klines(local_cfg.symbol, local_cfg.interval, local_cfg.lookback)
    report = run_walkforward(
        base_cfg=local_cfg,
        df=df,
        train_size=train_size,
        test_size=test_size,
        step_size=step_size,
        top_n=top,
        fast_grid=not full_grid,
    )
    telemetry = build_telemetry(local_cfg)
    telemetry.record(
        "walkforward",
        local_cfg.symbol,
        "walkforward_summary",
        report["summary"],
    )
    print(json.dumps(report, indent=2, default=str))


def _assert_live_ready(cfg: BotConfig, confirm_live: str) -> None:
    if not cfg.live_enabled:
        raise RuntimeError(
            "LIVE_ENABLED=false. Set LIVE_ENABLED=true in .env to unlock live mode."
        )
    if confirm_live != "I_UNDERSTAND_LIVE_RISK":
        raise RuntimeError(
            "Missing live confirmation. Use --confirm-live I_UNDERSTAND_LIVE_RISK"
        )
    if not cfg.binance_api_key or not cfg.binance_api_secret:
        raise RuntimeError("BINANCE_API_KEY/BINANCE_API_SECRET are required for live mode.")
    if not cfg.use_testnet and not cfg.allow_real_trading:
        raise RuntimeError(
            "USE_TESTNET=false requires ALLOW_REAL_TRADING=true. Keep testnet enabled until live risk is explicitly accepted."
        )


def _build_autotune_grid(strategy_modes: list[str]) -> dict[str, list[Any]]:
    return {
        "strategy_mode": strategy_modes,
        "use_regime_filter": [True],
        "use_multi_timeframe": [False],
        "stop_atr_mult": [1.4, 1.8, 2.2],
        "take_profit_rr": [1.5, 2.2, 2.8],
        "trailing_atr_mult": [1.0],
        "min_confidence": [0.5, 0.6],
        "min_entry_quality": [0.7, 0.75],
        "min_trend_strength": [0.001],
        "risk_per_trade": [0.01],
    }


def _autotune_candidate_is_acceptable(candidate: dict[str, Any]) -> bool:
    metrics = candidate.get("metrics", {})
    return (
        float(candidate.get("score", 0.0)) > 0.0
        and float(metrics.get("roi_pct", 0.0)) > 0.0
        and float(metrics.get("profit_factor", 0.0)) >= 1.0
        and int(metrics.get("num_trades", 0)) >= 5
    )


def perform_auto_tuning(
    cfg: BotConfig,
    telemetry,
    lease_key: str = "runtime:live-loop",
    lease_owner: str | None = None,
) -> None:
    """Ejecuta auto-optimización para todos los símbolos activos y guarda la mejor configuración en DB."""
    if not cfg.auto_tune_enabled:
        return

    now = datetime.utcnow()
    # Comprobar cuándo se ejecutó el último tuning en la DB
    from bot.db import get_db_session, DBBotState
    session = get_db_session(cfg.event_db_path)
    try:
        last_tune_record = session.query(DBBotState).filter(DBBotState.key == "last_auto_tune_time").first()
        
        should_tune = False
        if not last_tune_record:
            should_tune = True
        else:
            try:
                last_tune_time = datetime.fromisoformat(json.loads(last_tune_record.value_json))
                hours_passed = (now - last_tune_time).total_seconds() / 3600.0
                if hours_passed >= cfg.auto_tune_interval_hours:
                    should_tune = True
            except Exception:
                should_tune = True

        if not should_tune:
            session.close()
            return
    except Exception as e:
        logging.warning("Error comprobando fecha del último auto-tune: %s", e)
    data = BinanceDataClient()
    
    tuned_summary = []
    for symbol in cfg.symbols_to_trade:
        if lease_owner and hasattr(telemetry, "store"):
            try:
                telemetry.store.refresh_lease(lease_key, lease_owner)
            except Exception as lexc:
                logging.warning("No se pudo refrescar lease durante auto-tune: %s", lexc)
        try:
            logging.info(f"[{symbol}] Corriendo optimización de parámetros...")
            from dataclasses import replace
            from bot.optimizer import optimize_config
            
            # Crear config temporal para el tuning
            local_cfg = replace(cfg, symbol=symbol)
            # Descargar histórico de klines
            df = data.get_klines(symbol, local_cfg.interval, local_cfg.lookback)
            if df is None or len(df) < 100:
                logging.warning(f"[{symbol}] No hay suficientes datos para auto-tune ({len(df) if df is not None else 0} velas)")
                continue

            grid = _build_autotune_grid([
                "auto",
                "turtle_breakout",
                "connors_rsi",
                "elder_triple",
                "williams_alligator",
                "mean_reversion"
            ])
            candidates = optimize_config(
                local_cfg,
                df,
                top_n=1,
                grid=grid,
                min_trades=3,
            )
            if candidates:
                best = candidates[0]
                if not _autotune_candidate_is_acceptable(best):
                    logging.warning(
                        "[%s] Auto-tune descartado: score=%s roi=%s profit_factor=%s trades=%s",
                        symbol,
                        best.get("score"),
                        best.get("metrics", {}).get("roi_pct"),
                        best.get("metrics", {}).get("profit_factor"),
                        best.get("metrics", {}).get("num_trades"),
                    )
                    continue
                optimal_params = best["params"]
                # Guardar el set de parámetros óptimos en la DB
                opt_key = f"optimal_config:{symbol}"
                opt_record = session.query(DBBotState).filter(DBBotState.key == opt_key).first()
                payload = {
                    "time": now.isoformat(),
                    "params": optimal_params,
                    "metrics": best["metrics"],
                    "score": best["score"]
                }
                if not opt_record:
                    opt_record = DBBotState(key=opt_key, value_json=json.dumps(payload))
                    session.add(opt_record)
                else:
                    opt_record.value_json = json.dumps(payload)
                    opt_record.updated_ts = now
                session.commit()
                logging.info(f"[{symbol}] Configuración guardada en DB. Parámetros ganadores: {optimal_params}")
                # Registrar evento
                telemetry.record("auto_tune", symbol, "optimal_config_saved", payload)
                mode = optimal_params.get("strategy_mode", "auto")
                stop_atr = optimal_params.get("stop_atr_mult", 1.8)
                tp_rr = optimal_params.get("take_profit_rr", 2.2)
                STRATEGY_DESCS = {
                    "turtle_breakout": "Ruptura de Canal",
                    "connors_rsi": "Retroceso en Tendencia (Buy the Dip)",
                    "elder_triple": "Triple Pantalla de Elder",
                    "williams_alligator": "Tendencia Alligator",
                    "mean_reversion": "Reversión a la Media (Rango)",
                    "auto": "Estructura Adaptativa"
                }
                mode_desc = STRATEGY_DESCS.get(mode, mode)
                tuned_summary.append(
                    f"• #{symbol}: Estrategia de {mode_desc} (SL: {stop_atr:.1f}x ATR | Target R:R: {tp_rr:.1f})"
                )
        except Exception as e:
            logging.error(f"[{symbol}] Error durante el auto-tuning: {e}")

    # Guardar timestamp de último tuning exitoso
    try:
        fresh_record = session.query(DBBotState).filter(DBBotState.key == "last_auto_tune_time").first()
        if not fresh_record:
            fresh_record = DBBotState(key="last_auto_tune_time", value_json=json.dumps(now.isoformat()))
            session.add(fresh_record)
        else:
            fresh_record.value_json = json.dumps(now.isoformat())
            fresh_record.updated_ts = now
        session.commit()
    except Exception as e:
        session.rollback()
        logging.warning(f"Error al guardar last_auto_tune_time: {e}. Reintentando con ORM...")
        try:
            session.merge(
                DBBotState(
                    key="last_auto_tune_time",
                    value_json=json.dumps(now.isoformat()),
                    updated_ts=now,
                )
            )
            session.commit()
        except Exception as e2:
            logging.error(f"Fallo crítico al guardar timestamp de auto-tune: {e2}")
    finally:
        session.close()
    logging.info("--- Ciclo de Auto-Tuning finalizado ---")

    # Enviar reporte a Telegram si está activo
    if tuned_summary:
        msg = (
            "<b>[RECALIBRACIÓN CUANTITATIVA] ESTRATEGIAS RECALIBRADAS</b>\n\n"
            "Se ha completado el ciclo de optimización walk-forward programado. Las siguientes configuraciones han sido actualizadas:\n\n"
            + "\n".join(tuned_summary)
        )
        telemetry.alert(msg, category="autotune")

    # Enviar reporte a Binance Square si está activo
    if tuned_summary and cfg.binance_square_enabled:
        try:
            from bot.growth_traffic_engine import enqueue_square_post
            from bot.news_sentiment import NewsSentimentAnalyzer
            
            news_analyzer = NewsSentimentAnalyzer(cfg.event_db_path)
            sentiment_score, headlines = news_analyzer.get_sentiment()
            
            if sentiment_score > 0.15:
                sent_emoji = "Alcista"
            elif sentiment_score < -0.15:
                sent_emoji = "Bajista"
            else:
                sent_emoji = "Neutral"
                
            headline_str = ""
            if headlines:
                headline_str = "\nTitulares destacados que estoy vigilando:\n" + "\n".join([f"• {h['title']}" for h in headlines[:2]])

            msg = (
                f"Actualización de estrategia y cobertura técnica para la sesión de hoy.\n\n"
                f"Tras revisar el mapa de liquidez global y el comportamiento de la volatilidad en los principales pares, ajustamos los parámetros de entrada en la cartera para mantener la mejor relación riesgo/beneficio en cada activo:\n\n"
                + "\n".join(tuned_summary) + f"\n\n"
                f"En el plano macroeconómico, la lectura de sentimiento en los titulares se mantiene en tono {sent_emoji} (sesgo {sentiment_score:+.2f})."
                + headline_str + "\n\n"
                f"Seguimos priorizando la paciencia y una ejecución disciplinada en cada zona clave."
            )
            enqueue_square_post(
                msg,
                is_priority=False,
                metadata={"archetype": "recalibration_summary", "source": "auto_tune_cycle"},
                cfg=cfg,
            )
        except Exception as e:
            logging.error(f"Error al enviar reporte de auto-tuning a Binance Square: {e}")

run_auto_tune_cycle = perform_auto_tuning


def _publish_live_event_to_square(cfg: BotConfig, symbol: str, event: dict[str, Any]) -> None:
    if not cfg.binance_square_enabled:
        return
    try:
        from bot.binance_square import BinanceSquareContentGenerator, sanitize_for_square
        from bot.growth_traffic_engine import enqueue_square_post
        from bot.news_sentiment import NewsSentimentAnalyzer
        
        event_name = event.get("event")
        price = float(event.get("price", 0.0))
        qty = float(event.get("qty", 0.0))
        stop = float(event.get("stop", 0.0))
        take = float(event.get("take", 0.0))
        reason = str(event.get("reason", "unknown"))
        strategy_mode = str(event.get("strategy_mode", cfg.strategy_mode))
        composite_score = float(event.get("composite_score", event.get("quality", 0.75)))
        
        # Mapeo de descripción humana y profesional de la estrategia
        REASON_DESCS = {
            "turtle_breakout": "Confirmación de ruptura de resistencia local por encima del Canal Donchian de corto plazo, apoyada por una aceleración del volumen y un ADX en zona de expansión de tendencia.",
            "connors_rsi": "Retroceso técnico controlado dentro de una estructura alcista de largo plazo. El RSI de periodo ultra corto muestra condiciones extremas de sobreventa temporal, ofreciendo un punto de entrada de alta probabilidad (comprando en zona de soporte local).",
            "elder_triple": "Setup tendencial de Triple Pantalla. La tendencia estructural macro (EMA 200) es alcista, se completó una corrección menor a nivel intermedio, y el gatillo en 15m confirma el reinicio del flujo de compra con MACD e histograma ascendentes.",
            "williams_alligator": "Fase inicial de expansión de medias móviles rápidas (Alligator en vigilia). Tras un periodo de compresión de rango, el precio rompe al alza confirmando un desequilibrio entre oferta y demanda con volumen creciente.",
            "mean_reversion": "Rebotando desde niveles extremos de desviación en la banda inferior de Bollinger. El precio muestra absorción de ventas cerca de zonas de liquidez clave en el soporte del rango lateral."
        }
        reason_desc = REASON_DESCS.get(strategy_mode, f"Setup técnico de confirmación en base a {reason}.")
        
        # Consultar sentimiento de noticias para acompañar la señal (solo caché local / no bloqueante)
        sent_desc = "neutral, lo que favorece setups técnicos limpios"
        sent_emoji = "Neutral (0.00)"
        headline_bullet = ""
        try:
            news_analyzer = NewsSentimentAnalyzer(cfg.event_db_path)
            # Evitar fetch de red síncrono de 15 segundos en el hilo en vivo; solo consultar datos en caché
            try:
                sentiment_score, headlines = news_analyzer.get_sentiment(allow_network=False)
            except TypeError:
                sentiment_score, headlines = 0.0, []
                
            if sentiment_score > 0.15:
                sent_desc = "bastante alcista, con noticias muy positivas impulsando al sector"
                sent_emoji = "Alcista (+{:.2f})".format(sentiment_score)
            elif sentiment_score < -0.15:
                sent_desc = "algo bajista por titulares negativos, pero vemos absorción de compra"
                sent_emoji = "Bajista ({:.2f})".format(sentiment_score)
            else:
                sent_desc = "neutral, lo que favorece setups técnicos limpios"
                sent_emoji = "Neutral ({:.2f})".format(sentiment_score)
                
            if headlines:
                headline_bullet = f"\nTitular clave del momento: \"{headlines[0]['title']}\""
        except Exception as sent_exc:
            logging.debug("Error leyendo sentimiento para Binance Square: %s", sent_exc)
            
        msg = ""
        is_priority = False
        archetype = "live_event"

        if event_name == "live_buy":
            is_priority = True
            archetype = "quant_setup"
            diff = abs(price - stop) if (stop > 0 and stop != price) else (price * 0.025)
            tp1 = take if take > price else (price + diff * 0.75)
            tp2 = price + (diff * 1.50)
            tp3 = price + (diff * 2.50)
            
            full_thesis = f"{reason_desc} Con respecto al flujo de noticias del sector, detectamos un entorno {sent_desc}.{headline_bullet}"
            msg = BinanceSquareContentGenerator.generate_quant_setup_post(
                symbol=symbol,
                action="BUY",
                entry_price=price,
                stop_price=stop,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                composite_score=max(composite_score, getattr(cfg, "binance_square_min_score", 0.72)),
                timeframe=getattr(cfg, "interval", "15m"),
                thesis=full_thesis,
            )
        elif event_name == "live_sell":
            archetype = "trade_close"
            pnl = float(event.get("pnl", 0.0))
            pnl_pct = float(event.get("pnl_pct", 0.0))
            
            pnl_title = "Objetivo de ganancias alcanzado (Take Profit)" if pnl >= 0 else "Activación de límite de protección (Stop Loss)"
            pnl_desc = "Acabamos de cerrar la posición asegurando beneficios en nuestra zona objetivo de liquidez." if pnl >= 0 else "La posición se cerró automáticamente en el nivel de invalidación para proteger el capital de la cartera."
            
            if "exit" in reason.lower() or "prematura" in reason.lower() or "trend_or_momentum" in reason.lower() or "target_exit" in reason.lower():
                pnl_title = "Salida táctica por rotación de flujo"
                pnl_desc = "Decidimos cerrar la operación de forma anticipada tras detectar pérdida de impulso comprador en las temporalidades menores."
            
            raw_msg = (
                f"Cierre de operación en #{symbol}: {pnl_title}.\n\n"
                f"{pnl_desc}\n\n"
                f"Resumen de la ejecución:\n"
                f"• Precio de salida: {price:.4f} USDT\n"
                f"• Resultado neto: {pnl_pct:+.2f}% ({pnl:+.4f} USDT)\n"
                f"• Motivo técnico: {reason}\n"
                f"• Entorno de sentimiento: {sent_emoji}"
                f"{headline_bullet}\n\n"
                f"Mantenemos la liquidez lista para el próximo setup de alta probabilidad.\n\n"
                f"{BinanceSquareContentGenerator.CTA_TELEGRAM}\n"
                f"{BinanceSquareContentGenerator.CTA_PORTAL}\n\n"
                f"{BinanceSquareContentGenerator.HASHTAGS}"
            )
            msg = sanitize_for_square(raw_msg)
            
        if msg:
            enqueue_square_post(
                msg,
                is_priority=is_priority,
                metadata={"symbol": symbol, "event": event_name, "archetype": archetype},
                cfg=cfg,
            )
    except Exception as e:
        logging.error(f"Error al encolar publicación de señal en vivo a Binance Square: {e}")


def run_live(cfg: BotConfig, confirm_live: str) -> None:
    _assert_live_ready(cfg, confirm_live)
    telemetry = build_telemetry(cfg)
    try:
        perform_auto_tuning(cfg, telemetry)
        
        from dataclasses import replace
        for symbol in cfg.symbols_to_trade:
            trader = None
            try:
                symbol_cfg = replace(cfg, symbol=symbol)
                trader = LiveTrader(symbol_cfg, state_store=telemetry.store)
                event = trader.step()
                trader.save_state()
                event_name = str(event.get("event", "live_event"))
                telemetry.record("live", symbol, event_name, event)
                if event_name in {"live_buy", "live_sell", "risk_pause", "live_guard"}:
                    category = "general"
                    if "buy" in event_name:
                        category = "buys"
                    elif "sell" in event_name:
                        category = "sells"
                    elif "error" in event_name or "pause" in event_name or "guard" in event_name:
                        category = "errors"
                    try:
                        telemetry.alert(_format_live_alert(symbol, event), category=category)
                    except Exception as a_exc:
                        logging.warning("[%s] Error enviando alerta de telemetría: %s", symbol, a_exc)
                    try:
                        _publish_live_event_to_square(cfg, symbol, event)
                    except Exception as sq_exc:
                        logging.debug("[%s] Error publicando a Square: %s", symbol, sq_exc)
                print(f"[{symbol}] Step Result:", json.dumps(event, indent=2, default=str))
            except Exception as e:
                import traceback
                logging.error(f"[{symbol}] Error en run_live: {e}")
                err_event = {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "event": "live_error",
                    "error_type": type(e).__name__,
                    "message": str(e),
                    "traceback": traceback.format_exc(),
                }
                try:
                    telemetry.record("live", symbol, "live_error", err_event)
                except Exception:
                    pass
                try:
                    telemetry.alert(_format_live_alert(symbol, err_event), category="errors")
                except Exception:
                    pass
            finally:
                if trader is not None:
                    try:
                        trader.close()
                    except Exception:
                        pass

        _run_earn_sweep(cfg, telemetry)
    finally:
        try:
            telemetry.close()
        except Exception:
            pass


def _run_earn_sweep(cfg: BotConfig, telemetry, earn: EarnManager | None = None) -> None:
    """Barrido Earn tolerante a fallos: nunca interrumpe el trading."""
    if not cfg.earn_enabled or cfg.use_testnet:
        return
    try:
        if earn is None:
            exec_client = BinanceExecutionClient(cfg)
            earn = build_earn_manager(cfg, exec_client.client)
        if earn is None:
            return
        summary = earn.sweep_cycle(telemetry)
        if summary.get("event") == "earn_sweep":
            telemetry.record("live", "EARN", "earn_sweep", summary)
            logging.info("Earn sweep: %s", json.dumps(summary, default=str))
    except Exception as exc:
        logging.warning("Earn sweep fallo: %s", exc)


def run_live_loop(
    cfg: BotConfig,
    confirm_live: str,
    cycles: int = 0,
    sleep_seconds: int = 300,
) -> None:
    _assert_live_ready(cfg, confirm_live)
    telemetry = build_telemetry(cfg)
    lease_key = "runtime:live-loop"
    lease_owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
    lease_ttl = max(600, sleep_seconds * 3)
    if not telemetry.store.acquire_lease(lease_key, lease_owner, lease_ttl):
        try:
            telemetry.close()
        except Exception:
            pass
        raise RuntimeError(
            "Otra instancia live-loop posee el bloqueo distribuido. "
            "Detenla antes de iniciar una segunda instancia."
        )

    # Configuración de captura elegante de señales (SIGINT/SIGTERM)
    stop_event = threading.Event()

    def _shutdown_signal_handler(signum: int, frame: Any) -> None:
        sig_name = "SIGINT" if signum == signal.SIGINT else (
            "SIGTERM" if hasattr(signal, "SIGTERM") and signum == signal.SIGTERM else str(signum)
        )
        logging.info("Señal de interrupción %s recibida. Iniciando parada ordenada (graceful teardown)...", sig_name)
        stop_event.set()

    old_sigint = None
    old_sigterm = None
    try:
        old_sigint = signal.signal(signal.SIGINT, _shutdown_signal_handler)
    except (ValueError, AttributeError):
        pass

    if hasattr(signal, "SIGTERM"):
        try:
            old_sigterm = signal.signal(signal.SIGTERM, _shutdown_signal_handler)
        except (ValueError, AttributeError):
            pass

    traders: dict[str, LiveTrader] = {}
    tuning_thread: threading.Thread | None = None

    def _trigger_async_auto_tune() -> None:
        nonlocal tuning_thread
        if not cfg.auto_tune_enabled:
            return
        if tuning_thread is not None and tuning_thread.is_alive():
            return
        tuning_thread = threading.Thread(
            target=perform_auto_tuning,
            args=(cfg, telemetry, lease_key, lease_owner),
            daemon=True,
            name="AutoTuningWorker",
        )
        tuning_thread.start()
        logging.info("Auto-tuning cuantitativo iniciado en hilo secundario (sin bloquear el bucle de trading).")

    try:
        # Ejecutar auto-tuning en segundo plano sin retrasar el arranque del loop
        _trigger_async_auto_tune()

        # Inicializar traders para cada símbolo activo
        from dataclasses import replace
        for symbol in cfg.symbols_to_trade:
            symbol_cfg = replace(cfg, symbol=symbol)
            traders[symbol] = LiveTrader(symbol_cfg, state_store=telemetry.store)

        # Inicializar bot interactivo de ventas y comandos VIP en Telegram
        if cfg.telegram_enabled and cfg.telegram_bot_token:
            try:
                from bot.vip_signal_bot import VIPSignalTracker, TelegramVIPSalesBot
                tracker = VIPSignalTracker(cfg.event_db_path, notifier=telemetry.notifier)
                sales_bot = TelegramVIPSalesBot(cfg, tracker)
                sales_bot.start_polling()
                logging.info("Bot Comercial VIP de Telegram activo y respondiendo a comandos de clientes.")
            except Exception as sexc:
                logging.warning("No se pudo iniciar bot interactivo de ventas VIP: %s", sexc)

        # Inicializar worker asíncrono de Binance Square
        if cfg.binance_square_enabled:
            try:
                from bot.growth_traffic_engine import get_square_worker
                sq_worker = get_square_worker(cfg)
                if sq_worker:
                    logging.info("Worker asíncrono de Binance Square activo y escuchando cola de publicaciones.")
            except Exception as sqw_exc:
                logging.warning("No se pudo iniciar worker de Binance Square: %s", sqw_exc)

        # Inicializar generador y publicador autónomo de tráfico y alto ROI
        if cfg.telegram_enabled or cfg.binance_square_enabled:
            try:
                from bot.growth_traffic_engine import AutoTrafficPublisher
                interval_min = int(getattr(cfg, "binance_square_post_interval_hours", 2.0) * 60)
                traffic_publisher = AutoTrafficPublisher(cfg)
                traffic_publisher.start_background_loop(interval_minutes=max(60, interval_min))
                logging.info("Motor Autónomo de Tráfico y Alto ROI activo (publicando cada %dm).", interval_min)
            except Exception as texc:
                logging.warning("No se pudo iniciar publicador de tráfico: %s", texc)

        # EarnManager compartido para el loop (reutiliza el cliente firmado)
        loop_earn: EarnManager | None = None
        if cfg.earn_enabled and not cfg.use_testnet and traders:
            first_trader = next(iter(traders.values()))
            loop_earn = build_earn_manager(cfg, first_trader.exec.client)

        completed_cycles = 0

        while not stop_event.is_set():
            # Refresco de cerrojo distribuido sin robo de lock
            try:
                if not telemetry.store.refresh_lease(lease_key, lease_owner):
                    if not telemetry.store.acquire_lease(lease_key, lease_owner, lease_ttl):
                        logging.error(
                            "Pérdida crítica de cerrojo distribuido para %s: otra instancia posee el lease activo. Abortando ciclo live-loop sin forzar robo de lock.",
                            lease_owner,
                        )
                        break
            except Exception as lexc:
                logging.warning("Advertencia al refrescar cerrojo distribuido: %s", lexc)

            if stop_event.is_set():
                break

            # Ejecutar auto-tuning periódico en segundo plano si corresponde
            _trigger_async_auto_tune()

            if stop_event.is_set():
                break

            # Aislamiento por símbolo: cada activo tiene su frontera de error dedicada
            for symbol, trader in traders.items():
                if stop_event.is_set():
                    logging.info("Parada solicitada; interrumpiendo escaneo de símbolos.")
                    break
                try:
                    event = trader.step()
                    trader.save_state()
                    event_name = str(event.get("event", "live_event"))
                    telemetry.record("live", symbol, event_name, event)
                    if event_name in {"live_buy", "live_sell", "risk_pause", "live_guard"}:
                        category = "general"
                        if "buy" in event_name:
                            category = "buys"
                        elif "sell" in event_name:
                            category = "sells"
                        elif "error" in event_name or "pause" in event_name or "guard" in event_name:
                            category = "errors"
                        try:
                            telemetry.alert(_format_live_alert(symbol, event), category=category)
                        except Exception as alert_exc:
                            logging.warning("[%s] Error enviando alerta de telemetría: %s", symbol, alert_exc)
                        try:
                            _publish_live_event_to_square(cfg, symbol, event)
                        except Exception as sq_exc:
                            logging.debug("[%s] Error publicando en Binance Square: %s", symbol, sq_exc)
                    print(f"[{symbol}] Event:", json.dumps(event, indent=2, default=str))
                except Exception as exc:
                    import traceback
                    event = {
                        "time": datetime.now(timezone.utc).isoformat(),
                        "event": "live_error",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    }
                    try:
                        telemetry.record("live", symbol, "live_error", event)
                    except Exception:
                        pass
                    try:
                        telemetry.alert(_format_live_alert(symbol, event), category="errors")
                    except Exception:
                        pass
                    print(f"[{symbol}] Error:", json.dumps(event, indent=2, default=str))

            if stop_event.is_set():
                break

            if loop_earn is not None:
                _run_earn_sweep(cfg, telemetry, earn=loop_earn)

            completed_cycles += 1
            if cycles > 0 and completed_cycles >= cycles:
                return

            # Espera sensible a señales: se interrumpe de inmediato si se activa stop_event
            stop_event.wait(timeout=sleep_seconds)
    finally:
        # Restaurar señales originales
        if old_sigint is not None:
            try:
                signal.signal(signal.SIGINT, old_sigint)
            except Exception:
                pass
        if old_sigterm is not None and hasattr(signal, "SIGTERM"):
            try:
                signal.signal(signal.SIGTERM, old_sigterm)
            except Exception:
                pass

        if sales_bot is not None:
            try:
                sales_bot.stop()
            except Exception as s_err:
                logging.warning("Error al detener bot de ventas VIP: %s", s_err)
        if traffic_publisher is not None:
            try:
                traffic_publisher.stop()
            except Exception as t_err:
                logging.warning("Error al detener publicador de tráfico: %s", t_err)

        # Cerrar traders y sus sesiones de DB
        for sym, trader in traders.items():
            try:
                trader.close()
            except Exception as tr_err:
                logging.debug("Error cerrando trader %s: %s", sym, tr_err)

        # Liberar cerrojo distribuido (sin robar ni destruir leases de terceros)
        try:
            telemetry.store.release_lease(lease_key, lease_owner)
            logging.info("Cerrojo distribuido liberado en parada ordenada (%s).", lease_key)
        except Exception as exc:
            logging.warning("No se pudo liberar el bloqueo distribuido: %s", exc)

        # Cerrar telemetría
        try:
            telemetry.close()
        except Exception as tel_err:
            logging.debug("Error al cerrar telemetría: %s", tel_err)


def _format_live_alert(symbol: str, event: dict[str, Any]) -> str:
    import traceback
    import html
    event_type = event.get("event", "live_event")
    
    if event_type == "vip_signal_published":
        try:
            from bot.vip_signal_bot import VIPSignalFormatter
            price = _to_float(event.get("price"), 0.0)
            stop = _to_float(event.get("stop"), 0.0)
            reason = event.get("reason", "Estructura Cuantitativa")
            strat = event.get("strategy_mode", "auto")
            conf = _to_float(event.get("confidence"), 0.85)
            sent = _to_float(event.get("news_sentiment"), 0.0)
            return VIPSignalFormatter.format_vip_entry_signal(
                symbol=symbol,
                action="BUY",
                entry_price=price,
                stop_price=stop,
                reason=reason,
                strategy_mode=strat,
                confidence=conf,
                news_sentiment=sent,
            )
        except Exception:
            pass

    emoji = "🔔"
    if "buy" in event_type:
        emoji = "🟢 <b>BUY</b>"
    elif "sell" in event_type:
        emoji = "🔴 <b>SELL</b>"
    elif "error" in event_type:
        emoji = "❌ <b>ERROR</b>"
    elif "tune" in event_type:
        emoji = "⚡ <b>AUTOTUNE</b>"
    elif "pause" in event_type:
        emoji = "⚠️ <b>RISK PAUSE</b>"
    elif "guard" in event_type:
        emoji = "🛡️ <b>GUARD</b>"
        
    safe_symbol = html.escape(str(symbol))
    parts = [f"{emoji} | <b>{safe_symbol}</b>"]
    
    for key in ("price", "qty", "pnl", "pnl_pct", "reason", "signal_confidence", "error_type", "message", "score", "strategy_mode"):
        if key in event:
            val = event[key]
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                val_float = float(val)
                if "pnl_pct" in key:
                    val_str = f"{val_float:+.2f}%"
                elif "pct" in key:
                    val_str = f"{val_float:.2f}%"
                elif "pnl" in key:
                    val_str = f"{val_float:+.4f}"
                else:
                    val_str = f"{val_float:.4f}"
            else:
                val_str = str(val)
            parts.append(f"\u2022 <b>{key.replace('_', ' ').title()}:</b> {html.escape(val_str)}")
            
    # Si incluye análisis fundamental enriquecido (Why Yes / Why No)
    fund_info = event.get("fundamental_info")
    if fund_info:
        why_yes = [f"Gatillo Técnico: {html.escape(str(event.get('reason', 'unknown')))}"]
        for s in fund_info.get("strengths", [])[:2]:
            why_yes.append(s)
        why_no = fund_info.get("risks", [])[:2]
        parts.append(f"\n<b>[TESIS CUANTITATIVA] (¿Por qué sí?):</b>\n" + "\n".join([f"  • {item}" for item in why_yes]))
        parts.append(f"<b>[FACTORES DE RIESGO] (¿Por qué no?):</b>\n" + ("\n".join([f"  • {item}" for item in why_no]) if why_no else "  • Ninguno detectado."))
        if fund_info.get("ai_synthesis") and "clave API no configurada" not in fund_info.get("ai_synthesis"):
            parts.append(f"\n<b>[SÍNTESIS FUNDAMENTAL] (IA Gemini):</b>\n<i>{html.escape(fund_info.get('ai_synthesis'))}</i>")

    if "traceback" in event:
        parts.append(f"\n<b>Traceback:</b>\n<pre><code>{html.escape(str(event['traceback']))}</code></pre>")
        
    return "\n".join(parts)


def _format_paper_alert(symbol: str, event: dict[str, Any]) -> str:
    import traceback
    event_type = event.get("event", "paper_event")
    
    emoji = "📝"
    if "buy" in event_type:
        emoji = "🟢 <b>PAPER BUY</b>"
    elif "sell" in event_type:
        emoji = "🔴 <b>PAPER SELL</b>"
    elif "error" in event_type:
        emoji = "❌ <b>PAPER ERROR</b>"
    elif "tune" in event_type:
        emoji = "⚡ <b>PAPER AUTOTUNE</b>"
    elif "pause" in event_type:
        emoji = "⚠️ <b>PAPER RISK PAUSE</b>"
        
    safe_symbol = html.escape(str(symbol))
    parts = [f"{emoji} | <b>{safe_symbol}</b>"]
    
    for key in ("price", "qty", "pnl", "pnl_pct", "reason", "equity", "stop", "take_profit", "error_type", "message", "score", "strategy_mode"):
        if key in event:
            val = event[key]
            if isinstance(val, float):
                if "pnl_pct" in key:
                    val_str = f"{val:+.2f}%"
                elif "pct" in key:
                    val_str = f"{val:.2f}%"
                elif "pnl" in key:
                    val_str = f"{val:+.4f}"
                else:
                    val_str = f"{val:.4f}"
            else:
                val_str = str(val)
            parts.append(f"\u2022 <b>{key.replace('_', ' ').title()}:</b> {html.escape(val_str)}")
            
    if "traceback" in event:
        parts.append(f"\n<b>Traceback:</b>\n<pre><code>{html.escape(str(event['traceback']))}</code></pre>")
        
    return "\n".join(parts)


def _assert_ibkr_ready(cfg: BotConfig, confirm_live: str) -> None:
    if not cfg.ibkr_enabled:
        raise RuntimeError("IBKR_ENABLED debe ser true en .env.")
    from bot.ibkr_client import PAPER_PORTS
    if cfg.ibkr_port not in PAPER_PORTS:
        if not cfg.ibkr_allow_real_trading:
            raise RuntimeError(
                "Puerto IBKR real detectado pero IBKR_ALLOW_REAL_TRADING=false. "
                "Valida primero en paper (puerto 4002 o 7497)."
            )
        if confirm_live != "I_UNDERSTAND_LIVE_RISK":
            raise RuntimeError(
                "Trading real en IBKR requiere --confirm-live I_UNDERSTAND_LIVE_RISK."
            )


def run_ibkr_loop(
    cfg: BotConfig,
    confirm_live: str,
    cycles: int = 0,
    sleep_seconds: int = 300,
) -> None:
    """Loop de trading de acciones en IBKR reutilizando el motor del bot.

    - Solo opera en horario regular de NYSE/NASDAQ.
    - Entradas con bracket nativo (limit + TP + SL) gestionado por IB.
    - Sin filtro macro BTC ni multi-timeframe (contexto cripto no aplica).
    """
    _assert_ibkr_ready(cfg, confirm_live)
    from bot.ibkr_client import IBKRClient

    telemetry = build_telemetry(cfg)
    client = IBKRClient(cfg)
    client.connect()
    mode = "PAPER" if client.is_paper else "REAL"
    telemetry.alert(f"<b>IBKR</b> loop iniciado en modo {mode}")

    base_cfg = replace(
        cfg,
        interval=cfg.ibkr_interval,
        use_btc_macro_filter=False,
        use_multi_timeframe=False,
        live_max_quote_per_trade=cfg.ibkr_max_quote_per_trade,
    )
    strategies = {}
    risks = {}
    for symbol in cfg.ibkr_symbols_list:
        sym_cfg = replace(base_cfg, symbol=symbol)
        strategies[symbol] = HybridStrategy(sym_cfg)
        risks[symbol] = RiskManager(sym_cfg)

    last_close: dict[str, datetime] = {}
    completed = 0
    try:
        while True:
            session = get_market_session()
            if session in (MarketSession.US_CLOSED, MarketSession.US_POST_MARKET):
                event = {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "event": "hold",
                    "reason": f"market_{session.value.lower()}",
                    "session": session.value,
                }
                print(f"[IBKR] Mercado en sesión {session.value}; esperando.")
            elif session == MarketSession.US_PRE_MARKET:
                print("[IBKR] Sesión US_PRE_MARKET activa (04:00-09:30 ET). Evaluando cotizaciones dinámicas y setups...")
                for symbol in cfg.ibkr_symbols_list:
                    try:
                        event = _ibkr_premarket_step(
                            client, base_cfg, symbol,
                            strategies[symbol], risks[symbol], last_close,
                        )
                        event_name = str(event.get("event", "pre_market_eval"))
                        telemetry.record("ibkr_premarket", symbol, event_name, event)
                        print(f"[IBKR:PRE-MARKET:{symbol}]", json.dumps(event, default=str))
                    except Exception as exc:
                        err = {
                            "time": datetime.now(timezone.utc).isoformat(),
                            "event": "ibkr_premarket_error",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                        telemetry.record("ibkr", symbol, "ibkr_premarket_error", err)
                        print(f"[IBKR:PRE-MARKET:{symbol}] Error:", json.dumps(err, default=str))
            else:  # US_REGULAR
                for symbol in cfg.ibkr_symbols_list:
                    try:
                        event = _ibkr_step(
                            client, base_cfg, symbol,
                            strategies[symbol], risks[symbol], last_close,
                        )
                        event_name = str(event.get("event", "ibkr_event"))
                        telemetry.record("ibkr", symbol, event_name, event)
                        if event_name in {"ibkr_buy", "ibkr_sell", "risk_pause"}:
                            telemetry.alert(_format_live_alert(f"IBKR:{symbol}", event))
                        print(f"[IBKR:{symbol}]", json.dumps(event, default=str))
                    except Exception as exc:
                        err = {
                            "time": datetime.now(timezone.utc).isoformat(),
                            "event": "ibkr_error",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                        telemetry.record("ibkr", symbol, "ibkr_error", err)
                        print(f"[IBKR:{symbol}] Error:", json.dumps(err, default=str))
            completed += 1
            if cycles > 0 and completed >= cycles:
                return
            if hasattr(client, "ib") and hasattr(client.ib, "sleep"):
                try:
                    client.ib.sleep(sleep_seconds)
                except Exception:
                    time.sleep(sleep_seconds)
            else:
                time.sleep(sleep_seconds)
    finally:
        client.disconnect()


def _fetch_equity_klines(
    symbol: str, interval: str = "15m", limit: int = 500, client: Any = None
) -> pd.DataFrame:
    """
    Fetches real-time equity/ETF klines from yfinance engine or connected IBKR client.
    Standardized schema: [open_time, open, high, low, close, volume, close_time]
    """
    # 1. Try YFinanceDataEngine if available
    try:
        from bot.yfinance_engine import YFinanceDataEngine
        yfe = YFinanceDataEngine()
        df = yfe.get_klines(symbol, interval=interval)
        if df is not None and len(df) >= 15:
            return df.tail(limit).reset_index(drop=True)
    except Exception:
        pass

    # 2. Try direct yfinance
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        raw_df = ticker.history(period="5d", interval=interval)
        if raw_df is not None and not raw_df.empty:
            raw_df = raw_df.reset_index()
            time_col = "Datetime" if "Datetime" in raw_df.columns else "Date"
            raw_df["open_time"] = pd.to_datetime(raw_df[time_col], utc=True)
            raw_df["close_time"] = raw_df["open_time"] + pd.Timedelta(minutes=15)
            raw_df = raw_df.rename(columns={
                "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
            })
            clean_df = raw_df[["open_time", "open", "high", "low", "close", "volume", "close_time"]]
            return clean_df.tail(limit).reset_index(drop=True)
    except Exception:
        pass

    # 3. Fallback to client if connected
    if client is not None and hasattr(client, "get_klines"):
        try:
            return client.get_klines(symbol, interval, limit)
        except Exception:
            pass

    return pd.DataFrame()


def _ibkr_premarket_step(
    client: Any,
    base_cfg: BotConfig,
    symbol: str,
    strategy: Any,
    risk: Any,
    last_close: dict[str, datetime],
) -> dict[str, Any]:
    """
    Evaluates setups during US pre-market (04:00 - 09:30 ET).
    Downloads dynamic quotes/klines (via yfinance or client) and evaluates 5-factor scoring
    without executing premature fills until regular market open.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    df = _fetch_equity_klines(symbol, base_cfg.interval, base_cfg.lookback, client=client)
    if len(df) < 20:
        return {"time": now_iso, "event": "pre_market_eval", "symbol": symbol, "status": "insufficient_bars"}

    analysis_df = df.iloc[:-1].copy() if len(df) > 20 else df.copy()
    row = analysis_df.iloc[-1]
    raw_ct = row["close_time"]
    close_time = raw_ct.to_pydatetime() if hasattr(raw_ct, "to_pydatetime") else pd.to_datetime(raw_ct).to_pydatetime()

    from bot.quant_engine import QuantEngine
    score = QuantEngine.evaluate_setup(symbol, analysis_df, is_crypto=False)
    close_val = float(row["close"])

    last_close[symbol] = close_time

    if score.is_buy_authorized:
        return {
            "time": now_iso,
            "event": "pre_market_setup",
            "symbol": symbol,
            "price": close_val,
            "composite_score": score.composite,
            "factors": score.details,
            "status": "deferred_to_regular_open",
            "reason": f"pre_market_hurdle_cleared_{score.composite:.3f}_ge_0.72",
        }
    return {
        "time": now_iso,
        "event": "pre_market_eval",
        "symbol": symbol,
        "price": close_val,
        "composite_score": score.composite,
        "status": "score_below_hurdle",
        "reason": f"composite_{score.composite:.3f}_below_hurdle",
    }


def _ibkr_step(client, base_cfg: BotConfig, symbol: str, strategy, risk, last_close) -> dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat()
    df = client.get_klines(symbol, base_cfg.interval, base_cfg.lookback)
    if len(df) < 60:
        return {"time": now_iso, "event": "hold", "reason": "insufficient_bars"}
    analysis_df = df.iloc[:-1].copy()  # solo velas cerradas
    row = analysis_df.iloc[-1]
    close_time = row["close_time"].to_pydatetime()
    if last_close.get(symbol) is not None and close_time <= last_close[symbol]:
        return {"time": now_iso, "event": "hold", "reason": "duplicate_candle"}

    position = client.position_qty(symbol)
    if position > 0 or client.has_open_orders(symbol):
        last_close[symbol] = close_time
        return {"time": now_iso, "event": "hold", "reason": "position_or_orders_open",
                "qty": position}

    signal = strategy.generate(analysis_df, None, macro_df=None, in_position=False)
    if signal.action != "buy":
        last_close[symbol] = close_time
        return {"time": now_iso, "event": "hold", "reason": signal.reason}

    regime = classify_market(analysis_df, base_cfg)
    equity = client.net_liquidation()
    allowed, reason = risk.can_trade(
        close_time, equity, regime_name=regime.name, interval=base_cfg.interval
    )
    if not allowed:
        last_close[symbol] = close_time
        return {"time": now_iso, "event": "risk_pause", "reason": reason}

    # 5-Factor Quantitative Brain Evaluation (S_composite >= 0.72)
    from bot.quant_engine import QuantEngine
    score = QuantEngine.evaluate_setup(symbol=symbol, df=analysis_df, is_crypto=False)
    if not score.is_buy_authorized:
        last_close[symbol] = close_time
        return {
            "time": now_iso,
            "event": "hold",
            "reason": f"score_below_hurdle: {score.composite:.3f} < {score.details.get('hurdle', 0.72)}",
            "composite_score": score.composite,
        }

    # Volatility spike check
    vol_allowed, vol_ratio, vol_reason = risk.validate_volatility_regime(analysis_df)
    if not vol_allowed:
        last_close[symbol] = close_time
        return {"time": now_iso, "event": "risk_pause", "reason": vol_reason, "volatility_ratio": vol_ratio}

    # Consolidated circuit breaker check
    cb_allowed, cb_reason = risk.check_global_circuit_breaker(equity)
    if not cb_allowed:
        last_close[symbol] = close_time
        return {"time": now_iso, "event": "risk_pause", "reason": cb_reason}

    close = float(row["close"])
    atr_value = float(atr(analysis_df["high"], analysis_df["low"], analysis_df["close"], 14).iloc[-1])
    entry_ref = close * (1 + base_cfg.slippage)
    stop = entry_ref - atr_value * base_cfg.stop_atr_mult
    take = entry_ref + (entry_ref - stop) * base_cfg.take_profit_rr
    if stop <= 0 or take <= entry_ref:
        last_close[symbol] = close_time
        return {"time": now_iso, "event": "hold", "reason": "invalid_levels"}

    cash = client.account_cash_usd()
    qty = risk.position_size(
        equity=cash, entry_price=entry_ref, stop_price=stop, fee_rate=base_cfg.fee_rate
    )
    quote_cap = min(cash, base_cfg.live_max_quote_per_trade)
    qty = min(qty, quote_cap / entry_ref)
    qty = int(qty)  # acciones enteras
    if qty < 1:
        last_close[symbol] = close_time
        return {"time": now_iso, "event": "hold", "reason": "qty_below_one_share",
                "cash": cash, "entry_ref": entry_ref}

    order = client.buy_bracket(symbol, qty, entry_ref, take, stop)
    risk.register_entry(close_time)
    last_close[symbol] = close_time
    return {
        "time": now_iso,
        "event": "ibkr_buy",
        "price": order.get("limit_price"),
        "qty": qty,
        "stop": round(stop, 2),
        "take": round(take, 2),
        "reason": signal.reason,
        "order_status": order.get("status"),
    }


def run_hybrid_loop(
    cfg: BotConfig,
    confirm_live: str = "",
    cycles: int = 0,
    sleep_seconds: int = 300,
) -> None:
    """
    Continuous hybrid portfolio scan across all 14 elite assets:
    - 8 Top Crypto: BTC, ETH, SOL, BNB, XRP, LINK, AVAX, SUI (24/7/365 continuous scan via Binance).
    - 6 Top Equities & ETFs: NVDA, AAPL, MSFT, AMZN, SPY, QQQ (IBKR / yfinance market session scan).

    Operates on a 5-state market session machine:
    - CRYPTO_24_7: Crypto scans continuously and never sleeps or halts on US market closure.
    - US_PRE_MARKET (04:00 - 09:30 ET): Evaluates dynamic yfinance klines and 5-factor scoring without premature fills.
    - US_REGULAR (09:30 - 16:00 ET): Evaluates setups and executes regular orders.
    - US_POST_MARKET & US_CLOSED: Keeps equity state and continues 24/7 crypto scanning.
    """
    telemetry = build_telemetry(cfg)
    telemetry.alert("<b>ArcaFid Quantitative</b>: Hybrid Portfolio Loop Iniciado (14 Activos: 8 Cripto + 6 Acciones/ETFs)")

    # 1. Initialize crypto traders
    crypto_traders: dict[str, LiveTrader] = {}
    for symbol in cfg.symbols_to_trade:
        sym_cfg = replace(cfg, symbol=symbol)
        try:
            crypto_traders[symbol] = LiveTrader(sym_cfg, state_store=telemetry.store)
        except Exception as c_err:
            logging.warning("No se pudo inicializar LiveTrader para crypto %s: %s", symbol, c_err)

    # 2. Initialize stock strategies and risks
    stock_base_cfg = replace(
        cfg,
        interval=cfg.ibkr_interval,
        use_btc_macro_filter=False,
        use_multi_timeframe=False,
        live_max_quote_per_trade=cfg.ibkr_max_quote_per_trade,
    )
    stock_strategies: dict[str, Any] = {}
    stock_risks: dict[str, Any] = {}
    for symbol in cfg.ibkr_symbols_list:
        sym_cfg = replace(stock_base_cfg, symbol=symbol)
        stock_strategies[symbol] = HybridStrategy(sym_cfg)
        stock_risks[symbol] = RiskManager(sym_cfg)

    # 3. Optional IBKR client
    ibkr_client = None
    if cfg.ibkr_enabled:
        try:
            from bot.ibkr_client import IBKRClient
            ibkr_client = IBKRClient(cfg)
            ibkr_client.connect()
        except Exception as ib_err:
            logging.warning("IBKR client en modo standby (yfinance feed activo): %s", ib_err)

    last_stock_close: dict[str, datetime] = {}
    completed_cycles = 0

    try:
        while True:
            # A. Continuous Crypto Scan (24/7/365)
            for symbol, trader in crypto_traders.items():
                try:
                    event = trader.step()
                    trader.save_state()
                    telemetry.record("live_crypto", symbol, str(event.get("event")), event)
                    print(f"[HYBRID:CRYPTO:{symbol}]", json.dumps(event, default=str))
                except Exception as exc:
                    logging.error(f"[HYBRID:CRYPTO:{symbol}] Error: {exc}")

            # B. Equities Market Session Scan
            session = get_market_session()
            if session == MarketSession.US_PRE_MARKET:
                print("[HYBRID:EQUITIES] Sesión US_PRE_MARKET activa. Evaluando 6 activos vía yfinance...")
                for symbol in cfg.ibkr_symbols_list:
                    try:
                        event = _ibkr_premarket_step(
                            ibkr_client, stock_base_cfg, symbol,
                            stock_strategies[symbol], stock_risks[symbol], last_stock_close
                        )
                        telemetry.record("hybrid_premarket", symbol, str(event.get("event")), event)
                        print(f"[HYBRID:PRE-MARKET:{symbol}]", json.dumps(event, default=str))
                    except Exception as exc:
                        logging.warning(f"[HYBRID:PRE-MARKET:{symbol}] Error: {exc}")
            elif session == MarketSession.US_REGULAR and ibkr_client is not None:
                print("[HYBRID:EQUITIES] Sesión US_REGULAR activa. Evaluando ejecución...")
                for symbol in cfg.ibkr_symbols_list:
                    try:
                        event = _ibkr_step(
                            ibkr_client, stock_base_cfg, symbol,
                            stock_strategies[symbol], stock_risks[symbol], last_stock_close
                        )
                        telemetry.record("hybrid_stock", symbol, str(event.get("event")), event)
                        print(f"[HYBRID:STOCK:{symbol}]", json.dumps(event, default=str))
                    except Exception as exc:
                        logging.warning(f"[HYBRID:STOCK:{symbol}] Error: {exc}")
            else:
                print(f"[HYBRID:EQUITIES] Estado {session.value}. Cripto continúa 24/7 sin interrupción.")

            completed_cycles += 1
            if cycles > 0 and completed_cycles >= cycles:
                return

            time.sleep(sleep_seconds)
    finally:
        for sym, trader in crypto_traders.items():
            try:
                trader.close()
            except Exception:
                pass
        if ibkr_client is not None:
            try:
                ibkr_client.disconnect()
            except Exception:
                pass
        try:
            telemetry.close()
        except Exception:
            pass


def run_earn_cmd(cfg: BotConfig, dry_run: bool) -> None:
    """Ejecuta un barrido Earn manual (o simulado con --dry-run)."""
    if cfg.use_testnet:
        raise RuntimeError("Earn no existe en testnet. Usa USE_TESTNET=false.")
    if not cfg.binance_api_key or not cfg.binance_api_secret:
        raise RuntimeError("Faltan credenciales de API de Binance en .env.")
    exec_client = BinanceExecutionClient(cfg)
    earn = EarnManager(cfg=cfg, client=exec_client.client, dry_run=dry_run)
    telemetry = build_telemetry(cfg)
    print("Modo:", "DRY-RUN (simulacion)" if dry_run else "REAL")
    moves = earn.sweep_idle_balances()
    print("Movimientos:", json.dumps(moves, indent=2, default=str, ensure_ascii=False))
    dust = earn.convert_dust()
    print("Dust:", json.dumps(dust, indent=2, default=str, ensure_ascii=False))
    if moves and not dry_run:
        try:
            telemetry.alert("<b>Earn</b> barrido manual ejecutado")
        except Exception:
            pass


def run_earn_report_cmd(cfg: BotConfig, send: bool) -> None:
    if cfg.use_testnet:
        raise RuntimeError("Earn no existe en testnet. Usa USE_TESTNET=false.")
    exec_client = BinanceExecutionClient(cfg)
    earn = EarnManager(cfg=cfg, client=exec_client.client)
    report = earn.build_report()
    print(report.replace("<b>", "").replace("</b>", ""))
    if send:
        telemetry = build_telemetry(cfg)
        telemetry.alert(report)
        print("\nReporte enviado por Telegram.")


def run_telegram_test(cfg: BotConfig, message: str) -> None:
    telemetry = build_telemetry(cfg)
    if not cfg.telegram_enabled:
        raise RuntimeError("TELEGRAM_ENABLED=false. Ejecuta scripts\\setup_telegram.ps1 primero.")
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        raise RuntimeError("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env.")
    ok = telemetry.notifier.send(message)
    if not ok:
        raise RuntimeError("Telegram no envio el mensaje. Revisa token/chat_id.")
    print(json.dumps({"status": "ok", "message": "telegram_sent"}, indent=2))


def run_cloud_check(cfg: BotConfig, require_postgres: bool = False) -> None:
    telemetry = build_telemetry(cfg)
    dialect = telemetry.store.engine.dialect.name
    with telemetry.store.engine.connect() as conn:
        conn.exec_driver_sql("SELECT 1")

    if require_postgres and dialect != "postgresql":
        raise RuntimeError(
            "Cloud requiere DATABASE_URL de PostgreSQL/Supabase; "
            f"se detecto dialecto {dialect}."
        )

    print(
        json.dumps(
            {
                "status": "ok",
                "database_dialect": dialect,
                "database_url_set": bool(os.getenv("DATABASE_URL")),
                "live_enabled": cfg.live_enabled,
                "use_testnet": cfg.use_testnet,
                "allow_real_trading": cfg.allow_real_trading,
                "active_symbols": cfg.symbols_to_trade,
                "telegram_enabled": cfg.telegram_enabled,
            },
            indent=2,
        )
    )


def _record_paper_event(telemetry, symbol: str, event: dict[str, Any]) -> None:
    event_name = str(event.get("event", "paper_event"))
    telemetry.record("paper", symbol, event_name, event)
    if event_name in {"buy", "sell", "risk_pause", "paper_error"}:
        category = "general"
        if "buy" in event_name:
            category = "buys"
        elif "sell" in event_name:
            category = "sells"
        elif "error" in event_name or "pause" in event_name:
            category = "errors"
        telemetry.alert(_format_paper_alert(symbol, event), category=category)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; if ($p) {{ 'running' }}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return "running" in (result.stdout or "")


def _pid_file_status(path: Path) -> dict[str, Any]:
    payload = {
        "pid_file": str(path),
        "pid_file_exists": path.exists(),
        "pid": None,
        "process_running": False,
    }
    if not path.exists():
        return payload
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (TypeError, ValueError):
        payload["pid_file_valid"] = False
        return payload
    payload["pid_file_valid"] = True
    payload["pid"] = pid
    payload["process_running"] = _pid_is_running(pid)
    return payload


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class RuntimePidFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> "RuntimePidFile":
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            current = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if current == str(os.getpid()):
            try:
                self.path.unlink()
            except OSError:
                pass


def run_healthcheck(
    cfg: BotConfig,
    dashboard_url: str = "http://127.0.0.1:8765/api/summary",
    stale_seconds: int = 900,
) -> None:
    telemetry = build_telemetry(cfg)
    summary = telemetry.store.dashboard_summary()
    now = datetime.now(timezone.utc)
    root = Path.cwd()
    recent_events = telemetry.store.recent_events(1000)

    dashboard_pid = _pid_file_status(root / "dashboard.pid")
    watchdog_pid = _pid_file_status(root / "watchdog.pid")
    paper_pid = _pid_file_status(root / "paper.pid")

    dashboard_http = {"url": dashboard_url, "ok": False, "status_code": None, "error": None}
    try:
        with urlopen(dashboard_url, timeout=5) as response:
            dashboard_http["ok"] = True
            dashboard_http["status_code"] = int(getattr(response, "status", 200))
    except URLError as exc:
        dashboard_http["error"] = str(exc)
    except Exception as exc:
        dashboard_http["error"] = str(exc)

    data_errors = [
        e for e in recent_events if e["event"] in {"data_error", "paper_error", "risk_pause"}
    ]
    since_24h = now - timedelta(hours=24)
    recent_failures = [
        e
        for e in recent_events
        if (dt := _parse_iso_dt(e.get("ts"))) is not None and dt >= since_24h
        and e["event"] in {"data_error", "paper_error", "paper_process_exit"}
    ]

    paper_state = next(
        (
            state
            for state in summary.get("states", [])
            if str(state.get("key", "")).startswith("paper:")
        ),
        None,
    )
    paper_state_dt = _parse_iso_dt(paper_state.get("updated_ts") if paper_state else None)
    paper_state_age = (
        (now - paper_state_dt).total_seconds() if paper_state_dt is not None else None
    )
    paper_state_fresh = paper_state_age is not None and paper_state_age <= stale_seconds

    latest_watchdog_event = next(
        (
            e
            for e in recent_events
            if e["mode"] == "watchdog"
            and e["event"] in {"paper_process_start", "paper_process_restart_scheduled", "watchdog_start"}
        ),
        None,
    )
    latest_paper_event = next(
        (
            e
            for e in recent_events
            if e["mode"] == "paper"
            and e["event"] in {"buy", "sell", "hold", "position_open", "data_error", "paper_error"}
        ),
        None,
    )

    latest_watchdog_dt = _parse_iso_dt(latest_watchdog_event.get("ts") if latest_watchdog_event else None)
    latest_paper_dt = _parse_iso_dt(latest_paper_event.get("ts") if latest_paper_event else None)
    latest_watchdog_age = (
        (now - latest_watchdog_dt).total_seconds() if latest_watchdog_dt is not None else None
    )
    latest_paper_age = (
        (now - latest_paper_dt).total_seconds() if latest_paper_dt is not None else None
    )

    dashboard_service_ok = bool(dashboard_http["ok"] or dashboard_pid["process_running"])
    watchdog_service_ok = bool(
        watchdog_pid["process_running"]
        or (latest_watchdog_age is not None and latest_watchdog_age <= (stale_seconds * 2))
    )
    paper_activity_fresh = bool(
        latest_paper_age is not None and latest_paper_age <= stale_seconds
    )

    checks = {
        "dashboard_service": dashboard_service_ok,
        "watchdog_service": watchdog_service_ok,
        "paper_activity_fresh": paper_activity_fresh,
        "paper_state_fresh": bool(paper_state_fresh),
    }
    overall = "ok" if all(checks.values()) else "warn"

    payload = {
        "status": overall,
        "symbol": cfg.symbol,
        "interval": cfg.interval,
        "checks": checks,
        "dashboard": {
            "pid": dashboard_pid,
            "http": dashboard_http,
        },
        "watchdog": {
            "pid": watchdog_pid,
            "latest_event": latest_watchdog_event,
            "latest_event_age_seconds": latest_watchdog_age,
        },
        "paper": {
            "pid": paper_pid,
            "latest_event": latest_paper_event,
            "latest_event_age_seconds": latest_paper_age,
            "latest_summary": summary.get("latest_paper"),
            "state_updated_ts": paper_state.get("updated_ts") if paper_state else None,
            "state_age_seconds": paper_state_age,
            "recent_failure_count_24h": len(recent_failures),
            "recent_data_error_count": len(data_errors),
        },
    }
    print(json.dumps(payload, indent=2, default=str))


def run_report(cfg: BotConfig, day: str | None, send: bool) -> None:
    telemetry = build_telemetry(cfg)
    if day:
        target_day = date.fromisoformat(day)
    else:
        target_day = datetime.now(timezone.utc).date()

    report = telemetry.reporter.build(target_day)
    telemetry.record(
        "report",
        cfg.symbol,
        "daily_report",
        {"day": target_day.isoformat(), "sent": send},
    )
    if send:
        from bot.telemetry import TelegramNotifier
        escaped_report = TelegramNotifier.escape_html(report)
        telemetry.alert(f"<pre><code>{escaped_report}</code></pre>", category="autotune")
    print(report)


def run_paper_report(cfg: BotConfig) -> None:
    telemetry = build_telemetry(cfg)
    report = build_paper_report(telemetry.store)
    telemetry.record("paper", cfg.symbol, "paper_report", report)
    print(json.dumps(report, indent=2, default=str))


def run_regime_cmd(
    cfg: BotConfig,
    symbol: str | None,
    interval: str | None,
    higher_interval: str | None,
) -> None:
    local_cfg = replace(
        cfg,
        symbol=symbol or cfg.symbol,
        interval=interval or cfg.interval,
        higher_interval=higher_interval or cfg.higher_interval,
    )
    data = BinanceDataClient()
    df = data.get_klines(local_cfg.symbol, local_cfg.interval, local_cfg.lookback)
    higher_df = data.get_klines(
        local_cfg.symbol, local_cfg.higher_interval, local_cfg.higher_lookback
    )
    current_regime = classify_market(df, local_cfg)
    higher_regime = classify_market(higher_df, local_cfg)
    payload = {
        "symbol": local_cfg.symbol,
        "interval": local_cfg.interval,
        "higher_interval": local_cfg.higher_interval,
        "current": asdict(current_regime),
        "higher": asdict(higher_regime),
    }
    telemetry = build_telemetry(local_cfg)
    telemetry.record("regime", local_cfg.symbol, "regime_snapshot", payload)
    print(json.dumps(payload, indent=2, default=str))


def _compare_score(metrics: dict[str, float]) -> float:
    roi = float(metrics.get("roi_pct", 0.0))
    drawdown = abs(float(metrics.get("max_drawdown_pct", 0.0)))
    win_rate = float(metrics.get("win_rate_pct", 0.0))
    profit_factor = float(metrics.get("profit_factor", 0.0))
    trades = float(metrics.get("num_trades", 0.0))
    profit_factor = min(profit_factor, 3.0)
    score = (roi * 2.0) + (profit_factor * 6.0) + (win_rate * 0.03)
    score -= drawdown * 1.2
    if roi <= 0:
        score -= 8.0
    if profit_factor < 1.0:
        score -= (1.0 - profit_factor) * 8.0
    if trades < 5:
        score -= 5.0
    return score


def run_compare_cmd(
    cfg: BotConfig,
    symbols: str,
    intervals: str,
    lookback: int | None,
    min_trades: int,
) -> None:
    data = BinanceDataClient()
    rows: list[dict[str, Any]] = []
    strategy_modes = ["auto", "trend", "breakout", "pullback_trend", "mean_reversion"]
    bool_options = [True, False]

    for symbol in [x.strip().upper() for x in symbols.split(",") if x.strip()]:
        for interval in [x.strip() for x in intervals.split(",") if x.strip()]:
            df = data.get_klines(symbol, interval, lookback or cfg.lookback)
            for strategy_mode in strategy_modes:
                for use_regime in bool_options:
                    for use_mtf in bool_options:
                        local_cfg = replace(
                            cfg,
                            symbol=symbol,
                            interval=interval,
                            lookback=lookback or cfg.lookback,
                            strategy_mode=strategy_mode,
                            use_regime_filter=use_regime,
                            use_multi_timeframe=use_mtf,
                        )
                        result = Backtester(
                            local_cfg,
                            HybridStrategy(local_cfg),
                            RiskManager(local_cfg),
                        ).run(df)
                        metrics = result["metrics"]
                        if metrics.get("num_trades", 0.0) < float(min_trades):
                            continue
                        rows.append(
                            {
                                "score": _compare_score(metrics),
                                "symbol": symbol,
                                "interval": interval,
                                "strategy_mode": strategy_mode,
                                "use_regime_filter": use_regime,
                                "use_multi_timeframe": use_mtf,
                                "metrics": metrics,
                            }
                        )

    rows.sort(key=lambda row: row["score"], reverse=True)
    payload = {"top": rows[:15], "tested": len(rows), "min_trades": min_trades}
    telemetry = build_telemetry(cfg)
    telemetry.record("compare", cfg.symbol, "compare_result", payload)
    print(json.dumps(payload, indent=2, default=str))


def _parse_bool_option(value: str) -> list[bool]:
    normalized = value.strip().lower()
    if normalized == "both":
        return [True, False]
    if normalized in {"true", "1", "yes", "on"}:
        return [True]
    if normalized in {"false", "0", "no", "off"}:
        return [False]
    raise ValueError(f"Invalid bool option: {value}")


def _build_tune_grid(strategy_modes: list[str], full_grid: bool) -> dict[str, list[Any]]:
    grid: dict[str, list[Any]] = {
        "strategy_mode": strategy_modes,
        "use_regime_filter": [True, False],
        "use_multi_timeframe": [True, False],
        "stop_atr_mult": [1.0, 1.6, 1.8, 2.2],
        "take_profit_rr": [0.8, 1.2, 2.0, 2.2],
        "trailing_atr_mult": [0.8, 1.0],
        "min_confidence": [0.35, 0.5, 0.55, 0.65],
        "min_entry_quality": [0.68, 0.72, 0.78],
        "min_trend_strength": [0.0005, 0.002],
        "risk_per_trade": [0.005, 0.01],
    }
    if full_grid:
        grid.update(
            {
                "stop_atr_mult": [0.8, 1.0, 1.2, 1.6, 2.0, 2.4],
                "take_profit_rr": [0.8, 1.0, 1.2, 1.6, 2.0, 2.8],
                "trailing_atr_mult": [0.6, 0.8, 1.0, 1.2],
                "min_confidence": [0.35, 0.45, 0.55, 0.65],
                "min_entry_quality": [0.62, 0.68, 0.72, 0.78, 0.84],
                "min_trend_strength": [0.0005, 0.001, 0.002, 0.003],
                "risk_per_trade": [0.003, 0.005, 0.01],
            }
        )
    return grid


def _build_research_grid(strategy_mode: str, full_grid: bool) -> dict[str, list[Any]]:
    if full_grid:
        return _build_tune_grid([strategy_mode], full_grid=True)
    return {
        "strategy_mode": [strategy_mode],
        "use_regime_filter": [True],
        "use_multi_timeframe": [False],
        "stop_atr_mult": [1.0, 1.6, 2.2],
        "take_profit_rr": [0.8, 1.2, 2.0],
        "trailing_atr_mult": [0.8, 1.0],
        "min_confidence": [0.35, 0.55],
        "min_entry_quality": [0.68, 0.72, 0.78],
        "min_trend_strength": [0.0005, 0.002],
        "risk_per_trade": [0.005],
    }


def run_tune_cmd(
    cfg: BotConfig,
    symbol: str,
    interval: str,
    strategy_mode: str,
    use_regime_filter: str,
    use_multi_timeframe: str,
    lookback: int | None,
    top: int,
    min_trades: int,
    full_grid: bool,
) -> None:
    local_cfg = replace(
        cfg,
        symbol=symbol.upper(),
        interval=interval,
        lookback=lookback or cfg.lookback,
    )
    strategy_modes = (
        ["auto", "trend", "breakout", "pullback_trend", "mean_reversion"]
        if strategy_mode == "all"
        else [strategy_mode]
    )
    grid = _build_tune_grid(strategy_modes, full_grid)
    grid["use_regime_filter"] = _parse_bool_option(use_regime_filter)
    grid["use_multi_timeframe"] = _parse_bool_option(use_multi_timeframe)

    data = BinanceDataClient()
    df = data.get_klines(local_cfg.symbol, local_cfg.interval, local_cfg.lookback)
    candidates = optimize_config(
        local_cfg,
        df,
        top_n=top,
        grid=grid,
        min_trades=min_trades,
    )
    payload = {
        "symbol": local_cfg.symbol,
        "interval": local_cfg.interval,
        "lookback": local_cfg.lookback,
        "top": candidates,
    }
    telemetry = build_telemetry(local_cfg)
    telemetry.record("tune", local_cfg.symbol, "tune_result", payload)
    print(json.dumps(payload, indent=2, default=str))


def run_research_cmd(
    cfg: BotConfig,
    symbols: str,
    intervals: str,
    lookback: int,
    min_trades: int,
    top_compare: int,
    top_tune: int,
    validate_top: int,
    full_grid: bool,
    train_size: int,
    test_size: int,
    step_size: int,
) -> None:
    data = BinanceDataClient()
    dataframes: dict[tuple[str, str], Any] = {}
    compare_rows: list[dict[str, Any]] = []
    strategy_modes = ["auto", "trend", "breakout", "pullback_trend", "mean_reversion"]

    for symbol in [x.strip().upper() for x in symbols.split(",") if x.strip()]:
        for interval in [x.strip() for x in intervals.split(",") if x.strip()]:
            df = data.get_klines(symbol, interval, lookback)
            dataframes[(symbol, interval)] = df
            for strategy_mode in strategy_modes:
                for use_regime in [True, False]:
                    for use_mtf in [True, False]:
                        local_cfg = replace(
                            cfg,
                            symbol=symbol,
                            interval=interval,
                            lookback=lookback,
                            strategy_mode=strategy_mode,
                            use_regime_filter=use_regime,
                            use_multi_timeframe=use_mtf,
                        )
                        result = Backtester(
                            local_cfg,
                            HybridStrategy(local_cfg),
                            RiskManager(local_cfg),
                        ).run(df)
                        metrics = result["metrics"]
                        if metrics.get("num_trades", 0.0) < float(min_trades):
                            continue
                        compare_rows.append(
                            {
                                "score": _compare_score(metrics),
                                "symbol": symbol,
                                "interval": interval,
                                "strategy_mode": strategy_mode,
                                "use_regime_filter": use_regime,
                                "use_multi_timeframe": use_mtf,
                                "metrics": metrics,
                            }
                        )

    compare_rows.sort(key=lambda row: row["score"], reverse=True)
    tuned_validations: list[dict[str, Any]] = []

    for row in compare_rows[:top_compare]:
        symbol = row["symbol"]
        interval = row["interval"]
        local_cfg = replace(
            cfg,
            symbol=symbol,
            interval=interval,
            lookback=lookback,
            strategy_mode=row["strategy_mode"],
            use_regime_filter=row["use_regime_filter"],
            use_multi_timeframe=row["use_multi_timeframe"],
        )
        grid = _build_research_grid(row["strategy_mode"], full_grid)
        grid["use_regime_filter"] = [row["use_regime_filter"]]
        grid["use_multi_timeframe"] = [row["use_multi_timeframe"]]
        df = dataframes[(symbol, interval)]
        tuned = optimize_config(
            local_cfg,
            df,
            top_n=top_tune,
            grid=grid,
            min_trades=min_trades,
        )
        for candidate in tuned[:top_tune]:
            tuned_cfg = replace(local_cfg, **candidate["params"])
            try:
                validation = run_fixed_walkforward(
                    tuned_cfg,
                    df,
                    train_size=train_size,
                    test_size=test_size,
                    step_size=step_size,
                )
                validation_summary = validation["summary"]
            except ValueError as exc:
                validation_summary = {
                    "quality_gate_passed": False,
                    "quality_gate_reasons": [str(exc)],
                }
            tuned_validations.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "compare_score": row["score"],
                    "compare_metrics": row["metrics"],
                    "tuned_score": candidate["score"],
                    "params": candidate["params"],
                    "tuned_metrics": candidate["metrics"],
                    "validation_summary": validation_summary,
                }
            )

    tuned_validations.sort(
        key=lambda row: (
            bool(row["validation_summary"].get("quality_gate_passed", False)),
            float(row["validation_summary"].get("compounded_roi_pct", -999.0)),
            float(row["validation_summary"].get("aggregate_profit_factor", 0.0)),
        ),
        reverse=True,
    )
    payload = {
        "compare_tested": len(compare_rows),
        "compare_top": compare_rows[:top_compare],
        "validated_top": tuned_validations[:validate_top],
        "passed": [
            row
            for row in tuned_validations
            if row["validation_summary"].get("quality_gate_passed", False)
        ],
    }
    telemetry = build_telemetry(cfg)
    telemetry.record("research", cfg.symbol, "research_result", payload)
    print(json.dumps(payload, indent=2, default=str))


def run_validate_cmd(
    cfg: BotConfig,
    symbol: str,
    interval: str,
    strategy_mode: str,
    use_regime_filter: str,
    use_multi_timeframe: str,
    stop_atr_mult: float,
    take_profit_rr: float,
    trailing_atr_mult: float,
    min_confidence: float,
    min_trend_strength: float,
    risk_per_trade: float,
    lookback: int | None,
    train_size: int,
    test_size: int,
    step_size: int,
) -> None:
    local_cfg = replace(
        cfg,
        symbol=symbol.upper(),
        interval=interval,
        lookback=lookback or cfg.lookback,
        strategy_mode=strategy_mode,
        use_regime_filter=_parse_bool_option(use_regime_filter)[0],
        use_multi_timeframe=_parse_bool_option(use_multi_timeframe)[0],
        stop_atr_mult=stop_atr_mult,
        take_profit_rr=take_profit_rr,
        trailing_atr_mult=trailing_atr_mult,
        min_confidence=min_confidence,
        min_trend_strength=min_trend_strength,
        risk_per_trade=risk_per_trade,
    )
    data = BinanceDataClient()
    df = data.get_klines(local_cfg.symbol, local_cfg.interval, local_cfg.lookback)
    report = run_fixed_walkforward(
        local_cfg,
        df,
        train_size=train_size,
        test_size=test_size,
        step_size=step_size,
    )
    telemetry = build_telemetry(local_cfg)
    telemetry.record(
        "validate",
        local_cfg.symbol,
        "fixed_walkforward_summary",
        report["summary"],
    )
    print(json.dumps(report, indent=2, default=str))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Binance trading bot")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_backtest = sub.add_parser("backtest", help="Run historical simulation")
    p_backtest.add_argument("--symbol", type=str, default=None)
    p_backtest.add_argument("--interval", type=str, default=None)
    p_backtest.add_argument("--lookback", type=int, default=None)

    p_paper = sub.add_parser("paper", help="Run paper trading loop")
    p_paper.add_argument("--cycles", type=int, default=30)
    p_paper.add_argument("--sleep-seconds", type=int, default=60)

    p_opt = sub.add_parser("optimize", help="Search better params on historical data")
    p_opt.add_argument("--symbol", type=str, default=None)
    p_opt.add_argument("--interval", type=str, default=None)
    p_opt.add_argument("--lookback", type=int, default=None)
    p_opt.add_argument("--top", type=int, default=5)

    p_wf = sub.add_parser("walkforward", help="Run walk-forward validation")
    p_wf.add_argument("--symbol", type=str, default=None)
    p_wf.add_argument("--interval", type=str, default=None)
    p_wf.add_argument("--lookback", type=int, default=None)
    p_wf.add_argument("--train-size", type=int, default=360)
    p_wf.add_argument("--test-size", type=int, default=180)
    p_wf.add_argument("--step-size", type=int, default=180)
    p_wf.add_argument("--top", type=int, default=1)
    p_wf.add_argument("--full-grid", action="store_true")

    p_live = sub.add_parser("live", help="Run one live step")
    p_live.add_argument("--confirm-live", type=str, default="")

    p_live_loop = sub.add_parser("live-loop", help="Run guarded live loop")
    p_live_loop.add_argument("--confirm-live", type=str, default="")
    p_live_loop.add_argument("--cycles", type=int, default=0)
    p_live_loop.add_argument("--sleep-seconds", type=int, default=300)

    p_report = sub.add_parser("report", help="Build daily telemetry report")
    p_report.add_argument("--day", type=str, default=None, help="UTC date: YYYY-MM-DD")
    p_report.add_argument("--send", action="store_true", help="Send report to Telegram")

    p_ibkr = sub.add_parser("ibkr", help="Run IBKR stock trading loop (paper by default)")
    p_ibkr.add_argument("--confirm-live", default="", help="Required for real IBKR trading")
    p_ibkr.add_argument("--cycles", type=int, default=0, help="0 = infinite")
    p_ibkr.add_argument("--sleep-seconds", type=int, default=300)

    p_hybrid = sub.add_parser("hybrid", help="Run 14-asset continuous hybrid portfolio loop (Crypto 24/7 + US Equities)")
    p_hybrid.add_argument("--confirm-live", default="", help="Required for real trading")
    p_hybrid.add_argument("--cycles", type=int, default=0, help="0 = infinite")
    p_hybrid.add_argument("--sleep-seconds", type=int, default=300)

    p_earn = sub.add_parser("earn", help="Run one Binance Earn sweep (flexible/locked/dust)")
    p_earn.add_argument("--dry-run", action="store_true", help="Simulate without moving funds")

    p_earn_report = sub.add_parser("earn-report", help="Show Earn positions, APR and rewards")
    p_earn_report.add_argument("--send", action="store_true", help="Send report via Telegram")

    p_telegram = sub.add_parser("telegram-test", help="Send a Telegram test alert")
    p_telegram.add_argument(
        "--message",
        type=str,
        default="Bot de trading: prueba de Telegram OK.",
    )

    p_cloud_check = sub.add_parser(
        "cloud-check", help="Validate cloud database and configuration"
    )
    p_cloud_check.add_argument("--require-postgres", action="store_true")

    sub.add_parser("paper-report", help="Summarize paper trading performance windows")

    p_dashboard = sub.add_parser("dashboard", help="Run local telemetry dashboard")
    p_dashboard.add_argument("--host", type=str, default="127.0.0.1")
    p_dashboard.add_argument("--port", type=int, default=8765)
    p_dashboard.add_argument("--no-browser", action="store_true")

    p_app = sub.add_parser("app", help="Run Next-Gen VIP Control Center & App")
    p_app.add_argument("--host", type=str, default="127.0.0.1")
    p_app.add_argument("--port", type=int, default=8765)
    p_app.add_argument("--no-browser", action="store_true")

    p_portal = sub.add_parser("portal", help="Run Institutional Quantitative Investment Portal")
    p_portal.add_argument("--host", type=str, default="127.0.0.1")
    p_portal.add_argument("--port", type=int, default=8765)
    p_portal.add_argument("--no-browser", action="store_true")

    p_panel = sub.add_parser("panel", help="Run unified panel (Binance + Earn + IBKR)")
    p_panel.add_argument("--host", type=str, default="127.0.0.1")
    p_panel.add_argument("--port", type=int, default=8765)
    p_panel.add_argument("--no-browser", action="store_true")

    p_watchdog = sub.add_parser("watchdog", help="Keep paper trading process alive")
    p_watchdog.add_argument("--cycles", type=int, default=0)
    p_watchdog.add_argument("--sleep-seconds", type=int, default=300)
    p_watchdog.add_argument("--restart-delay", type=int, default=30)
    p_watchdog.add_argument("--max-restarts", type=int, default=0)

    p_health = sub.add_parser("healthcheck", help="Inspect bot health and recent failures")
    p_health.add_argument(
        "--dashboard-url",
        type=str,
        default="http://127.0.0.1:8765/api/summary",
    )
    p_health.add_argument("--stale-seconds", type=int, default=900)

    p_regime = sub.add_parser("regime", help="Inspect current market regime")
    p_regime.add_argument("--symbol", type=str, default=None)
    p_regime.add_argument("--interval", type=str, default=None)
    p_regime.add_argument("--higher-interval", type=str, default=None)

    p_compare = sub.add_parser("compare", help="Compare strategy modes and filters")
    p_compare.add_argument("--symbols", type=str, default="BTCUSDT")
    p_compare.add_argument("--intervals", type=str, default="15m")
    p_compare.add_argument("--lookback", type=int, default=None)
    p_compare.add_argument("--min-trades", type=int, default=5)

    p_tune = sub.add_parser("tune", help="Tune strategy and risk parameters")
    p_tune.add_argument("--symbol", type=str, required=True)
    p_tune.add_argument("--interval", type=str, required=True)
    p_tune.add_argument(
        "--strategy-mode",
        type=str,
        default="all",
        choices=["all", "auto", "trend", "breakout", "pullback_trend", "mean_reversion"],
    )
    p_tune.add_argument("--use-regime-filter", type=str, default="both")
    p_tune.add_argument("--use-multi-timeframe", type=str, default="both")
    p_tune.add_argument("--lookback", type=int, default=None)
    p_tune.add_argument("--top", type=int, default=10)
    p_tune.add_argument("--min-trades", type=int, default=5)
    p_tune.add_argument("--full-grid", action="store_true")

    p_research = sub.add_parser(
        "research", help="Compare, tune and validate candidates automatically"
    )
    p_research.add_argument(
        "--symbols",
        type=str,
        default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,LINKUSDT,AVAXUSDT,DOGEUSDT",
    )
    p_research.add_argument("--intervals", type=str, default="1h,4h")
    p_research.add_argument("--lookback", type=int, default=3000)
    p_research.add_argument("--min-trades", type=int, default=8)
    p_research.add_argument("--top-compare", type=int, default=6)
    p_research.add_argument("--top-tune", type=int, default=2)
    p_research.add_argument("--validate-top", type=int, default=6)
    p_research.add_argument("--full-grid", action="store_true")
    p_research.add_argument("--train-size", type=int, default=1500)
    p_research.add_argument("--test-size", type=int, default=500)
    p_research.add_argument("--step-size", type=int, default=250)

    p_validate = sub.add_parser("validate", help="Validate fixed params with walk-forward")
    p_validate.add_argument("--symbol", type=str, required=True)
    p_validate.add_argument("--interval", type=str, required=True)
    p_validate.add_argument("--strategy-mode", type=str, required=True)
    p_validate.add_argument("--use-regime-filter", type=str, required=True)
    p_validate.add_argument("--use-multi-timeframe", type=str, required=True)
    p_validate.add_argument("--stop-atr-mult", type=float, required=True)
    p_validate.add_argument("--take-profit-rr", type=float, required=True)
    p_validate.add_argument("--trailing-atr-mult", type=float, required=True)
    p_validate.add_argument("--min-confidence", type=float, required=True)
    p_validate.add_argument("--min-trend-strength", type=float, required=True)
    p_validate.add_argument("--risk-per-trade", type=float, required=True)
    p_validate.add_argument("--lookback", type=int, default=None)
    p_validate.add_argument("--train-size", type=int, default=500)
    p_validate.add_argument("--test-size", type=int, default=200)
    p_validate.add_argument("--step-size", type=int, default=100)

    p_gen_report = sub.add_parser("generate-report", help="Generate monthly PDF report for an investor")
    p_gen_report.add_argument("--name", type=str, default="Inversor de Prueba")
    p_gen_report.add_argument("--email", type=str, required=True)
    p_gen_report.add_argument("--initial-balance", type=float, default=10000.0)
    p_gen_report.add_argument("--fee", type=float, default=0.20)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = BotConfig.from_env()

    if args.mode == "backtest":
        run_backtest(cfg, args.symbol, args.interval, args.lookback)
        return

    if args.mode == "generate-report":
        from bot.pdf_generator import PDFReportGenerator
        from bot.db import DBTrade, get_db_session
        
        session = get_db_session(cfg.event_db_path)
        try:
            trades_orm = session.query(DBTrade).order_by(DBTrade.exit_time.desc()).all()
            
            trades_list = []
            for t in trades_orm:
                trades_list.append({
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "symbol": t.symbol,
                    "quantity": t.quantity,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct
                })
        finally:
            session.close()
            
        if not trades_list:
            trades_list = [
                {
                    "entry_time": datetime.now(),
                    "exit_time": datetime.now(),
                    "symbol": "BTCUSDT",
                    "quantity": 0.5,
                    "entry_price": 60000.0,
                    "exit_price": 63000.0,
                    "pnl": 1500.0,
                    "pnl_pct": 5.0
                }
            ]
            
        pdf_path = PDFReportGenerator.generate_investor_report(
            user_name=args.name,
            email=args.email,
            trades=trades_list,
            initial_balance=args.initial_balance,
            performance_fee_pct=args.fee
        )
        print(f"Reporte generado exitosamente en: {pdf_path}")
        return

    if args.mode == "paper":
        telemetry = build_telemetry(cfg)
        try:
            with RuntimePidFile(Path.cwd() / "paper.pid"):
                summary = run_paper(
                    cfg=cfg,
                    data_client=BinanceDataClient(),
                    cycles=args.cycles,
                    sleep_seconds=args.sleep_seconds,
                    event_callback=lambda event: _record_paper_event(
                        telemetry, cfg.symbol, event
                    ),
                    state_store=telemetry.store,
                )
            telemetry.record("paper", cfg.symbol, "paper_summary", summary)
        finally:
            telemetry.close()
        return

    if args.mode == "optimize":
        run_optimize(cfg, args.symbol, args.interval, args.lookback, args.top)
        return

    if args.mode == "walkforward":
        run_walkforward_cmd(
            cfg=cfg,
            symbol=args.symbol,
            interval=args.interval,
            lookback=args.lookback,
            train_size=args.train_size,
            test_size=args.test_size,
            step_size=args.step_size,
            top=args.top,
            full_grid=args.full_grid,
        )
        return

    if args.mode == "live":
        run_live(cfg, args.confirm_live)
        return

    if args.mode == "live-loop":
        with RuntimePidFile(Path.cwd() / "live.pid"):
            run_live_loop(
                cfg=cfg,
                confirm_live=args.confirm_live,
                cycles=args.cycles,
                sleep_seconds=args.sleep_seconds,
            )
        return

    if args.mode == "report":
        run_report(cfg, args.day, args.send)
        return

    if args.mode == "ibkr":
        run_ibkr_loop(
            cfg,
            confirm_live=args.confirm_live,
            cycles=args.cycles,
            sleep_seconds=args.sleep_seconds,
        )
        return

    if args.mode in ("hybrid", "hybrid-loop"):
        run_hybrid_loop(
            cfg=cfg,
            confirm_live=args.confirm_live,
            cycles=args.cycles,
            sleep_seconds=args.sleep_seconds,
        )
        return

    if args.mode == "earn":
        run_earn_cmd(cfg, dry_run=args.dry_run)
        return

    if args.mode == "earn-report":
        run_earn_report_cmd(cfg, send=args.send)
        return

    if args.mode == "telegram-test":
        try:
            run_telegram_test(cfg, args.message)
        except RuntimeError as exc:
            print(json.dumps({"status": "error", "message": str(exc)}, indent=2))
            raise SystemExit(1)
        return

    if args.mode == "cloud-check":
        try:
            run_cloud_check(cfg, args.require_postgres)
        except RuntimeError as exc:
            print(json.dumps({"status": "error", "message": str(exc)}, indent=2))
            raise SystemExit(1)
        return

    if args.mode == "paper-report":
        run_paper_report(cfg)
        return

    if args.mode in ("portal", "app", "panel", "dashboard"):
        from bot.institutional_portal import run_institutional_portal
        open_browser = not getattr(args, "no_browser", False)
        run_institutional_portal(cfg, args.host, args.port, open_browser=open_browser)
        return

    if args.mode == "watchdog":
        with RuntimePidFile(Path.cwd() / "watchdog.pid"):
            run_watchdog(
                cfg=cfg,
                cycles=args.cycles,
                sleep_seconds=args.sleep_seconds,
                restart_delay=args.restart_delay,
                max_restarts=args.max_restarts,
            )
        return

    if args.mode == "healthcheck":
        run_healthcheck(
            cfg=cfg,
            dashboard_url=args.dashboard_url,
            stale_seconds=args.stale_seconds,
        )
        return

    if args.mode == "regime":
        run_regime_cmd(cfg, args.symbol, args.interval, args.higher_interval)
        return

    if args.mode == "compare":
        run_compare_cmd(cfg, args.symbols, args.intervals, args.lookback, args.min_trades)
        return

    if args.mode == "tune":
        run_tune_cmd(
            cfg=cfg,
            symbol=args.symbol,
            interval=args.interval,
            strategy_mode=args.strategy_mode,
            use_regime_filter=args.use_regime_filter,
            use_multi_timeframe=args.use_multi_timeframe,
            lookback=args.lookback,
            top=args.top,
            min_trades=args.min_trades,
            full_grid=args.full_grid,
        )
        return

    if args.mode == "research":
        run_research_cmd(
            cfg=cfg,
            symbols=args.symbols,
            intervals=args.intervals,
            lookback=args.lookback,
            min_trades=args.min_trades,
            top_compare=args.top_compare,
            top_tune=args.top_tune,
            validate_top=args.validate_top,
            full_grid=args.full_grid,
            train_size=args.train_size,
            test_size=args.test_size,
            step_size=args.step_size,
        )
        return

    if args.mode == "validate":
        run_validate_cmd(
            cfg=cfg,
            symbol=args.symbol,
            interval=args.interval,
            strategy_mode=args.strategy_mode,
            use_regime_filter=args.use_regime_filter,
            use_multi_timeframe=args.use_multi_timeframe,
            stop_atr_mult=args.stop_atr_mult,
            take_profit_rr=args.take_profit_rr,
            trailing_atr_mult=args.trailing_atr_mult,
            min_confidence=args.min_confidence,
            min_trend_strength=args.min_trend_strength,
            risk_per_trade=args.risk_per_trade,
            lookback=args.lookback,
            train_size=args.train_size,
            test_size=args.test_size,
            step_size=args.step_size,
        )
        return

    raise RuntimeError(f"Unsupported mode: {args.mode}")


if __name__ == "__main__":
    main()
