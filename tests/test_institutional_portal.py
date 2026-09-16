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

from bot.config import BotConfig
from bot.institutional_portal import InstitutionalPortalHandler, ThreadingHTTPServer


class TestInstitutionalPortal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = BotConfig.from_env()
        cls.port = 8798
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), InstitutionalPortalHandler)
        cls.server.cfg = cls.cfg  # type: ignore
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_get_institutional_homepage(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        body = res.read().decode("utf-8")
        self.assertIn("Aethelgard Quantitative", body)
        self.assertIn("Mandatos & Estructuras de Inversión", body)
        self.assertIn("Simulador Cuantitativo de Retornos", body)
        self.assertIn("Ratio Sharpe", body)
        conn.close()

    def test_get_investor_stats_api(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/investor/stats")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        data = json.loads(res.read().decode("utf-8"))
        self.assertEqual(data.get("agency_name"), "Aethelgard Quantitative Capital")
        self.assertEqual(data.get("sharpe_ratio"), 2.42)
        self.assertEqual(data.get("sortino_ratio"), 3.10)
        self.assertIn("wallet_usdt", data)
        conn.close()

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_post_investor_deposit(self, mock_send):
        mock_send.return_value = True
        conn = HTTPConnection("127.0.0.1", self.port)
        payload = json.dumps({
            "name": "Alejandro Dupont",
            "email": "alejandro@dupontcapital.com",
            "txid": "0x489fbe92a819c4882e819b18274a",
            "tier": "Syndicate Multi-Asset ($2,500)",
        })
        conn.request("POST", "/api/investor/deposit", body=payload, headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        data = json.loads(res.read().decode("utf-8"))
        self.assertEqual(data.get("status"), "success")
        self.assertIn("registrado", data.get("message"))
        conn.close()

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_post_connect_api_non_custodial(self, mock_send):
        mock_send.return_value = True
        conn = HTTPConnection("127.0.0.1", self.port)
        payload = json.dumps({
            "name": "Valeria Rossi",
            "email": "valeria@rossifamily.org",
            "platform": "binance",
            "apiKey": "test_public_binance_api_key_123",
            "apiSecret": "test_secret_binance_key_456",
        })
        conn.request("POST", "/api/investor/connect-api", body=payload, headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        data = json.loads(res.read().decode("utf-8"))
        self.assertIn(data.get("status"), ("connected", "success"))
        self.assertEqual(data.get("platform"), "binance")
        self.assertTrue(data.get("encrypted"))
        self.assertIn("AES-256", data.get("message"))
        conn.close()

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_post_connect_api_bybit_and_ibkr(self, mock_send):
        mock_send.return_value = True
        for plat in ["bybit", "ibkr"]:
            conn = HTTPConnection("127.0.0.1", self.port)
            payload = json.dumps({
                "name": f"Investor {plat.upper()}",
                "email": f"investor_{plat}@aethelgard.com",
                "platform": plat,
                "apiKey": f"key_{plat}_999",
                "apiSecret": f"sec_{plat}_888",
            })
            conn.request("POST", "/api/investor/connect-api", body=payload, headers={"Content-Type": "application/json"})
            res = conn.getresponse()
            self.assertEqual(res.status, 200)
            data = json.loads(res.read().decode("utf-8"))
            self.assertIn(data.get("status"), ("connected", "success"))
            self.assertEqual(data.get("platform"), plat)
            self.assertTrue(data.get("encrypted"))
            conn.close()

    def test_get_investor_report_pdf(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/investor/report-pdf")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader("Content-Type"), "application/pdf")
        pdf_bytes = res.read()
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        conn.close()

    def test_get_investor_report_csv(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/investor/report-csv")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        self.assertTrue(res.getheader("Content-Type", "").startswith("text/csv"))
        csv_text = res.read().decode("utf-8")
        self.assertIn("ID,Timestamp,Asset,Market,Side", csv_text)
        self.assertIn("SOL/USDT", csv_text)
        conn.close()

    def test_get_investor_report_json(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/investor/report-json")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        self.assertTrue(res.getheader("Content-Type", "").startswith("application/json"))
        data = json.loads(res.read().decode("utf-8"))
        self.assertIn("syndicate", data)
        self.assertIn("telemetry", data)
        self.assertIn("audited_ledger", data)
        self.assertEqual(data["telemetry"]["sharpe_ratio"], 2.42)
        conn.close()

    def test_backward_compatibility_dashboard_api(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/status")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        data = json.loads(res.read().decode("utf-8"))
        self.assertIn("balance_usdt", data)
        conn.close()

    def test_get_admin_dashboard(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/admin")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        body = res.read().decode("utf-8")
        self.assertIn("TERMINAL OPERATIVO", body)
        conn.close()

    def test_get_investor_portfolio_api(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/investor/portfolio?email=test_investor@aethelgard.com")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        data = json.loads(res.read().decode("utf-8"))
        self.assertIn("total_equity", data)
        self.assertIn("positions", data)
        self.assertEqual(data.get("status"), "VERIFICADO / ACTIVO")
        conn.close()

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_post_investor_withdrawal(self, mock_send):
        mock_send.return_value = True
        conn = HTTPConnection("127.0.0.1", self.port)
        payload = json.dumps({
            "email": "alejandro@dupontcapital.com",
            "amount": 1500.0,
            "network": "TRC20",
            "address": "TYDzsYocNCWiSCxZ5B29Y5c26q9wR18W3X",
            "code2fa": "842915",
        })
        conn.request("POST", "/api/investor/withdraw", body=payload, headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        data = json.loads(res.read().decode("utf-8"))
        self.assertIn(data.get("status"), ("ticket_created", "success"))
        self.assertIn("ticket_id", data)
        self.assertEqual(data.get("sla"), "< 24h")
        conn.close()

    def test_post_investor_withdrawal_invalid_totp(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        payload = json.dumps({
            "email": "alejandro@dupontcapital.com",
            "amount": 1500.0,
            "network": "TRC20",
            "address": "TYDzsYocNCWiSCxZ5B29Y5c26q9wR18W3X",
            "code2fa": "bad_totp",
        })
        conn.request("POST", "/api/investor/withdraw", body=payload, headers={"Content-Type": "application/json"})
        res = conn.getresponse()
        self.assertEqual(res.status, 400)
        data = json.loads(res.read().decode("utf-8"))
        self.assertEqual(data.get("status"), "error")
        self.assertIn("TOTP", data.get("message", ""))
        conn.close()


if __name__ == "__main__":
    unittest.main()
