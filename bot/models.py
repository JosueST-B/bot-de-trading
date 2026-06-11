from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Signal:
    action: str
    confidence: float
    reason: str


@dataclass
class Position:
    entry_time: datetime
    entry_price: float
    quantity: float
    stop_price: float
    take_profit_price: float


@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    reason: str

