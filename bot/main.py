from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import time
import logging
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
from bot.risk import RiskManager
from bot.strategy import HybridStrategy
from bot.telemetry import build_paper_report, build_telemetry
from bot.watchdog import run_watchdog
from bot.walkforward import run_fixed_walkforward, run_walkforward
from bot.db import DBPosition, DBTrade, DBBotState, get_db_session
from bot.news_sentiment import NewsSentimentAnalyzer




def _to_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


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
        self.cash = cfg.initial_balance


        self.position: Position | None = None
        self.entry_fee_paid = 0.0
        self.trades: list[Trade] = []
        self.last_processed_close_time: datetime | None = None
        self.load_state()

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
        except Exception as e:
            logging.error(f"Error al cargar estado de riesgo desde DB: {e}")

        # Cargar y reconciliar posición activa
        self.position = None
        try:
            db_pos = self.db_session.query(DBPosition).filter(
                DBPosition.symbol == self.cfg.symbol,
                DBPosition.is_active == True
            ).first()
            if db_pos:
                self.position = self.reconcile_position_with_binance(db_pos)
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
                if params:
                    from dataclasses import replace
                    self.cfg = replace(self.cfg, **params)
                    self.strategy = HybridStrategy(self.cfg)
                    self.risk = RiskManager(self.cfg)
                    logging.info(f"[{self.cfg.symbol}] Configuración auto-optimizada cargada con éxito: {params}")
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
                self._close_db_position_as_lost(db_pos, "external_close_no_balance")
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

            logging.info("La posición sigue activa en Binance.")
            return Position(
                entry_time=db_pos.entry_time.replace(tzinfo=timezone.utc) if db_pos.entry_time.tzinfo is None else db_pos.entry_time,
                entry_price=db_pos.entry_price,
                quantity=db_pos.quantity,
                stop_price=db_pos.stop_price,
                take_profit_price=db_pos.take_profit_price
            )

        except Exception as e:
            logging.error(f"Error durante la reconciliación con Binance: {e}")
            return Position(
                entry_time=db_pos.entry_time.replace(tzinfo=timezone.utc) if db_pos.entry_time.tzinfo is None else db_pos.entry_time,
                entry_price=db_pos.entry_price,
                quantity=db_pos.quantity,
                stop_price=db_pos.stop_price,
                take_profit_price=db_pos.take_profit_price
            )

    def _close_db_position(self, db_pos: DBPosition, exit_price: float, exit_time: datetime, reason: str):
        db_pos.is_active = False
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
        quote_free, quote_locked = self.exec.get_asset_balance_values(self.quote_asset)
        base_free, base_locked = self.exec.get_asset_balance_values(self.base_asset)
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

    def step(self) -> dict[str, Any]:
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
        signal = self.strategy.generate(
            analysis_df,
            higher_analysis_df,
            macro_df=macro_analysis_df,
            in_position=self.position is not None,
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

                    order = self.exec.create_market_sell(self.cfg.symbol, qty_to_sell)
                    fills = order.get("fills", []) if isinstance(order, dict) else []
                    if fills:
                        exit_price = _to_float(fills[0].get("price"), close)
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


        entry_ref = close * (1 + self.cfg.slippage)
        stop = entry_ref - (atr_value * self.cfg.stop_atr_mult)
        take = entry_ref + (entry_ref - stop) * self.cfg.take_profit_rr
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
        qty = min(qty, quote_cap / (entry_ref * (1 + self.cfg.fee_rate)))
        qty, qty_reason = self._prepare_live_qty(qty, entry_ref)
        if qty <= 0:
            self.last_processed_close_time = now
            return {"time": now.isoformat(), "event": "hold", "reason": qty_reason}

        # Ejecutar compra a mercado
        order = self.exec.create_market_buy(self.cfg.symbol, qty)
        fills = order.get("fills", []) if isinstance(order, dict) else []
        if fills:
            entry_price = _to_float(fills[0].get("price"), entry_ref)
        else:
            quote_qty = _to_float(order.get("cummulativeQuoteQty"), 0.0)
            exec_qty = _to_float(order.get("executedQty"), qty)
            entry_price = quote_qty / exec_qty if exec_qty > 0 else entry_ref

        executed_qty = _to_float(order.get("executedQty"), qty)
        if executed_qty <= 0:
            executed_qty = qty

        # Calcular precios de SL y TP basados en el precio de entrada real
        stop = entry_price - (atr_value * self.cfg.stop_atr_mult)
        take = entry_price + (entry_price - stop) * self.cfg.take_profit_rr

        sl_order_id = None
        tp_order_id = None

        # Colocar órdenes nativas de SL y TP en Binance
        logging.info("Colocando órdenes de protección SL/TP nativas en Binance...")
        try:
            limit_sl_price = stop * (1 - self.cfg.slippage)
            sl_order = self.exec.create_stop_loss_limit(
                self.cfg.symbol,
                executed_qty,
                stop,
                limit_sl_price,
                self.symbol_filters
            )
            sl_order_id = str(sl_order.get("orderId"))
            logging.info(f"Orden de Stop Loss colocada. ID: {sl_order_id}")
        except Exception as e:
            logging.error(f"FALLO CRÍTICO al colocar Stop Loss en Binance: {e}. Venta inmediata por seguridad.")
            try:
                self.exec.create_market_sell(self.cfg.symbol, executed_qty)
            except Exception as ex:
                logging.error(f"Fallo al vender posición tras fallo de SL: {ex}")
            self.last_processed_close_time = now
            return {"time": now.isoformat(), "event": "live_error", "reason": f"failed_to_place_sl: {e}"}

        try:
            tp_order = self.exec.create_take_profit_limit(
                self.cfg.symbol,
                executed_qty,
                take,
                take,
                self.symbol_filters
            )
            tp_order_id = str(tp_order.get("orderId"))
            logging.info(f"Orden de Take Profit colocada. ID: {tp_order_id}")
        except Exception as e:
            logging.warning(f"Error al colocar Take Profit en Binance: {e}. Se gestionará de forma manual si falla.")

        # Guardar en base de datos local
        db_pos = DBPosition(
            symbol=self.cfg.symbol,
            entry_time=now,
            entry_price=entry_price,
            quantity=executed_qty,
            stop_price=stop,
            take_profit_price=take,
            stop_loss_order_id=sl_order_id,
            take_profit_order_id=tp_order_id,
            is_active=True
        )
        self.db_session.add(db_pos)
        self.db_session.commit()

        self.position = Position(
            entry_time=now,
            entry_price=entry_price,
            quantity=executed_qty,
            stop_price=stop,
            take_profit_price=take,
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


def perform_auto_tuning(cfg: BotConfig, telemetry) -> None:
    """Ejecuta auto-optimización para todos los símbolos activos y guarda la mejor configuración en DB."""
    if not cfg.auto_tune_enabled:
        return

    now = datetime.utcnow()
    # Comprobar cuándo se ejecutó el último tuning en la DB
    from bot.db import get_db_session, DBBotState
    session = get_db_session(cfg.event_db_path)
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

    logging.info("--- Iniciando ciclo programado de Auto-Tuning inteligente ---")
    data = BinanceDataClient()
    
    tuned_summary = []
    for symbol in cfg.symbols_to_trade:
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
                    f"▪️ #{symbol}: Estrategia de {mode_desc} (SL: {stop_atr:.1f}x ATR | Target R:R: {tp_rr:.1f})"
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
        logging.warning(f"Error al guardar last_auto_tune_time: {e}. Intentando forzar inserción...")
        try:
            from sqlalchemy import text
            session.execute(
                text("INSERT OR REPLACE INTO bot_state_orm (key, value_json, updated_ts) VALUES (:key, :val, :ts)"),
                {"key": "last_auto_tune_time", "val": json.dumps(now.isoformat()), "ts": now}
            )
            session.commit()
        except Exception as e2:
            logging.error(f"Fallo crítico al guardar timestamp de auto-tune: {e2}")
    finally:
        session.close()
    logging.info("--- Ciclo de Auto-Tuning finalizado ---")

    # Enviar reporte a Binance Square si está activo
    if tuned_summary and cfg.binance_square_enabled:
        try:
            from bot.binance_square import BinanceSquarePublisher
            from bot.news_sentiment import NewsSentimentAnalyzer
            
            news_analyzer = NewsSentimentAnalyzer(cfg.event_db_path)
            sentiment_score, headlines = news_analyzer.get_sentiment()
            
            if sentiment_score > 0.15:
                sent_emoji = "🟢 Alcista"
            elif sentiment_score < -0.15:
                sent_emoji = "🔴 Bajista"
            else:
                sent_emoji = "🟡 Neutral"
                
            headline_str = ""
            if headlines:
                headline_str = "\nTitulares destacados que estoy vigilando:\n" + "\n".join([f"▪️ {h['title']}" for h in headlines[:2]])

            publisher = BinanceSquarePublisher(cfg)
            msg = (
                f"📊 ANÁLISIS DE MERCADO & PORTAFOLIO DIARIO 📊\n\n"
                f"Hola a todos. Acabo de revisar los gráficos y el comportamiento reciente de nuestras monedas principales. He ajustado mis estrategias técnicas para las próximas horas para adaptarnos a las condiciones de volatilidad:\n\n"
                f"🎯 CONFIGURACIÓN DE ESTRATEGIAS:\n"
                + "\n".join(tuned_summary) + f"\n\n"
                f"📰 SENTIMIENTO DEL MERCADO:\n"
                f"El sentimiento general del sector se percibe {sent_emoji} (Índice: {sentiment_score:+.2f}).\n\n"
                + headline_str + "\n\n"
                f"📈 Gráficos en TradingView: https://es.tradingview.com/chart/\n\n"
                f"¡Tengan un gran día de trading y operen siempre con Stop Loss! 🚀💸"
            )
            publisher.publish_post(msg)
        except Exception as e:
            logging.error(f"Error al enviar reporte de auto-tuning a Binance Square: {e}")


def _publish_live_event_to_square(cfg: BotConfig, symbol: str, event: dict[str, Any]) -> None:
    if not cfg.binance_square_enabled:
        return
    try:
        from bot.binance_square import BinanceSquarePublisher
        from bot.news_sentiment import NewsSentimentAnalyzer
        
        publisher = BinanceSquarePublisher(cfg)
        
        event_name = event.get("event")
        price = event.get("price", 0.0)
        qty = event.get("qty", 0.0)
        stop = event.get("stop", 0.0)
        take = event.get("take", 0.0)
        reason = event.get("reason", "unknown")
        strategy_mode = event.get("strategy_mode", cfg.strategy_mode)
        
        # Mapeo de descripción humana de la estrategia
        REASON_DESCS = {
            "turtle_breakout": "Ruptura del canal de Donchian de 20 velas con ADX alcista confirmando la fuerza del impulso.",
            "connors_rsi": "Retroceso rápido en tendencia alcista. El RSI de corto plazo está en zona de sobreventa extrema, ideal para comprar el dip.",
            "elder_triple": "Gatillo técnico por encima del máximo anterior con alineación de la marea macro (MACD e histograma en verde).",
            "williams_alligator": "El Alligator está despertando. Las medias rápidas se cruzan al alza con buen incremento de volumen de compra.",
            "mean_reversion": "El precio tocó la banda inferior del rango lateral y esperamos un rebote rápido hacia la media central."
        }
        reason_desc = REASON_DESCS.get(strategy_mode, f"Setup técnico de confirmación en base a {reason}.")
        
        # Consultar sentimiento de noticias para acompañar la señal
        news_analyzer = NewsSentimentAnalyzer(cfg.event_db_path)
        sentiment_score, headlines = news_analyzer.get_sentiment()
        
        if sentiment_score > 0.15:
            sent_desc = "bastante alcista, con noticias muy positivas impulsando al sector"
            sent_emoji = "🟢 Alcista (+{:.2f})".format(sentiment_score)
        elif sentiment_score < -0.15:
            sent_desc = "algo bajista por titulares negativos, pero vemos absorción de compra"
            sent_emoji = "🔴 Bajista ({:.2f})".format(sentiment_score)
        else:
            sent_desc = "neutral, lo que favorece setups técnicos limpios"
            sent_emoji = "🟡 Neutral ({:.2f})".format(sentiment_score)
            
        headline_bullet = ""
        if headlines:
            headline_bullet = f"\n📰 Titular clave del momento: \"{headlines[0]['title']}\""
            
        msg = ""
        if event_name == "live_buy":
            msg = (
                f"🚨 SEÑAL DE COMPRA SPOT: #{symbol} 🚨\n\n"
                f"Veo un patrón muy claro en el gráfico. El precio está mostrando fuerza y acabamos de entrar en una posición de compra en Spot.\n\n"
                f"📊 CONFIGURACIÓN DE ENTRADA:\n"
                f"▪️ Zona de Entrada: {price:.4f} USDT\n"
                f"▪️ Stop Loss (SL): {stop:.4f} USDT\n"
                f"▪️ Target de Salida: {take:.4f} USDT\n"
                f"▪️ Cantidad Operada: {qty:.6f}\n\n"
                f"💡 ANÁLISIS RÁPIDO:\n"
                f"- {reason_desc}\n"
                f"- El sentimiento del mercado según las noticias recientes es {sent_desc}."
                + headline_bullet + "\n\n"
                f"📈 Gráfico en vivo: https://es.tradingview.com/chart/?symbol=BINANCE:{symbol}\n\n"
                f"¡A por el target! Gestionen bien su capital y no sobreoperen. 🚀💸"
            )
        elif event_name == "live_sell":
            pnl = event.get("pnl", 0.0)
            pnl_pct = event.get("pnl_pct", 0.0)
            
            pnl_emoji = "🎯 TARGET ALCANZADO" if pnl >= 0 else "🛑 STOP LOSS ALCANZADO"
            pnl_msg = "Aseguramos ganancias en el target establecido." if pnl >= 0 else "Salimos del mercado para proteger capital."
            
            exit_reason_desc = pnl_msg
            if "exit" in reason.lower() or "prematura" in reason.lower():
                exit_reason_desc = "Salimos de la operación antes de tiempo por debilidad en la estructura de precios."
            
            msg = (
                f"📊 {pnl_emoji}: #{symbol} 📊\n\n"
                f"Posición cerrada en Spot. {exit_reason_desc}\n\n"
                f"▪️ Precio de Salida: {price:.4f} USDT\n"
                f"▪️ Resultado Neto: {pnl_pct:+.2f}% ({pnl:+.4f} USDT)\n"
                f"▪️ Motivo de Salida: {reason}\n"
                f"▪️ Sentimiento general: {sent_emoji}"
                + headline_bullet + "\n\n"
                f"📈 Gráficos en TradingView: https://es.tradingview.com/chart/?symbol=BINANCE:{symbol}\n\n"
                f"¡Seguimos buscando las mejores oportunidades! 🚀💰"
            )
            
        if msg:
            publisher.publish_post(msg)
    except Exception as e:
        logging.error(f"Error al enviar publicación de señal en vivo a Binance Square: {e}")


def run_live(cfg: BotConfig, confirm_live: str) -> None:
    _assert_live_ready(cfg, confirm_live)
    telemetry = build_telemetry(cfg)
    perform_auto_tuning(cfg, telemetry)
    
    from dataclasses import replace
    for symbol in cfg.symbols_to_trade:
        try:
            symbol_cfg = replace(cfg, symbol=symbol)
            trader = LiveTrader(symbol_cfg, state_store=telemetry.store)
            event = trader.step()
            trader.save_state()
            event_name = str(event.get("event", "live_event"))
            telemetry.record("live", symbol, event_name, event)
            if event_name in {"live_buy", "live_sell", "risk_pause", "live_guard"}:
                telemetry.alert(_format_live_alert(symbol, event))
                _publish_live_event_to_square(cfg, symbol, event)
            print(f"[{symbol}] Step Result:", json.dumps(event, indent=2, default=str))
        except Exception as e:
            logging.error(f"[{symbol}] Error en run_live: {e}")


def run_live_loop(
    cfg: BotConfig,
    confirm_live: str,
    cycles: int = 0,
    sleep_seconds: int = 300,
) -> None:
    _assert_live_ready(cfg, confirm_live)
    telemetry = build_telemetry(cfg)
    
    # Ejecutar auto-tuning inicial si corresponde
    perform_auto_tuning(cfg, telemetry)

    # Inicializar traders para cada símbolo activo
    from dataclasses import replace
    traders = {}
    for symbol in cfg.symbols_to_trade:
        symbol_cfg = replace(cfg, symbol=symbol)
        traders[symbol] = LiveTrader(symbol_cfg, state_store=telemetry.store)

    completed_cycles = 0

    while True:
        # Ejecutar auto-tuning periódico si corresponde
        perform_auto_tuning(cfg, telemetry)

        for symbol, trader in traders.items():
            try:
                event = trader.step()
                trader.save_state()
                event_name = str(event.get("event", "live_event"))
                telemetry.record("live", symbol, event_name, event)
                if event_name in {"live_buy", "live_sell", "risk_pause", "live_guard"}:
                    telemetry.alert(_format_live_alert(symbol, event))
                    _publish_live_event_to_square(cfg, symbol, event)
                print(f"[{symbol}] Event:", json.dumps(event, indent=2, default=str))
            except Exception as exc:
                event = {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "event": "live_error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
                telemetry.record("live", symbol, "live_error", event)
                telemetry.alert(_format_live_alert(symbol, event))
                print(f"[{symbol}] Error:", json.dumps(event, indent=2, default=str))

        completed_cycles += 1
        if cycles > 0 and completed_cycles >= cycles:
            return

        time.sleep(sleep_seconds)


def _format_live_alert(symbol: str, event: dict[str, Any]) -> str:
    name = html.escape(str(event.get("event", "live_event")))
    safe_symbol = html.escape(str(symbol))
    parts = [f"<b>{safe_symbol}</b> {name}"]
    for key in ("price", "qty", "pnl", "pnl_pct", "reason", "signal_confidence"):
        if key in event:
            parts.append(f"{html.escape(str(key))}: {html.escape(str(event[key]))}")
    return "\n".join(parts)


def _format_paper_alert(symbol: str, event: dict[str, Any]) -> str:
    name = html.escape(str(event.get("event", "paper_event")))
    safe_symbol = html.escape(str(symbol))
    parts = [f"<b>{safe_symbol}</b> paper {name}"]
    for key in ("price", "qty", "pnl", "pnl_pct", "reason", "equity", "stop", "take_profit"):
        if key in event:
            parts.append(f"{html.escape(str(key))}: {html.escape(str(event[key]))}")
    return "\n".join(parts)


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


def _record_paper_event(telemetry, symbol: str, event: dict[str, Any]) -> None:
    event_name = str(event.get("event", "paper_event"))
    telemetry.record("paper", symbol, event_name, event)
    if event_name in {"buy", "sell", "risk_pause"}:
        telemetry.alert(_format_paper_alert(symbol, event))


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
        telemetry.alert(f"<pre>{report}</pre>")
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

    p_telegram = sub.add_parser("telegram-test", help="Send a Telegram test alert")
    p_telegram.add_argument(
        "--message",
        type=str,
        default="Bot de trading: prueba de Telegram OK.",
    )

    sub.add_parser("paper-report", help="Summarize paper trading performance windows")

    p_dashboard = sub.add_parser("dashboard", help="Run local telemetry dashboard")
    p_dashboard.add_argument("--host", type=str, default="127.0.0.1")
    p_dashboard.add_argument("--port", type=int, default=8765)

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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = BotConfig.from_env()

    if args.mode == "backtest":
        run_backtest(cfg, args.symbol, args.interval, args.lookback)
        return

    if args.mode == "paper":
        telemetry = build_telemetry(cfg)
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

    if args.mode == "telegram-test":
        try:
            run_telegram_test(cfg, args.message)
        except RuntimeError as exc:
            print(json.dumps({"status": "error", "message": str(exc)}, indent=2))
            raise SystemExit(1)
        return

    if args.mode == "paper-report":
        run_paper_report(cfg)
        return

    if args.mode == "dashboard":
        with RuntimePidFile(Path.cwd() / "dashboard.pid"):
            run_dashboard(cfg, args.host, args.port)
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
