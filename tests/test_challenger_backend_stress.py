"""
Adversarial Challenger 2 Stress Test Suite for Backend & Quantitative Engine.
Location: tests/test_challenger_backend_stress.py

Focuses strictly on:
1. yfinance invalid tickers, caching, stale fallbacks, concurrency, and UTC schema adherence.
2. IBKR port probing with all ports closed, verifying clean non-blocking transition to STANDBY_YFINANCE.
3. Quant Brain boundary and extreme inputs: ATR spike ratio > 3.0x veto, negative/extreme RSI, neutral sentiment, composite score 0.72 threshold.
4. Consolidated -6.4% circuit breaker breach across dual brokers (Binance + IBKR), testing dual order cancellation and state persistence.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from bot.config import BotConfig
from bot.ibkr_client import IBKRClient, PAPER_PORTS, LIVE_PORTS, probe_ibkr_port
from bot.quant_engine import (
    COMPOSITE_THRESHOLD,
    FactorBreakdown,
    QuantEngine,
    calculate_composite_score,
)
from bot.risk import (
    CircuitBreakerStatus,
    RiskManager,
    evaluate_drawdown_lock,
    trigger_dual_broker_cancellation,
)
from bot.yfinance_engine import QUANT_COLUMNS, YFinanceDataEngine


class TestYFinanceAdversarialStress(unittest.TestCase):
    """Adversarial stress tests for YFinanceDataEngine."""

    def setUp(self):
        self.engine = YFinanceDataEngine(ttl_seconds=5.0, max_cache_size=10, quote_ttl_seconds=2.0)

    def test_invalid_and_pathological_tickers_raise_clean_runtime_error(self):
        """Tests that invalid, empty, or garbage tickers raise clean RuntimeError, never unhandled crash."""
        pathological_tickers = [
            "INVALID_TICKER_XYZ_999",
            "NONEXISTENT_STOCK_12345",
            "",
            "   ",
            "$$$CORRUPT###",
        ]

        for ticker in pathological_tickers:
            with patch("yfinance.Ticker") as mock_ticker_cls:
                mock_inst = mock_ticker_cls.return_value
                # yfinance returns empty DataFrame for invalid ticker
                mock_inst.history.return_value = pd.DataFrame()

                with self.assertRaises(RuntimeError) as ctx:
                    self.engine.get_klines(ticker, interval="15m")
                self.assertIn("No kline data returned by yfinance", str(ctx.exception))

    def test_network_blackout_and_rate_limit_stale_cache_fallback(self):
        """Tests stale cache fallback under simulated HTTP 429 throttling and network exceptions."""
        valid_df = pd.DataFrame(
            {
                "Open": [100.0, 101.0],
                "High": [102.0, 103.0],
                "Low": [99.0, 100.0],
                "Close": [101.5, 102.5],
                "Volume": [1000.0, 1500.0],
            },
            index=pd.date_range("2026-09-21 14:00:00", periods=2, freq="15min", tz="UTC"),
        )
        valid_df.index.name = "Datetime"

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_inst = mock_ticker_cls.return_value
            mock_inst.history.return_value = valid_df

            # Initial warm up: prime cache
            df_initial = self.engine.get_klines("NVDA", interval="15m", limit=10)
            self.assertEqual(len(df_initial), 2)
            self.assertEqual(mock_ticker_cls.call_count, 1)

            # Age cache beyond TTL
            cache_key = list(self.engine._kline_cache.keys())[0]
            self.engine._kline_cache[cache_key].timestamp -= 100.0

            # Simulate network failure on subsequent fetch (HTTP 429 / ConnectionReset)
            mock_inst.history.side_effect = ConnectionError("HTTP 429: Too Many Requests")

            # Must NOT raise exception; must cleanly return stale cache copy
            df_stale = self.engine.get_klines("NVDA", interval="15m", limit=10)
            self.assertEqual(len(df_stale), 2)
            self.assertEqual(df_stale["close"].iloc[-1], 102.5)

    def test_column_schema_and_strict_utc_timezone(self):
        """Tests that normalized output strictly adheres to [open_time, open, high, low, close, volume, close_time] in UTC."""
        # Non-UTC eastern time input
        eastern_dates = pd.date_range(
            "2026-09-21 09:30:00", periods=4, freq="15min", tz="America/New_York"
        )
        raw_df = pd.DataFrame(
            {
                "Open": [150.0, 151.0, 152.0, 153.0],
                "High": [152.0, 153.0, 154.0, 155.0],
                "Low": [149.0, 150.0, 151.0, 152.0],
                "Close": [151.0, 152.0, 153.0, 154.0],
                "Volume": [10000, 12000, 11000, 13000],
            },
            index=eastern_dates,
        )
        raw_df.index.name = "Datetime"

        norm = self.engine.normalize_dataframe(raw_df, interval="15m", limit=10)

        # 1. Exact column list and order
        self.assertEqual(list(norm.columns), QUANT_COLUMNS)

        # 2. Strict UTC verification
        self.assertTrue(hasattr(norm["open_time"].dt, "tz"))
        self.assertEqual(str(norm["open_time"].dt.tz), "UTC")
        self.assertEqual(str(norm["close_time"].dt.tz), "UTC")

        # 3. 09:30 EDT = 13:30 UTC
        self.assertEqual(norm["open_time"].iloc[0].hour, 13)
        self.assertEqual(norm["open_time"].iloc[0].minute, 30)

        # 4. close_time delta is 15min - 1s = 899s
        delta = (norm["close_time"].iloc[0] - norm["open_time"].iloc[0]).total_seconds()
        self.assertEqual(delta, 899)

    def test_cache_lru_eviction_under_pressure(self):
        """Tests that cache stays within max_cache_size and evicts oldest entries cleanly."""
        max_size = 5
        engine = YFinanceDataEngine(ttl_seconds=60.0, max_cache_size=max_size)

        dummy_df = pd.DataFrame(
            {"Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.5], "Volume": [100]},
            index=pd.date_range("2026-09-21 12:00:00", periods=1, tz="UTC"),
        )
        dummy_df.index.name = "Date"

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_inst = mock_ticker_cls.return_value
            mock_inst.history.return_value = dummy_df

            # Populate with 10 different tickers
            for i in range(10):
                sym = f"TICKER_{i}"
                engine.get_klines(sym, interval="15m")

            stats = engine.cache_stats()
            self.assertLessEqual(stats["klines_cached"], max_size)

    def test_multi_threaded_concurrency_stress(self):
        """Stress-tests thread safety under concurrent queries to same and different tickers."""
        dummy_df = pd.DataFrame(
            {"Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.5], "Volume": [100]},
            index=pd.date_range("2026-09-21 12:00:00", periods=1, tz="UTC"),
        )
        dummy_df.index.name = "Date"

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_inst = mock_ticker_cls.return_value
            mock_inst.history.return_value = dummy_df

            def query(ticker):
                return self.engine.get_klines(ticker, interval="15m")

            tickers = ["NVDA", "AAPL", "MSFT", "AMZN", "SPY", "QQQ"] * 10
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(query, tickers))

            self.assertEqual(len(results), 60)
            for res in results:
                self.assertEqual(list(res.columns), QUANT_COLUMNS)


class TestIBKRPortProbingAndStandbyStress(unittest.TestCase):
    """Adversarial stress tests for IBKRClient port probing and STANDBY_YFINANCE mode."""

    def setUp(self):
        self.cfg_paper = BotConfig(
            ibkr_enabled=True,
            ibkr_host="127.0.0.1",
            ibkr_port=4002,
            ibkr_allow_real_trading=False,
            ibkr_standby_cash=15000.0,
        )
        self.cfg_live = BotConfig(
            ibkr_enabled=True,
            ibkr_host="127.0.0.1",
            ibkr_port=7496,
            ibkr_allow_real_trading=True,
        )

    def test_security_policy_enforces_paper_ports_when_real_trading_disabled(self):
        """Verifies that live ports (7496, 4001) are NEVER included when ibkr_allow_real_trading is False."""
        client = IBKRClient(self.cfg_paper)
        candidates = client.get_candidate_ports()

        for live_p in LIVE_PORTS:
            self.assertNotIn(
                live_p,
                candidates,
                f"Security violation: Live trading port {live_p} must not be probed when real trading is disabled",
            )

    def test_all_ports_closed_enters_standby_yfinance_deterministically(self):
        """Verifies that when all ports are closed, client enters STANDBY_YFINANCE without hanging."""
        with patch("bot.ibkr_client.probe_ibkr_port", return_value=None) as mock_probe:
            client = IBKRClient(self.cfg_paper)
            start_t = time.perf_counter()
            client.connect()
            elapsed = time.perf_counter() - start_t

            # Verify fast non-blocking execution (< 1.0s)
            self.assertLess(elapsed, 1.0, "Port probe when all closed must be fast and non-blocking")

            # Verify fiduciary standby invariants
            self.assertEqual(client.status, "STANDBY_YFINANCE")
            self.assertFalse(client.is_connected)
            self.assertEqual(client.active_provider, "yfinance")
            self.assertIsNone(client.connected_port)

    def test_standby_mode_account_summary_returns_fiduciary_defaults(self):
        """Verifies get_account_summary, account_cash_usd, net_liquidation, and buying_power in standby."""
        with patch("bot.ibkr_client.probe_ibkr_port", return_value=None):
            client = IBKRClient(self.cfg_paper)
            client.connect()

            summary = client.get_account_summary()
            self.assertEqual(summary["net_liquidation"], 15000.0)
            self.assertEqual(summary["total_cash"], 15000.0)
            self.assertEqual(summary["buying_power"], 30000.0)  # 2x cash

    def test_standby_mode_serves_bars_via_yfinance_without_exceptions(self):
        """Verifies that get_bars and get_klines route directly to yfinance in standby mode."""
        dummy_df = pd.DataFrame(
            {
                "open_time": pd.date_range("2026-09-21 15:00:00", periods=3, tz="UTC"),
                "open": [100.0, 101.0, 102.0],
                "high": [102.0, 103.0, 104.0],
                "low": [99.0, 100.0, 101.0],
                "close": [101.0, 102.0, 103.0],
                "volume": [500.0, 600.0, 700.0],
                "close_time": pd.date_range("2026-09-21 15:14:59", periods=3, tz="UTC"),
            }
        )

        mock_yf = MagicMock()
        mock_yf.get_klines.return_value = dummy_df

        with patch("bot.ibkr_client.probe_ibkr_port", return_value=None):
            client = IBKRClient(self.cfg_paper, yfinance_engine=mock_yf)
            client.connect()

            bars = client.get_bars("SPY", interval="15m")
            self.assertEqual(len(bars), 3)
            mock_yf.get_klines.assert_called_once_with("SPY", interval="15m", limit=500)

    def test_standby_mode_simulates_bracket_orders_cleanly(self):
        """Verifies simulated order generation when desktop application is offline."""
        with patch("bot.ibkr_client.probe_ibkr_port", return_value=None):
            client = IBKRClient(self.cfg_paper)
            client.connect()

            order_res = client.buy_bracket(
                symbol="NVDA", qty=10, entry_ref=120.0, take_profit=125.0, stop_loss=116.0
            )

            self.assertTrue(order_res["ok"])
            self.assertEqual(order_res["status"], "standby_simulated")
            self.assertEqual(order_res["provider"], "yfinance")
            self.assertTrue(order_res["order_id"].startswith("STANDBY_NVDA_"))
            self.assertEqual(order_res["qty"], 10)


class TestQuantBrainExtremeBoundaryStress(unittest.TestCase):
    """Adversarial stress tests for QuantEngine boundary inputs and composite scores."""

    def _make_bars(self, n=30, base=100.0, tr_mult=1.0):
        dates = pd.date_range("2026-09-21 00:00:00", periods=n, freq="15min", tz="UTC")
        prices = np.full(n, base)
        return pd.DataFrame(
            {
                "open_time": dates,
                "open": prices,
                "high": prices + tr_mult,
                "low": prices - tr_mult,
                "close": prices,
                "volume": np.full(n, 1000.0),
                "close_time": dates + pd.Timedelta(minutes=15),
            }
        )

    def test_atr_spike_ratio_vetoes_buy_authorization(self):
        """Stress-tests ATR flash spike ratio at boundary: ratio > 3.0x vetoes entry (score = 0.0)."""
        df = self._make_bars(30, base=100.0, tr_mult=1.0)
        # Normal baseline: ratio = 1.0 <= 3.0 -> no veto
        s_norm, d_norm = QuantEngine.compute_volatility_score(df, is_crypto=False)
        self.assertNotIn("veto", d_norm)
        self.assertGreater(s_norm, 0.0)

        # Inject flash spike in the last candle: high jump resulting in ratio > 3.0
        df_spike = df.copy()
        df_spike.iloc[-1, df_spike.columns.get_loc("high")] = 180.0
        df_spike.iloc[-1, df_spike.columns.get_loc("low")] = 80.0

        s_spike, d_spike = QuantEngine.compute_volatility_score(df_spike, is_crypto=False)
        self.assertEqual(s_spike, 0.0)
        self.assertEqual(d_spike.get("veto"), "extreme_volatility_spike")
        self.assertGreater(d_spike.get("ratio", 0.0), 3.0)

        # Comprehensive evaluate_setup must reject buy authorization under volatility spike
        setup = QuantEngine.evaluate_setup("NVDA", df_spike)
        self.assertFalse(
            setup.is_buy_authorized,
            "Volatility flash spike ratio > 3.0x must veto buy authorization regardless of score",
        )

    def test_composite_score_threshold_boundary_72_percent(self):
        """Tests exact threshold behavior around S_composite >= 0.72."""
        # Case 1: Exactly 0.7200 -> Authorized
        score_7200 = calculate_composite_score(trend=0.72, mom=0.72, vol=0.72, ml=0.72, sent=0.72)
        self.assertAlmostEqual(score_7200, 0.7200, places=4)
        self.assertTrue(score_7200 >= COMPOSITE_THRESHOLD)

        # Case 2: 0.7199 -> Rejected
        score_7199 = calculate_composite_score(trend=0.7199, mom=0.7199, vol=0.7199, ml=0.7199, sent=0.7199)
        self.assertAlmostEqual(score_7199, 0.7199, places=4)
        self.assertFalse(score_7199 >= COMPOSITE_THRESHOLD)

        # Case 3: 0.7201 -> Authorized
        score_7201 = calculate_composite_score(trend=0.7201, mom=0.7201, vol=0.7201, ml=0.7201, sent=0.7201)
        self.assertAlmostEqual(score_7201, 0.7201, places=4)
        self.assertTrue(score_7201 >= COMPOSITE_THRESHOLD)

    def test_negative_rsi_and_extreme_momentum_inputs(self):
        """Stress-tests compute_momentum_score when indicators return negative, NaN or out-of-band values."""
        df = self._make_bars(25, base=100.0)

        # Mock rsi to return negative value (pathological edge case)
        with patch("bot.quant_engine.rsi", return_value=pd.Series([-15.0] * 25)):
            s_mom, d_mom = QuantEngine.compute_momentum_score(df)
            self.assertTrue(0.0 <= s_mom <= 1.0)
            self.assertEqual(d_mom["rsi"], -15.0)

        # Mock rsi to return extreme overbought (98.0)
        with patch("bot.quant_engine.rsi", return_value=pd.Series([98.0] * 25)):
            s_mom_high, d_mom_high = QuantEngine.compute_momentum_score(df)
            self.assertTrue(0.0 <= s_mom_high <= 1.0)
            # RSI outside sweet spot [48, 68] or buffer [40, 75] gives 0.0 points for RSI factor
            self.assertEqual(d_mom_high["rsi"], 98.0)

    def test_sentiment_score_normalization_boundaries(self):
        """Tests FinBERT polarity mapping [-1.0, 1.0] -> score [0.0, 1.0] and neutral 0.50 fallback."""
        # 1. Neutral (polarity = 0.0) -> score = 0.50
        mock_news_neutral = MagicMock()
        mock_news_neutral.get_sentiment.return_value = (0.0, {})
        s_neut, _ = QuantEngine.compute_sentiment_score("AAPL", mock_news_neutral)
        self.assertAlmostEqual(s_neut, 0.50, places=4)

        # 2. Maximum bullish (polarity = +1.0) -> score = 1.00
        mock_news_bull = MagicMock()
        mock_news_bull.get_sentiment.return_value = (1.0, {})
        s_bull, _ = QuantEngine.compute_sentiment_score("AAPL", mock_news_bull)
        self.assertAlmostEqual(s_bull, 1.00, places=4)

        # 3. Maximum bearish (polarity = -1.0) -> score = 0.00
        mock_news_bear = MagicMock()
        mock_news_bear.get_sentiment.return_value = (-1.0, {})
        s_bear, _ = QuantEngine.compute_sentiment_score("AAPL", mock_news_bear)
        self.assertAlmostEqual(s_bear, 0.00, places=4)

        # 4. No analyzer (None) -> default neutral 0.50
        s_none, d_none = QuantEngine.compute_sentiment_score("AAPL", None)
        self.assertAlmostEqual(s_none, 0.50, places=4)
        self.assertEqual(d_none["status"], "default_neutral")

    def test_pathological_candlestick_inputs_zero_volume_and_inverted_prices(self):
        """Tests zero volume, inverted prices (high < low), and short dataframes."""
        # 1. Short DataFrame (< 20 bars) -> returns neutral 0.50 without crash
        short_df = self._make_bars(10)
        s_short_trend, _ = QuantEngine.compute_trend_score(short_df)
        self.assertEqual(s_short_trend, 0.50)

        # 2. Zero volume DataFrame
        df_zero_vol = self._make_bars(25)
        df_zero_vol["volume"] = 0.0
        s_mom_zv, d_mom_zv = QuantEngine.compute_momentum_score(df_zero_vol)
        self.assertTrue(0.0 <= s_mom_zv <= 1.0)

        # 3. Inverted prices (high < low)
        df_inv = self._make_bars(25)
        df_inv["high"] = 90.0
        df_inv["low"] = 110.0
        s_vol_inv, _ = QuantEngine.compute_volatility_score(df_inv)
        self.assertTrue(0.0 <= s_vol_inv <= 1.0)


class TestConsolidatedCircuitBreakerStress(unittest.TestCase):
    """Adversarial stress tests for Consolidated -6.4% Drawdown Circuit Breaker."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_events.db")
        self.state_file = os.path.join(self.temp_dir, "global_state.json")
        self.cfg = BotConfig(event_db_path=self.db_path)
        self.risk = RiskManager(self.cfg, shared_state_path=self.state_file)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_consolidated_drawdown_exact_boundary(self):
        """Tests exact boundary around -6.4% consolidated drawdown limit."""
        baseline = 100000.0  # Combined starting equity

        # Drawdown = -6.39% -> Combined = 93610.0 -> ALLOWED
        allowed_639, reason_639 = self.risk.check_consolidated_circuit_breaker(
            binance_equity=50000.0, ibkr_equity=43610.0, baseline_equity=baseline
        )
        self.assertTrue(allowed_639)
        self.assertEqual(reason_639, "ok")
        self.assertEqual(self.risk.state.circuit_breaker_status, CircuitBreakerStatus.NORMAL)

        # Drawdown = -6.40% -> Combined = 93600.0 -> LOCKED
        allowed_640, reason_640 = self.risk.check_consolidated_circuit_breaker(
            binance_equity=50000.0, ibkr_equity=43600.0, baseline_equity=baseline
        )
        self.assertFalse(allowed_640)
        self.assertIn("fiduciary_drawdown_limit_breached", reason_640)
        self.assertEqual(self.risk.state.circuit_breaker_status, CircuitBreakerStatus.LOCKED_DEFENSIVE)
        self.assertTrue(self.risk.state.circuit_breaker_paused)

        # Drawdown = -6.41% -> Combined = 93590.0 -> LOCKED
        self.risk.reset_circuit_breaker()
        allowed_641, reason_641 = self.risk.check_consolidated_circuit_breaker(
            binance_equity=45000.0, ibkr_equity=48590.0, baseline_equity=baseline
        )
        self.assertFalse(allowed_641)
        self.assertIn("fiduciary_drawdown_limit_breached", reason_641)
        self.assertEqual(self.risk.state.circuit_breaker_status, CircuitBreakerStatus.LOCKED_DEFENSIVE)

    def test_asymmetric_cross_broker_drawdown_breach(self):
        """Verifies breach detection when one venue gains but the other suffers a larger loss exceeding -6.4% net."""
        baseline = 50000.0  # e.g. 25k Binance + 25k IBKR

        # Binance gains +$2,000 (27k), IBKR collapses -$5,300 (19.7k)
        # Net equity = 46,700 -> Drawdown = (46700 - 50000) / 50000 = -6.60% (<= -6.4%)
        allowed, reason = self.risk.check_consolidated_circuit_breaker(
            binance_equity=27000.0, ibkr_equity=19700.0, baseline_equity=baseline
        )
        self.assertFalse(allowed)
        self.assertIn("fiduciary_drawdown_limit_breached", reason)
        self.assertEqual(self.risk.state.circuit_breaker_status, CircuitBreakerStatus.LOCKED_DEFENSIVE)

    def test_dual_broker_cancellation_triggers_and_protects_stop_loss(self):
        """Verifies that circuit breaker breach triggers dual broker cancellation while preserving protective SL orders."""
        mock_binance = MagicMock()
        mock_ibkr = MagicMock()

        # Binance has 3 orders:
        # 1. LIMIT BUY (must be cancelled)
        # 2. MARKET BUY (must be cancelled)
        # 3. STOP_LOSS_LIMIT (MUST BE PRESERVED)
        mock_binance.get_open_orders.return_value = [
            {"orderId": 5001, "type": "LIMIT", "side": "BUY"},
            {"orderId": 5002, "type": "MARKET", "side": "BUY"},
            {"orderId": 5003, "type": "STOP_LOSS_LIMIT", "side": "SELL"},
        ]

        # Attach mock clients to risk manager
        self.risk.binance_client = mock_binance
        self.risk.ibkr_client = mock_ibkr

        # Trigger -6.41% breach
        allowed, _ = self.risk.check_consolidated_circuit_breaker(
            binance_equity=40000.0, ibkr_equity=53590.0, baseline_equity=100000.0
        )
        self.assertFalse(allowed)

        # 1. Verify Binance orders 5001 and 5002 cancelled, but 5003 preserved
        mock_binance.cancel_order.assert_any_call("BTCUSDT", order_id=5001)
        mock_binance.cancel_order.assert_any_call("BTCUSDT", order_id=5002)

        # Check call args to ensure 5003 was NEVER cancelled
        cancelled_ids = [c[1].get("order_id") for c in mock_binance.cancel_order.call_args_list]
        self.assertNotIn(
            5003, cancelled_ids, "Fiduciary violation: Protective STOP_LOSS order must not be cancelled"
        )

        # 2. Verify IBKR global cancel invoked
        mock_ibkr.ib.reqGlobalCancel.assert_called_once()

    def test_shared_state_and_sqlite_persistence_on_consolidated_lock(self):
        """Verifies that consolidated breach persists to shared JSON file and SQLite bot_state table."""
        self.risk.check_consolidated_circuit_breaker(
            binance_equity=10000.0, ibkr_equity=8600.0, baseline_equity=20000.0
        )

        # Check JSON shared state file
        self.assertTrue(os.path.exists(self.state_file))
        with open(self.state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertTrue(data.get("paused"))
        self.assertEqual(data.get("status"), CircuitBreakerStatus.LOCKED_DEFENSIVE.value)

        # Check SQLite database
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT value_json FROM bot_state WHERE key='circuit_breaker:global'")
            row = cur.fetchone()
            self.assertIsNotNone(row, "SQLite bot_state must contain circuit_breaker:global record")
            val = json.loads(row[0])
            self.assertEqual(val["status"], CircuitBreakerStatus.LOCKED_DEFENSIVE.value)
            self.assertTrue(val["paused"])


if __name__ == "__main__":
    unittest.main()
