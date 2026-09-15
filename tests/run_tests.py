import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import tempfile
import traceback
import json
import html
from datetime import date, datetime, timezone

# Add parent directory to path so bot can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.config import BotConfig
from bot.telemetry import TelegramNotifier, Telemetry, DailyReporter, EventStore, build_telemetry
from bot.main import (
    _format_live_alert,
    _format_paper_alert,
    _record_paper_event,
    perform_auto_tuning,
)


class MockResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self.json_data = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP Error {self.status_code}")

    def json(self):
        return self.json_data


class TestTelegramNotifierExpansion(unittest.TestCase):
    def setUp(self):
        # Create a temporary file for the database to isolate tests
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.temp_db.close()
        self.db_path = self.temp_db.name

        # Setup standard base config
        self.base_cfg = BotConfig(
            telegram_enabled=True,
            telegram_bot_token="test_token_12345",
            telegram_chat_id="test_chat_67890",
            event_db_path=self.db_path,
        )

        # List to capture outgoing Telegram notifications
        self.captured_calls = []

        def mock_post(url, json=None, timeout=None, **kwargs):
            token = url.split("/bot")[-1].split("/sendMessage")[0] if "/bot" in url else None
            self.captured_calls.append({
                "url": url,
                "token": token,
                "chat_id": json.get("chat_id") if json else None,
                "text": json.get("text") if json else None,
                "parse_mode": json.get("parse_mode") if json else None,
                "disable_web_page_preview": json.get("disable_web_page_preview") if json else None,
                "timeout": timeout
            })
            return MockResponse(200, {"ok": True})

        self.session_post_patcher = patch("requests.Session.post", side_effect=mock_post)
        self.mock_post_func = self.session_post_patcher.start()

    def tearDown(self):
        self.session_post_patcher.stop()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                pass

    # =========================================================================
    # TIER 1: FEATURE COVERAGE (15 test cases, >= 5 per feature)
    # =========================================================================

    # Feature 1: HTML Format Rich Messages (F1)
    def test_f1_basic_buy_alert_formatting(self):
        event = {"event": "live_buy", "price": 60500.5, "qty": 0.05, "reason": "Ruptura Canal"}
        formatted = _format_live_alert("BTCUSDT", event)
        self.assertIn("🟢 <b>BUY</b>", formatted)
        self.assertIn("<b>BTCUSDT</b>", formatted)
        self.assertIn("\u2022 <b>Price:</b> 60500.5000", formatted)
        self.assertIn("\u2022 <b>Qty:</b> 0.0500", formatted)
        self.assertIn("\u2022 <b>Reason:</b> Ruptura Canal", formatted)

    def test_f1_basic_sell_alert_formatting(self):
        event = {"event": "live_sell", "price": 61200.0, "qty": 0.05, "pnl": 35.0, "pnl_pct": 1.15}
        formatted = _format_live_alert("BTCUSDT", event)
        self.assertIn("🔴 <b>SELL</b>", formatted)
        self.assertIn("\u2022 <b>Pnl:</b> +35.0000", formatted)
        self.assertIn("\u2022 <b>Pnl Pct:</b> +1.15%", formatted)

    def test_f1_basic_error_traceback_formatting(self):
        tb_str = "Traceback (most recent call last):\n  File \"main.py\", line 10\nZeroDivisionError: division by zero"
        event = {
            "event": "live_error",
            "error_type": "ZeroDivisionError",
            "message": "division by zero",
            "traceback": tb_str
        }
        formatted = _format_live_alert("BTCUSDT", event)
        self.assertIn("❌ <b>ERROR</b>", formatted)
        self.assertIn("<pre><code>" + html.escape(tb_str) + "</code></pre>", formatted)

    def test_f1_basic_autotune_formatting(self):
        event = {"event": "optimal_config_saved", "strategy_mode": "mean_reversion", "score": 85.0}
        formatted = _format_live_alert("BTCUSDT", event)
        self.assertIn("\u2022 <b>Score:</b> 85.0000", formatted)

    def test_f1_parse_mode_is_html(self):
        notifier = TelegramNotifier(self.base_cfg)
        notifier.send("Test message")
        self.assertEqual(len(self.captured_calls), 1)
        self.assertEqual(self.captured_calls[0]["parse_mode"], "HTML")

    # Feature 2: HTML Escaping/Sanitization (F2)
    def test_f2_escape_html_less_than(self):
        escaped = TelegramNotifier.escape_html("price < 50000")
        self.assertEqual(escaped, "price &lt; 50000")

    def test_f2_escape_html_greater_than(self):
        escaped = TelegramNotifier.escape_html("price > 50000")
        self.assertEqual(escaped, "price &gt; 50000")

    def test_f2_escape_html_ampersand(self):
        escaped = TelegramNotifier.escape_html("BTC & ETH")
        self.assertEqual(escaped, "BTC &amp; ETH")

    def test_f2_escape_html_combined(self):
        escaped = TelegramNotifier.escape_html("<script>alert('dangerous & bad')</script>")
        self.assertEqual(escaped, "&lt;script&gt;alert('dangerous &amp; bad')&lt;/script&gt;")

    def test_f2_escape_html_static_method(self):
        escaped_str = TelegramNotifier.escape_html(123.45)
        self.assertEqual(escaped_str, "123.45")
        escaped_none = TelegramNotifier.escape_html(None)
        self.assertEqual(escaped_none, "None")

    # Feature 3: Configuration-based routing (F3)
    def test_f3_notify_buys_true(self):
        cfg = BotConfig(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            telegram_notify_buys=True,
        )
        notifier = TelegramNotifier(cfg)
        sent = notifier.send("Buy alert details", category="buys")
        self.assertTrue(sent)
        self.assertEqual(len(self.captured_calls), 1)

    def test_f3_notify_buys_false(self):
        cfg = BotConfig(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            telegram_notify_buys=False,
        )
        notifier = TelegramNotifier(cfg)
        sent = notifier.send("Buy alert details", category="buys")
        self.assertFalse(sent)
        self.assertEqual(len(self.captured_calls), 0)

    def test_f3_notify_sells_false(self):
        cfg = BotConfig(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            telegram_notify_sells=False,
        )
        notifier = TelegramNotifier(cfg)
        sent = notifier.send("Sell alert details", category="sells")
        self.assertFalse(sent)
        self.assertEqual(len(self.captured_calls), 0)

    def test_f3_notify_autotune_false(self):
        cfg = BotConfig(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            telegram_notify_autotune=False,
        )
        notifier = TelegramNotifier(cfg)
        sent = notifier.send("AutoTune alert details", category="autotune")
        self.assertFalse(sent)
        self.assertEqual(len(self.captured_calls), 0)

    def test_f3_notify_errors_false(self):
        cfg = BotConfig(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            telegram_notify_errors=False,
        )
        notifier = TelegramNotifier(cfg)
        sent = notifier.send("Error details", category="errors")
        self.assertFalse(sent)
        self.assertEqual(len(self.captured_calls), 0)

    # =========================================================================
    # TIER 2: BOUNDARY & CORNER CASES (15 test cases, >= 5 per feature)
    # =========================================================================

    # Feature 1 Boundary Cases
    def test_f1_extreme_large_numbers(self):
        event = {"event": "live_buy", "price": 999999999999.9999, "qty": 0.00000001}
        formatted = _format_live_alert("BTCUSDT", event)
        self.assertIn("\u2022 <b>Price:</b> 999999999999.9999", formatted)
        self.assertIn("\u2022 <b>Qty:</b> 0.0000", formatted) # formats to 4 decimals

    def test_f1_empty_traceback_string(self):
        event = {"event": "live_error", "traceback": ""}
        formatted = _format_live_alert("BTCUSDT", event)
        self.assertIn("<pre><code></code></pre>", formatted)

    def test_f1_very_long_message(self):
        long_text = "A" * 6000
        notifier = TelegramNotifier(self.base_cfg)
        sent = notifier.send(long_text)
        self.assertTrue(sent)
        self.assertEqual(self.captured_calls[0]["text"], long_text)

    def test_f1_no_valid_fields_in_event(self):
        event = {"event": "unknown_event"}
        formatted = _format_live_alert("BTCUSDT", event)
        self.assertEqual(formatted, "🔔 | <b>BTCUSDT</b>")

    def test_f1_mixed_types_in_event(self):
        event = {"event": "live_buy", "price": "InvalidFloat", "qty": [1, 2, 3], "reason": {"nested": "value"}}
        formatted = _format_live_alert("BTCUSDT", event)
        self.assertIn("\u2022 <b>Price:</b> InvalidFloat", formatted)
        self.assertIn("\u2022 <b>Qty:</b> [1, 2, 3]", formatted)
        self.assertIn("\u2022 <b>Reason:</b> {&#x27;nested&#x27;: &#x27;value&#x27;}", formatted)

    # Feature 2 Boundary Cases
    def test_f2_nested_unclosed_html_tags(self):
        escaped = TelegramNotifier.escape_html("<<script>nested & unclosed")
        self.assertEqual(escaped, "&lt;&lt;script&gt;nested &amp; unclosed")

    def test_f2_double_escaping_prevention(self):
        first = TelegramNotifier.escape_html("A < B")
        self.assertEqual(first, "A &lt; B")
        second = TelegramNotifier.escape_html(first)
        self.assertEqual(second, "A &amp;lt; B")

    def test_f2_special_entities_in_reason(self):
        event = {"event": "live_buy", "reason": "MA_CROSS < 20 & RSI > 80"}
        formatted = _format_live_alert("BTCUSDT", event)
        self.assertIn("\u2022 <b>Reason:</b> MA_CROSS &lt; 20 &amp; RSI &gt; 80", formatted)

    def test_f2_traceback_with_html_chars(self):
        tb_str = "Error in <module> with client & api key"
        event = {"event": "live_error", "traceback": tb_str}
        formatted = _format_live_alert("BTCUSDT", event)
        self.assertIn("<pre><code>Error in &lt;module&gt; with client &amp; api key</code></pre>", formatted)

    def test_f2_empty_or_none_escaping(self):
        self.assertEqual(TelegramNotifier.escape_html(""), "")
        self.assertEqual(TelegramNotifier.escape_html("   "), "   ")

    # Feature 3 Boundary Cases
    def test_f3_env_var_case_insensitivity(self):
        # We manually test different parsed formats
        with patch.dict(os.environ, {
            "TELEGRAM_NOTIFY_BUYS": "true",
            "TELEGRAM_NOTIFY_SELLS": "FALSE",
            "TELEGRAM_NOTIFY_AUTOTUNE": "1",
            "TELEGRAM_NOTIFY_ERRORS": "0",
        }):
            cfg = BotConfig.from_env()
            self.assertTrue(cfg.telegram_notify_buys)
            self.assertFalse(cfg.telegram_notify_sells)
            self.assertTrue(cfg.telegram_notify_autotune)
            self.assertFalse(cfg.telegram_notify_errors)

    def test_f3_empty_env_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = BotConfig.from_env()
            self.assertTrue(cfg.telegram_notify_buys)
            self.assertTrue(cfg.telegram_notify_sells)
            self.assertTrue(cfg.telegram_notify_autotune)
            self.assertTrue(cfg.telegram_notify_errors)

    def test_f3_partially_enabled_configs(self):
        cfg = BotConfig(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            telegram_notify_buys=False,
            telegram_notify_sells=True,
            telegram_notify_autotune=False,
            telegram_notify_errors=True,
        )
        notifier = TelegramNotifier(cfg)
        self.assertFalse(notifier.send("Buy info", category="buys"))
        self.assertTrue(notifier.send("Sell info", category="sells"))
        self.assertFalse(notifier.send("Tune info", category="autotune"))
        self.assertTrue(notifier.send("Error info", category="errors"))

    def test_f3_invalid_env_types(self):
        with patch.dict(os.environ, {
            "TELEGRAM_NOTIFY_BUYS": "maybe",
            "TELEGRAM_NOTIFY_SELLS": "none",
            "TELEGRAM_NOTIFY_AUTOTUNE": "not_a_bool",
            "TELEGRAM_NOTIFY_ERRORS": "",
        }):
            cfg = BotConfig.from_env()
            self.assertFalse(cfg.telegram_notify_buys)
            self.assertFalse(cfg.telegram_notify_sells)
            self.assertFalse(cfg.telegram_notify_autotune)
            self.assertFalse(cfg.telegram_notify_errors)

    def test_f3_notifier_disabled_takes_precedence(self):
        cfg = BotConfig(
            telegram_enabled=False,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            telegram_notify_buys=True,
        )
        notifier = TelegramNotifier(cfg)
        sent = notifier.send("Buy alert", category="buys")
        self.assertFalse(sent)
        self.assertEqual(len(self.captured_calls), 0)

    # =========================================================================
    # TIER 3: CROSS-FEATURE COMBINATIONS (3 test cases)
    # =========================================================================

    def test_cross_html_and_config_routing(self):
        # HTML formatting must execute, but routing stops delivery if category disabled
        cfg = BotConfig(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            telegram_notify_errors=False,
        )
        telemetry = build_telemetry(cfg)
        event = {"event": "live_error", "message": "Critical failure!", "traceback": "tb"}
        formatted = _format_live_alert("BTCUSDT", event)
        self.assertIn("❌ <b>ERROR</b>", formatted)
        
        # Dispatch
        telemetry.alert(formatted, category="errors")
        self.assertEqual(len(self.captured_calls), 0) # blocked by config

    def test_cross_escaping_and_config_routing(self):
        # Escape characters inside a buy alert, and verify routing sends it if buys are enabled
        cfg = BotConfig(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            telegram_notify_buys=True,
        )
        telemetry = build_telemetry(cfg)
        event = {"event": "live_buy", "reason": "MA < 20 & RSI > 80"}
        formatted = _format_live_alert("BTCUSDT", event)
        self.assertIn("MA &lt; 20 &amp; RSI &gt; 80", formatted)
        
        telemetry.alert(formatted, category="buys")
        self.assertEqual(len(self.captured_calls), 1)
        self.assertEqual(self.captured_calls[0]["text"], formatted)

    def test_cross_autotune_escaping_and_routing(self):
        # Auto-tuning strategy containing special chars must be escaped and routed correctly
        cfg = BotConfig(
            telegram_enabled=True,
            telegram_bot_token="token",
            telegram_chat_id="chat",
            telegram_notify_autotune=True,
        )
        telemetry = build_telemetry(cfg)
        strategy_desc = "Mean < Reversion & Trend > Following"
        escaped_strategy = TelegramNotifier.escape_html(strategy_desc)
        self.assertIn("Mean &lt; Reversion &amp; Trend &gt; Following", escaped_strategy)
        
        msg = f"⚡ <b>AUTOTUNE</b> ⚡\n- {escaped_strategy}"
        telemetry.alert(msg, category="autotune")
        self.assertEqual(len(self.captured_calls), 1)
        self.assertEqual(self.captured_calls[0]["text"], msg)

    # =========================================================================
    # TIER 4: REAL-WORLD APPLICATION SCENARIOS (6 test cases)
    # =========================================================================

    def test_scenario_trading_loop_exception(self):
        # Simulate a real trading loop exception: caught, traceback extracted, formatted as HTML error, and dispatched to category "errors"
        telemetry = build_telemetry(self.base_cfg)
        
        try:
            # Trigger exception
            result = 1 / 0
        except Exception as exc:
            event = {
                "time": datetime.now(timezone.utc).isoformat(),
                "event": "live_error",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            formatted = _format_live_alert("BTCUSDT", event)
            telemetry.record("live", "BTCUSDT", "live_error", event)
            telemetry.alert(formatted, category="errors")

        # Verify
        self.assertEqual(len(self.captured_calls), 1)
        self.assertEqual(self.captured_calls[0]["chat_id"], "test_chat_67890")
        self.assertIn("❌ <b>ERROR</b>", self.captured_calls[0]["text"])
        self.assertIn("ZeroDivisionError", self.captured_calls[0]["text"])
        self.assertIn("<pre><code>", self.captured_calls[0]["text"])
        self.assertIn("ZeroDivisionError: division by zero", self.captured_calls[0]["text"])

    def test_scenario_autotune_daily_report_generation(self):
        # Run daily reporter telemetry report generation, assert it formats correctly and routes via Telegram
        telemetry = build_telemetry(self.base_cfg)
        
        # Populate mock events in DB
        store = telemetry.store
        store.record("live", "BTCUSDT", "live_buy", {"price": 60000, "qty": 0.1})
        store.record("live", "BTCUSDT", "live_sell", {"price": 61000, "qty": 0.1, "pnl": 100.0, "pnl_pct": 1.67})
        
        # Build daily report
        target_day = date.today()
        report = telemetry.reporter.build(target_day)
        
        # Format HTML message wrapping text in pre/code
        escaped_report = TelegramNotifier.escape_html(report)
        msg = f"<pre><code>{escaped_report}</code></pre>"
        telemetry.alert(msg, category="autotune")
        
        # Verify
        self.assertEqual(len(self.captured_calls), 1)
        self.assertIn("<pre><code>Reporte diario", self.captured_calls[0]["text"])
        self.assertIn("Compras live: 1", self.captured_calls[0]["text"])
        self.assertIn("Ventas live: 1", self.captured_calls[0]["text"])
        self.assertIn("PnL live: 100.0000", self.captured_calls[0]["text"])

    def test_scenario_news_sentiment_airdrop_alert(self):
        # News/airdrop announcements may contain HTML characters (<, >, &). Verify they are sanitised.
        telemetry = build_telemetry(self.base_cfg)
        
        raw_news = {
            "title": "Binance Launchpool: HODLer airdrop for user balances < 500 BNB & > 10 BNB",
            "url": "https://binance.com/announcement?id=123&type=airdrop"
        }
        
        escaped_title = TelegramNotifier.escape_html(raw_news["title"])
        escaped_url = TelegramNotifier.escape_html(raw_news["url"])
        
        msg = (
            f"<b>Earn</b> nuevo evento de airdrop detectado:\n"
            f"- {escaped_title}\n"
            f"  {escaped_url}\n"
            f"Tu BNB en flexible ya cuenta para Launchpool/HODLer."
        )
        
        telemetry.alert(msg, category="general")
        
        # Verify
        self.assertEqual(len(self.captured_calls), 1)
        text = self.captured_calls[0]["text"]
        self.assertIn("<b>Earn</b> nuevo evento de airdrop", text)
        self.assertIn("balances &lt; 500 BNB &amp; &gt; 10 BNB", text)
        self.assertIn("id=123&amp;type=airdrop", text)

    def test_scenario_paper_event_recording_dispatch(self):
        # Simulate paper trading manager recording events
        telemetry = build_telemetry(self.base_cfg)
        
        # 1. Paper Buy
        event_buy = {"event": "buy", "price": 59000.0, "qty": 0.1, "reason": "EMA cross"}
        _record_paper_event(telemetry, "BTCUSDT", event_buy)
        self.assertEqual(len(self.captured_calls), 1)
        self.assertIn("🟢 <b>PAPER BUY</b>", self.captured_calls[0]["text"])
        
        # 2. Paper Risk Pause
        event_pause = {"event": "risk_pause", "reason": "Max Drawdown Reached"}
        _record_paper_event(telemetry, "BTCUSDT", event_pause)
        self.assertEqual(len(self.captured_calls), 2)
        self.assertIn("⚠️ <b>PAPER RISK PAUSE</b>", self.captured_calls[1]["text"])

    def test_scenario_live_loop_buy_sell_flow(self):
        # Simulate continuous execution flow where loop triggers buying and selling
        telemetry = build_telemetry(self.base_cfg)
        
        # Cycle 1: Buy triggered
        buy_event = {"event": "live_buy", "price": 60500.0, "qty": 0.1, "reason": "ADX break"}
        category_buy = "buys"
        formatted_buy = _format_live_alert("ETHUSDT", buy_event)
        telemetry.record("live", "ETHUSDT", "live_buy", buy_event)
        telemetry.alert(formatted_buy, category=category_buy)
        
        # Cycle 2: Sell triggered
        sell_event = {"event": "live_sell", "price": 61800.0, "qty": 0.1, "pnl": 130.0, "pnl_pct": 2.15}
        category_sell = "sells"
        formatted_sell = _format_live_alert("ETHUSDT", sell_event)
        telemetry.record("live", "ETHUSDT", "live_sell", sell_event)
        telemetry.alert(formatted_sell, category=category_sell)
        
        # Verify both captured
        self.assertEqual(len(self.captured_calls), 2)
        self.assertIn("🟢 <b>BUY</b>", self.captured_calls[0]["text"])
        self.assertIn("ETHUSDT", self.captured_calls[0]["text"])
        self.assertIn("🔴 <b>SELL</b>", self.captured_calls[1]["text"])

    def test_scenario_manual_report_command(self):
        # Simulate CLI report generator manually sending a telemetry report
        telemetry = build_telemetry(self.base_cfg)
        
        # Mock database entries
        store = telemetry.store
        store.record("live", "BTCUSDT", "live_buy", {"price": 62000.0, "qty": 0.5})
        store.record("live", "BTCUSDT", "live_sell", {"price": 62500.0, "qty": 0.5, "pnl": 250.0, "pnl_pct": 0.8})
        
        # Report builder execution
        report = telemetry.reporter.build(date.today())
        escaped_report = TelegramNotifier.escape_html(report)
        msg = f"<pre><code>{escaped_report}</code></pre>"
        
        # Routing via "autotune" config option
        telemetry.alert(msg, category="autotune")
        
        self.assertEqual(len(self.captured_calls), 1)
        self.assertIn("<pre><code>Reporte diario", self.captured_calls[0]["text"])
        self.assertIn("PnL live: 250.0000", self.captured_calls[0]["text"])

    @patch("bot.earn_manager.requests.get")
    def test_earn_manager_airdrop_title_escaping_vulnerability(self, mock_get):
        # Setup mock response from Binance CMS API with unescaped HTML characters
        mock_payload = {
            "data": {
                "catalogs": [
                    {
                        "articles": [
                            {
                                "title": "Binance launchpool: HODLer airdrop <test_token> & other rewards",
                                "code": "98765"
                            }
                        ]
                    }
                ]
            }
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_payload
        mock_get.return_value = mock_response

        # Setup EarnManager config
        cfg = BotConfig(
            telegram_enabled=True,
            telegram_bot_token="test_token_12345",
            telegram_chat_id="test_chat_67890",
            event_db_path=self.db_path,
            earn_enabled=True,
            earn_news_enabled=True,
            earn_news_interval_minutes=0,
        )

        from bot.earn_manager import EarnManager
        telemetry = build_telemetry(cfg)
        earn_mgr = EarnManager(cfg=cfg, client=MagicMock())

        # Ensure seen news is cleared for this test
        if earn_mgr.NEWS_SEEN_PATH.exists():
            try:
                earn_mgr.NEWS_SEEN_PATH.unlink()
            except Exception:
                pass

        # Trigger news check
        res = earn_mgr.check_announcements(telemetry)
        self.assertTrue(res["ok"])

        # Clean up the JSON file created by EarnManager to avoid side effects
        if earn_mgr.NEWS_SEEN_PATH.exists():
            try:
                earn_mgr.NEWS_SEEN_PATH.unlink()
            except Exception:
                pass

        # Verify telemetry alert was sent with escaped characters
        self.assertEqual(len(self.captured_calls), 1)
        sent_text = self.captured_calls[0]["text"]
        
        # Verify that it does NOT contain raw unescaped "<test_token>" and "& other rewards"
        self.assertNotIn("<test_token>", sent_text)
        self.assertNotIn("& other rewards", sent_text)
        
        # Also confirm it IS escaped (which would be &lt;test_token&gt; and &amp; other rewards)
        self.assertIn("&lt;test_token&gt;", sent_text)
        self.assertIn("&amp; other rewards", sent_text)


if __name__ == "__main__":
    unittest.main()
