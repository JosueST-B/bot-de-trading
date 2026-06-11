from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BotConfig:
    symbol: str = "BTCUSDT"
    interval: str = "15m"
    lookback: int = 1000

    initial_balance: float = 10_000.0
    fee_rate: float = 0.001
    slippage: float = 0.0005

    risk_per_trade: float = 0.01
    max_position_pct: float = 0.25
    stop_atr_mult: float = 1.8
    take_profit_rr: float = 2.2
    trailing_atr_mult: float = 1.0
    max_daily_drawdown: float = 0.05
    max_consecutive_losses: int = 4
    max_trades_per_day: int = 4
    weak_regime_max_trades_per_day: int = 2
    entry_cooldown_candles: int = 2

    min_trend_strength: float = 0.002
    min_confidence: float = 0.55
    strategy_mode: str = "auto"
    min_entry_quality: float = 0.72
    min_volume_z: float = -0.15
    max_entry_rsi: float = 70.0
    max_chase_atr_mult: float = 1.35

    use_regime_filter: bool = True
    use_multi_timeframe: bool = True
    higher_interval: str = "1h"
    higher_lookback: int = 500
    min_regime_trend_strength: float = 0.0015
    max_regime_atr_pct: float = 0.055
    use_btc_macro_filter: bool = True
    macro_symbol: str = "BTCUSDT"
    macro_interval: str = "4h"
    macro_lookback: int = 500
    macro_ema_length: int = 200
    max_macro_drawdown_pct: float = 0.12

    live_enabled: bool = False
    use_testnet: bool = True
    allow_real_trading: bool = False
    binance_api_key: str = ""
    binance_api_secret: str = ""
    live_max_quote_per_trade: float = 25.0
    live_block_unknown_position: bool = True

    log_file: str = "bot.log"
    event_db_path: str = "bot_events.sqlite3"
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    active_symbols: str = "BTCUSDT"
    auto_tune_enabled: bool = False
    auto_tune_interval_hours: int = 24
    dynamic_timeframe_enabled: bool = True
    binance_square_enabled: bool = False
    binance_square_api_key: str = ""

    @property
    def symbols_to_trade(self) -> list[str]:
        return [s.strip().upper() for s in self.active_symbols.split(",") if s.strip()]

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            symbol=os.getenv("SYMBOL", cls.symbol),
            interval=os.getenv("INTERVAL", cls.interval),
            lookback=int(os.getenv("LOOKBACK", cls.lookback)),
            initial_balance=float(os.getenv("INITIAL_BALANCE", cls.initial_balance)),
            fee_rate=float(os.getenv("FEE_RATE", cls.fee_rate)),
            slippage=float(os.getenv("SLIPPAGE", cls.slippage)),
            risk_per_trade=float(os.getenv("RISK_PER_TRADE", cls.risk_per_trade)),
            max_position_pct=float(os.getenv("MAX_POSITION_PCT", cls.max_position_pct)),
            stop_atr_mult=float(os.getenv("STOP_ATR_MULT", cls.stop_atr_mult)),
            take_profit_rr=float(os.getenv("TAKE_PROFIT_RR", cls.take_profit_rr)),
            trailing_atr_mult=float(
                os.getenv("TRAILING_ATR_MULT", cls.trailing_atr_mult)
            ),
            max_daily_drawdown=float(
                os.getenv("MAX_DAILY_DRAWDOWN", cls.max_daily_drawdown)
            ),
            max_consecutive_losses=int(
                os.getenv("MAX_CONSECUTIVE_LOSSES", cls.max_consecutive_losses)
            ),
            max_trades_per_day=int(
                os.getenv("MAX_TRADES_PER_DAY", cls.max_trades_per_day)
            ),
            weak_regime_max_trades_per_day=int(
                os.getenv(
                    "WEAK_REGIME_MAX_TRADES_PER_DAY",
                    cls.weak_regime_max_trades_per_day,
                )
            ),
            entry_cooldown_candles=int(
                os.getenv("ENTRY_COOLDOWN_CANDLES", cls.entry_cooldown_candles)
            ),
            min_trend_strength=float(
                os.getenv("MIN_TREND_STRENGTH", cls.min_trend_strength)
            ),
            min_confidence=float(os.getenv("MIN_CONFIDENCE", cls.min_confidence)),
            strategy_mode=os.getenv("STRATEGY_MODE", cls.strategy_mode).lower(),
            min_entry_quality=float(
                os.getenv("MIN_ENTRY_QUALITY", cls.min_entry_quality)
            ),
            min_volume_z=float(os.getenv("MIN_VOLUME_Z", cls.min_volume_z)),
            max_entry_rsi=float(os.getenv("MAX_ENTRY_RSI", cls.max_entry_rsi)),
            max_chase_atr_mult=float(
                os.getenv("MAX_CHASE_ATR_MULT", cls.max_chase_atr_mult)
            ),
            use_regime_filter=_as_bool(
                os.getenv("USE_REGIME_FILTER"), cls.use_regime_filter
            ),
            use_multi_timeframe=_as_bool(
                os.getenv("USE_MULTI_TIMEFRAME"), cls.use_multi_timeframe
            ),
            higher_interval=os.getenv("HIGHER_INTERVAL", cls.higher_interval),
            higher_lookback=int(os.getenv("HIGHER_LOOKBACK", cls.higher_lookback)),
            min_regime_trend_strength=float(
                os.getenv("MIN_REGIME_TREND_STRENGTH", cls.min_regime_trend_strength)
            ),
            max_regime_atr_pct=float(
                os.getenv("MAX_REGIME_ATR_PCT", cls.max_regime_atr_pct)
            ),
            use_btc_macro_filter=_as_bool(
                os.getenv("USE_BTC_MACRO_FILTER"), cls.use_btc_macro_filter
            ),
            macro_symbol=os.getenv("MACRO_SYMBOL", cls.macro_symbol),
            macro_interval=os.getenv("MACRO_INTERVAL", cls.macro_interval),
            macro_lookback=int(os.getenv("MACRO_LOOKBACK", cls.macro_lookback)),
            macro_ema_length=int(os.getenv("MACRO_EMA_LENGTH", cls.macro_ema_length)),
            max_macro_drawdown_pct=float(
                os.getenv("MAX_MACRO_DRAWDOWN_PCT", cls.max_macro_drawdown_pct)
            ),
            live_enabled=_as_bool(os.getenv("LIVE_ENABLED"), cls.live_enabled),
            use_testnet=_as_bool(os.getenv("USE_TESTNET"), cls.use_testnet),
            allow_real_trading=_as_bool(
                os.getenv("ALLOW_REAL_TRADING"),
                cls.allow_real_trading,
            ),
            binance_api_key=os.getenv("BINANCE_API_KEY", ""),
            binance_api_secret=os.getenv("BINANCE_API_SECRET", ""),
            live_max_quote_per_trade=float(
                os.getenv("LIVE_MAX_QUOTE_PER_TRADE", cls.live_max_quote_per_trade)
            ),
            live_block_unknown_position=_as_bool(
                os.getenv("LIVE_BLOCK_UNKNOWN_POSITION"),
                cls.live_block_unknown_position,
            ),
            log_file=os.getenv("LOG_FILE", cls.log_file),
            event_db_path=os.getenv("EVENT_DB_PATH", cls.event_db_path),
            telegram_enabled=_as_bool(os.getenv("TELEGRAM_ENABLED"), cls.telegram_enabled),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            active_symbols=os.getenv("ACTIVE_SYMBOLS", os.getenv("SYMBOL", cls.active_symbols)),
            auto_tune_enabled=_as_bool(os.getenv("AUTO_TUNE_ENABLED"), cls.auto_tune_enabled),
            auto_tune_interval_hours=int(os.getenv("AUTO_TUNE_INTERVAL_HOURS", str(cls.auto_tune_interval_hours))),
            dynamic_timeframe_enabled=_as_bool(os.getenv("DYNAMIC_TIMEFRAME_ENABLED"), cls.dynamic_timeframe_enabled),
            binance_square_enabled=_as_bool(os.getenv("BINANCE_SQUARE_ENABLED"), cls.binance_square_enabled),
            binance_square_api_key=os.getenv("BINANCE_SQUARE_API_KEY", ""),
        )
