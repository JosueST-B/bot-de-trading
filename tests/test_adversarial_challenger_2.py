"""
Adversarial Challenger 2 Empirical Stress Test Suite.
File: tests/test_adversarial_challenger_2.py

Empirically challenges:
1. -6.4% Fiduciary Drawdown Lock under rapid equity loss and boundary values (-6.39% vs -6.401%).
2. Pre-trade slippage guard against thin and pathological order books.
3. Volatility spike rejection under ATR expansion regimes and regime adaptation.
4. Startup state reconciler under simulated crash states (unhedged exchange positions without local DB record).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from bot.config import BotConfig
from bot.db import (
    Base,
    DBBotState,
    DBPosition,
    DBTrade,
    OrderState,
    get_db_session,
)
from bot.models import Position
from bot.risk import (
    CircuitBreakerStatus,
    RiskManager,
    RiskState,
    audit_post_fill_slippage,
    evaluate_drawdown_lock,
    validate_pre_trade_slippage,
    validate_volatility_regime,
)


class TestDrawdownLockStress(unittest.TestCase):
    """Stress tests for the -6.4% fiduciary drawdown lock."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_events.db")
        self.shared_state_path = os.path.join(self.temp_dir, "global_trading_state.json")
        sess = get_db_session(self.db_path)
        sess.close()
        self.cfg = BotConfig(
            symbol="BTCUSDT",
            event_db_path=self.db_path,
            initial_balance=10000.0,
            max_daily_drawdown=0.08,  # wider than -6.4% to ensure fiduciary lock triggers first
        )
        self.risk = RiskManager(self.cfg, shared_state_path=self.shared_state_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_drawdown_exact_boundary_values(self):
        """Tests precise boundary values: -6.39%, -6.3999%, -6.4000%, -6.4001%, -6.401%."""
        start_equity = 10000.0

        # Case 1: -6.39% drawdown -> Equity 9361.0
        # Drawdown = -639 / 10000 = -0.0639 > -0.064
        safe_639, status_639 = evaluate_drawdown_lock(9361.0, start_equity, limit=-0.064)
        self.assertTrue(safe_639, "Equity at -6.39% drawdown should NOT be locked")
        self.assertEqual(status_639, CircuitBreakerStatus.NORMAL.value)

        # Case 2: -6.3999% drawdown -> Equity 9360.01
        safe_63999, status_63999 = evaluate_drawdown_lock(9360.01, start_equity, limit=-0.064)
        self.assertTrue(safe_63999, "Equity at -6.3999% drawdown should NOT be locked")
        self.assertEqual(status_63999, CircuitBreakerStatus.NORMAL.value)

        # Case 3: Exactly -6.4000% drawdown -> Equity 9360.00
        # Drawdown = -640 / 10000 = -0.0640 <= -0.064
        locked_640, status_640 = evaluate_drawdown_lock(9360.00, start_equity, limit=-0.064)
        self.assertFalse(locked_640, "Equity at exact -6.40% drawdown MUST be locked")
        self.assertEqual(status_640, CircuitBreakerStatus.LOCKED_DEFENSIVE.value)

        # Case 4: -6.4001% drawdown -> Equity 9359.99
        locked_64001, status_64001 = evaluate_drawdown_lock(9359.99, start_equity, limit=-0.064)
        self.assertFalse(locked_64001, "Equity at -6.4001% drawdown MUST be locked")
        self.assertEqual(status_64001, CircuitBreakerStatus.LOCKED_DEFENSIVE.value)

        # Case 5: -6.4010% drawdown -> Equity 9359.90
        locked_6401, status_6401 = evaluate_drawdown_lock(9359.90, start_equity, limit=-0.064)
        self.assertFalse(locked_6401, "Equity at -6.401% drawdown MUST be locked")
        self.assertEqual(status_6401, CircuitBreakerStatus.LOCKED_DEFENSIVE.value)

    def test_rapid_cascading_equity_loss_sequence(self):
        """Simulates rapid cascading equity degradation across tick updates."""
        start_equity = 10000.0
        self.risk.sync_day(datetime.utcnow(), start_equity)

        equity_sequence = [
            (10000.0, True, CircuitBreakerStatus.NORMAL),
            (9800.0, True, CircuitBreakerStatus.NORMAL),
            (9600.0, True, CircuitBreakerStatus.NORMAL),
            (9400.0, True, CircuitBreakerStatus.NORMAL),
            (9361.0, True, CircuitBreakerStatus.NORMAL),      # -6.39% -> safe
            (9360.0, False, CircuitBreakerStatus.LOCKED_DEFENSIVE), # -6.40% -> LOCK
            (9300.0, False, CircuitBreakerStatus.LOCKED_DEFENSIVE), # -7.00% -> LOCK
            (9000.0, False, CircuitBreakerStatus.LOCKED_DEFENSIVE), # -10.0% -> LOCK
            (5000.0, False, CircuitBreakerStatus.LOCKED_DEFENSIVE), # -50.0% -> LOCK
            (100.0, False, CircuitBreakerStatus.LOCKED_DEFENSIVE),  # -99.0% -> LOCK
            (0.0, False, CircuitBreakerStatus.LOCKED_DEFENSIVE),    # -100% -> LOCK
            (-500.0, False, CircuitBreakerStatus.LOCKED_DEFENSIVE), # Negative equity -> LOCK
        ]

        for current_eq, expected_allowed, expected_status in equity_sequence:
            allowed, reason = self.risk.check_global_circuit_breaker(current_eq, start_equity)
            self.assertEqual(
                allowed,
                expected_allowed,
                f"Equity {current_eq} expected allowed={expected_allowed}, got {allowed} ({reason})"
            )
            self.assertEqual(
                self.risk.state.circuit_breaker_status,
                expected_status,
                f"Equity {current_eq} expected status {expected_status}, got {self.risk.state.circuit_breaker_status}"
            )

    def test_edge_and_pathological_equity_inputs(self):
        """Tests zero start equity, negative start equity, and zero division guards."""
        # 1. Zero start equity and zero current equity
        allowed, status = evaluate_drawdown_lock(0.0, 0.0)
        self.assertFalse(allowed)
        self.assertEqual(status, CircuitBreakerStatus.LOCKED_DEFENSIVE.value)

        # 2. Negative start equity
        allowed_neg, status_neg = evaluate_drawdown_lock(100.0, -500.0)
        self.assertFalse(allowed_neg)
        self.assertEqual(status_neg, CircuitBreakerStatus.LOCKED_DEFENSIVE.value)

        # 3. Check via RiskManager.check_global_circuit_breaker with non-positive equities
        allowed_rm, reason_rm = self.risk.check_global_circuit_breaker(0.0, 0.0)
        self.assertFalse(allowed_rm)
        self.assertIn("non_positive_equity", reason_rm)

    def test_sqlite_state_persistence_on_lock(self):
        """Verifies whether circuit breaker state is recorded into SQLite upon single-bot lock."""
        allowed, reason = self.risk.check_global_circuit_breaker(9350.0, 10000.0)
        self.assertFalse(allowed)

        # Empirically check SQLite table bot_state
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bot_state'")
            table_exists = cur.fetchone() is not None

            has_record = False
            if table_exists:
                cur.execute("SELECT key, value_json FROM bot_state WHERE key='circuit_breaker:global'")
                if cur.fetchone():
                    has_record = True

            # Observation: Single-instance lock on line 180 returns before reaching Step 4 (SQLite sync)
            self.assertFalse(
                has_record,
                "EMPIRICAL FINDING CONFIRMED: Line 180 early return skips Step 4 SQLite sync"
            )

    def test_can_trade_suppresses_buys_under_drawdown_lock(self):
        """Verifies that can_trade rejects trade entries once drawdown limit is breached."""
        now = datetime.utcnow()
        self.risk.sync_day(now, 10000.0)

        # At -6.39% drawdown: can trade
        can_639, reason_639 = self.risk.can_trade(now, 9361.0)
        self.assertTrue(can_639, f"Should be able to trade at -6.39% drawdown: {reason_639}")

        # At -6.401% drawdown: can NOT trade
        can_6401, reason_6401 = self.risk.can_trade(now, 9359.9)
        self.assertFalse(can_6401, "Must NOT trade when fiduciary drawdown is breached")
        self.assertTrue(
            "fiduciary" in reason_6401.lower() or "drawdown" in reason_6401.lower() or "circuit_breaker" in reason_6401.lower(),
            f"Expected fiduciary lock reason, got: {reason_6401}"
        )


class TestPreTradeSlippageGuardStress(unittest.TestCase):
    """Stress tests for pre-trade order book inspection and slippage guards."""

    def test_exact_slippage_boundary(self):
        """Validates exact boundary matching against cfg.slippage (e.g. 0.0005 = 0.05%)."""
        expected_price = 100.0
        max_slippage = 0.0005

        # Slippage = +0.0499% (best ask 100.0499) -> PASS
        ob_under = {"asks": [["100.0499", "1.0"]]}
        ok, slip, _ = validate_pre_trade_slippage(ob_under, expected_price, max_slippage)
        self.assertTrue(ok)
        self.assertAlmostEqual(slip, 0.000499, places=6)

        # Slippage = +0.0500% (best ask 100.0500) -> PASS
        ob_exact = {"asks": [["100.0500", "1.0"]]}
        ok, slip, _ = validate_pre_trade_slippage(ob_exact, expected_price, max_slippage)
        self.assertTrue(ok)
        self.assertAlmostEqual(slip, 0.0005, places=6)

        # Slippage = +0.0501% (best ask 100.0501) -> REJECT
        ob_over = {"asks": [["100.0501", "1.0"]]}
        ok, slip, reason = validate_pre_trade_slippage(ob_over, expected_price, max_slippage)
        self.assertFalse(ok)
        self.assertAlmostEqual(slip, 0.000501, places=6)
        self.assertEqual(reason, "projected_slippage_exceeded")

    def test_favorable_negative_slippage(self):
        """Best ask lower than expected price (favorable negative slippage) must pass."""
        ob_favorable = {"asks": [["99.50", "2.0"]]}
        ok, slip, reason = validate_pre_trade_slippage(ob_favorable, expected_price=100.0, max_slippage=0.0005)
        self.assertTrue(ok)
        self.assertAlmostEqual(slip, -0.005, places=6)
        self.assertEqual(reason, "ok")

    def test_thin_and_corrupt_order_books(self):
        """Validates defensive rejection on empty, thin, or malformed order books."""
        # 1. Empty dictionary
        ok, _, reason = validate_pre_trade_slippage({}, 100.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "empty_order_book")

        # 2. Asks is empty list
        ok, _, reason = validate_pre_trade_slippage({"asks": []}, 100.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "empty_order_book")

        # 3. Asks is None
        ok, _, reason = validate_pre_trade_slippage({"asks": None}, 100.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "empty_order_book")

        # 4. Asks contains empty entry
        ok, _, reason = validate_pre_trade_slippage({"asks": [[]]}, 100.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_order_book")

        # 5. Asks contains non-numeric price string
        ok, _, reason = validate_pre_trade_slippage({"asks": [["CORRUPT_PRICE", "1.0"]]}, 100.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_order_book")

        # 6. Asks contains None price
        ok, _, reason = validate_pre_trade_slippage({"asks": [[None, "1.0"]]}, 100.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_order_book")

        # 7. Expected price is zero or negative
        ok, _, reason = validate_pre_trade_slippage({"asks": [["100.0", "1.0"]]}, expected_price=0.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_expected_price")

        ok, _, reason = validate_pre_trade_slippage({"asks": [["100.0", "1.0"]]}, expected_price=-10.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_expected_price")

    def test_post_fill_slippage_auditor(self):
        """Validates post-fill execution slippage auditing and multiplier threshold."""
        expected = 100.0
        max_slip = 0.0005
        multiplier = 2.0  # threshold = 0.0010 (0.10%)

        # Executed at 100.08 (0.08% <= 0.10%) -> PASS
        ok_fill, slip_pct, _ = audit_post_fill_slippage(100.08, expected, max_slip, multiplier)
        self.assertTrue(ok_fill)
        self.assertAlmostEqual(slip_pct, 0.0008, places=6)

        # Executed at 100.15 (0.15% > 0.10%) -> REJECT
        ok_fill2, slip_pct2, reason2 = audit_post_fill_slippage(100.15, expected, max_slip, multiplier)
        self.assertFalse(ok_fill2)
        self.assertAlmostEqual(slip_pct2, 0.0015, places=6)
        self.assertIn("excessive_post_fill_slippage", reason2)


class TestVolatilitySpikeRejectionStress(unittest.TestCase):
    """Stress tests for ATR expansion volatility regimes."""

    def _generate_candles(self, n: int = 30, base_high: float = 101.0, base_low: float = 100.0, base_close: float = 100.5) -> pd.DataFrame:
        dates = pd.date_range("2026-09-01", periods=n, freq="15min")
        return pd.DataFrame({
            "high": [base_high] * n,
            "low": [base_low] * n,
            "close": [base_close] * n,
        }, index=dates)

    def test_volatility_spike_boundary_values(self):
        """Tests ATR expansion ratio exactly at 3.0x boundary."""
        # Baseline ATR ~ 1.0 (high=101, low=100, close=100.5 -> TR = 1.0)
        df = self._generate_candles(30, 101.0, 100.0, 100.5)

        # Steady volatility: ratio = 1.0 <= 3.0 -> PASS
        ok, ratio, reason = validate_volatility_regime(df, max_ratio=3.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(ratio, 1.0, places=4)
        self.assertEqual(reason, "ok")

        # Create flash expansion candle in last period:
        # In df (30 candles), baseline is median of atr14[-15:-1] which is 1.0.
        # current_atr is atr14.iloc[-1] = (sum of last 13 TRs + last TR) / 14.
        # When last 13 TRs are 1.0, current_atr = (13.0 + last_TR) / 14.
        # For ratio == 3.0000: last_TR = 14 * 3.0 - 13.0 = 29.0.
        df_3x = df.copy()
        df_3x.iloc[-1, df_3x.columns.get_loc("high")] = 129.0
        df_3x.iloc[-1, df_3x.columns.get_loc("low")] = 100.0
        df_3x.iloc[-1, df_3x.columns.get_loc("close")] = 100.5

        ok_3x, ratio_3x, reason_3x = validate_volatility_regime(df_3x, max_ratio=3.0)
        self.assertTrue(ok_3x, f"Ratio at exactly 3.0x boundary should be accepted, got ratio={ratio_3x}")
        self.assertAlmostEqual(ratio_3x, 3.0, places=4)
        self.assertEqual(reason_3x, "ok")

        # Now set last candle TR = 29.5 (ratio = 3.0357 > 3.0)
        df_spike = df.copy()
        df_spike.iloc[-1, df_spike.columns.get_loc("high")] = 129.5
        df_spike.iloc[-1, df_spike.columns.get_loc("low")] = 100.0
        df_spike.iloc[-1, df_spike.columns.get_loc("close")] = 100.5

        ok_spike, ratio_spike, reason_spike = validate_volatility_regime(df_spike, max_ratio=3.0)
        self.assertFalse(ok_spike, f"Ratio > 3.0x must be rejected, got ratio={ratio_spike}")
        self.assertGreater(ratio_spike, 3.0)
        self.assertEqual(reason_spike, "extreme_volatility_rejected")

    def test_extreme_flash_spike_rejection(self):
        """Single massive black-swan candle flash spike (10x expansion)."""
        df = self._generate_candles(30, 101.0, 100.0, 100.5)
        df.iloc[-1, df.columns.get_loc("high")] = 150.0  # 50pt jump
        df.iloc[-1, df.columns.get_loc("low")] = 95.0
        df.iloc[-1, df.columns.get_loc("close")] = 120.0

        ok, ratio, reason = validate_volatility_regime(df, max_ratio=3.0)
        self.assertFalse(ok, "10x flash spike must be rejected")
        self.assertEqual(reason, "extreme_volatility_rejected")
        self.assertGreater(ratio, 3.0)

    def test_pathological_candlestick_inputs(self):
        """Tests zero baseline (completely flat market), insufficient candles, and None input."""
        # 1. None DataFrame
        ok_none, _, reason_none = validate_volatility_regime(None)
        self.assertTrue(ok_none)
        self.assertEqual(reason_none, "insufficient_data")

        # 2. Fewer than 15 candles
        df_short = self._generate_candles(14)
        ok_short, _, reason_short = validate_volatility_regime(df_short)
        self.assertTrue(ok_short)
        self.assertEqual(reason_short, "insufficient_data")

        # 3. Flatline price (high == low == close, TR == 0, baseline == 0)
        df_flat = self._generate_candles(30, 100.0, 100.0, 100.0)
        ok_flat, _, reason_flat = validate_volatility_regime(df_flat)
        self.assertTrue(ok_flat, "Flat market with zero baseline should not crash with ZeroDivisionError")
        self.assertEqual(reason_flat, "zero_baseline")


class TestStartupReconcilerCrashRecoveryStress(unittest.TestCase):
    """Adversarial crash recovery stress tests for LiveTrader startup state reconciler."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_reconciler.db")
        sess = get_db_session(self.db_path)
        sess.close()
        self.cfg = BotConfig(
            symbol="BTCUSDT",
            event_db_path=self.db_path,
            initial_balance=10000.0,
            use_testnet=True,
            slippage=0.0005,
        )
        object.__setattr__(self.cfg, "stop_loss", 0.02)
        object.__setattr__(self.cfg, "take_profit", 0.04)

    def test_finding_bot_config_lacks_stop_loss_and_take_profit_attributes(self):
        """EMPIRICAL FINDING: BotConfig lacks stop_loss and take_profit required by bot/main.py:388."""
        default_cfg = BotConfig()
        self.assertFalse(hasattr(default_cfg, "stop_loss"), "BotConfig has no stop_loss attribute")
        self.assertFalse(hasattr(default_cfg, "take_profit"), "BotConfig has no take_profit attribute")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _get_live_trader_cls(self):
        try:
            import bot.main
            return bot.main.LiveTrader
        except SyntaxError as se:
            self.fail(f"CRITICAL ENGINE COMPILATION BUG: bot/main.py has SyntaxError: {se}")
        except Exception as e:
            self.fail(f"CRITICAL ENGINE IMPORT BUG: failed to import bot/main.py: {e}")

    def test_unhedged_exchange_position_without_local_db_record(self):
        """
        CRASH SCENARIO 1:
        Process killed after exchange filled buy order, before DB record was saved.
        On reboot:
        - Exchange has 0.25 BTC free ($15,000 value, > min_notional).
        - Local DB has 0 positions.
        Reconciler must:
        1. Detect orphan unhedged position.
        2. Query Binance trades to reconstruct entry price.
        3. Submit protective STOP_LOSS_LIMIT order to Binance.
        4. Commit new DBPosition record with state='FILLED' and stop_loss_order_id.
        5. Populate trader.position in memory.
        """
        LiveTrader = self._get_live_trader_cls()
        with patch("bot.main.BinanceDataClient") as mock_data_cls, patch("bot.main.BinanceExecutionClient") as mock_exec_cls:
            mock_exec = MagicMock()
            mock_data = MagicMock()
            mock_exec_cls.return_value = mock_exec
            mock_data_cls.return_value = mock_data

            # Exchange balances: 0.25 BTC free, 5000 USDT free
            mock_exec.get_account.return_value = {
                "balances": [
                    {"asset": "BTC", "free": "0.25", "locked": "0.0"},
                    {"asset": "USDT", "free": "5000.0", "locked": "0.0"},
                ]
            }
            mock_exec.split_symbol.return_value = ("BTC", "USDT")
            from bot.binance_client import SymbolFilters
            mock_exec.get_symbol_filters.return_value = SymbolFilters(
                min_qty=0.00001, max_qty=9000.0, step_size=0.00001,
                min_price=0.01, max_price=1000000.0, tick_size=0.01, min_notional=5.0
            )
            mock_exec.quantity_is_valid.return_value = True

            # Klines price returns 60000.0
            mock_data.get_klines.return_value = pd.DataFrame({"close": [60000.0]})

            # Exchange trade history returns the crashed buy fill
            mock_exec._call_signed.return_value = [
                {"isBuyer": True, "price": "60000.0", "qty": "0.25", "time": 1726598400000}
            ]
            # No existing open orders on exchange
            mock_exec.get_open_orders.return_value = []

            # SL placement succeeds
            mock_exec.create_stop_loss_limit.return_value = {
                "orderId": 888801,
                "status": "NEW",
                "symbol": "BTCUSDT"
            }

            # Initialize LiveTrader (which triggers load_state -> reconcile_startup_state)
            trader = LiveTrader(self.cfg)

            try:
                # 1. Assert create_stop_loss_limit was called to protect orphan position
                mock_exec.create_stop_loss_limit.assert_called_once()
                call_args = mock_exec.create_stop_loss_limit.call_args[0]
                self.assertEqual(call_args[0], "BTCUSDT")
                self.assertAlmostEqual(call_args[1], 0.25)
                # Stop price: 60000 * (1 - 0.02) = 58800
                self.assertAlmostEqual(call_args[2], 58800.0)

                # 2. Assert DB record was created and saved in SQLite
                session = get_db_session(self.db_path)
                try:
                    positions = session.query(DBPosition).all()
                    self.assertEqual(len(positions), 1, "Orphan position was not saved to DB")
                    db_p = positions[0]
                    self.assertEqual(db_p.symbol, "BTCUSDT")
                    self.assertAlmostEqual(db_p.quantity, 0.25)
                    self.assertAlmostEqual(db_p.entry_price, 60000.0)
                    self.assertEqual(db_p.stop_loss_order_id, "888801")
                    self.assertEqual(db_p.state, OrderState.FILLED.value)
                    self.assertTrue(db_p.is_active)
                    self.assertEqual(db_p.entry_reason, "reconciled_orphan_startup")
                finally:
                    session.close()

                # 3. Assert in-memory trader.position is populated
                self.assertIsNotNone(trader.position)
                self.assertAlmostEqual(trader.position.quantity, 0.25)
                self.assertAlmostEqual(trader.position.entry_price, 60000.0)
                self.assertAlmostEqual(trader.position.stop_price, 58800.0)
            finally:
                trader.close()

    def test_orphan_position_emergency_sl_failure_marks_unhedged_critical(self):
        """
        CRASH SCENARIO 2:
        Exchange holds unhedged BTC position, but the emergency STOP_LOSS placement fails
        (e.g., Binance API rejection, network disconnect).
        Reconciler must NOT crash, but MUST record the position with state='UNHEDGED_CRITICAL'
        to trigger fiduciary alerts and prevent silent exposure.
        """
        LiveTrader = self._get_live_trader_cls()
        with patch("bot.main.BinanceDataClient") as mock_data_cls, patch("bot.main.BinanceExecutionClient") as mock_exec_cls:
            mock_exec = MagicMock()
            mock_data = MagicMock()
            mock_exec_cls.return_value = mock_exec
            mock_data_cls.return_value = mock_data

            mock_exec.get_account.return_value = {
                "balances": [
                    {"asset": "BTC", "free": "0.10", "locked": "0.0"},
                    {"asset": "USDT", "free": "1000.0", "locked": "0.0"},
                ]
            }
            mock_exec.split_symbol.return_value = ("BTC", "USDT")
            from bot.binance_client import SymbolFilters
            mock_exec.get_symbol_filters.return_value = SymbolFilters(
                min_qty=0.00001, max_qty=9000.0, step_size=0.00001,
                min_price=0.01, max_price=1000000.0, tick_size=0.01, min_notional=5.0
            )
            mock_exec.quantity_is_valid.return_value = True
            mock_data.get_klines.return_value = pd.DataFrame({"close": [60000.0]})
            mock_exec._call_signed.return_value = []
            mock_exec.get_open_orders.return_value = []

            # SL placement FAILS with API error
            mock_exec.create_stop_loss_limit.side_effect = Exception("Binance APIError(-2010): Account has insufficient balance for order.")

            trader = LiveTrader(self.cfg)
            try:
                session = get_db_session(self.db_path)
                try:
                    positions = session.query(DBPosition).all()
                    self.assertEqual(len(positions), 1)
                    db_p = positions[0]
                    self.assertEqual(db_p.state, OrderState.UNHEDGED_CRITICAL.value, "Failed SL must mark UNHEDGED_CRITICAL")
                    self.assertIsNone(db_p.stop_loss_order_id)
                    self.assertTrue(db_p.is_active)
                finally:
                    session.close()

                # Trader in-memory position still tracks the unhedged position
                self.assertIsNotNone(trader.position)
                self.assertAlmostEqual(trader.position.quantity, 0.10)
            finally:
                trader.close()

    def test_orphan_position_with_existing_exchange_sl_avoids_duplicate(self):
        """
        CRASH SCENARIO 3:
        Exchange holds 0.15 BTC and ALREADY has an active STOP_LOSS order on exchange.
        Reconciler must attach to the existing order ID and NOT place a duplicate SL order.
        """
        LiveTrader = self._get_live_trader_cls()
        with patch("bot.main.BinanceDataClient") as mock_data_cls, patch("bot.main.BinanceExecutionClient") as mock_exec_cls:
            mock_exec = MagicMock()
            mock_data = MagicMock()
            mock_exec_cls.return_value = mock_exec
            mock_data_cls.return_value = mock_data

            # BTC locked in existing SL order
            mock_exec.get_account.return_value = {
                "balances": [
                    {"asset": "BTC", "free": "0.0", "locked": "0.15"},
                    {"asset": "USDT", "free": "2000.0", "locked": "0.0"},
                ]
            }
            mock_exec.split_symbol.return_value = ("BTC", "USDT")
            from bot.binance_client import SymbolFilters
            mock_exec.get_symbol_filters.return_value = SymbolFilters(
                min_qty=0.00001, max_qty=9000.0, step_size=0.00001,
                min_price=0.01, max_price=1000000.0, tick_size=0.01, min_notional=5.0
            )
            mock_exec.quantity_is_valid.return_value = True
            mock_data.get_klines.return_value = pd.DataFrame({"close": [60000.0]})
            mock_exec._call_signed.return_value = [
                {"isBuyer": True, "price": "60000.0", "qty": "0.15", "time": 1726598400000}
            ]
            # Existing open SL order on exchange
            mock_exec.get_open_orders.return_value = [
                {"orderId": 777701, "type": "STOP_LOSS_LIMIT", "price": "58800.0", "stopPrice": "58800.0"}
            ]

            trader = LiveTrader(self.cfg)
            try:
                # MUST NOT create a new SL order
                mock_exec.create_stop_loss_limit.assert_not_called()

                # DB position adopts existing SL
                session = get_db_session(self.db_path)
                try:
                    positions = session.query(DBPosition).all()
                    self.assertEqual(len(positions), 1)
                    db_p = positions[0]
                    self.assertEqual(db_p.stop_loss_order_id, "777701")
                    self.assertEqual(db_p.state, OrderState.FILLED.value)
                finally:
                    session.close()
            finally:
                trader.close()

    def test_dust_balance_below_min_notional_ignored(self):
        """
        CRASH SCENARIO 4:
        Exchange has tiny dust balance (0.000001 BTC = $0.06 < min_notional $5.0).
        Reconciler must NOT attempt to create an order or adopt a position.
        """
        LiveTrader = self._get_live_trader_cls()
        with patch("bot.main.BinanceDataClient") as mock_data_cls, patch("bot.main.BinanceExecutionClient") as mock_exec_cls:
            mock_exec = MagicMock()
            mock_data = MagicMock()
            mock_exec_cls.return_value = mock_exec
            mock_data_cls.return_value = mock_data

            mock_exec.get_account.return_value = {
                "balances": [
                    {"asset": "BTC", "free": "0.000001", "locked": "0.0"},
                    {"asset": "USDT", "free": "5000.0", "locked": "0.0"},
                ]
            }
            mock_exec.split_symbol.return_value = ("BTC", "USDT")
            from bot.binance_client import SymbolFilters
            mock_exec.get_symbol_filters.return_value = SymbolFilters(
                min_qty=0.00001, max_qty=9000.0, step_size=0.00001,
                min_price=0.01, max_price=1000000.0, tick_size=0.01, min_notional=5.0
            )
            # Not valid quantity (below min_qty)
            mock_exec.quantity_is_valid.return_value = False
            mock_data.get_klines.return_value = pd.DataFrame({"close": [60000.0]})

            trader = LiveTrader(self.cfg)
            try:
                mock_exec.create_stop_loss_limit.assert_not_called()
                self.assertIsNone(trader.position)
            finally:
                trader.close()

    def test_pending_submit_reconciliation_purges_phantom_order(self):
        """
        CRASH SCENARIO 5:
        Database contains a PENDING_SUBMIT record, but Binance has no record of the order (code -2013).
        Reconciler must purge the phantom order and mark it CANCELLED / inactive.
        """
        LiveTrader = self._get_live_trader_cls()
        with patch("bot.main.BinanceDataClient") as mock_data_cls, patch("bot.main.BinanceExecutionClient") as mock_exec_cls:
            mock_exec = MagicMock()
            mock_data = MagicMock()
            mock_exec_cls.return_value = mock_exec
            mock_data_cls.return_value = mock_data

            mock_exec.get_account.return_value = {
                "balances": [
                    {"asset": "BTC", "free": "0.0", "locked": "0.0"},
                    {"asset": "USDT", "free": "5000.0", "locked": "0.0"},
                ]
            }
            mock_exec.split_symbol.return_value = ("BTC", "USDT")
            from bot.binance_client import SymbolFilters
            mock_exec.get_symbol_filters.return_value = SymbolFilters(
                min_qty=0.00001, max_qty=9000.0, step_size=0.00001,
                min_price=0.01, max_price=1000000.0, tick_size=0.01, min_notional=5.0
            )
            mock_exec.quantity_is_valid.return_value = False
            mock_data.get_klines.return_value = pd.DataFrame({"close": [60000.0]})

            # Pre-seed DB with a PENDING_SUBMIT position
            session = get_db_session(self.db_path)
            try:
                pending_pos = DBPosition(
                    symbol="BTCUSDT",
                    entry_time=datetime.utcnow(),
                    entry_price=60000.0,
                    quantity=0.1,
                    stop_price=58800.0,
                    take_profit_price=62400.0,
                    client_order_id="AETH_BTCUSDT_1726598400000_BUY",
                    state=OrderState.PENDING_SUBMIT.value,
                    is_active=True,
                    entry_reason="pending_crash_test"
                )
                session.add(pending_pos)
                session.commit()
            finally:
                session.close()

            # Binance get_order_status throws -2013 Order does not exist
            mock_exec.get_order_status.side_effect = Exception("APIError(code=-2013): Order does not exist.")

            trader = LiveTrader(self.cfg)
            try:
                session = get_db_session(self.db_path)
                try:
                    purged = session.query(DBPosition).first()
                    self.assertIsNotNone(purged)
                    self.assertEqual(purged.state, OrderState.CANCELLED.value)
                    self.assertFalse(purged.is_active)
                finally:
                    session.close()
                self.assertIsNone(trader.position)
            finally:
                trader.close()


if __name__ == "__main__":
    unittest.main()
