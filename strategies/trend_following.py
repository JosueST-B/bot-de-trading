from __future__ import annotations

import pandas as pd

from bot.indicators import atr, rsi
from strategies.base_strategy import BaseStrategy


class TrendFollowingStrategy(BaseStrategy):
    """
    Legacy trend-following strategy kept for compatibility.

    The production bot uses `bot.strategy.HybridStrategy`, but this module now
    shares the same indicator layer and no longer depends on a separate stack.
    """

    def __init__(self, short_window: int = 20, long_window: int = 50, rsi_period: int = 14):
        super().__init__("Trend Following (SMA + RSI)")
        self.short_window = short_window
        self.long_window = long_window
        self.rsi_period = rsi_period

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out[f"SMA_{self.short_window}"] = out["close"].rolling(self.short_window).mean()
        out[f"SMA_{self.long_window}"] = out["close"].rolling(self.long_window).mean()
        out[f"RSI_{self.rsi_period}"] = rsi(out["close"], self.rsi_period)
        out["ATR"] = atr(out["high"], out["low"], out["close"], 14)
        return out

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self.add_indicators(df)
        out = out.dropna().copy()
        out["signal"] = 0

        col_sma_short = f"SMA_{self.short_window}"
        col_sma_long = f"SMA_{self.long_window}"
        col_rsi = f"RSI_{self.rsi_period}"

        bullish_cross = (out[col_sma_short] > out[col_sma_long]) & (
            out[col_sma_short].shift(1) <= out[col_sma_long].shift(1)
        )
        bearish_cross = (out[col_sma_short] < out[col_sma_long]) & (
            out[col_sma_short].shift(1) >= out[col_sma_long].shift(1)
        )

        out.loc[bullish_cross & (out[col_rsi] > 50), "signal"] = 1
        out.loc[bearish_cross, "signal"] = -1
        return out
