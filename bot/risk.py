from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import pandas as pd

from bot.config import BotConfig

logger = logging.getLogger(__name__)

FIDUCIARY_DRAWDOWN_LIMIT: float = -0.064  # -6.4% absolute drawdown lock
MAX_VOLATILITY_RATIO: float = 3.0         # 3x ATR baseline expansion limit


class CircuitBreakerStatus(str, Enum):
    NORMAL = "NORMAL"
    DEFENSIVE = "DEFENSIVE"
    LOCKED = "LOCKED"
    LOCKED_DEFENSIVE = "LOCKED_DEFENSIVE"


@dataclass
class RiskState:
    day_anchor: datetime | None = None
    day_start_equity: float = 0.0
    consecutive_losses: int = 0
    daily_trade_count: int = 0
    last_entry_time: datetime | None = None
    last_exit_time: datetime | None = None
    circuit_breaker_status: CircuitBreakerStatus = CircuitBreakerStatus.NORMAL
    circuit_breaker_reason: str = "ok"
    circuit_breaker_paused: bool = False
    circuit_breaker_pause_until: float = 0.0


def _interval_to_minutes(interval: str) -> int:
    value = int(interval[:-1])
    unit = interval[-1].lower()
    if unit == "m":
        return value
    if unit == "h":
        return value * 60
    if unit == "d":
        return value * 60 * 24
    raise ValueError(f"Unsupported interval: {interval}")


def validate_pre_trade_slippage(
    order_book: dict[str, Any],
    expected_price: float,
    max_slippage: float = 0.0005,
) -> tuple[bool, float, str]:
    """Inspects top of ask order book against expected price."""
    if not order_book or not order_book.get("asks"):
        return False, 1.0, "empty_order_book"
    try:
        best_ask = float(order_book["asks"][0][0])
    except (IndexError, ValueError, TypeError):
        return False, 1.0, "invalid_order_book"
    if expected_price <= 0:
        return False, 1.0, "invalid_expected_price"
    slippage = (best_ask - expected_price) / expected_price
    if slippage > max_slippage:
        return False, slippage, "projected_slippage_exceeded"
    return True, slippage, "ok"


def evaluate_drawdown_lock(
    current_equity: float,
    start_equity: float,
    limit: float = -0.064,
) -> tuple[bool, str]:
    """Evaluates absolute equity drawdown against -6.4% fiduciary limit."""
    if start_equity <= 0:
        return False, CircuitBreakerStatus.LOCKED_DEFENSIVE.value
    drawdown = (current_equity - start_equity) / start_equity
    if drawdown <= limit:
        return False, CircuitBreakerStatus.LOCKED_DEFENSIVE.value
    return True, CircuitBreakerStatus.NORMAL.value


def validate_volatility_regime(
    klines_df: pd.DataFrame,
    max_ratio: float = 3.0,
) -> tuple[bool, float, str]:
    """Rejects new entries when ATR14 exceeds max_ratio x baseline volatility."""
    if klines_df is None or len(klines_df) < 15:
        return True, 1.0, "insufficient_data"
    high = klines_df["high"].astype(float)
    low = klines_df["low"].astype(float)
    close = klines_df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    baseline = atr14.iloc[-15:-1].median() if len(atr14) >= 15 else atr14.mean()
    current_atr = atr14.iloc[-1]
    if baseline <= 0 or math.isnan(baseline):
        return True, 1.0, "zero_baseline"
    ratio = current_atr / baseline
    if ratio > max_ratio:
        return False, float(ratio), "extreme_volatility_rejected"
    return True, float(ratio), "ok"


def audit_post_fill_slippage(
    executed_price: float,
    expected_price: float,
    max_slippage: float = 0.0005,
    multiplier: float = 2.0,
) -> tuple[bool, float, str]:
    """Validates effective fill price against expected price.
    Returns (is_acceptable, slippage_pct, reason)."""
    if expected_price <= 0:
        return False, 1.0, "invalid_expected_price"
    slippage = (executed_price - expected_price) / expected_price
    threshold = max_slippage * multiplier
    if slippage > threshold:
        return False, slippage, f"excessive_post_fill_slippage: {slippage:.4%} > {threshold:.4%}"
    return True, slippage, "ok"


class RiskManager:
    FIDUCIARY_DRAWDOWN_LIMIT: float = -0.064  # -6.4%
    MAX_VOLATILITY_RATIO: float = 3.0         # 3x ATR baseline

    def __init__(self, cfg: BotConfig, shared_state_path: str | None = None) -> None:
        self.cfg = cfg
        self.state = RiskState()
        self.shared_state_path = (
            shared_state_path
            or os.environ.get("GLOBAL_TRADING_STATE_FILE")
            or os.environ.get("GLOBAL_TRADING_STATE_PATH")
            or getattr(cfg, "global_trading_state_file", None)
            or os.path.join(tempfile.gettempdir(), "global_trading_state.json")
        )

    def sync_day(self, now: datetime, equity: float) -> None:
        if self.state.day_anchor is None or self.state.day_start_equity <= 0:
            self.state.day_anchor = now
            self.state.day_start_equity = equity
            return

        if now.date() != self.state.day_anchor.date():
            self.state.day_anchor = now
            self.state.day_start_equity = equity
            self.state.consecutive_losses = 0
            self.state.daily_trade_count = 0

    def check_global_circuit_breaker(
        self, current_equity: float, start_equity: float | None = None
    ) -> tuple[bool, str]:
        # 1. Check temporary circuit breaker pause (e.g. from post-fill slippage)
        if self.state.circuit_breaker_paused and time.time() < self.state.circuit_breaker_pause_until:
            return False, f"circuit_breaker_paused: {self.state.circuit_breaker_reason}"

        # 2. Check per-bot / per-instance fiduciary drawdown limit (-6.4%)
        if start_equity is None:
            start_equity = self.state.day_start_equity if self.state.day_start_equity > 0 else current_equity

        if start_equity <= 0 and current_equity <= 0:
            self.state.circuit_breaker_status = CircuitBreakerStatus.LOCKED_DEFENSIVE
            self.state.circuit_breaker_paused = True
            return False, "circuit_breaker_locked_defensive: non_positive_equity"

        if start_equity > 0:
            bot_dd = (current_equity - start_equity) / start_equity
            if bot_dd <= self.FIDUCIARY_DRAWDOWN_LIMIT:
                self.state.circuit_breaker_status = CircuitBreakerStatus.LOCKED_DEFENSIVE
                self.state.circuit_breaker_paused = True
                return False, f"circuit_breaker_locked_defensive: fiduciary drawdown limit {self.FIDUCIARY_DRAWDOWN_LIMIT:.1%} breached ({bot_dd:.2%})"

        # 3. Consolidated cross-bot circuit breaker check via decoupled shared state
        shared_file = self.shared_state_path
        now_str = datetime.utcnow().date().isoformat()
        state = {"date": now_str, "paused": False, "bots": {}}

        if os.path.exists(shared_file):
            try:
                with open(shared_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict) and "bots" in loaded:
                        state = loaded
            except Exception as read_err:
                logger.debug("No se pudo leer shared state %s: %s", shared_file, read_err)

        if state.get("date") != now_str:
            state["date"] = now_str
            state["paused"] = False
            for bid in state.get("bots", {}):
                state["bots"][bid]["start_equity"] = state["bots"][bid].get("current_equity", 0.0)

        is_ibkr = any(sym in self.cfg.symbol for sym in ("AAPL", "TSLA", "MSFT", "NVDA", "SPY", "QQQ"))
        bot_id = f"{'ibkr' if is_ibkr else 'binance'}_{self.cfg.symbol}"

        bot_data = state.get("bots", {}).get(bot_id, {})
        stored_start = bot_data.get("start_equity", 0.0)
        if start_equity > 0:
            eff_start = start_equity
        elif stored_start > 0:
            eff_start = stored_start
        else:
            eff_start = current_equity

        if "bots" not in state:
            state["bots"] = {}
        state["bots"][bot_id] = {
            "start_equity": eff_start,
            "current_equity": current_equity,
            "timestamp": time.time(),
        }

        total_start = 0.0
        total_current = 0.0
        current_ts = time.time()
        for bid, data in state["bots"].items():
            if current_ts - data.get("timestamp", 0) < 900:
                total_start += data.get("start_equity", 0.0)
                total_current += data.get("current_equity", 0.0)

        global_drawdown = 0.0
        if total_start > 0.0:
            global_drawdown = (total_current - total_start) / total_start
            if global_drawdown <= self.FIDUCIARY_DRAWDOWN_LIMIT:
                state["paused"] = True
            elif state.get("paused", False) and global_drawdown > self.FIDUCIARY_DRAWDOWN_LIMIT:
                state["paused"] = False

        try:
            parent_dir = os.path.dirname(os.path.abspath(shared_file))
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            with open(shared_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as write_err:
            logger.debug("No se pudo escribir shared state %s: %s", shared_file, write_err)

        # 4. Sync with SQLite bot_state table if available
        db_path = getattr(self.cfg, "event_db_path", None)
        if db_path and os.path.exists(db_path):
            try:
                import sqlite3
                with sqlite3.connect(db_path, timeout=2.0) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "CREATE TABLE IF NOT EXISTS bot_state ("
                        "key TEXT PRIMARY KEY, updated_ts TEXT, value_json TEXT)"
                    )
                    if state.get("paused", False):
                        cb_payload = json.dumps({
                            "status": CircuitBreakerStatus.LOCKED_DEFENSIVE.value,
                            "paused": True,
                            "drawdown": global_drawdown,
                            "reason": f"drawdown consolidado {global_drawdown:.2%}",
                            "timestamp": current_ts,
                        })
                        cur.execute(
                            "INSERT INTO bot_state (key, updated_ts, value_json) VALUES ('circuit_breaker:global', datetime('now'), ?) "
                            "ON CONFLICT(key) DO UPDATE SET updated_ts=datetime('now'), value_json=excluded.value_json",
                            (cb_payload,),
                        )
                        conn.commit()
            except Exception as db_err:
                logger.debug("Error sincronizando circuit breaker con SQLite: %s", db_err)

        if state.get("paused", False):
            self.state.circuit_breaker_status = CircuitBreakerStatus.LOCKED_DEFENSIVE
            self.state.circuit_breaker_paused = True
            return False, f"global_circuit_breaker_active: drawdown consolidado {global_drawdown:.2%}"

        self.state.circuit_breaker_status = CircuitBreakerStatus.NORMAL
        self.state.circuit_breaker_paused = False
        return True, "ok"

    def validate_pre_trade_slippage(
        self,
        order_book: dict[str, Any],
        expected_price: float,
        max_slippage: float | None = None,
    ) -> tuple[bool, float, str]:
        limit = max_slippage if max_slippage is not None else self.cfg.slippage
        return validate_pre_trade_slippage(order_book, expected_price, limit)

    def validate_volatility_regime(
        self,
        klines_df: pd.DataFrame,
        max_ratio: float | None = None,
    ) -> tuple[bool, float, str]:
        ratio_limit = max_ratio if max_ratio is not None else self.MAX_VOLATILITY_RATIO
        return validate_volatility_regime(klines_df, ratio_limit)

    def evaluate_drawdown_lock(
        self,
        current_equity: float,
        start_equity: float | None = None,
        limit: float | None = None,
    ) -> tuple[bool, str]:
        start = start_equity if start_equity is not None else self.state.day_start_equity
        lim = limit if limit is not None else self.FIDUCIARY_DRAWDOWN_LIMIT
        return evaluate_drawdown_lock(current_equity, start, lim)

    def audit_post_fill_slippage(
        self,
        executed_price: float,
        expected_price: float,
        max_slippage: float | None = None,
        multiplier: float = 2.0,
    ) -> tuple[bool, float, str]:
        limit = max_slippage if max_slippage is not None else self.cfg.slippage
        return audit_post_fill_slippage(executed_price, expected_price, limit, multiplier)

    def trigger_slippage_pause(
        self, actual_slippage: float, reason: str, pause_duration_sec: float = 300.0
    ) -> None:
        self.state.circuit_breaker_status = CircuitBreakerStatus.DEFENSIVE
        self.state.circuit_breaker_paused = True
        self.state.circuit_breaker_reason = f"post_fill_slippage_pause: {reason}"
        self.state.circuit_breaker_pause_until = time.time() + pause_duration_sec

    def reset_circuit_breaker(self) -> None:
        self.state.circuit_breaker_status = CircuitBreakerStatus.NORMAL
        self.state.circuit_breaker_paused = False
        self.state.circuit_breaker_reason = "ok"
        self.state.circuit_breaker_pause_until = 0.0
        try:
            if os.path.exists(self.shared_state_path):
                with open(self.shared_state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                state["paused"] = False
                with open(self.shared_state_path, "w", encoding="utf-8") as f:
                    json.dump(state, f)
        except Exception:
            pass

    def can_trade(
        self,
        now: datetime,
        equity: float,
        regime_name: str | None = None,
        interval: str | None = None,
    ) -> tuple[bool, str]:
        self.sync_day(now, equity)
        if self.state.day_start_equity <= 0:
            return False, "invalid_day_start_equity"

        allowed, global_reason = self.check_global_circuit_breaker(equity, self.state.day_start_equity)
        if not allowed:
            return False, global_reason

        day_dd = 1 - (equity / self.state.day_start_equity)
        if day_dd >= abs(self.FIDUCIARY_DRAWDOWN_LIMIT):
            return False, "fiduciary_drawdown_limit_reached"

        if day_dd >= self.cfg.max_daily_drawdown:
            return False, "max_daily_drawdown_reached"

        if self.state.consecutive_losses >= self.cfg.max_consecutive_losses:
            return False, "max_consecutive_losses_reached"

        max_daily_trades = self.cfg.max_trades_per_day
        if regime_name in {"range", "unknown"}:
            max_daily_trades = min(
                max_daily_trades,
                self.cfg.weak_regime_max_trades_per_day,
            )
        if max_daily_trades > 0 and self.state.daily_trade_count >= max_daily_trades:
            return False, "max_daily_trades_reached"

        if (
            interval is not None
            and self.cfg.entry_cooldown_candles > 0
            and self.state.last_exit_time is not None
        ):
            cooldown_minutes = _interval_to_minutes(interval) * self.cfg.entry_cooldown_candles
            elapsed_minutes = (now - self.state.last_exit_time).total_seconds() / 60
            if elapsed_minutes < cooldown_minutes:
                return False, "entry_cooldown_active"

        return True, "ok"

    def position_size(
        self,
        equity: float,
        entry_price: float,
        stop_price: float,
        fee_rate: float,
    ) -> float:
        risk_budget = equity * self.cfg.risk_per_trade
        risk_per_unit = abs(entry_price - stop_price) + (entry_price * fee_rate * 2)
        if risk_per_unit <= 0:
            return 0.0

        raw_qty = risk_budget / risk_per_unit
        max_qty = (equity * self.cfg.max_position_pct) / entry_price
        return max(min(raw_qty, max_qty), 0.0)

    def register_entry(self, now: datetime) -> None:
        self.state.last_entry_time = now
        self.state.daily_trade_count += 1

    def register_trade_result(self, pnl: float, now: datetime | None = None) -> None:
        if now is not None:
            self.state.last_exit_time = now
        if pnl < 0:
            self.state.consecutive_losses += 1
            return
        self.state.consecutive_losses = 0

