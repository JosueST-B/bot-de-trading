from __future__ import annotations

from bot.config import BotConfig


def _legacy_symbol(symbol: str) -> str:
    if "/" in symbol:
        return symbol
    if symbol.endswith("USDT") and len(symbol) > 4:
        base = symbol[:-4]
        return f"{base}/USDT"
    return symbol


class Config:
    """
    Compatibility facade over the current bot configuration.

    The active engine lives in `bot/`. This class keeps the old imports working
    while reading from the same `.env` and risk model as the new architecture.
    """

    _cfg = BotConfig.from_env()

    TRADING_MODE = "paper" if _cfg.use_testnet else "live"
    API_KEY = _cfg.binance_api_key
    SECRET_KEY = _cfg.binance_api_secret

    SYMBOL = _legacy_symbol(_cfg.symbol)
    TIMEFRAME = _cfg.interval
    MAX_RISK_PER_TRADE = _cfg.risk_per_trade

    TELEGRAM_BOT_TOKEN = _cfg.telegram_bot_token
    TELEGRAM_CHAT_ID = _cfg.telegram_chat_id

    @staticmethod
    def is_testnet() -> bool:
        return Config._cfg.use_testnet
