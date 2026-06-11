from __future__ import annotations

import pandas as pd

from bot.indicators import atr, rsi
from strategies.base_strategy import BaseStrategy


class MeanReversionStrategy(BaseStrategy):
    """
    Legacy mean-reversion strategy kept compatible with the new indicator layer.
    """

    def __init__(self, bb_length: int = 20, bb_std: float = 2.0, rsi_period: int = 14):
        super().__init__("Mean Reversion (BB + RSI)")
        self.bb_length = bb_length
        self.bb_std = bb_std
        self.rsi_period = rsi_period

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        mid = out["close"].rolling(self.bb_length).mean()
        std = out["close"].rolling(self.bb_length).std()
        out[f"BBL_{self.bb_length}_{float(self.bb_std)}"] = mid - (std * self.bb_std)
        out[f"BBM_{self.bb_length}_{float(self.bb_std)}"] = mid
        out[f"BBU_{self.bb_length}_{float(self.bb_std)}"] = mid + (std * self.bb_std)
        out[f"RSI_{self.rsi_period}"] = rsi(out["close"], self.rsi_period)
        out["ATR"] = atr(out["high"], out["low"], out["close"], 14)
        return out

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self.add_indicators(df)
        out["signal"] = 0

        col_bbl = f"BBL_{self.bb_length}_{float(self.bb_std)}"
        col_bbu = f"BBU_{self.bb_length}_{float(self.bb_std)}"
        col_rsi = f"RSI_{self.rsi_period}"

        buy_condition = (out["close"] < out[col_bbl]) & (out[col_rsi] < 30)
        sell_condition = (out["close"] > out[col_bbu]) & (out[col_rsi] > 70)

        out.loc[buy_condition, "signal"] = 1
        out.loc[sell_condition, "signal"] = -1
        return out
