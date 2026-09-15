from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath("."))

from bot.config import BotConfig
from bot.growth_traffic_engine import HighROIScreener, HuggingFaceSentimentEngine, AutoTrafficPublisher


class TestGrowthTrafficEngine(unittest.TestCase):
    def setUp(self):
        self.cfg = BotConfig.from_env()

    def test_sentiment_engine(self):
        engine = HuggingFaceSentimentEngine()
        sent = engine.fetch_latest_sentiment()
        self.assertIn("score", sent)
        self.assertIn("label", sent)
        self.assertIn("headlines", sent)
        self.assertGreaterEqual(sent["score"], -1.0)
        self.assertLessEqual(sent["score"], 1.0)

    @patch("requests.get")
    def test_high_roi_screener_mock(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [
            {"symbol": "BTCUSDT", "quoteVolume": "100000000", "priceChangePercent": "2.5", "lastPrice": "60000", "highPrice": "61000", "lowPrice": "59000"},
            {"symbol": "RAYUSDT", "quoteVolume": "45000000", "priceChangePercent": "21.5", "lastPrice": "2.5", "highPrice": "2.8", "lowPrice": "2.1"},
            {"symbol": "SOLUSDT", "quoteVolume": "80000000", "priceChangePercent": "5.5", "lastPrice": "150", "highPrice": "155", "lowPrice": "145"},
            {"symbol": "BTCUPUSDT", "quoteVolume": "99999999", "priceChangePercent": "50.0", "lastPrice": "10", "highPrice": "12", "lowPrice": "8"},  # Excluded
        ]

        movers = HighROIScreener.get_top_movers(min_volume_usdt=5_000_000.0, top_n=5)
        self.assertEqual(len(movers), 3)
        self.assertEqual(movers[0]["symbol"], "RAYUSDT")
        self.assertEqual(movers[0]["change_pct"], 21.5)

    @patch("bot.telemetry.TelegramNotifier.send")
    @patch("bot.growth_traffic_engine.HighROIScreener.get_top_movers")
    @patch("bot.growth_traffic_engine.HighROIScreener.get_btc_macro")
    def test_auto_traffic_publisher_methods(self, mock_btc, mock_movers, mock_send):
        mock_send.return_value = True
        mock_btc.return_value = {"price": 60500.0, "change_pct": 2.1}
        mock_movers.return_value = [
            {"symbol": "SOLUSDT", "price": 145.5, "change_pct": 6.8, "quote_volume": 45000000.0, "high": 150.0, "low": 140.0}
        ]
        publisher = AutoTrafficPublisher(self.cfg)

        # Test market pulse
        res_pulse = publisher.publish_market_pulse()
        self.assertTrue(res_pulse)
        self.assertTrue(mock_send.called)
        sent_text = mock_send.call_args[0][0]
        self.assertIn("PULSO DE MERCADO", sent_text)

        # Test hot coin alert
        res_hot = publisher.publish_hot_coin_alert()
        self.assertTrue(res_hot)
        sent_hot = mock_send.call_args[0][0]
        self.assertIn("ALERTA DE VOLATILIDAD", sent_hot)

        # Test VIP promo
        res_promo = publisher.publish_vip_promo()
        self.assertTrue(res_promo)
        sent_promo = mock_send.call_args[0][0]
        self.assertIn("MEMBRESÍAS DISPONIBLES", sent_promo)


if __name__ == "__main__":
    unittest.main()
