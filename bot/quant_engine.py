"""Unified Symmetric 5-Factor Quantitative Alpha Scoring Engine.

Applies symmetric multi-factor scoring across both Crypto and Equities/ETFs:
S_composite = 0.25 * S_trend + 0.20 * S_mom + 0.15 * S_vol + 0.20 * S_ml + 0.20 * S_sent >= 0.72

Factors:
1. Trend (EMA Golden Stack, Slopes, VWAP / Close Position): Weight = 0.25
2. Momentum (RSI [45, 68] Expansion, MACD Histogram, Volume Z-Score): Weight = 0.20
3. Volatility (ATR% Normalization by asset class, Flash Spike Check): Weight = 0.15
4. ML Regime (RandomForest predictive probability / regime filter): Weight = 0.20
5. Sentiment (FinBERT / News compound polarity normalized): Weight = 0.20
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from bot.indicators import atr, ema, macd, rolling_vwap, rsi, zscore

logger = logging.getLogger(__name__)

COMPOSITE_THRESHOLD: float = 0.72
WEIGHT_TREND: float = 0.25
WEIGHT_MOMENTUM: float = 0.20
WEIGHT_VOLATILITY: float = 0.15
WEIGHT_ML: float = 0.20
WEIGHT_SENTIMENT: float = 0.20

CRYPTO_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT",
    "BTC", "ETH", "SOL", "BNB", "XRP", "LINK", "AVAX", "SUI",
}

STOCK_SYMBOLS = {
    "NVDA", "AAPL", "MSFT", "AMZN", "SPY", "QQQ"
}


@dataclass
class FactorBreakdown:
    symbol: str
    trend: float
    momentum: float
    volatility: float
    ml: float
    sentiment: float
    composite: float
    is_buy_authorized: bool
    details: dict[str, Any] = field(default_factory=dict)


def calculate_composite_score(
    trend: float,
    mom: float,
    vol: float,
    ml: float,
    sent: float,
    w_trend: float = WEIGHT_TREND,
    w_mom: float = WEIGHT_MOMENTUM,
    w_vol: float = WEIGHT_VOLATILITY,
    w_ml: float = WEIGHT_ML,
    w_sent: float = WEIGHT_SENTIMENT,
) -> float:
    """Calculates weighted linear composite alpha score S_composite in [0.0, 1.0]."""
    score = (
        w_trend * max(0.0, min(1.0, trend))
        + w_mom * max(0.0, min(1.0, mom))
        + w_vol * max(0.0, min(1.0, vol))
        + w_ml * max(0.0, min(1.0, ml))
        + w_sent * max(0.0, min(1.0, sent))
    )
    return round(float(score), 4)


class QuantEngine:
    """Unified 5-factor quantitative alpha brain for 14-asset portfolio."""

    THRESHOLD: float = COMPOSITE_THRESHOLD
    W_TREND: float = WEIGHT_TREND
    W_MOM: float = WEIGHT_MOMENTUM
    W_VOL: float = WEIGHT_VOLATILITY
    W_ML: float = WEIGHT_ML
    W_SENT: float = WEIGHT_SENTIMENT

    @staticmethod
    def is_crypto_asset(symbol: str) -> bool:
        clean = symbol.replace("/", "").replace("-", "").replace("_", "").upper()
        if clean in CRYPTO_SYMBOLS or clean.endswith("USDT"):
            return True
        if any(stock in clean for stock in STOCK_SYMBOLS):
            return False
        return True

    @classmethod
    def compute_trend_score(cls, df: pd.DataFrame) -> tuple[float, dict[str, Any]]:
        """
        Factor 1: Trend alignment (0.0 to 1.0).
        - EMA21 > EMA55 > EMA200 golden stack (+0.50)
        - EMA55 slope > 0 (+0.25)
        - Close >= VWAP or Close >= EMA21 (+0.25)
        """
        if df is None or len(df) < 20:
            return 0.50, {"reason": "insufficient_data_neutral"}

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series(1.0, index=df.index)

        curr_close = float(close.iloc[-1])
        ema21 = ema(close, 21)
        ema55 = ema(close, 55)
        ema200 = ema(close, min(200, len(close)))

        curr_ema21 = float(ema21.iloc[-1])
        curr_ema55 = float(ema55.iloc[-1])
        curr_ema200 = float(ema200.iloc[-1])

        # Slope over last 5 bars
        slope55 = (curr_ema55 - float(ema55.iloc[-5])) / max(float(ema55.iloc[-5]), 1e-6) if len(ema55) >= 5 else 0.0

        # VWAP
        try:
            vwap_series = rolling_vwap(high, low, close, volume, 20)
            curr_vwap = float(vwap_series.iloc[-1])
            if np.isnan(curr_vwap) or curr_vwap <= 0:
                curr_vwap = float(close.rolling(20).mean().iloc[-1])
        except Exception:
            curr_vwap = float(close.rolling(min(20, len(close))).mean().iloc[-1])

        score = 0.0
        # 1. Alignment stack
        if curr_close > curr_ema21 > curr_ema55 > curr_ema200:
            score += 0.50
        elif curr_close > curr_ema21 > curr_ema55:
            score += 0.38
        elif curr_ema21 > curr_ema55:
            score += 0.25
        elif curr_close > curr_ema21:
            score += 0.15

        # 2. Slope momentum
        if slope55 > 0.001:
            score += 0.25
        elif slope55 > 0:
            score += 0.15
        elif slope55 > -0.001:
            score += 0.05

        # 3. VWAP / Price level
        if curr_close >= curr_vwap:
            score += 0.25
        elif curr_close >= curr_ema21:
            score += 0.15

        score = round(max(0.0, min(1.0, score)), 4)
        return score, {
            "close": curr_close,
            "ema21": curr_ema21,
            "ema55": curr_ema55,
            "ema200": curr_ema200,
            "slope55": round(slope55, 6),
            "vwap": round(curr_vwap, 4),
        }

    @classmethod
    def compute_momentum_score(cls, df: pd.DataFrame) -> tuple[float, dict[str, Any]]:
        """
        Factor 2: Momentum (0.0 to 1.0).
        - RSI 14 in [45, 68] bullish expansion (+0.40)
        - MACD histogram > 0 and expanding (+0.35)
        - Volume confirmation z-score >= -0.15 (+0.25)
        """
        if df is None or len(df) < 20:
            return 0.50, {"reason": "insufficient_data_neutral"}

        close = df["close"].astype(float)
        rsi_series = rsi(close, 14)
        curr_rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty and not np.isnan(rsi_series.iloc[-1]) else 50.0

        macd_line, sig_line, hist = macd(close, 12, 26, 9)
        curr_hist = float(hist.iloc[-1]) if not hist.empty and not np.isnan(hist.iloc[-1]) else 0.0
        prev_hist = float(hist.iloc[-3]) if len(hist) >= 3 and not np.isnan(hist.iloc[-3]) else curr_hist
        hist_slope = curr_hist - prev_hist

        curr_z = 0.0
        if "volume" in df.columns:
            try:
                z_series = zscore(df["volume"].astype(float), 20)
                if not z_series.empty and not np.isnan(z_series.iloc[-1]):
                    curr_z = float(z_series.iloc[-1])
            except Exception:
                curr_z = 0.0

        score = 0.0
        # 1. RSI sweet spot
        if 48.0 <= curr_rsi <= 68.0:
            score += 0.40
        elif 40.0 <= curr_rsi < 48.0 or 68.0 < curr_rsi <= 75.0:
            score += 0.25
        elif 35.0 <= curr_rsi < 40.0:
            score += 0.10

        # 2. MACD expansion
        if curr_hist > 0 and hist_slope >= 0:
            score += 0.35
        elif curr_hist > 0:
            score += 0.22
        elif hist_slope > 0:
            score += 0.12

        # 3. Volume confirmation
        if curr_z >= 0.0:
            score += 0.25
        elif curr_z >= -0.15:
            score += 0.15
        elif curr_z >= -0.30:
            score += 0.05

        score = round(max(0.0, min(1.0, score)), 4)
        return score, {
            "rsi": round(curr_rsi, 2),
            "macd_hist": round(curr_hist, 4),
            "hist_slope": round(hist_slope, 4),
            "volume_z": round(curr_z, 3),
        }

    @classmethod
    def compute_volatility_score(
        cls, df: pd.DataFrame, is_crypto: bool = True
    ) -> tuple[float, dict[str, Any]]:
        """
        Factor 3: Volatility quality (0.0 to 1.0).
        - Asset-class scaled ATR% within healthy band (+0.50)
        - ATR baseline ratio <= 2.0x (+0.50)
        - Spike ratio > 3.0x vetoes entry (score = 0.0)
        """
        if df is None or len(df) < 15:
            return 0.50, {"reason": "insufficient_data_neutral"}

        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)

        atr_series = atr(high, low, close, 14)
        curr_atr = float(atr_series.iloc[-1])
        curr_close = float(close.iloc[-1])
        atr_pct = (curr_atr / curr_close) if curr_close > 0 else 0.0

        baseline = atr_series.iloc[-15:-1].median() if len(atr_series) >= 15 else atr_series.mean()
        ratio = (curr_atr / baseline) if baseline > 0 and not np.isnan(baseline) else 1.0

        if ratio > 3.0:
            return 0.0, {
                "atr_pct": round(atr_pct, 4),
                "ratio": round(ratio, 2),
                "veto": "extreme_volatility_spike",
            }

        score = 0.0
        # 1. Healthy volatility band
        if is_crypto:
            if 0.010 <= atr_pct <= 0.060:
                score += 0.50
            elif 0.006 <= atr_pct <= 0.080:
                score += 0.30
            else:
                score += 0.10
        else:
            if 0.002 <= atr_pct <= 0.030:
                score += 0.50
            elif 0.001 <= atr_pct <= 0.045:
                score += 0.30
            else:
                score += 0.10

        # 2. Volatility stability
        if ratio <= 1.8:
            score += 0.50
        elif ratio <= 2.5:
            score += 0.30
        elif ratio <= 3.0:
            score += 0.10

        score = round(max(0.0, min(1.0, score)), 4)
        return score, {
            "atr_pct": round(atr_pct, 4),
            "ratio": round(ratio, 2),
            "is_crypto": is_crypto,
        }

    @classmethod
    def compute_ml_score(
        cls, df: pd.DataFrame, ml_filter: Any = None, default_score: float = 0.50
    ) -> tuple[float, dict[str, Any]]:
        """
        Factor 4: Machine learning regime probability (0.0 to 1.0).
        Evaluates win probability via MLFilter when available.
        Defaults to neutral 0.50 when model is training or unavailable.
        """
        if ml_filter is None or df is None or len(df) < 10:
            return default_score, {"status": "default_neutral", "prob": default_score}

        try:
            if hasattr(ml_filter, "predict_probability"):
                prob = float(ml_filter.predict_probability(df))
                score = prob
                return round(max(0.0, min(1.0, score)), 4), {"status": "ml_evaluated", "prob": prob}
        except Exception as e:
            logger.debug("QuantEngine ML evaluation fallback: %s", e)

        return default_score, {"status": "fallback_neutral", "prob": default_score}

    @classmethod
    def compute_sentiment_score(
        cls, symbol: str, news_analyzer: Any = None, default_score: float = 0.50
    ) -> tuple[float, dict[str, Any]]:
        """
        Factor 5: FinBERT / News Sentiment score (0.0 to 1.0).
        Normalizes compound polarity C in [-1.0, 1.0] -> S = (C + 1.0) / 2.0.
        Defaults to neutral 0.50 when no headlines are recorded.
        """
        if news_analyzer is None:
            return default_score, {"status": "default_neutral", "polarity": 0.0}

        try:
            if hasattr(news_analyzer, "get_sentiment"):
                polarity, details = news_analyzer.get_sentiment()
                score = (float(polarity) + 1.0) / 2.0
                return round(max(0.0, min(1.0, score)), 4), {
                    "status": "sentiment_evaluated",
                    "polarity": polarity,
                    "details": details,
                }
        except Exception as e:
            logger.debug("QuantEngine Sentiment evaluation fallback: %s", e)

        return default_score, {"status": "fallback_neutral", "polarity": 0.0}

    @classmethod
    def evaluate_setup(
        cls,
        symbol: str,
        df: pd.DataFrame,
        ml_filter: Any = None,
        news_analyzer: Any = None,
        is_crypto: bool | None = None,
    ) -> FactorBreakdown:
        """
        Comprehensive multi-factor evaluation for any of the 14 assets.
        S_composite = 0.25*trend + 0.20*mom + 0.15*vol + 0.20*ml + 0.20*sent >= 0.72.
        """
        crypto_flag = is_crypto if is_crypto is not None else cls.is_crypto_asset(symbol)

        s_trend, d_trend = cls.compute_trend_score(df)
        s_mom, d_mom = cls.compute_momentum_score(df)
        s_vol, d_vol = cls.compute_volatility_score(df, is_crypto=crypto_flag)
        s_ml, d_ml = cls.compute_ml_score(df, ml_filter=ml_filter)
        s_sent, d_sent = cls.compute_sentiment_score(symbol, news_analyzer=news_analyzer)

        composite = calculate_composite_score(
            trend=s_trend,
            mom=s_mom,
            vol=s_vol,
            ml=s_ml,
            sent=s_sent,
        )

        is_authorized = bool(composite >= cls.THRESHOLD and d_vol.get("veto") is None)

        details = {
            "trend_details": d_trend,
            "momentum_details": d_mom,
            "volatility_details": d_vol,
            "ml_details": d_ml,
            "sentiment_details": d_sent,
            "hurdle": cls.THRESHOLD,
        }

        return FactorBreakdown(
            symbol=symbol,
            trend=s_trend,
            momentum=s_mom,
            volatility=s_vol,
            ml=s_ml,
            sentiment=s_sent,
            composite=composite,
            is_buy_authorized=is_authorized,
            details=details,
        )


def evaluate_asset_setup(
    symbol: str,
    df: pd.DataFrame,
    ml_filter: Any = None,
    news_analyzer: Any = None,
    is_crypto: bool | None = None,
) -> FactorBreakdown:
    """Convenience functional interface for 5-factor scoring."""
    return QuantEngine.evaluate_setup(
        symbol=symbol,
        df=df,
        ml_filter=ml_filter,
        news_analyzer=news_analyzer,
        is_crypto=is_crypto,
    )
