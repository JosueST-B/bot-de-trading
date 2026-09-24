from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath("."))

from bot.binance_square import (
    BinanceSquareContentGenerator,
    BinanceSquarePublisher,
    SquareRateLimiter,
    sanitize_for_square,
)
from bot.config import BotConfig


class TestAdversarialExtremeTextSizes(unittest.TestCase):
    """Stress tests for extreme text inputs, boundary enforcement, and CTA preservation."""

    def test_massive_overflow_setup_post_bounds(self) -> None:
        """Massive thesis (50,000 characters) must never exceed 1,950 characters."""
        massive_thesis = "Divergencia cuantitativa extrema con absorción fiduciaria. " * 1000  # ~58,000 chars
        post = BinanceSquareContentGenerator.generate_quant_setup_post(
            symbol="BTCUSDT",
            action="BUY",
            entry_price=65432.10,
            stop_price=64000.00,
            tp1=66500.00,
            tp2=68000.00,
            tp3=70000.00,
            composite_score=0.892,
            timeframe="15m",
            thesis=massive_thesis,
        )

        self.assertLessEqual(len(post), 1950, f"Post length {len(post)} exceeded 1950 limit")
        self.assertLess(len(post), 2000, "Post length must be strictly under 2,000 chars")

        # Mandatory conversion CTAs must survive truncation
        self.assertIn("@AdminVIPSignals", post, "Telegram handle lost in truncation")
        self.assertIn("/subscribe", post, "Telegram command lost in truncation")
        self.assertIn("https://josuest-b.github.io/bot-de-trading/", post, "Web portal URL lost in truncation")
        self.assertIn("#BinanceSquare", post, "Strategic hashtag lost in truncation")

    def test_massive_macro_report_gainers_overflow(self) -> None:
        """Macro report with 50 top gainers and 20,000 character summary remains bounded."""
        huge_gainers = [
            {"symbol": f"TOKEN{i}USDT", "price": 1.23 * i, "change_pct": 5.0 + i, "quote_volume": 1000000.0 * i}
            for i in range(1, 51)
        ]
        huge_summary = "Análisis de macro liquidez y tasas de financiamiento. " * 400

        post = BinanceSquareContentGenerator.generate_macro_market_report(
            btc_price=63500.0,
            btc_change_pct=1.85,
            top_gainers=huge_gainers,
            sentiment_label="[ALCISTA] Fuerte Presión",
            sentiment_score=0.72,
            macro_summary=huge_summary,
        )

        self.assertLessEqual(len(post), 1950)
        self.assertIn("@AdminVIPSignals", post)
        self.assertIn("https://josuest-b.github.io/bot-de-trading/", post)

    def test_raw_sanitizer_arbitrary_unstructured_overflow(self) -> None:
        """Arbitrary string of 100,000 continuous characters without spaces or markers."""
        huge_blob = "A" * 100000
        sanitized = sanitize_for_square(huge_blob, max_chars=1950)
        self.assertLessEqual(len(sanitized), 1950)
        self.assertTrue(sanitized.endswith("..."))

    def test_oversized_footer_boundary(self) -> None:
        """When footer itself is extremely large, total length must still strictly respect max_chars."""
        huge_footer_text = (
            "Encabezado de prueba.\n\n"
            + "📲 Canal VIP & Señales Cuantitativas: @AdminVIPSignals (Comando /subscribe) "
            + ("DETALLES ADICIONALES " * 200)
            + "\n🏛️ Portal Institucional & Simulador Actuarial: https://josuest-b.github.io/bot-de-trading/"
            + "\n#BinanceSquare"
        )
        sanitized = sanitize_for_square(huge_footer_text, max_chars=1950)
        self.assertLessEqual(len(sanitized), 1950)


class TestAdversarialHTMLInjectionAndBypasses(unittest.TestCase):
    """Stress tests for HTML injection, XSS vectors, and Markdown tag hygiene."""

    def test_script_tag_injection_purged(self) -> None:
        """Script tags with inline code or remote sources must be completely purged."""
        vectors = [
            "<script>alert('xss')</script>",
            "<SCRIPT SRC=\"https://attacker.com/malware.js\"></SCRIPT>",
            "<script type=\"text/javascript\">document.cookie='stolen';</script>",
            "<<SCRIPT>alert('nested')</SCRIPT>",
        ]
        for vector in vectors:
            sanitized = sanitize_for_square(f"Análisis cuantitativo {vector} seguro")
            self.assertNotIn("<script", sanitized.lower(), f"Script tag bypassed sanitizer: {vector}")
            self.assertNotIn("</script", sanitized.lower(), f"Closing script tag bypassed: {vector}")

    def test_img_and_iframe_event_handlers_purged(self) -> None:
        """Image and iframe tags with onerror or javascript URIs must be completely stripped."""
        payloads = [
            "<img src='x' onerror='alert(1)'>",
            "<IMG SRC=javascript:alert('XSS')>",
            "<iframe src=\"javascript:alert('xss')\"></iframe>",
            "<svg/onload=alert(1)>",
            "<body onload=alert('test')>",
        ]
        for payload in payloads:
            sanitized = sanitize_for_square(f"Setup {payload} confirmado")
            self.assertNotIn("<img", sanitized.lower())
            self.assertNotIn("<iframe", sanitized.lower())
            self.assertNotIn("<svg", sanitized.lower())
            self.assertNotIn("<body", sanitized.lower())

    def test_disallowed_formatting_converted_or_removed(self) -> None:
        """HTML formatting tags (b, strong, i, em, pre, code) must be converted or stripped."""
        raw = "<b>Bold</b> <strong>Strong</strong> <i>Italic</i> <em>Em</em> <pre>Code</pre> <code>inline</code>"
        sanitized = sanitize_for_square(raw)

        # Forbidden HTML tags must not exist in output
        for tag in ["<b", "</b>", "<strong", "</strong>", "<i", "</i>", "<em", "</em>", "<pre", "</pre>", "<code", "</code>"]:
            self.assertNotIn(tag, sanitized.lower())

        # Converted markdown equivalents should be present
        self.assertIn("**Bold**", sanitized)
        self.assertIn("**Strong**", sanitized)
        self.assertIn("*Italic*", sanitized)
        self.assertIn("*Em*", sanitized)
        self.assertIn("Code", sanitized)
        self.assertIn("inline", sanitized)

    def test_anchor_tag_sanitization(self) -> None:
        """Anchor tags <a href='url'>text</a> are converted to text (url) without raw HTML."""
        raw = 'Consulte nuestro <a href="https://example.com/signals">Canal VIP</a> para alertas.'
        sanitized = sanitize_for_square(raw)
        self.assertNotIn("<a", sanitized)
        self.assertNotIn("</a>", sanitized)
        self.assertIn("Canal VIP (https://example.com/signals)", sanitized)

    def test_mathematical_angle_brackets_behavior(self) -> None:
        """Adversarial probe: tests how mathematical comparisons (Drawdown < -6.4% y Sharpe > 2.0) are handled.
        
        Documents the behavior when text contains < ... > on the same line.
        """
        raw = "Operativa con Score >= 0.72, Drawdown < -6.4% y Sharpe > 2.0 con capital < $50k."
        sanitized = sanitize_for_square(raw)
        self.assertIn("Score >= 0.72", sanitized)
        self.assertIn("Drawdown < -6.4%", sanitized)
        self.assertIn("Sharpe > 2.0", sanitized)
        self.assertIn("< $50k", sanitized)


class TestAdversarialRateLimitingAndDeduplication(unittest.TestCase):
    """Stress tests for SquareRateLimiter: rapid bursts, cooldowns, hourly ceilings, and 24h deduplication."""

    def setUp(self) -> None:
        self.db_path = f"test_adv_rate_{uuid.uuid4().hex[:8]}.sqlite3"
        self.limiter = SquareRateLimiter(
            db_path=self.db_path,
            rate_limit_per_hour=5,
            min_cooldown_seconds=900.0,  # 15 minutes standard
        )

    def tearDown(self) -> None:
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                pass

    def test_rapid_burst_calls_cleanly_throttled(self) -> None:
        """Rapid burst calls (10 attempts in instant sequence) are throttled after the first."""
        # 1st call must succeed
        can_pub_1, reason_1 = self.limiter.can_publish("Market Update #1", is_priority_alert=False)
        self.assertTrue(can_pub_1, f"First call should succeed: {reason_1}")
        self.limiter.record_published("Market Update #1")

        # Calls 2..10 within the same second must all fail due to cooldown
        for i in range(2, 11):
            can_pub, reason = self.limiter.can_publish(f"Market Update #{i}", is_priority_alert=False)
            self.assertFalse(can_pub, f"Burst call #{i} should be throttled")
            self.assertIn("cooldown", reason.lower())

    def test_priority_alert_reduced_cooldown_handling(self) -> None:
        """Priority alerts (Score >= 0.72) enforce a shorter 180s cooldown instead of 900s."""
        self.limiter.record_published("Initial Post")

        # At t + 10s: Priority alert is still blocked by 180s cooldown
        with patch("time.time", return_value=self.limiter.last_post_ts + 10.0):
            can_pub, reason = self.limiter.can_publish("Priority Alert", is_priority_alert=True)
            self.assertFalse(can_pub)
            self.assertIn("170s", reason)

        # At t + 185s: Standard alert is STILL blocked (needs 900s), but Priority alert is ALLOWED (needs 180s)
        with patch("time.time", return_value=self.limiter.last_post_ts + 185.0):
            can_std, reason_std = self.limiter.can_publish("Standard Alert", is_priority_alert=False)
            self.assertFalse(can_std, "Standard alert should remain blocked within 900s cooldown")

            can_prio, reason_prio = self.limiter.can_publish("Priority Alert", is_priority_alert=True)
            self.assertTrue(can_prio, f"Priority alert should be permitted after 180s: {reason_prio}")

    def test_hourly_sliding_window_ceiling_at_5_posts(self) -> None:
        """Even with cooldown satisfied, the 6th post within 3600 seconds is strictly blocked."""
        base_time = 100000.0

        # Simulate 5 successful priority posts spaced by 200s (satisfying 180s cooldown)
        for i in range(1, 6):
            post_time = base_time + (i * 200.0)
            with patch("time.time", return_value=post_time):
                can_pub, reason = self.limiter.can_publish(f"Alert #{i}", is_priority_alert=True)
                self.assertTrue(can_pub, f"Alert #{i} at t={post_time} should succeed, got {reason}")
                self.limiter.record_published(f"Alert #{i}")

        # Attempt 6th post at t = base_time + 1200s (cooldown is satisfied: 1200 - 1000 = 200 > 180)
        # BUT hourly window has 5 posts in the last 1200s (< 3600s)
        with patch("time.time", return_value=base_time + 1200.0):
            can_pub_6, reason_6 = self.limiter.can_publish("Alert #6", is_priority_alert=True)
            self.assertFalse(can_pub_6, "6th post in 1 hour must be strictly throttled")
            self.assertIn("Hourly rate limit exceeded: 5/5", reason_6)

        # Window recovery: at t = base_time + 3801s (more than 3600s after post #1)
        # Post #1 (at base_time + 200s) has aged 3601s, sliding out of the window
        with patch("time.time", return_value=base_time + 3801.0):
            can_pub_7, reason_7 = self.limiter.can_publish("Alert #7", is_priority_alert=True)
            self.assertTrue(can_pub_7, f"Post #7 should succeed after window slides: {reason_7}")

    def test_duplicate_content_hash_blocked_within_24h(self) -> None:
        """Identical content hash is blocked within 24h rolling window, even if rate quota is available."""
        base_time = 100000.0
        post_content = "Setup de alta convicción en SOLUSDT con R:R 1:3.0"

        with patch("time.time", return_value=base_time):
            can_pub, _ = self.limiter.can_publish(post_content)
            self.assertTrue(can_pub)
            self.limiter.record_published(post_content)

        # 2 hours later (t + 7200s): Cooldown (900s) and hourly quota (max 5) are 100% free
        # Attempt to publish the EXACT same content
        with patch("time.time", return_value=base_time + 7200.0):
            can_dup, reason_dup = self.limiter.can_publish(post_content)
            self.assertFalse(can_dup, "Duplicate content within 24h must be rejected")
            self.assertIn("Duplicate content detected within 24 hours", reason_dup)

            # Different content must be permitted
            can_diff, reason_diff = self.limiter.can_publish("Different post content")
            self.assertTrue(can_diff, f"Different content should be accepted: {reason_diff}")

        # 24 hours and 1 minute later (t + 86460s): 24h window has passed
        with patch("time.time", return_value=base_time + 86460.0):
            can_after_24h, reason_after_24h = self.limiter.can_publish(post_content)
            self.assertTrue(can_after_24h, f"Same content should be allowed after 24 hours: {reason_after_24h}")

    def test_sqlite_state_persistence_across_instances(self) -> None:
        """Limiter state persisted in SQLite bot_state is cleanly reloaded by a new instance."""
        post_text = "Persisted State Test Post"
        self.limiter.record_published(post_text)

        # Instantiate a second limiter on the same SQLite file
        new_limiter = SquareRateLimiter(
            db_path=self.db_path,
            rate_limit_per_hour=5,
            min_cooldown_seconds=900.0,
        )

        # Verify state was reloaded
        self.assertEqual(len(new_limiter.hourly_timestamps), 1)
        self.assertEqual(len(new_limiter.recent_hashes), 1)
        self.assertEqual(new_limiter.last_post_ts, self.limiter.last_post_ts)

        # Rapid second post in new instance is blocked
        can_pub, reason = new_limiter.can_publish("Another Post")
        self.assertFalse(can_pub)
        self.assertIn("cooldown", reason.lower())


if __name__ == "__main__":
    unittest.main()
