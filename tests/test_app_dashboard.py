from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from http.client import HTTPConnection
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath("."))

from bot.app_dashboard import AppDashboardHandler, ThreadingHTTPServer
from bot.config import BotConfig
from bot.vip_signal_bot import VIPSignalTracker


class TestAppDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = BotConfig.from_env()
        cls.port = 8799
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), AppDashboardHandler)
        cls.server.cfg = cls.cfg  # type: ignore
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_get_html_index(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        body = res.read().decode("utf-8")
        self.assertIn("TERMINAL OPERATIVO", body)
        self.assertIn("Matriz", body)
        conn.close()

    def test_get_api_status(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/status")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        data = json.loads(res.read().decode("utf-8"))
        self.assertIn("balance_usdt", data)
        self.assertIn("stats", data)
        self.assertIn("subscribers_count", data)
        conn.close()

    def test_get_api_scanner(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/scanner")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        data = json.loads(res.read().decode("utf-8"))
        self.assertIn("symbols", data)
        self.assertGreater(len(data["symbols"]), 0)
        first_sym = data["symbols"][0]
        self.assertIn("symbol", first_sym)
        self.assertIn("rsi", first_sym)
        self.assertIn("score", first_sym)
        conn.close()

    def test_get_api_signals(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/signals")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        data = json.loads(res.read().decode("utf-8"))
        self.assertIsInstance(data, list)
        conn.close()

    @patch("requests.post")
    def test_post_test_signal(self, mock_post):
        mock_post.return_value.status_code = 200
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("POST", "/api/test_signal")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        data = json.loads(res.read().decode("utf-8"))
        self.assertEqual(data.get("status"), "success")
        conn.close()


if __name__ == "__main__":
    unittest.main()
