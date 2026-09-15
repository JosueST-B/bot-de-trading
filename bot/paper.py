from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable

import requests

from bot.config import BotConfig
from bot.indicators import atr
from bot.models import Position, Trade
from bot.regime import classify_market
from bot.risk import RiskManager
from bot.strategy import HybridStrategy
from bot.telemetry import EventStore


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _position_to_dict(position: Position | None) -> dict[str, Any] | None:
    if position is None:
        return None
    return {
        "entry_time": position.entry_time.isoformat(),
        "entry_price": position.entry_price,
        "quantity": position.quantity,
        "stop_price": position.stop_price,
        "take_profit_price": position.take_profit_price,
        "entry_reason": getattr(position, "entry_reason", ""),
    }


def _position_from_dict(payload: dict[str, Any] | None) -> Position | None:
    if payload is None:
        return None
    return Position(
        entry_time=_parse_dt(str(payload["entry_time"])),
        entry_price=float(payload["entry_price"]),
        quantity=float(payload["quantity"]),
        stop_price=float(payload["stop_price"]),
        take_profit_price=float(payload["take_profit_price"]),
        entry_reason=str(payload.get("entry_reason", "")),
    )


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


def _trade_from_dict(payload: dict[str, Any]) -> Trade:
    return Trade(
        entry_time=_parse_dt(str(payload["entry_time"])),
        exit_time=_parse_dt(str(payload["exit_time"])),
        entry_price=float(payload["entry_price"]),
        exit_price=float(payload["exit_price"]),
        quantity=float(payload["quantity"]),
        pnl=float(payload["pnl"]),
        pnl_pct=float(payload["pnl_pct"]),
        reason=str(payload["reason"]),
    )


class PaperTrader:
    def __init__(
        self,
        cfg: BotConfig,
        strategy: HybridStrategy,
        risk: RiskManager,
        state_store: EventStore | None = None,
    ) -> None:
        self.cfg = cfg
        self.strategy = strategy
        self.risk = risk
        self.state_store = state_store
        self.state_key = f"paper:{cfg.symbol}:{cfg.interval}"
        self.cash = cfg.initial_balance
        self.position: Position | None = None
        self.entry_fee_paid = 0.0
        self.trades: list[Trade] = []
        self.last_processed_close_time: datetime | None = None
        self.load_state()

    def load_state(self) -> None:
        if self.state_store is None:
            return
        payload = self.state_store.get_state(self.state_key)
        if not payload:
            return

        self.cash = float(payload.get("cash", self.cfg.initial_balance))
        self.entry_fee_paid = float(payload.get("entry_fee_paid", 0.0))
        self.position = _position_from_dict(payload.get("position"))
        self.trades = [_trade_from_dict(x) for x in payload.get("trades", [])]
        last_processed = payload.get("last_processed_close_time")
        self.last_processed_close_time = (
            _parse_dt(str(last_processed)) if last_processed else None
        )

        risk_state = payload.get("risk_state", {})
        day_anchor = risk_state.get("day_anchor")
        self.risk.state.day_anchor = _parse_dt(str(day_anchor)) if day_anchor else None
        self.risk.state.day_start_equity = float(risk_state.get("day_start_equity", 0.0))
        self.risk.state.consecutive_losses = int(
            risk_state.get("consecutive_losses", 0)
        )
        self.risk.state.daily_trade_count = int(risk_state.get("daily_trade_count", 0))
        last_entry = risk_state.get("last_entry_time")
        last_exit = risk_state.get("last_exit_time")
        self.risk.state.last_entry_time = _parse_dt(str(last_entry)) if last_entry else None
        self.risk.state.last_exit_time = _parse_dt(str(last_exit)) if last_exit else None

    def save_state(self) -> None:
        if self.state_store is None:
            return
        payload = {
            "symbol": self.cfg.symbol,
            "interval": self.cfg.interval,
            "initial_balance": self.cfg.initial_balance,
            "cash": self.cash,
            "entry_fee_paid": self.entry_fee_paid,
            "position": _position_to_dict(self.position),
            "trades": [_trade_to_dict(t) for t in self.trades[-200:]],
            "last_processed_close_time": self.last_processed_close_time.isoformat()
            if self.last_processed_close_time
            else None,
            "risk_state": {
                "day_anchor": self.risk.state.day_anchor.isoformat()
                if self.risk.state.day_anchor
                else None,
                "day_start_equity": self.risk.state.day_start_equity,
                "consecutive_losses": self.risk.state.consecutive_losses,
                "daily_trade_count": self.risk.state.daily_trade_count,
                "last_entry_time": self.risk.state.last_entry_time.isoformat()
                if self.risk.state.last_entry_time
                else None,
                "last_exit_time": self.risk.state.last_exit_time.isoformat()
                if self.risk.state.last_exit_time
                else None,
            },
        }
        self.state_store.set_state(self.state_key, payload)

    def _equity(self, mark_price: float) -> float:
        return self.cash + ((self.position.quantity * mark_price) if self.position else 0.0)

    @staticmethod
    def _closed_candles(df):
        if len(df) < 2:
            return df.iloc[0:0].copy()
        return df.iloc[:-1].copy()

    def on_candle(self, df, higher_df=None, macro_df=None) -> dict[str, Any]:
        analysis_df = self._closed_candles(df)
        if analysis_df.empty:
            return {
                "time": datetime.utcnow().isoformat(),
                "event": "hold",
                "reason": "waiting_closed_candle",
                "equity": self._equity(self.cash),
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
            return {
                "time": now.isoformat(),
                "event": "hold",
                "reason": "duplicate_candle",
                "equity": self._equity(float(row["close"])),
            }

        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        atr_value = float(
            atr(analysis_df["high"], analysis_df["low"], analysis_df["close"], 14).iloc[-1]
        )
        regime_source = higher_analysis_df if higher_analysis_df is not None else analysis_df
        regime = classify_market(regime_source, self.cfg)

        if self.position is None:
            allowed, reason = self.risk.can_trade(
                now,
                self._equity(close),
                regime_name=regime.name,
                interval=self.cfg.interval,
            )
            if allowed:
                signal = self.strategy.generate(
                    analysis_df,
                    higher_analysis_df,
                    macro_df=macro_analysis_df,
                    in_position=False,
                )
                if signal.action == "buy":
                    entry = close * (1 + self.cfg.slippage)
                    stop = entry - (atr_value * self.cfg.stop_atr_mult)
                    take = entry + (entry - stop) * self.cfg.take_profit_rr
                    qty = self.risk.position_size(
                        equity=self.cash,
                        entry_price=entry,
                        stop_price=stop,
                        fee_rate=self.cfg.fee_rate,
                    )
                    max_affordable = self.cash / (entry * (1 + self.cfg.fee_rate))
                    qty = min(qty, max_affordable)
                    if qty > 0:
                        cost = qty * entry
                        self.entry_fee_paid = cost * self.cfg.fee_rate
                        self.cash -= cost + self.entry_fee_paid
                        self.position = Position(
                            entry_time=now,
                            entry_price=entry,
                            quantity=qty,
                            stop_price=stop,
                            take_profit_price=take,
                            entry_reason=signal.reason,
                        )
                        self.risk.register_entry(now)
                        self.last_processed_close_time = now
                        return {
                            "time": now.isoformat(),
                            "event": "buy",
                            "price": entry,
                            "qty": qty,
                            "equity": self._equity(close),
                        }
                self.last_processed_close_time = now
                return {
                    "time": now.isoformat(),
                    "event": "hold",
                    "reason": signal.reason,
                    "equity": self._equity(close),
                }

            self.last_processed_close_time = now
            return {
                "time": now.isoformat(),
                "event": "risk_pause",
                "reason": reason,
                "equity": self._equity(close),
            }

        self.position.stop_price = max(
            self.position.stop_price,
            close - (atr_value * self.cfg.trailing_atr_mult),
        )

        exit_price = None
        reason = "hold"
        if low <= self.position.stop_price:
            exit_price = self.position.stop_price * (1 - self.cfg.slippage)
            reason = "stop_or_trailing"
        elif high >= self.position.take_profit_price:
            exit_price = self.position.take_profit_price * (1 - self.cfg.slippage)
            reason = "take_profit"
        else:
            signal = self.strategy.generate(
                analysis_df,
                higher_analysis_df,
                macro_df=macro_analysis_df,
                in_position=True,
                entry_reason=self.position.entry_reason,
            )
            if signal.action == "exit":
                exit_price = close * (1 - self.cfg.slippage)
                reason = signal.reason

        if exit_price is None:
            self.last_processed_close_time = now
            return {
                "time": now.isoformat(),
                "event": "position_open",
                "equity": self._equity(close),
                "stop": self.position.stop_price,
                "take_profit": self.position.take_profit_price,
            }

        gross = self.position.quantity * exit_price
        exit_fee = gross * self.cfg.fee_rate
        self.cash += gross - exit_fee
        pnl = (
            (exit_price - self.position.entry_price) * self.position.quantity
            - self.entry_fee_paid
            - exit_fee
        )
        invested = self.position.entry_price * self.position.quantity
        pnl_pct = pnl / invested if invested > 0 else 0.0
        trade = Trade(
            entry_time=self.position.entry_time,
            exit_time=now,
            entry_price=self.position.entry_price,
            exit_price=exit_price,
            quantity=self.position.quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            reason=reason,
        )
        self.trades.append(trade)
        self.risk.register_trade_result(pnl, now=now)
        self.position = None
        self.entry_fee_paid = 0.0
        self.last_processed_close_time = now

        return {
            "time": now.isoformat(),
            "event": "sell",
            "price": exit_price,
            "pnl": pnl,
            "pnl_pct": pnl_pct * 100,
            "equity": self._equity(close),
            "reason": reason,
        }

    def summary(self) -> dict[str, Any]:
        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl < 0]
        total_pnl = sum(t.pnl for t in self.trades)
        return {
            "trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": (len(wins) / len(self.trades) * 100) if self.trades else 0.0,
            "total_pnl": total_pnl,
            "balance": self.cash,
            "roi_pct": ((self.cash / self.cfg.initial_balance) - 1) * 100,
            "position_open": self.position is not None,
            "position": _position_to_dict(self.position),
            "trades_detail": [asdict(t) for t in self.trades],
        }


def run_paper(
    cfg: BotConfig,
    data_client,
    cycles: int = 30,
    sleep_seconds: int = 60,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    state_store: EventStore | None = None,
) -> dict[str, Any]:
    strategy = HybridStrategy(cfg)
    risk = RiskManager(cfg)
    trader = PaperTrader(cfg, strategy, risk, state_store=state_store)

    completed_cycles = 0
    while True:
        try:
            df = data_client.get_klines(cfg.symbol, cfg.interval, cfg.lookback)
            higher_df = None
            if cfg.use_multi_timeframe:
                higher_df = data_client.get_klines(
                    cfg.symbol, cfg.higher_interval, cfg.higher_lookback
                )
            macro_df = None
            if cfg.use_btc_macro_filter:
                macro_df = data_client.get_klines(
                    cfg.macro_symbol, cfg.macro_interval, cfg.macro_lookback
                )
            event = trader.on_candle(df, higher_df, macro_df)
            trader.save_state()
            print(event)
            if event_callback is not None:
                event_callback(event)

            completed_cycles += 1
            if cycles > 0 and completed_cycles >= cycles:
                break
        except requests.exceptions.RequestException as exc:
            error_event = {
                "time": datetime.utcnow().isoformat(),
                "event": "data_error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            print(error_event)
            if event_callback is not None:
                event_callback(error_event)
        except Exception as exc:
            error_event = {
                "time": datetime.utcnow().isoformat(),
                "event": "paper_error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            print(error_event)
            if event_callback is not None:
                event_callback(error_event)

        if cycles > 0 and completed_cycles >= cycles:
            break

        time.sleep(sleep_seconds)

    out = trader.summary()
    print("paper_summary:", out)
    return out
