from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath("."))

from bot.config import BotConfig
from bot.vip_signal_bot import VIPSignalFormatter, VIPSignalTracker, TelegramVIPSalesBot


class TestVIPSignalSuite(unittest.TestCase):
    def setUp(self):
        import uuid
        self.db_path = f"test_vip_{uuid.uuid4().hex[:8]}.sqlite3"
        self.trackers = []
        self.cfg = BotConfig.from_env()

    def tearDown(self):
        for t in self.trackers:
            try:
                t.close()
            except Exception:
                pass
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                pass

    def test_vip_signal_formatter_entry(self):
        msg = VIPSignalFormatter.format_vip_entry_signal(
            symbol="BTCUSDT",
            action="BUY",
            entry_price=60000.0,
            stop_price=59000.0,
            reason="Turtle Breakout",
            strategy_mode="turtle_breakout",
            confidence=0.90,
            news_sentiment=0.25,
        )
        self.assertIn("SEÑAL VIP PREMIUM | #BTCUSDT", msg)
        self.assertIn("60000.0000 USDT", msg)
        self.assertIn("Target 1:", msg)
        self.assertIn("Target 2:", msg)
        self.assertIn("Target 3:", msg)
        self.assertIn("STOP LOSS ESTRICTO:", msg)
        self.assertIn("59000.0000", msg)
        self.assertIn("TradingView", msg)
        self.assertIn("Binance", msg)

    def test_vip_signal_formatter_target_hit(self):
        msg = VIPSignalFormatter.format_target_hit(
            symbol="SOLUSDT",
            target_num=1,
            target_price=150.0,
            pnl_pct=2.5,
            action="BUY",
        )
        self.assertIn("TARGET 1 ALCANZADO CON ÉXITO", msg)
        self.assertIn("#SOLUSDT", msg)
        self.assertIn("+2.50% de Beneficio", msg)
        self.assertIn("BREAK-EVEN", msg)

    def test_vip_signal_formatter_stop_loss(self):
        msg = VIPSignalFormatter.format_stop_loss_hit(
            symbol="ETHUSDT",
            exit_price=2600.0,
            pnl_pct=-1.5,
        )
        self.assertIn("STOP LOSS ALCANZADO | #ETHUSDT", msg)
        self.assertIn("2600.0000 USDT", msg)

    def test_vip_signal_tracker_lifecycle(self):
        mock_notifier = MagicMock()
        tracker = VIPSignalTracker(self.db_path, notifier=mock_notifier)
        self.trackers.append(tracker)
        
        # 1. Registrar señal
        sig_id = tracker.register_signal("BTCUSDT", "BUY", 60000.0, 59000.0, reason="Breakout")
        self.assertGreater(sig_id, 0)

        # 2. Precio sube a TP1
        # diff = 1000, tp1 = 60000 + 750 = 60750
        alerts = tracker.check_price("BTCUSDT", high=60800.0, low=60000.0, close=60750.0)
        self.assertEqual(len(alerts), 1)
        self.assertIn("TARGET 1", alerts[0])
        self.assertTrue(mock_notifier.send.called)

        # 3. Precio sube a TP2 (60000 + 1500 = 61500)
        alerts2 = tracker.check_price("BTCUSDT", high=61600.0, low=60500.0, close=61500.0)
        self.assertEqual(len(alerts2), 1)
        self.assertIn("TARGET 2", alerts2[0])

        # 4. Precio sube a TP3 (60000 + 2500 = 62500)
        alerts3 = tracker.check_price("BTCUSDT", high=63000.0, low=61000.0, close=62800.0)
        self.assertEqual(len(alerts3), 1)
        self.assertIn("TARGET 3", alerts3[0])

        # 5. Consultar estadísticas
        stats = tracker.get_stats()
        self.assertEqual(stats["total_signals"], 1)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["win_rate_pct"], 100.0)

    @patch("requests.post")
    def test_telegram_sales_bot_commands(self, mock_post):
        mock_post.return_value.status_code = 200
        tracker = VIPSignalTracker(self.db_path)
        self.trackers.append(tracker)
        bot = TelegramVIPSalesBot(self.cfg, tracker)

        # Test /start
        update_start = {
            "update_id": 1,
            "message": {
                "chat": {"id": 12345},
                "from": {"first_name": "Carlos"},
                "text": "/start",
            },
        }
        bot._handle_update(update_start)
        self.assertTrue(mock_post.called)
        sent_text = mock_post.call_args[1]["json"]["text"]
        self.assertIn("Carlos", sent_text)
        self.assertIn("Planes de Membresía VIP", sent_text)

        # Test /plans
        update_plans = {
            "update_id": 2,
            "message": {"chat": {"id": 12345}, "text": "/plans"},
        }
        bot._handle_update(update_plans)
        sent_plans = mock_post.call_args[1]["json"]["text"]
        self.assertIn("PLAN MENSUAL", sent_plans)
        self.assertIn("PLAN VITALICIO", sent_plans)

        # Test /subscribe
        update_sub = {
            "update_id": 3,
            "message": {"chat": {"id": 12345}, "text": "/subscribe"},
        }
        bot._handle_update(update_sub)
        sent_sub = mock_post.call_args[1]["json"]["text"]
        self.assertIn("Billetera", sent_sub)

        # Test /stats
        update_stats = {
            "update_id": 4,
            "message": {"chat": {"id": 12345}, "text": "/stats"},
        }
        bot._handle_update(update_stats)
        sent_stats = mock_post.call_args[1]["json"]["text"]
        self.assertIn("ESTADÍSTICAS OFICIALES", sent_stats)


if __name__ == "__main__":
    unittest.main()
