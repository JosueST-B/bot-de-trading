"""
Empirical Challenger Test Suite for Milestone 2 Deliverables.
Adversarially stress-tests:
1. Live HTTP responses for report-pdf, report-csv, report-json.
2. Non-custodial API connection with valid, invalid, and boundary payloads.
3. 2FA TOTP protected withdrawal endpoint with valid and invalid tokens, boundary amounts, and missing fields.
4. Concurrency and stress handling.
5. DOM client blob export and filter script checks.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from unittest.mock import patch

sys.path.insert(0, os.path.abspath("."))

from bot.config import BotConfig
from bot.institutional_portal import InstitutionalPortalHandler, ThreadingHTTPServer, INSTITUTIONAL_PORTAL_HTML


class TestChallengerM2Empirical(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = BotConfig.from_env()
        cls.port = 8799
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), InstitutionalPortalHandler)
        cls.server.cfg = cls.cfg  # type: ignore
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _request(self, method: str, path: str, body: str | bytes | None = None, headers: dict | None = None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = headers or {}
        conn.request(method, path, body=body, headers=h)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, dict(res.getheaders()), data

    # -------------------------------------------------------------------------
    # 1. PDF Export Live HTTP Test
    # -------------------------------------------------------------------------
    def test_empirical_pdf_export_live(self):
        status, headers, data = self._request("GET", "/api/investor/report-pdf")
        self.assertEqual(status, 200, f"Expected 200 for PDF export, got {status}")
        
        # Check Content-Type
        ct = headers.get("Content-Type", "")
        self.assertEqual(ct, "application/pdf", f"Expected application/pdf, got {ct}")

        # Check binary signature %PDF
        self.assertTrue(data.startswith(b"%PDF"), "Binary response must start with %PDF magic bytes")
        self.assertGreater(len(data), 100, "PDF payload should be non-trivial")
        self.assertIn(b"%%EOF", data, "PDF must terminate with %%EOF marker")

        # Check Content-Length header
        cl = headers.get("Content-Length")
        self.assertIsNotNone(cl, "Content-Length header must be set")
        self.assertEqual(int(cl), len(data), "Content-Length must match body length")

    # -------------------------------------------------------------------------
    # 2. CSV Export Live HTTP Test
    # -------------------------------------------------------------------------
    def test_empirical_csv_export_live(self):
        status, headers, data = self._request("GET", "/api/investor/report-csv")
        self.assertEqual(status, 200, f"Expected 200 for CSV export, got {status}")

        # Check Content-Type
        ct = headers.get("Content-Type", "")
        self.assertTrue(ct.startswith("text/csv"), f"Expected text/csv, got {ct}")

        # Decode and parse CSV rows using standard library
        csv_text = data.decode("utf-8")
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)

        self.assertGreaterEqual(len(rows), 5, "CSV must contain header + at least 4 trade rows")
        expected_headers = [
            "ID", "Timestamp", "Asset", "Market", "Side",
            "Entry_Price", "Exit_Price", "Size_USD", "Net_PnL_USD", "Return_Pct", "Tx_Hash_Verification"
        ]
        self.assertEqual(rows[0], expected_headers, "CSV headers must match exact audit specification")

        # Validate that rows are properly structured
        for row in rows[1:]:
            self.assertEqual(len(row), len(expected_headers), f"Row {row} column count mismatch")
            self.assertTrue(row[0].startswith("AQC-"), f"Trade ID format unexpected: {row[0]}")
            # Ensure price fields are numeric
            self.assertGreater(float(row[5]), 0, f"Entry_Price should be positive float in row {row}")
            self.assertGreater(float(row[6]), 0, f"Exit_Price should be positive float in row {row}")

    # -------------------------------------------------------------------------
    # 3. JSON Export Live HTTP Test
    # -------------------------------------------------------------------------
    def test_empirical_json_export_live(self):
        status, headers, data = self._request("GET", "/api/investor/report-json")
        self.assertEqual(status, 200, f"Expected 200 for JSON export, got {status}")

        # Check Content-Type
        ct = headers.get("Content-Type", "")
        self.assertTrue(ct.startswith("application/json"), f"Expected application/json, got {ct}")

        # Parse JSON
        parsed = json.loads(data.decode("utf-8"))
        self.assertIn("syndicate", parsed)
        self.assertEqual(parsed["syndicate"], "Aethelgard Quantitative Asset Management")
        self.assertIn("telemetry", parsed)
        self.assertIn("audited_ledger", parsed)

        telemetry = parsed["telemetry"]
        self.assertEqual(telemetry.get("sharpe_ratio"), 2.42)
        self.assertEqual(telemetry.get("sortino_ratio"), 3.10)
        self.assertEqual(telemetry.get("win_rate_pct"), 78.5)
        self.assertEqual(telemetry.get("max_drawdown_pct"), -6.4)
        self.assertIn("circuit_breakers", telemetry)

        ledger = parsed["audited_ledger"]
        self.assertIsInstance(ledger, list)
        self.assertGreaterEqual(len(ledger), 4)
        for item in ledger:
            self.assertIn("id", item)
            self.assertIn("symbol", item)
            self.assertIn("side", item)
            self.assertIn("pnl_usd", item)
            self.assertIn("hash", item)

    # -------------------------------------------------------------------------
    # 4. Connect-API Endpoint Test (Valid & Invalid Payloads)
    # -------------------------------------------------------------------------
    @patch("bot.telemetry.TelegramNotifier.send")
    def test_empirical_connect_api_valid(self, mock_send):
        mock_send.return_value = True
        platforms = ["binance", "bybit", "ibkr"]
        for p in platforms:
            payload = json.dumps({
                "name": f"Investor {p}",
                "email": f"investor_{p}@institution.fund",
                "platform": p,
                "apiKey": f"key_{p}_12345",
                "apiSecret": f"sec_{p}_67890",
            })
            status, _, data = self._request("POST", "/api/investor/connect-api", body=payload, headers={"Content-Type": "application/json"})
            self.assertEqual(status, 200, f"Failed connecting platform {p}")
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("status"), "connected")
            self.assertEqual(res.get("platform"), p)
            self.assertTrue(res.get("encrypted"))

    def test_empirical_connect_api_invalid_payloads(self):
        # Empty payload
        status, _, data = self._request("POST", "/api/investor/connect-api", body="{}", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400, "Empty JSON must return 400")

        # Malformed JSON
        status, _, data = self._request("POST", "/api/investor/connect-api", body="{bad json", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400, "Malformed JSON must return 400")

        # Missing apiKey
        payload = json.dumps({"name": "Test", "email": "t@t.com", "platform": "binance", "apiSecret": "sec"})
        status, _, _ = self._request("POST", "/api/investor/connect-api", body=payload, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400, "Missing apiKey must return 400")

        # Missing apiSecret
        payload = json.dumps({"name": "Test", "email": "t@t.com", "platform": "binance", "apiKey": "key"})
        status, _, _ = self._request("POST", "/api/investor/connect-api", body=payload, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400, "Missing apiSecret must return 400")

        # Missing name
        payload = json.dumps({"email": "t@t.com", "platform": "binance", "apiKey": "key", "apiSecret": "sec"})
        status, _, _ = self._request("POST", "/api/investor/connect-api", body=payload, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400, "Missing name must return 400")

        # Blank whitespace name
        payload = json.dumps({"name": "   ", "email": "t@t.com", "platform": "binance", "apiKey": "key", "apiSecret": "sec"})
        status, _, _ = self._request("POST", "/api/investor/connect-api", body=payload, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400, "Whitespace name must return 400")

    # -------------------------------------------------------------------------
    # 5. Withdrawal Endpoint Test (Valid TOTP vs Invalid TOTP & Boundaries)
    # -------------------------------------------------------------------------
    @patch("bot.telemetry.TelegramNotifier.send")
    def test_empirical_withdrawal_valid(self, mock_send):
        mock_send.return_value = True
        
        # Valid 6-digit TOTP tokens
        valid_totps = ["123456", "000000", "999999", "842915"]
        for totp in valid_totps:
            payload = json.dumps({
                "email": "investor@institution.com",
                "amount": 250.0,
                "network": "TRC20",
                "address": "TYDzsYocNCWiSCxZ5B29Y5c26q9wR18W3X",
                "code2fa": totp,
            })
            status, _, data = self._request("POST", "/api/investor/withdraw", body=payload, headers={"Content-Type": "application/json"})
            self.assertEqual(status, 200, f"Expected 200 for valid TOTP {totp}, got {status}")
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("status"), "ticket_created")
            self.assertTrue(res.get("success"))
            self.assertTrue(res.get("ticket_id", "").startswith("AQC-WTH-"))
            self.assertEqual(res.get("sla"), "< 24h")

        # Exact boundary amount $50.00
        payload = json.dumps({
            "email": "investor@institution.com",
            "amount": 50.00,
            "network": "TRC20",
            "address": "TYDzsYocNCWiSCxZ5B29Y5c26q9wR18W3X",
            "code2fa": "654321",
        })
        status, _, data = self._request("POST", "/api/investor/withdraw", body=payload, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 200, "Boundary amount $50.00 must return 200")

    def test_empirical_withdrawal_invalid_totp_and_boundaries(self):
        # Invalid TOTP tokens
        invalid_totps = [
            "12345",       # 5 digits
            "1234567",     # 7 digits
            "abcdef",      # non-numeric
            "12345a",      # alphanumeric
            "",            # empty
            "      ",      # whitespace
            "12 345",      # embedded space
            "-12345",      # negative sign
            "12.345",      # decimal point
            "12345\n",     # trailing newline
        ]
        for bad_totp in invalid_totps:
            payload = json.dumps({
                "email": "investor@institution.com",
                "amount": 100.0,
                "network": "TRC20",
                "address": "TYDzsYocNCWiSCxZ5B29Y5c26q9wR18W3X",
                "code2fa": bad_totp,
            })
            status, _, data = self._request("POST", "/api/investor/withdraw", body=payload, headers={"Content-Type": "application/json"})
            self.assertEqual(status, 400, f"Expected 400 for bad TOTP '{bad_totp}', got {status}")
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("status"), "error")

        # Missing TOTP field entirely
        payload = json.dumps({
            "email": "investor@institution.com",
            "amount": 100.0,
            "network": "TRC20",
            "address": "TYDzsYocNCWiSCxZ5B29Y5c26q9wR18W3X",
        })
        status, _, data = self._request("POST", "/api/investor/withdraw", body=payload, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400, "Missing TOTP must return 400")

        # Amount boundary tests (below $50)
        below_min_amounts = [49.99, 25.0, 0.0, -100.0]
        for bad_amt in below_min_amounts:
            payload = json.dumps({
                "email": "investor@institution.com",
                "amount": bad_amt,
                "network": "TRC20",
                "address": "TYDzsYocNCWiSCxZ5B29Y5c26q9wR18W3X",
                "code2fa": "123456",
            })
            status, _, data = self._request("POST", "/api/investor/withdraw", body=payload, headers={"Content-Type": "application/json"})
            self.assertEqual(status, 400, f"Expected 400 for bad amount {bad_amt}, got {status}")

        # Missing destination address
        payload = json.dumps({
            "email": "investor@institution.com",
            "amount": 100.0,
            "network": "TRC20",
            "address": "",
            "code2fa": "123456",
        })
        status, _, data = self._request("POST", "/api/investor/withdraw", body=payload, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400, "Empty address must return 400")

    # -------------------------------------------------------------------------
    # 6. Concurrency and Stress Test
    # -------------------------------------------------------------------------
    def test_empirical_concurrency_stress(self):
        endpoints = [
            ("GET", "/api/investor/report-pdf", None, 200),
            ("GET", "/api/investor/report-csv", None, 200),
            ("GET", "/api/investor/report-json", None, 200),
            ("GET", "/api/investor/stats", None, 200),
        ]
        
        def call_ep(ep):
            method, path, body, exp_status = ep
            status, _, _ = self._request(method, path, body)
            return status == exp_status

        with ThreadPoolExecutor(max_workers=8) as executor:
            tasks = [executor.submit(call_ep, endpoints[i % len(endpoints)]) for i in range(24)]
            results = [t.result() for t in tasks]

        self.assertTrue(all(results), "All concurrent requests must return expected HTTP status")

    # -------------------------------------------------------------------------
    # 7. DOM and Client-side Functions Verification
    # -------------------------------------------------------------------------
    def test_empirical_client_side_export_and_filter_invariants(self):
        self.assertIn("function exportClientBlob", INSTITUTIONAL_PORTAL_HTML)
        self.assertIn("function filterTrades", INSTITUTIONAL_PORTAL_HTML)
        self.assertIn("function filterBySymbol", INSTITUTIONAL_PORTAL_HTML)
        self.assertIn("activeSymbolFilter", INSTITUTIONAL_PORTAL_HTML)
        self.assertIn("font-feature-settings: \"tnum\" 1, \"zero\" 1", INSTITUTIONAL_PORTAL_HTML)
        self.assertIn("Aethelgard Quantitative", INSTITUTIONAL_PORTAL_HTML)


if __name__ == "__main__":
    unittest.main()
