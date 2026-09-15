from __future__ import annotations

from typing import Any

import pandas as pd

from bot.config import BotConfig
from bot.indicators import adx, atr, ema, macd, rolling_vwap, rsi, zscore
from bot.models import Signal
from bot.regime import MarketRegime, classify_market


class HybridStrategy:
    """
    Regime-aware strategy engine.

    Modes:
    - auto: choose trend/breakout/mean-reversion from market regime
    - trend: trend-following only
    - breakout: range/high breakout only
    - pullback_trend: buy controlled pullbacks inside an existing uptrend
    - mean_reversion: range pullback only
    """

    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg

    def _build_features(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["ema_fast"] = ema(out["close"], 21)
        out["ema_slow"] = ema(out["close"], 55)
        out["ema_200"] = ema(out["close"], 200)
        out["ema_5"] = ema(out["close"], 5)
        out["prev_ema_5"] = out["ema_5"].shift(1)
        out["ema_slow_slope"] = out["ema_slow"].pct_change(8)
        out["ema_200_slope"] = out["ema_200"].pct_change(8)
        out["rsi"] = rsi(out["close"], 14)
        out["rsi_3"] = rsi(out["close"], 3)
        out["atr"] = atr(out["high"], out["low"], out["close"], 14)
        out["atr_pct"] = out["atr"] / out["close"]
        out["adx"] = adx(out["high"], out["low"], out["close"], 14)
        out["macd_line"], out["macd_signal"], out["macd_hist"] = macd(out["close"])
        out["macd_hist_slope"] = out["macd_hist"].diff(3)
        out["vwap_20"] = rolling_vwap(
            out["high"], out["low"], out["close"], out["volume"], 20
        )
        out["vwap_50"] = rolling_vwap(
            out["high"], out["low"], out["close"], out["volume"], 50
        )
        out["mom_8"] = out["close"].pct_change(8)
        out["vol_z"] = zscore(out["volume"], 20)
        out["bb_mid"] = out["close"].rolling(20).mean()
        out["bb_std"] = out["close"].rolling(20).std()
        out["bb_upper"] = out["bb_mid"] + (out["bb_std"] * 2)
        out["bb_lower"] = out["bb_mid"] - (out["bb_std"] * 2)
        out["donchian_high_20"] = out["high"].rolling(20).max().shift(1)
        out["donchian_low_20"] = out["low"].rolling(20).min().shift(1)
        out["rolling_high_20"] = out["high"].rolling(20).max()
        out["candle_range_pct"] = (out["high"] - out["low"]) / out["close"]
        out["prev_close"] = out["close"].shift(1)
        out["prev_low"] = out["low"].shift(1)
        out["swing_low_5"] = out["low"].rolling(5).min()
        out["swing_low_20"] = out["low"].rolling(20).min()
        out["higher_low"] = out["swing_low_5"] > out["swing_low_20"].shift(5)
        out["green_candle"] = out["close"] > out["open"]
        out["close_location"] = (
            (out["close"] - out["low"]) / (out["high"] - out["low"]).replace(0, pd.NA)
        )
        return out

    def _macro_context(self, macro_df: pd.DataFrame | None) -> dict[str, Any]:
        if not self.cfg.use_btc_macro_filter:
            return {"enabled": False, "allow_long": True, "score": 1.0, "reason": "macro_disabled"}
        if macro_df is None or len(macro_df) < 80:
            return {
                "enabled": True,
                "allow_long": False,
                "score": 0.0,
                "reason": "macro_insufficient_data",
            }

        work = macro_df if "ema_200" in macro_df.columns else self._build_features(macro_df)
        work = work.dropna(
            subset=[
                "ema_slow",
                "ema_200",
                "ema_200_slope",
                "atr_pct",
                "macd_hist",
                "macd_hist_slope",
                "rolling_high_20",
            ]
        )
        if work.empty:
            return {
                "enabled": True,
                "allow_long": False,
                "score": 0.0,
                "reason": "macro_empty_features",
            }

        row = work.iloc[-1]
        close = float(row["close"])
        ema_slow = float(row["ema_slow"])
        ema_200 = float(row["ema_200"])
        rolling_high = float(row["rolling_high_20"])
        drawdown_pct = ((rolling_high - close) / rolling_high) if rolling_high > 0 else 0.0

        trend_ok = close > ema_200 and ema_slow > ema_200
        slope_ok = float(row["ema_200_slope"]) >= -0.002
        momentum_ok = float(row["macd_hist_slope"]) >= 0 or float(row["macd_hist"]) > 0
        volatility_ok = float(row["atr_pct"]) <= self.cfg.max_regime_atr_pct
        drawdown_ok = drawdown_pct <= self.cfg.max_macro_drawdown_pct

        score = 0.0
        score += 0.34 if trend_ok else 0.0
        score += 0.20 if slope_ok else 0.0
        score += 0.20 if momentum_ok else 0.0
        score += 0.14 if volatility_ok else 0.0
        score += 0.12 if drawdown_ok else 0.0
        allow_long = score >= 0.68 and trend_ok and volatility_ok and drawdown_ok
        reason = "macro_ok" if allow_long else "macro_block"
        return {
            "enabled": True,
            "allow_long": allow_long,
            "score": min(score, 1.0),
            "reason": reason,
        }

    def _entry_quality(
        self,
        fx: pd.Series,
        entry: Signal,
        regime: MarketRegime,
        macro: dict[str, Any],
    ) -> tuple[float, list[str]]:
        close = float(fx["close"])
        atr_value = float(fx["atr"]) if self._is_finite(fx["atr"]) else 0.0
        ema_fast = float(fx["ema_fast"])
        quality = 0.0
        blockers: list[str] = []

        regime_ok = not self.cfg.use_regime_filter or regime.allow_long
        macro_ok = bool(macro.get("allow_long", True))
        volume_ok = float(fx["vol_z"]) >= self.cfg.min_volume_z
        volatility_ok = 0.001 <= float(fx["atr_pct"]) <= self.cfg.max_regime_atr_pct
        rsi_ok = float(fx["rsi"]) <= self.cfg.max_entry_rsi
        chase_ok = atr_value > 0 and close <= ema_fast + (atr_value * self.cfg.max_chase_atr_mult)
        momentum_ok = float(fx["macd_hist_slope"]) >= 0 or float(fx["macd_hist"]) > 0
        price_context_ok = close >= float(fx["vwap_20"]) or entry.reason.startswith("mean_reversion")

        quality += 0.18 if regime_ok else 0.0
        quality += 0.18 * float(macro.get("score", 1.0))
        quality += 0.14 if volume_ok else 0.0
        quality += 0.12 if volatility_ok else 0.0
        quality += 0.10 if rsi_ok else 0.0
        quality += 0.10 if chase_ok else 0.0
        quality += 0.10 if momentum_ok else 0.0
        quality += 0.08 if price_context_ok else 0.0
        quality += 0.10 * entry.confidence

        if not regime_ok:
            blockers.append("regime")
        if not macro_ok:
            blockers.append(str(macro.get("reason", "macro")))
        if not volume_ok:
            blockers.append("volume")
        if not volatility_ok:
            blockers.append("volatility")
        if not rsi_ok:
            blockers.append("rsi")
        if not chase_ok:
            blockers.append("chasing")
        if not momentum_ok:
            blockers.append("momentum")
        if not price_context_ok:
            blockers.append("price_context")
        return min(quality, 1.0), blockers

    @staticmethod
    def _is_finite(value: object) -> bool:
        try:
            return pd.notna(float(value))
        except (TypeError, ValueError):
            return False

    def _trend_signal(self, fx: pd.Series, regime: MarketRegime) -> Signal:
        close = float(fx["close"])
        ema_fast = float(fx["ema_fast"])
        atr_value = float(fx["atr"]) if self._is_finite(fx["atr"]) else 0.0
        bullish_stack = fx["ema_fast"] > fx["ema_slow"] and close > fx["ema_fast"]
        momentum_ok = self.cfg.min_trend_strength <= fx["mom_8"] <= 0.08
        rsi_ok = 46 <= fx["rsi"] <= min(68, self.cfg.max_entry_rsi)
        volume_ok = fx["vol_z"] > self.cfg.min_volume_z
        volatility_ok = 0.0015 <= fx["atr_pct"] <= self.cfg.max_regime_atr_pct
        not_extended = atr_value > 0 and close <= ema_fast + (atr_value * self.cfg.max_chase_atr_mult)
        trend_quality_ok = fx["adx"] >= 18 and close >= fx["vwap_20"] >= fx["vwap_50"]
        pullback_quality_ok = atr_value > 0 and close <= ema_fast + (atr_value * 0.8)

        score = 0.0
        score += 0.24 if bullish_stack else 0.0
        score += 0.24 if momentum_ok else 0.0
        score += 0.16 if rsi_ok else 0.0
        score += 0.12 if volume_ok else 0.0
        score += 0.10 if volatility_ok else 0.0
        score += 0.06 if not_extended else 0.0
        score += 0.08 if trend_quality_ok else 0.0
        score += 0.06 if pullback_quality_ok else 0.0
        if regime.name == "bullish_trend":
            score += 0.12

        if (
            bullish_stack
            and momentum_ok
            and rsi_ok
            and volatility_ok
            and trend_quality_ok
            and pullback_quality_ok
        ):
            return Signal("buy", min(score, 1.0), "trend_following")
        return Signal("hold", min(score, 1.0), "trend_no_edge")

    def _breakout_signal(self, fx: pd.Series, regime: MarketRegime) -> Signal:
        if not self._is_finite(fx["donchian_high_20"]):
            return Signal("hold", 0.0, "breakout_insufficient_data")

        close = float(fx["close"])
        breakout_level = float(fx["donchian_high_20"])
        atr_value = float(fx["atr"]) if self._is_finite(fx["atr"]) else 0.0
        breakout_ok = close > breakout_level
        volume_ok = fx["vol_z"] > 0.15
        momentum_ok = fx["mom_8"] > self.cfg.min_trend_strength
        rsi_ok = 50 <= fx["rsi"] <= self.cfg.max_entry_rsi
        volatility_ok = 0.0015 <= fx["atr_pct"] <= self.cfg.max_regime_atr_pct
        candle_ok = fx["candle_range_pct"] <= self.cfg.max_regime_atr_pct * 0.8
        trend_quality_ok = fx["adx"] >= 18 and close >= fx["vwap_20"]
        reclaim_ok = fx["close_location"] >= 0.55
        breakout_buffer_ok = atr_value > 0 and close >= breakout_level + (atr_value * 0.12)
        not_chasing_ok = atr_value > 0 and close <= breakout_level + (atr_value * self.cfg.max_chase_atr_mult)
        prev_confirmation_ok = close > float(fx["prev_close"])

        score = 0.0
        score += 0.30 if breakout_ok else 0.0
        score += 0.18 if volume_ok else 0.0
        score += 0.16 if momentum_ok else 0.0
        score += 0.10 if rsi_ok else 0.0
        score += 0.08 if volatility_ok else 0.0
        score += 0.06 if candle_ok else 0.0
        score += 0.08 if trend_quality_ok else 0.0
        score += 0.04 if reclaim_ok else 0.0
        score += 0.06 if breakout_buffer_ok else 0.0
        score += 0.04 if not_chasing_ok else 0.0
        score += 0.04 if prev_confirmation_ok else 0.0
        if regime.name == "bullish_trend":
            score += 0.10

        if (
            breakout_ok
            and volume_ok
            and momentum_ok
            and volatility_ok
            and candle_ok
            and trend_quality_ok
            and reclaim_ok
            and breakout_buffer_ok
            and not_chasing_ok
            and prev_confirmation_ok
        ):
            return Signal("buy", min(score, 1.0), "breakout")
        return Signal("hold", min(score, 1.0), "breakout_no_edge")

    def _pullback_trend_signal(self, fx: pd.Series, regime: MarketRegime) -> Signal:
        close = float(fx["close"])
        atr_value = float(fx["atr"]) if self._is_finite(fx["atr"]) else 0.0
        if atr_value <= 0:
            return Signal("hold", 0.0, "pullback_insufficient_atr")

        trend_ok = (
            fx["ema_fast"] > fx["ema_slow"]
            and fx["ema_slow_slope"] > 0
            and close > fx["ema_slow"]
            and fx["adx"] >= 17
        )
        pullback_zone = (
            fx["low"] <= fx["ema_fast"] + (atr_value * 0.35)
            or fx["low"] <= fx["vwap_20"] + (atr_value * 0.35)
        )
        reclaim_ok = (
            close > fx["ema_fast"]
            and close >= fx["vwap_20"]
            and fx["green_candle"]
            and fx["close_location"] >= 0.55
        )
        rsi_ok = 40 <= fx["rsi"] <= min(64, self.cfg.max_entry_rsi)
        structure_ok = bool(fx["higher_low"]) or close > float(fx["prev_close"])
        volatility_ok = 0.0015 <= fx["atr_pct"] <= self.cfg.max_regime_atr_pct * 0.85
        not_extended = close <= float(fx["ema_fast"]) + (atr_value * 1.25)
        volume_ok = fx["vol_z"] > self.cfg.min_volume_z
        pullback_depth_ok = close >= float(fx["ema_slow"])

        score = 0.0
        score += 0.24 if trend_ok else 0.0
        score += 0.20 if pullback_zone else 0.0
        score += 0.18 if reclaim_ok else 0.0
        score += 0.12 if rsi_ok else 0.0
        score += 0.10 if structure_ok else 0.0
        score += 0.08 if volatility_ok else 0.0
        score += 0.08 if not_extended else 0.0
        score += 0.06 if volume_ok else 0.0
        score += 0.06 if pullback_depth_ok else 0.0
        if regime.name == "bullish_trend":
            score += 0.10

        if (
            trend_ok
            and pullback_zone
            and reclaim_ok
            and rsi_ok
            and structure_ok
            and volatility_ok
            and not_extended
            and volume_ok
            and pullback_depth_ok
        ):
            return Signal("buy", min(score, 1.0), "pullback_trend")
        return Signal("hold", min(score, 1.0), "pullback_no_edge")

    def _mean_reversion_signal(self, fx: pd.Series, regime: MarketRegime) -> Signal:
        if not self._is_finite(fx["bb_lower"]) or not self._is_finite(fx["bb_mid"]):
            return Signal("hold", 0.0, "mean_reversion_insufficient_data")

        close = float(fx["close"])
        lower_band = float(fx["bb_lower"])
        ema_slow = float(fx["ema_slow"])
        oversold = close <= lower_band or fx["rsi"] < 36
        range_ok = regime.name == "range" or not self.cfg.use_regime_filter
        volatility_ok = 0.001 <= fx["atr_pct"] <= (self.cfg.max_regime_atr_pct * 0.65)
        no_breakdown = close >= ema_slow * 0.97 and close >= float(fx["donchian_low_20"]) * 0.995
        volume_ok = fx["vol_z"] > -1.0
        range_quality_ok = fx["adx"] <= 24
        reclaim_ok = fx["close_location"] >= 0.45 and close >= float(fx["prev_close"]) * 0.997

        score = 0.0
        score += 0.28 if oversold else 0.0
        score += 0.18 if range_ok else 0.0
        score += 0.16 if volatility_ok else 0.0
        score += 0.14 if no_breakdown else 0.0
        score += 0.08 if volume_ok else 0.0
        score += 0.10 if range_quality_ok else 0.0
        score += 0.06 if reclaim_ok else 0.0

        if (
            oversold
            and range_ok
            and volatility_ok
            and no_breakdown
            and volume_ok
            and range_quality_ok
            and reclaim_ok
        ):
            return Signal("buy", min(score, 1.0), "mean_reversion_range")
        return Signal("hold", min(score, 1.0), "mean_reversion_no_edge")

    def _turtle_breakout_signal(self, fx: pd.Series, regime: MarketRegime) -> Signal:
        """Richard Dennis (Turtle Breakout): Ruptura de Canal Donchian con fuerza (ADX)."""
        if not self._is_finite(fx["donchian_high_20"]):
            return Signal("hold", 0.0, "turtle_insufficient_data")

        close = float(fx["close"])
        breakout_level = float(fx["donchian_high_20"])
        breakout_ok = close > breakout_level
        
        adx_ok = fx["adx"] >= 18
        trend_aligned = fx["ema_fast"] > fx["ema_slow"]
        volume_ok = fx["vol_z"] > 0.1
        
        score = 0.0
        score += 0.40 if breakout_ok else 0.0
        score += 0.25 if adx_ok else 0.0
        score += 0.20 if trend_aligned else 0.0
        score += 0.15 if volume_ok else 0.0
        if regime.name == "bullish_trend":
            score += 0.10

        if breakout_ok and adx_ok and trend_aligned:
            return Signal("buy", min(score, 1.0), "turtle_breakout")
        return Signal("hold", min(score, 1.0), "turtle_no_edge")

    def _connors_rsi_signal(self, fx: pd.Series, regime: MarketRegime) -> Signal:
        """Larry Connors (RSI Pullback): Compras rápidas en retroceso en tendencia alcista estructural."""
        if not self._is_finite(fx["rsi_3"]) or not self._is_finite(fx["ema_200"]):
            return Signal("hold", 0.0, "connors_insufficient_data")

        close = float(fx["close"])
        ema_200 = float(fx["ema_200"])
        
        uptrend = close > ema_200
        oversold = fx["rsi_3"] < 20.0
        rsi_ok = fx["rsi"] < 65.0
        
        score = 0.0
        score += 0.45 if oversold else 0.0
        score += 0.25 if uptrend else 0.0
        score += 0.15 if rsi_ok else 0.0
        score += 0.15 if fx["green_candle"] else 0.0

        if oversold and uptrend and rsi_ok:
            return Signal("buy", min(score, 1.0), "connors_rsi")
        return Signal("hold", min(score, 1.0), "connors_no_edge")

    def _elder_triple_signal(self, fx: pd.Series, regime: MarketRegime) -> Signal:
        """Alexander Elder (Triple Screen): Tendencia macro, oscilador intermedio y confirmación de precio."""
        if not self._is_finite(fx["macd_hist"]) or not self._is_finite(fx["ema_slow_slope"]):
            return Signal("hold", 0.0, "elder_insufficient_data")

        close = float(fx["close"])
        tide_ok = fx["ema_slow_slope"] > -0.0005 and close > fx["ema_200"]
        wave_ok = fx["rsi"] < 50 or fx["rsi_3"] < 32
        trigger_ok = close > float(fx["prev_close"]) and fx["macd_hist_slope"] > -0.0001
        volume_ok = fx["vol_z"] >= self.cfg.min_volume_z

        score = 0.0
        score += 0.35 if tide_ok else 0.0
        score += 0.35 if wave_ok else 0.0
        score += 0.20 if trigger_ok else 0.0
        score += 0.10 if volume_ok else 0.0
        if regime.name == "bullish_trend":
            score += 0.10

        if tide_ok and wave_ok and trigger_ok:
            return Signal("buy", min(score, 1.0), "elder_triple")
        return Signal("hold", min(score, 1.0), "elder_no_edge")

    def _williams_alligator_signal(self, fx: pd.Series, regime: MarketRegime) -> Signal:
        """Bill Williams (Alligator): Expansión de medias móviles suaves con volumen."""
        if not self._is_finite(fx["ema_5"]) or not self._is_finite(fx["ema_slow"]):
            return Signal("hold", 0.0, "williams_insufficient_data")

        close = float(fx["close"])
        lips = float(fx["ema_5"])
        teeth = float(fx["ema_fast"])
        jaw = float(fx["ema_slow"])
        
        mouth_open = lips > teeth and teeth > jaw
        pullback_ok = float(fx["low"]) <= teeth and close > teeth
        volume_ok = fx["vol_z"] >= 0.1
        
        score = 0.0
        score += 0.40 if mouth_open else 0.0
        score += 0.40 if pullback_ok else 0.0
        score += 0.20 if volume_ok else 0.0
        if regime.name == "bullish_trend":
            score += 0.10

        if mouth_open and pullback_ok and volume_ok:
            return Signal("buy", min(score, 1.0), "williams_alligator")
        return Signal("hold", min(score, 1.0), "williams_no_edge")

    def _entry_signal(self, fx: pd.Series, regime: MarketRegime) -> Signal:
        mode = self.cfg.strategy_mode
        if mode == "trend" or mode == "elder_triple":
            return self._elder_triple_signal(fx, regime)
        if mode == "breakout" or mode == "turtle_breakout":
            return self._turtle_breakout_signal(fx, regime)
        if mode == "pullback_trend" or mode == "connors_rsi":
            return self._connors_rsi_signal(fx, regime)
        if mode == "mean_reversion":
            return self._mean_reversion_signal(fx, regime)
        if mode == "williams_alligator":
            return self._williams_alligator_signal(fx, regime)

        # En modo auto, priorizamos pullbacks y reversión a la media
        if regime.name == "bullish_trend":
            candidates = [
                self._elder_triple_signal(fx, regime),
                self._connors_rsi_signal(fx, regime)
            ]
        elif regime.name == "range":
            candidates = [
                self._mean_reversion_signal(fx, regime),
                self._connors_rsi_signal(fx, regime)
            ]
        else:
            candidates = [self._elder_triple_signal(fx, regime)]

        return max(candidates, key=lambda signal: signal.confidence)

    def generate_from_features(
        self,
        fx: pd.Series,
        regime: MarketRegime,
        macro_context: dict[str, Any] | None = None,
        in_position: bool = False,
        entry_reason: str | None = None,
    ) -> Signal:
        hostile_regime_exit = self.cfg.use_regime_filter and regime.name in {
            "bearish_trend",
            "high_volatility",
        }

        if hostile_regime_exit:
            return Signal("exit", 0.75, f"regime_exit:{regime.reason}")
            
        if in_position:
            # 1. Salidas rápidas para estrategias de reversión / pullback rápido (Connors, Mean Reversion)
            is_pullback = entry_reason and ("connors_rsi" in entry_reason or "mean_reversion" in entry_reason)
            if is_pullback:
                pullback_exit = fx["rsi_3"] > 68 or fx["close"] >= fx["bb_mid"] or fx["rsi"] > 65
                if pullback_exit:
                    return Signal("exit", 0.6, "pullback_target_exit")
            else:
                # 2. Salidas para tendencias
                macro_flat = fx["close"] < fx["ema_200"]
                overbought_exit = fx["rsi"] > 78
                if macro_flat or overbought_exit:
                    return Signal("exit", 0.5, "trend_or_momentum_exit")
                
            return Signal("hold", 0.5, f"position_hold:{regime.name}")

        entry = self._entry_signal(fx, regime)
        regime_multiplier = 1.0
        if self.cfg.use_regime_filter:
            if entry.reason.startswith("mean_reversion") and regime.name == "range":
                regime_multiplier = 1.0
            else:
                regime_multiplier = regime.risk_multiplier
        confidence = min(entry.confidence * regime_multiplier, 1.0)

        if entry.action == "buy" and confidence >= self.cfg.min_confidence:
            macro = macro_context or {
                "enabled": False,
                "allow_long": True,
                "score": 1.0,
                "reason": "macro_unavailable",
            }
            quality, blockers = self._entry_quality(fx, entry, regime, macro)
            if quality < self.cfg.min_entry_quality:
                reason = ",".join(blockers) if blockers else "quality"
                return Signal("hold", quality, f"quality_block:{reason}:{regime.name}")
            if self.cfg.use_regime_filter and not regime.allow_long:
                return Signal("hold", confidence, f"regime_block:{regime.reason}")
            if macro.get("enabled") and not macro.get("allow_long", False):
                return Signal("hold", confidence, f"macro_block:{macro.get('reason')}:{regime.name}")
            return Signal("buy", max(confidence, quality), f"{entry.reason}:q{quality:.2f}:{regime.name}")

        return Signal("hold", confidence, f"{entry.reason}:{regime.name}")

    def generate(
        self,
        df: pd.DataFrame,
        higher_df: pd.DataFrame | None = None,
        macro_df: pd.DataFrame | None = None,
        in_position: bool = False,
        features_ready: bool = False,
        entry_reason: str | None = None,
    ) -> Signal:
        if len(df) < 80:
            return Signal("hold", 0.0, "insufficient_data")

        features = df if features_ready else self._build_features(df)
        features = features.dropna(
            subset=[
                "ema_fast",
                "ema_slow",
                "rsi",
                "atr",
                "adx",
                "vwap_20",
                "donchian_high_20",
                "donchian_low_20",
                "prev_close",
                "close_location",
            ]
        )
        if features.empty:
            return Signal("hold", 0.0, "insufficient_features")

        fx = features.iloc[-1]
        regime_df = higher_df if higher_df is not None and len(higher_df) >= 80 else df
        regime = classify_market(regime_df, self.cfg)
        macro_context = self._macro_context(macro_df)
        return self.generate_from_features(
            fx,
            regime,
            macro_context=macro_context,
            in_position=in_position,
            entry_reason=entry_reason,
        )
