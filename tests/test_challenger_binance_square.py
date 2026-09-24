"""Empirical Stress-Testing Suite for Binance Square Publisher & Growth Engine.

Author: Challenger 2 (Empirical Challenger)
Roles: critic, specialist
Objective:
1. OpenAPI fault injection: Mock HTTP 400, 401, 429 (with Retry-After), 500, 502, connection resets, and timeouts. Verify exponential backoff, jitter, and zero unhandled exceptions.
2. Standby / Degradation mode: Verify bot behavior when binance_square_enabled=False, API key is empty, or dry_run=True. Ensure zero disruption to trading.
3. SQLite telemetry under concurrency: Verify events and bot_state logging under rapid multi-thread calls. Verify absence of database locks ("database is locked") and memory leaks.
4. Live Loop Hook: Verify non-blocking queueing in bot/main.py produces low tick latency and identify blocking paths.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import random
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import requests

from bot.config import BotConfig
from bot.binance_square import (
    BinanceSquarePublisher,
    BinanceSquareContentGenerator,
    SquareRateLimiter,
    sanitize_for_square,
)
from bot.growth_traffic_engine import (
    BinanceSquareWorker,
    get_square_worker,
    enqueue_square_post,
)


class TestOpenAPIFaultInjection(unittest.TestCase):
    """Area 1: OpenAPI fault injection, backoff, jitter, and unhandled exception prevention."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.temp_db.close()
        self.cfg = BotConfig(
            binance_square_enabled=True,
            binance_square_api_key="valid_test_api_key_xyz",
            binance_square_dry_run=False,
            event_db_path=self.temp_db.name,
        )
        self.publisher = BinanceSquarePublisher(self.cfg)

    def tearDown(self) -> None:
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except Exception:
                pass

    @patch("requests.post")
    def test_http_400_bad_request_fails_cleanly_no_retry(self, mock_post: MagicMock) -> None:
        """HTTP 400 Bad Request should fail immediately without retry and record failure event."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("400 Client Error")
        mock_resp.json.return_value = {"code": "400001", "message": "Illegal parameter"}
        mock_post.return_value = mock_resp

        result = self.publisher.publish_post("Test HTTP 400")
        self.assertFalse(result)
        self.assertEqual(mock_post.call_count, 1, "HTTP 400 must NOT be retried")

        # Verify event logged to SQLite
        conn = sqlite3.connect(self.temp_db.name)
        try:
            cur = conn.cursor()
            cur.execute("SELECT event, payload_json FROM events WHERE mode = 'binance_square'")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "binance_square_failed")
            payload = json.loads(rows[0][1])
            self.assertEqual(payload.get("http_status"), 400)
        finally:
            conn.close()

    @patch("requests.post")
    def test_http_401_unauthorized_fails_cleanly_no_retry(self, mock_post: MagicMock) -> None:
        """HTTP 401 Unauthorized should fail immediately without retry and record failure event."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("401 Unauthorized")
        mock_resp.json.return_value = {"code": "401000", "message": "API-key invalid"}
        mock_post.return_value = mock_resp

        result = self.publisher.publish_post("Test HTTP 401")
        self.assertFalse(result)
        self.assertEqual(mock_post.call_count, 1, "HTTP 401 must NOT be retried")

        conn = sqlite3.connect(self.temp_db.name)
        try:
            cur = conn.cursor()
            cur.execute("SELECT event FROM events WHERE mode = 'binance_square'")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "binance_square_failed")
        finally:
            conn.close()

    @patch("time.sleep")
    @patch("requests.post")
    def test_http_429_with_valid_retry_after(self, mock_post: MagicMock, mock_sleep: MagicMock) -> None:
        """HTTP 429 with Retry-After header respects server-specified wait time and succeeds on retry."""
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "2.5"}
        resp_429.raise_for_status.side_effect = requests.exceptions.HTTPError("429 Too Many Requests")

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"code": "000000", "success": True}

        mock_post.side_effect = [resp_429, resp_200]

        result = self.publisher.publish_post("Test HTTP 429 Retry-After")
        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once_with(2.5)

    @patch("time.sleep")
    @patch("requests.post")
    def test_http_429_with_malformed_retry_after_falls_back_to_exponential_backoff(
        self, mock_post: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """HTTP 429 with malformed Retry-After header falls back to exponential backoff with jitter."""
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "not-a-number"}
        resp_429.raise_for_status.side_effect = requests.exceptions.HTTPError("429 Too Many Requests")

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"code": "000000", "success": True}

        mock_post.side_effect = [resp_429, resp_200]

        result = self.publisher.publish_post("Test HTTP 429 Malformed Header")
        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 2)
        mock_sleep.assert_called_once()
        slept = mock_sleep.call_args[0][0]
        # Attempt 1: 0.2 * 2^0 + jitter (0.01 to 0.05) -> 0.21 to 0.25
        self.assertGreaterEqual(slept, 0.20)
        self.assertLessEqual(slept, 0.30)

    @patch("time.sleep")
    @patch("requests.post")
    def test_http_500_exponential_backoff_and_jitter_progression(
        self, mock_post: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """HTTP 500 triggers exponential backoff progression across 4 attempts."""
        resp_500 = MagicMock()
        resp_500.status_code = 500
        resp_500.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Internal Server Error")

        # Fails all 4 attempts
        mock_post.side_effect = [resp_500, resp_500, resp_500, resp_500]

        result = self.publisher.publish_post("Test HTTP 500 Backoff")
        self.assertFalse(result)
        self.assertEqual(mock_post.call_count, 4)
        self.assertEqual(mock_sleep.call_count, 3)

        sleep_times = [call[0][0] for call in mock_sleep.call_args_list]
        # Attempt 1: ~0.20 - 0.25s
        self.assertGreaterEqual(sleep_times[0], 0.20)
        self.assertLessEqual(sleep_times[0], 0.30)
        # Attempt 2: ~0.40 - 0.45s
        self.assertGreaterEqual(sleep_times[1], 0.40)
        self.assertLessEqual(sleep_times[1], 0.50)
        # Attempt 3: ~0.80 - 0.85s
        self.assertGreaterEqual(sleep_times[2], 0.80)
        self.assertLessEqual(sleep_times[2], 0.95)

    @patch("time.sleep")
    @patch("requests.post")
    def test_http_502_bad_gateway_retried(self, mock_post: MagicMock, mock_sleep: MagicMock) -> None:
        """HTTP 502 Bad Gateway is recognized as retryable server error."""
        resp_502 = MagicMock()
        resp_502.status_code = 502
        resp_502.raise_for_status.side_effect = requests.exceptions.HTTPError("502 Bad Gateway")

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"code": "000000", "success": True}

        mock_post.side_effect = [resp_502, resp_200]
        result = self.publisher.publish_post("Test HTTP 502")
        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 2)

    @patch("time.sleep")
    @patch("requests.post")
    def test_connection_reset_and_timeout_fault_injection(
        self, mock_post: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Simulates ConnectionResetError followed by Timeout and eventual recovery."""
        mock_post.side_effect = [
            ConnectionResetError("Connection reset by peer"),
            requests.exceptions.Timeout("Read timeout after 15s"),
            MagicMock(status_code=200, json=lambda: {"code": "000000", "success": True}),
        ]

        result = self.publisher.publish_post("Test ConnectionReset & Timeout")
        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("requests.post")
    def test_corrupted_json_response_handled_cleanly(self, mock_post: MagicMock) -> None:
        """HTTP 200 with invalid/corrupt JSON response body returns False without uncaught exception."""
        resp_corrupt = MagicMock()
        resp_corrupt.status_code = 200
        resp_corrupt.json.side_effect = json.JSONDecodeError("Expecting value", "<html>Error</html>", 0)
        mock_post.return_value = resp_corrupt

        try:
            result = self.publisher.publish_post("Test Corrupt Response")
            self.assertFalse(result)
        except Exception as exc:
            self.fail(f"Corrupt JSON response raised unhandled exception: {exc}")


class TestStandbyAndDegradation(unittest.TestCase):
    """Area 2: Bot behavior when disabled, key empty, or dry-run active."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.temp_db.close()

    def tearDown(self) -> None:
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except Exception:
                pass

    @patch("requests.post")
    def test_service_disabled_produces_zero_network_calls_and_zero_db_writes(
        self, mock_post: MagicMock
    ) -> None:
        """When binance_square_enabled=False, publish_post returns False, 0 HTTP calls, 0 DB writes."""
        cfg = BotConfig(
            binance_square_enabled=False,
            binance_square_api_key="some_key",
            event_db_path=self.temp_db.name,
        )
        pub = BinanceSquarePublisher(cfg)
        self.assertFalse(pub.enabled)

        res = pub.publish_post("Silent test")
        self.assertFalse(res)
        mock_post.assert_not_called()

        # Database should be untouched (no events table or 0 rows)
        conn = sqlite3.connect(self.temp_db.name)
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
            row = cur.fetchone()
            if row:
                cur.execute("SELECT count(*) FROM events")
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            conn.close()

    @patch("requests.post")
    def test_empty_api_key_falls_back_to_simulation(self, mock_post: MagicMock) -> None:
        """When enabled=True but api_key='', publish_post returns False, 0 HTTP calls, logs simulation."""
        cfg = BotConfig(
            binance_square_enabled=True,
            binance_square_api_key="",
            event_db_path=self.temp_db.name,
        )
        pub = BinanceSquarePublisher(cfg)
        self.assertFalse(pub.enabled)

        res = pub.publish_post("Simulation test empty key")
        self.assertFalse(res)
        mock_post.assert_not_called()

        conn = sqlite3.connect(self.temp_db.name)
        try:
            cur = conn.cursor()
            cur.execute("SELECT event, payload_json FROM events WHERE mode = 'binance_square'")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "binance_square_simulation")
            payload = json.loads(rows[0][1])
            self.assertEqual(payload.get("status"), "simulated")
        finally:
            conn.close()

    @patch("requests.post")
    def test_dry_run_mode_simulates_success_and_records_telemetry(self, mock_post: MagicMock) -> None:
        """When binance_square_dry_run=True, publish_post returns True, 0 HTTP calls, logs simulation."""
        cfg = BotConfig(
            binance_square_enabled=True,
            binance_square_api_key="valid_key",
            binance_square_dry_run=True,
            event_db_path=self.temp_db.name,
        )
        pub = BinanceSquarePublisher(cfg)
        self.assertTrue(pub.dry_run)

        res = pub.publish_post("Dry-run test message")
        self.assertTrue(res)
        mock_post.assert_not_called()

        conn = sqlite3.connect(self.temp_db.name)
        try:
            cur = conn.cursor()
            cur.execute("SELECT event, payload_json FROM events WHERE mode = 'binance_square'")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "binance_square_simulation")
            payload = json.loads(rows[0][1])
            self.assertEqual(payload.get("status"), "dry_run")
        finally:
            conn.close()


class TestSQLiteConcurrencyAndLocks(unittest.TestCase):
    """Area 3: Stress-test SQLite telemetry under rapid multi-thread concurrency."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.temp_db.close()
        self.cfg = BotConfig(
            binance_square_enabled=True,
            binance_square_api_key="test_key",
            binance_square_dry_run=True,
            event_db_path=self.temp_db.name,
        )
        self.publisher = BinanceSquarePublisher(self.cfg)
        self.limiter = SquareRateLimiter(db_path=self.temp_db.name, rate_limit_per_hour=100)

    def tearDown(self) -> None:
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except Exception:
                pass

    def test_concurrent_telemetry_and_state_logging_under_load(self) -> None:
        """20 parallel threads executing 25 writes each (500 concurrent operations).
        
        Verifies zero 'database is locked' crashes, zero connection leaks, and data consistency.
        """
        num_threads = 20
        ops_per_thread = 25
        total_ops = num_threads * ops_per_thread
        errors: list[Exception] = []

        def worker_task(thread_id: int) -> None:
            for i in range(ops_per_thread):
                try:
                    if i % 2 == 0:
                        # Event write
                        self.publisher._record_event(
                            mode="binance_square",
                            symbol="SOLUSDT",
                            event="binance_square_simulation",
                            payload={"tid": thread_id, "op": i, "ts": time.time()},
                        )
                    else:
                        # Rate limiter state update
                        self.limiter.record_published(f"Unique post content {thread_id}_{i}_{time.time()}")
                except Exception as e:
                    errors.append(e)

        threads = [
            threading.Thread(target=worker_task, args=(tid,), name=f"StressThread-{tid}")
            for tid in range(num_threads)
        ]

        start_time = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
        duration = time.time() - start_time

        # Invariant 1: Zero errors occurred across all threads
        self.assertEqual(len(errors), 0, f"Concurrent operations threw errors: {errors[:5]}")

        # Invariant 2: Database integrity check passes
        conn = sqlite3.connect(self.temp_db.name, timeout=5.0)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check;")
            integrity = cur.fetchone()[0]
            self.assertEqual(integrity, "ok", f"SQLite integrity check failed: {integrity}")

            cur.execute("SELECT count(*) FROM events WHERE mode = 'binance_square';")
            event_count = cur.fetchone()[0]
            expected_events = num_threads * ((ops_per_thread + 1) // 2)
            self.assertEqual(event_count, expected_events)

            cur.execute("SELECT value_json FROM bot_state WHERE key = 'binance_square:rate_limit';")
            row = cur.fetchone()
            self.assertIsNotNone(row)
            data = json.loads(row[0])
            self.assertIn("hourly_timestamps", data)
            self.assertIn("recent_hashes", data)
        finally:
            conn.close()

    def test_database_connections_released_cleanly_without_file_locking(self) -> None:
        """Verifies that SQLite files can be immediately locked or unlinked without WinError 32."""
        # Perform writes
        for i in range(10):
            self.publisher._record_event(
                mode="binance_square",
                symbol="BTCUSDT",
                event="test_cleanup",
                payload={"i": i},
            )

        # Immediate external exclusive connection must succeed
        conn = sqlite3.connect(self.temp_db.name, timeout=1.0)
        try:
            conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
            conn.execute("INSERT INTO events (ts, mode, symbol, event, payload_json) VALUES ('now', 'test', 'BTC', 'exclusive', '{}');")
            conn.commit()
        finally:
            conn.close()

        # Immediate file removal must not be blocked by lingering connection handles
        os.remove(self.temp_db.name)
        self.assertFalse(os.path.exists(self.temp_db.name))


class TestLiveLoopHookAndTickLatency(unittest.TestCase):
    """Area 4: Measure tick latency of enqueue_square_post and investigate blocking paths."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.temp_db.close()
        self.cfg = BotConfig(
            binance_square_enabled=True,
            binance_square_api_key="valid_key",
            binance_square_dry_run=True,
            event_db_path=self.temp_db.name,
        )

    def tearDown(self) -> None:
        if os.path.exists(self.temp_db.name):
            try:
                os.remove(self.temp_db.name)
            except Exception:
                pass

    def test_pure_queue_enqueue_latency(self) -> None:
        """Measure pure enqueue latency into BinanceSquareWorker.
        
        Evaluates whether enqueueing alone meets the sub-millisecond requirement.
        """
        import queue
        worker = BinanceSquareWorker(self.cfg)
        worker.queue = queue.Queue(maxsize=10000)
        worker.start()
        try:
            latencies_ns: list[int] = []
            num_samples = 5000

            for i in range(num_samples):
                t0 = time.perf_counter_ns()
                worker.enqueue({"text": f"Quick post {i}", "is_priority_alert": False, "metadata": {}})
                t1 = time.perf_counter_ns()
                latencies_ns.append(t1 - t0)

            latencies_ms = [ns / 1_000_000.0 for ns in latencies_ns]
            median_ms = sorted(latencies_ms)[num_samples // 2]
            p95_ms = sorted(latencies_ms)[int(num_samples * 0.95)]
            p99_ms = sorted(latencies_ms)[int(num_samples * 0.99)]

            # Pure Queue.put_nowait takes ~0.0005 ms to 0.005 ms (< 0.1 ms sub-millisecond requirement)
            self.assertLess(median_ms, 0.1, f"Median queue latency ({median_ms:.5f} ms) exceeds 0.1 ms")
        finally:
            worker.stop()

    def test_live_hook_synchronous_blocking_vulnerability_discovery(self) -> None:
        """CRITICAL ADVERSARIAL STRESS TEST:
        
        Investigate whether _publish_live_event_to_square in bot/main.py performs
        synchronous news sentiment network fetching or DB querying on the live trading thread.
        """
        from bot.main import _publish_live_event_to_square

        # Scenario 1: binance_square_enabled is False
        disabled_cfg = BotConfig(binance_square_enabled=False)
        t0 = time.perf_counter()
        _publish_live_event_to_square(disabled_cfg, "SOLUSDT", {"event": "live_buy", "price": 145.0})
        t_disabled = (time.perf_counter() - t0) * 1000.0
        self.assertLess(t_disabled, 0.05, f"Disabled hook latency took {t_disabled:.4f} ms")

        # Scenario 2: binance_square_enabled is True, but worker is active
        worker = get_square_worker(self.cfg)

        # Mock NewsSentimentAnalyzer network fetch to avoid external network delay during test
        with patch("bot.news_sentiment.fetch_latest_news") as mock_news_fetch:
            mock_news_fetch.return_value = [{"title": "Crypto Surge", "description": "Bullish", "pub_date": ""}]
            
            t0 = time.perf_counter()
            _publish_live_event_to_square(
                self.cfg,
                "SOLUSDT",
                {
                    "event": "live_buy",
                    "price": 145.0,
                    "stop": 140.0,
                    "take": 155.0,
                    "strategy_mode": "turtle_breakout",
                    "composite_score": 0.82,
                },
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            # Notice: _publish_live_event_to_square instantiates NewsSentimentAnalyzer,
            # reads DB, parses sentiment, formats post, and then enqueues.
            # Record the actual elapsed time.
            print(f"\n[LATENCY BENCHMARK] _publish_live_event_to_square elapsed: {elapsed_ms:.4f} ms")


if __name__ == "__main__":
    unittest.main()
