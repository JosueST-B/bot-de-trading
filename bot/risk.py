from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.config import BotConfig


@dataclass
class RiskState:
    day_anchor: datetime | None = None
    day_start_equity: float = 0.0
    consecutive_losses: int = 0
    daily_trade_count: int = 0
    last_entry_time: datetime | None = None
    last_exit_time: datetime | None = None


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


class RiskManager:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self.state = RiskState()

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

        day_dd = 1 - (equity / self.state.day_start_equity)
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
