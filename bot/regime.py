from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from bot.config import BotConfig
from bot.indicators import atr, ema


@dataclass(frozen=True)
class MarketRegime:
    name: str
    direction: str
    trend_strength: float
    atr_pct: float
    risk_multiplier: float
    allow_long: bool
    reason: str


def classify_market(df: pd.DataFrame, cfg: BotConfig) -> MarketRegime:
    if len(df) < 80:
        return MarketRegime(
            name="unknown",
            direction="neutral",
            trend_strength=0.0,
            atr_pct=0.0,
            risk_multiplier=0.0,
            allow_long=False,
            reason="insufficient_regime_data",
        )

    work = df.copy()
    if "ema_fast" not in work.columns:
        work["ema_fast"] = ema(work["close"], 21)
    if "ema_slow" not in work.columns:
        work["ema_slow"] = ema(work["close"], 55)
    if "atr" not in work.columns:
        work["atr"] = atr(work["high"], work["low"], work["close"], 14)
    if "atr_pct" not in work.columns:
        work["atr_pct"] = work["atr"] / work["close"]
    if "ema_slow_slope" not in work.columns:
        work["ema_slow_slope"] = work["ema_slow"].pct_change(8)
    work = work.dropna()

    if work.empty:
        return MarketRegime(
            name="unknown",
            direction="neutral",
            trend_strength=0.0,
            atr_pct=0.0,
            risk_multiplier=0.0,
            allow_long=False,
            reason="empty_regime_features",
        )

    return classify_market_row(work.iloc[-1], cfg)


def classify_market_row(row: pd.Series, cfg: BotConfig) -> MarketRegime:
    last = row
    close = float(last["close"])
    ema_fast = float(last["ema_fast"])
    ema_slow = float(last["ema_slow"])
    trend_strength = (ema_fast - ema_slow) / close if close > 0 else 0.0
    atr_pct = float(last["atr_pct"])
    slow_slope = float(last["ema_slow_slope"])

    if atr_pct > cfg.max_regime_atr_pct:
        return MarketRegime(
            name="high_volatility",
            direction="neutral",
            trend_strength=trend_strength,
            atr_pct=atr_pct,
            risk_multiplier=0.0,
            allow_long=False,
            reason="atr_pct_too_high",
        )

    bullish = trend_strength >= cfg.min_regime_trend_strength and slow_slope > 0
    bearish = trend_strength <= -cfg.min_regime_trend_strength and slow_slope < 0

    if bullish:
        return MarketRegime(
            name="bullish_trend",
            direction="up",
            trend_strength=trend_strength,
            atr_pct=atr_pct,
            risk_multiplier=1.0,
            allow_long=True,
            reason="higher_trend_up",
        )

    if bearish:
        return MarketRegime(
            name="bearish_trend",
            direction="down",
            trend_strength=trend_strength,
            atr_pct=atr_pct,
            risk_multiplier=0.0,
            allow_long=False,
            reason="higher_trend_down",
        )

    return MarketRegime(
        name="range",
        direction="neutral",
        trend_strength=trend_strength,
        atr_pct=atr_pct,
        risk_multiplier=0.35,
        allow_long=atr_pct <= cfg.max_regime_atr_pct * 0.65,
        reason="range_low_volatility",
    )


def interval_to_pandas_rule(interval: str) -> str:
    value = int(interval[:-1])
    unit = interval[-1].lower()
    if unit == "m":
        return f"{value}min"
    if unit == "h":
        return f"{value}h"
    if unit == "d":
        return f"{value}D"
    raise ValueError(f"Unsupported interval: {interval}")


def resample_ohlcv(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    rule = interval_to_pandas_rule(interval)
    work = df.copy()
    work = work.set_index(pd.to_datetime(work["open_time"], utc=True))
    out = work.resample(rule, label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "close_time": "last",
        }
    )
    out = out.dropna(subset=["open", "high", "low", "close"]).reset_index()
    out = out.rename(columns={"open_time": "open_time"})
    if "open_time" not in out.columns:
        out = out.rename(columns={out.columns[0]: "open_time"})
    return out[["open_time", "open", "high", "low", "close", "volume", "close_time"]]
