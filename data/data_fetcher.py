from __future__ import annotations

from bot.binance_client import BinanceDataClient
from core.config import Config


def _normalize_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper().replace("/", "")
    return cleaned


class DataFetcher:
    """
    Legacy-compatible data adapter backed by the current Binance REST client.

    This keeps the old imports working while the real engine uses `bot/`.
    """

    def __init__(self) -> None:
        self.client = BinanceDataClient()
        self.exchange = None

    def fetch_historical_data(
        self,
        symbol: str = Config.SYMBOL,
        timeframe: str = Config.TIMEFRAME,
        limit: int = 1000,
    ):
        normalized = _normalize_symbol(symbol)
        df = self.client.get_klines(normalized, timeframe, limit=limit).copy()
        return df.rename(columns={"open_time": "timestamp"})

    def get_current_price(self, symbol: str = Config.SYMBOL):
        normalized = _normalize_symbol(symbol)
        df = self.client.get_klines(normalized, Config.TIMEFRAME, limit=2)
        if df.empty:
            return None
        return float(df.iloc[-1]["close"])


if __name__ == "__main__":
    fetcher = DataFetcher()
    precio = fetcher.get_current_price("BTC/USDT")
    print(f"Precio actual BTC/USDT: {precio}")
    df = fetcher.fetch_historical_data("BTC/USDT", "1h", limit=5)
    print("\nUltimas 5 velas (1h):")
    print(df.tail())
