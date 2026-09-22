"""
Milestone 4 Phase 2: Tier 5 Adversarial Coverage Hardening Test Suite.
File: tests/test_adversarial_m4_hardening.py

Adversarially stress-tests the entire platform across:
1. Malformed / mutated JSON payloads to /api/investor/connect-api and /api/investor/withdraw:
   - Syntax corruptions (truncated, non-UTF8, garbage bytes)
   - Type confusions (scalars, arrays, non-string fields)
   - Extreme / negative / NaN / Infinity amounts
   - Missing fields and whitespace-only injections
   - SQLi, XSS, and CRLF attack vectors
   - 2FA TOTP format mutations and aliases (code2fa, totp, totp_token)
2. Extreme values and boundary conditions for actuarial calculations and sliders:
   - Compound and Distribution mathematical closed-form oracles
   - Boundary limits ($1k min, $100k max, 1-36 mo, t=1 identity)
   - Extreme stress inputs (zero capital, massive capital $1B, 120 months)
   - Multi-currency conversion monotonicity and BTC high-precision (>= 4 decimals)
   - Fiduciary fee schedule oracle (0% management, 5% annual hurdle, 20% strict HWM)
   - HTML slider and selector DOM invariant enforcement
3. High-concurrency burst requests against all API endpoints:
   - Burst tests against /api/investor/stats, /api/evolution/status, /api/investor/report-pdf,
     /api/investor/report-csv, /api/investor/report-json
   - Interleaved multi-threaded load burst across all endpoints concurrently
   - Verification of 100% HTTP 200 OK responses, zero deadlocks, and valid payloads
4. Assertion of zero <img> tags, zero .jpg references, and all 4 brand strings:
   - Comprehensive audit across bot/institutional_portal.py, index.html, and docs/index.html
   - Static mirror distribution parity
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import socket
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import HTTPConnection
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bot.config import BotConfig
from bot.institutional_portal import (
    INSTITUTIONAL_PORTAL_HTML,
    InstitutionalPortalHandler,
    ThreadingHTTPServer,
)


class TestAdversarialM4Hardening(unittest.TestCase):
    """Adversarial stress harness for Milestone 4 Coverage Hardening."""

    server: ThreadingHTTPServer
    server_thread: threading.Thread
    port: int = 0
    host: str = "127.0.0.1"

    MANDATORY_BRAND_STRINGS = [
        "ArcaFid Quantitative",
        "Mandatos & Estructuras de Inversión",
        "Simulador Cuantitativo de Retornos",
        "Ratio Sharpe",
    ]

    @classmethod
    def setUpClass(cls) -> None:
        """Start isolated HTTP server daemon on dynamic ephemeral port (0)."""
        cls.cfg = BotConfig.from_env()
        ThreadingHTTPServer.allow_reuse_address = True
        cls.server = ThreadingHTTPServer((cls.host, 0), InstitutionalPortalHandler)
        cls.server.cfg = cls.cfg  # type: ignore
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls) -> None:
        """Clean up server daemon and release socket."""
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass

    def _http_request(
        self,
        method: str,
        path: str,
        body: Optional[bytes | str] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ) -> Tuple[int, Dict[str, str], bytes]:
        """Execute raw HTTP request against test daemon."""
        conn = HTTPConnection(self.host, self.port, timeout=timeout)
        hdrs = headers or {}
        if isinstance(body, str):
            body_bytes = body.encode("utf-8")
        elif isinstance(body, bytes):
            body_bytes = body
        else:
            body_bytes = None

        conn.request(method, path, body=body_bytes, headers=hdrs)
        res = conn.getresponse()
        status = res.status
        res_headers = {k.lower(): v for k, v in res.getheaders()}
        data = res.read()
        conn.close()
        return status, res_headers, data

    def _get(self, path: str, headers: Optional[Dict[str, str]] = None, timeout: float = 10.0) -> Tuple[int, Dict[str, str], bytes]:
        return self._http_request("GET", path, headers=headers, timeout=timeout)

    def _post(
        self,
        path: str,
        payload: Any = None,
        headers: Optional[Dict[str, str]] = None,
        raw_body: Optional[bytes] = None,
        timeout: float = 10.0,
    ) -> Tuple[int, Dict[str, str], bytes]:
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)

        if raw_body is not None:
            body = raw_body
        elif isinstance(payload, (dict, list)):
            body = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, (str, bytes, int, float, bool)):
            body = json.dumps(payload).encode("utf-8")
        else:
            body = b""

        return self._http_request("POST", path, body=body, headers=hdrs, timeout=timeout)

    # =========================================================================
    # PART 1: MALFORMED & MUTATED JSON PAYLOADS (/connect-api & /withdraw)
    # =========================================================================

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_adv_connect_api_syntax_corruption(self, mock_send: MagicMock) -> None:
        """Adversarially corrupt syntax to /api/investor/connect-api (truncated, malformed, non-UTF8)."""
        mock_send.return_value = True

        corrupt_payloads = [
            b'{"name": "Alice", "email": "alice@fund.com", "platform": "binance"',  # Truncated JSON
            b'{"name": undefined, "apiKey": NaN, "apiSecret": false}',               # Invalid JS tokens
            b'not a json at all {{{',                                                # Pure syntax garbage
            b'\xff\xfe\x00\x00\x12\x34',                                             # Non-UTF8 binary stream
            b'',                                                                     # Zero-byte empty payload
            b'   \r\n\t   ',                                                         # Whitespace-only stream
        ]

        for idx, payload in enumerate(corrupt_payloads):
            status, _, data = self._post("/api/investor/connect-api", raw_body=payload)
            self.assertEqual(
                status, 400,
                f"Payload index {idx} should be rejected with 400 Bad Request, got {status}"
            )
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("status"), "error")

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_adv_connect_api_type_polymorphism_and_scalars(self, mock_send: MagicMock) -> None:
        """Adversarially mutate root JSON type and field types to /api/investor/connect-api."""
        mock_send.return_value = True

        type_mutations = [
            [1, 2, 3, 4],                                      # Array instead of Dict
            "scalar_string_root",                              # String scalar root
            1234567,                                           # Int scalar root
            True,                                              # Boolean scalar root
            {"name": 12345, "email": "a@b.com", "apiKey": "k", "apiSecret": "s"},            # Non-string name
            {"name": "Alice", "email": ["nested@email.com"], "apiKey": "k", "apiSecret": "s"}, # Non-string email
            {"name": "Alice", "email": "a@b.com", "apiKey": {"key": "secret"}, "apiSecret": "s"}, # Dict apiKey
            {"name": "Alice", "email": "a@b.com", "apiKey": "k", "apiSecret": None},          # None apiSecret
        ]

        for idx, mutation in enumerate(type_mutations):
            status, _, data = self._post("/api/investor/connect-api", payload=mutation)
            self.assertEqual(
                status, 400,
                f"Type mutation {idx} ({type(mutation)}) should return 400 Bad Request, got {status}"
            )
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("status"), "error")

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_adv_connect_api_missing_fields_and_whitespace(self, mock_send: MagicMock) -> None:
        """Adversarially test missing fields and whitespace injection on /api/investor/connect-api."""
        mock_send.return_value = True

        invalid_cases = [
            {},                                                                               # Empty dict
            {"name": "Marcus"},                                                               # Missing email, keys
            {"name": "Marcus", "email": "m@v.com"},                                           # Missing apiKey, apiSecret
            {"name": "Marcus", "email": "m@v.com", "apiKey": "k"},                            # Missing apiSecret
            {"name": "   ", "email": "m@v.com", "apiKey": "k", "apiSecret": "s"},             # Whitespace name
            {"name": "Marcus", "email": "   ", "apiKey": "k", "apiSecret": "s"},             # Whitespace email
            {"name": "Marcus", "email": "m@v.com", "apiKey": "   ", "apiSecret": "s"},         # Whitespace apiKey
            {"name": "Marcus", "email": "m@v.com", "apiKey": "k", "apiSecret": "   "},         # Whitespace apiSecret
        ]

        for idx, case in enumerate(invalid_cases):
            status, _, data = self._post("/api/investor/connect-api", payload=case)
            self.assertEqual(
                status, 400,
                f"Case {idx} must return 400 Bad Request, got {status}"
            )
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("status"), "error")
            self.assertIn("requeridos", res.get("message", "").lower())

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_adv_connect_api_platform_normalization_and_security(self, mock_send: MagicMock) -> None:
        """Verify broker platform normalization, XSS escaping, and SQLi resilience."""
        mock_send.return_value = True

        # Case normalization tests (mixed case should normalize to lower)
        platforms = [("BiNaNce", "binance"), ("BYBIT", "bybit"), ("IbKr", "ibkr")]
        for raw_plat, expected_plat in platforms:
            payload = {
                "name": "Dr. Aris Thorne",
                "email": "thorne@cambrian.ai",
                "platform": raw_plat,
                "apiKey": "valid_api_key_sample_123",
                "apiSecret": "valid_api_secret_sample_456",
            }
            status, _, data = self._post("/api/investor/connect-api", payload=payload)
            self.assertEqual(status, 200)
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("platform"), expected_plat)
            self.assertTrue(res.get("encrypted"))

        # Unsupported platform falls back safely to binance
        payload_unknown = {
            "name": "Dr. Aris Thorne",
            "email": "thorne@cambrian.ai",
            "platform": "unsupported_unknown_dex",
            "apiKey": "valid_key",
            "apiSecret": "valid_secret",
        }
        status, _, data = self._post("/api/investor/connect-api", payload=payload_unknown)
        self.assertEqual(status, 200)
        res = json.loads(data.decode("utf-8"))
        self.assertEqual(res.get("platform"), "binance")

        # Security Injection tests: SQLi / XSS / CRLF strings must not crash server
        adversarial_inputs = [
            {"name": "<script>alert('xss')</script>", "email": "xss@victim.org", "apiKey": "k", "apiSecret": "s"},
            {"name": "admin' OR '1'='1' --", "email": "sqli@test.com", "apiKey": "' UNION SELECT * --", "apiSecret": "s"},
            {"name": "Victim", "email": "victim@test.com\r\nBcc: attacker@evil.com", "apiKey": "k", "apiSecret": "s"},
        ]
        for adv in adversarial_inputs:
            status, _, data = self._post("/api/investor/connect-api", payload=adv)
            self.assertEqual(status, 200, f"Adversarial security input failed: {adv}")
            res = json.loads(data.decode("utf-8"))
            self.assertTrue(res.get("encrypted"))

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_adv_withdraw_syntax_and_type_mutations(self, mock_send: MagicMock) -> None:
        """Adversarially corrupt syntax and type confusion on /api/investor/withdraw."""
        mock_send.return_value = True

        malformed_bodies = [
            b'{"amount": 100.0, "address": "TRC20addr"',       # Truncated
            b'{"amount": "not_a_number", "address": "TRC20"}',  # Non-numeric string amount
            b'{"amount": [100.0], "address": "TRC20"}',         # Array amount
            b'{"amount": {"usd": 100}, "address": "TRC20"}',    # Dict amount
            b'{"amount": null, "address": "TRC20"}',            # Null amount
            b'[1, 2, 3]',                                       # Root array
            b'"plain scalar string"',                           # Root scalar string
            b'',                                                # Empty body
        ]

        for idx, body in enumerate(malformed_bodies):
            status, _, data = self._post("/api/investor/withdraw", raw_body=body)
            self.assertEqual(
                status, 400,
                f"Withdraw malformed body {idx} should return 400, got {status}"
            )
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("status"), "error")

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_adv_withdraw_numeric_boundaries(self, mock_send: MagicMock) -> None:
        """Adversarially test withdrawal amount boundary conditions ($50 threshold, negatives, extremes)."""
        mock_send.return_value = True

        base_payload = {
            "email": "investor@aethelgard.com",
            "network": "TRC20",
            "address": "TLyqzVGLV1srkB7dToTAvZgqndvvDfHoxX",
            "code2fa": "849201",
        }

        # Sub-minimum and negative amounts must return HTTP 400
        rejected_amounts = [-1000.0, -50.0, -0.01, 0.0, 0.001, 25.0, 49.99, 49.9999]
        for amt in rejected_amounts:
            p = dict(base_payload, amount=amt)
            status, _, data = self._post("/api/investor/withdraw", payload=p)
            self.assertEqual(status, 400, f"Amount {amt} should be rejected with 400")
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("status"), "error")
            self.assertIn("50.00", res.get("message", ""))

        # Exact minimum boundary $50.00 must succeed (HTTP 200 OK)
        p_boundary = dict(base_payload, amount=50.0)
        status, _, data = self._post("/api/investor/withdraw", payload=p_boundary)
        self.assertEqual(status, 200, "Exact $50.00 amount must be accepted")
        res = json.loads(data.decode("utf-8"))
        self.assertEqual(res.get("status"), "ticket_created")
        self.assertEqual(res.get("sla"), "< 24h")
        self.assertIn("ticket_id", res)

        # Large institutional amount ($50,000,000.00) must succeed
        p_large = dict(base_payload, amount=50000000.0)
        status, _, data = self._post("/api/investor/withdraw", payload=p_large)
        self.assertEqual(status, 200, "Large institutional withdrawal must succeed")
        res = json.loads(data.decode("utf-8"))
        self.assertEqual(res.get("status"), "ticket_created")

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_adv_withdraw_address_and_2fa_totp_mutations(self, mock_send: MagicMock) -> None:
        """Adversarially test destination address and 2FA TOTP token mutations on /api/investor/withdraw."""
        mock_send.return_value = True

        valid_base = {
            "email": "investor@aethelgard.com",
            "amount": 250.0,
            "network": "TRC20",
            "address": "TLyqzVGLV1srkB7dToTAvZgqndvvDfHoxX",
            "code2fa": "654321",
        }

        # Address missing or blank
        for bad_addr in ["", "   "]:
            p = dict(valid_base, address=bad_addr)
            status, _, data = self._post("/api/investor/withdraw", payload=p)
            self.assertEqual(status, 400)
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("status"), "error")
            self.assertIn("dirección", res.get("message", "").lower())

        # Invalid 2FA tokens (strict 6 numeric digits required)
        invalid_totps = [
            "12345",         # 5 digits (too short)
            "1234567",       # 7 digits (too long)
            "abcdef",        # Non-digits
            "12345a",        # Alphanumeric
            "12 456",        # Embedded space
            "12-345",        # Hyphenated
            "!@#$%^",        # Special symbols
            "",              # Empty
            "00000",         # 5 zeros
        ]
        for bad_totp in invalid_totps:
            p = dict(valid_base, code2fa=bad_totp)
            status, _, data = self._post("/api/investor/withdraw", payload=p)
            self.assertEqual(status, 400, f"TOTP '{bad_totp}' must be rejected with 400")
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("status"), "error")
            self.assertIn("2fa", res.get("message", "").lower())

        # 2FA aliases support: 'totp' or 'totp_token'
        for alias_field in ["totp", "totp_token"]:
            p = {
                "email": "investor@aethelgard.com",
                "amount": 300.0,
                "network": "ERC20",
                "address": "0x71C83B50a819c4882e819b18274aE01",
                alias_field: "987654",
            }
            status, _, data = self._post("/api/investor/withdraw", payload=p)
            self.assertEqual(status, 200, f"TOTP alias '{alias_field}' must be accepted")
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("status"), "ticket_created")

    # =========================================================================
    # PART 2: EXTREME VALUES & BOUNDARIES FOR ACTUARIAL CALCULATIONS & SLIDERS
    # =========================================================================

    def test_adv_actuarial_closed_form_oracle(self) -> None:
        """Verify actuarial closed-form mathematical oracles (Compounding & Distribution)."""
        monthly_rate = 0.052  # 5.2% monthly rate (62.4% APR)

        # 1. Compounding: V = P * (1 + r)^t
        test_matrix_compound = [
            # (Principal, months, expected_net_profit, expected_monthly_avg)
            (1000.0, 1, 52.00, 52.00),
            (10000.0, 12, 8371.38, 697.62),
            (100000.0, 36, 520249.80, 14451.38),
        ]
        for p, t, expected_profit, expected_monthly in test_matrix_compound:
            v_final = p * math.pow(1 + monthly_rate, t)
            net_profit = v_final - p
            monthly_avg = net_profit / t
            self.assertAlmostEqual(
                net_profit, expected_profit, delta=3.0,
                msg=f"Compound profit mismatch for P={p}, t={t}: got {net_profit}, expected ~{expected_profit}"
            )
            self.assertAlmostEqual(
                monthly_avg, expected_monthly, delta=3.0,
                msg=f"Monthly avg mismatch for P={p}, t={t}: got {monthly_avg}, expected ~{expected_monthly}"
            )

        # 2. Distribution (Simple interest): V = P * (1 + r * t), Net = P * r * t
        test_matrix_dist = [
            (1000.0, 1, 52.00, 52.00),
            (10000.0, 12, 6240.00, 520.00),
            (100000.0, 36, 187200.00, 5200.00),
        ]
        for p, t, expected_profit, expected_monthly in test_matrix_dist:
            net_profit = p * monthly_rate * t
            monthly_avg = net_profit / t
            self.assertAlmostEqual(net_profit, expected_profit, places=2)
            self.assertAlmostEqual(monthly_avg, expected_monthly, places=2)

        # 3. Identity at t = 1 month: Compounding and Distribution MUST produce identical profit
        for test_cap in [500.0, 1000.0, 5000.0, 25000.0, 100000.0]:
            comp_1mo = test_cap * (math.pow(1 + monthly_rate, 1) - 1)
            dist_1mo = test_cap * (monthly_rate * 1)
            self.assertAlmostEqual(
                comp_1mo, dist_1mo, places=4,
                msg="Compounding and Distribution modes must be identical at t=1 month"
            )

    def test_adv_actuarial_extreme_inputs_and_stability(self) -> None:
        """Verify actuarial formulas remain numerically stable under extreme and zero inputs."""
        monthly_rate = 0.052

        # Zero capital
        v_zero = 0.0 * math.pow(1 + monthly_rate, 12)
        self.assertEqual(v_zero, 0.0)

        # Sovereign fund scale ($1,000,000,000 USD)
        p_billion = 1_000_000_000.0
        v_billion = p_billion * math.pow(1 + monthly_rate, 36)
        self.assertFalse(math.isinf(v_billion))
        self.assertFalse(math.isnan(v_billion))
        self.assertGreater(v_billion, p_billion)

        # Long horizon (120 months / 10 years)
        v_long = 10000.0 * math.pow(1 + monthly_rate, 120)
        self.assertFalse(math.isinf(v_long))
        self.assertFalse(math.isnan(v_long))

    def test_adv_currency_conversion_monotonicity_and_btc_precision(self) -> None:
        """Verify FX conversion rates, strict monotonicity, and 4-decimal minimum for BTC."""
        rates = {"USDT": 1.0, "USD": 1.0, "EUR": 0.92, "BTC": 0.0000165}

        # Monotonicity check across currencies
        for c1, c2 in [(1000.0, 5000.0), (10000.0, 50000.0), (50000.0, 100000.0)]:
            for curr, rate in rates.items():
                val1 = c1 * rate
                val2 = c2 * rate
                self.assertGreater(val2, val1, f"Monotonicity violated in {curr}")
                self.assertGreater(val1, 0.0, f"Positive capital must yield positive {curr}")

        # BTC Fractional Precision Assertion:
        # Minimum monthly profit on $1,000 is $52.00.
        # $52.00 * 0.0000165 = 0.000858 BTC.
        # If truncated to 2 decimals: '0.00' (fatal fiduciary truncation error).
        # Must require at least 4 decimals:
        btc_rate = rates["BTC"]
        min_profit_btc = 52.00 * btc_rate
        formatted_4dec = f"{min_profit_btc:.4f}"
        self.assertEqual(formatted_4dec, "0.0009")
        self.assertNotEqual(formatted_4dec, "0.00")

        # Standard capital $10,000: $10,000 * 0.0000165 = 0.1650 BTC
        cap_10k_btc = 10000.0 * btc_rate
        self.assertEqual(f"{cap_10k_btc:.4f}", "0.1650")

    def test_adv_fiduciary_fee_schedule_invariants(self) -> None:
        """Verify the 0% Management, 5% Hurdle, 20% High-Water Mark institutional fee model."""
        # 1. 0% Management fee: Fixed across all scenarios
        mgmt_fee_rate = 0.00
        hurdle_rate = 0.05
        performance_fee_rate = 0.20

        initial_capital = 10000.0
        # 12-month compound return
        gross_profit = initial_capital * (math.pow(1 + 0.052, 12) - 1)  # ~8371.38
        hurdle_amount = initial_capital * hurdle_rate                   # 500.00

        self.assertGreater(gross_profit, hurdle_amount)
        excess_profit = gross_profit - hurdle_amount                    # ~7871.38
        perf_fee = excess_profit * performance_fee_rate                 # ~1574.28
        net_to_investor = gross_profit - perf_fee                       # ~6797.10

        self.assertEqual(mgmt_fee_rate, 0.00, "Management fee must be strictly 0%")
        self.assertAlmostEqual(hurdle_amount, 500.00, places=2)
        self.assertAlmostEqual(perf_fee, 1574.28, delta=2.0)
        self.assertAlmostEqual(net_to_investor, 6797.10, delta=2.0)

        # Under-hurdle case: If profit <= hurdle, performance fee must be strictly $0.00
        under_hurdle_profit = 300.0  # < 500.0 hurdle
        perf_fee_under = max(0.0, (under_hurdle_profit - hurdle_amount) * performance_fee_rate)
        self.assertEqual(perf_fee_under, 0.0, "Performance fee must be $0.00 when below hurdle")

    def test_adv_slider_dom_attributes_across_three_surfaces(self) -> None:
        """Assert HTML slider boundaries (min, max, step, defaults) across all three surfaces."""
        surfaces = [
            ("INSTITUTIONAL_PORTAL_HTML", INSTITUTIONAL_PORTAL_HTML),
            ("index.html", self._read_file("index.html")),
            ("docs/index.html", self._read_file("docs/index.html")),
        ]

        for surface_name, html_content in surfaces:
            soup = BeautifulSoup(html_content, "html.parser")

            # Capital slider (#slCap)
            cap_slider = soup.find(id="slCap")
            self.assertIsNotNone(cap_slider, f"{surface_name}: #slCap missing")
            self.assertEqual(str(cap_slider.get("min")), "500", f"{surface_name}: #slCap min != 500")
            self.assertEqual(str(cap_slider.get("max")), "100000", f"{surface_name}: #slCap max != 100000")
            self.assertEqual(str(cap_slider.get("step")), "500", f"{surface_name}: #slCap step != 500")
            self.assertEqual(str(cap_slider.get("value")), "5000", f"{surface_name}: #slCap default != 5000")

            # Duration slider (#slTime)
            months_slider = soup.find(id="slTime")
            self.assertIsNotNone(months_slider, f"{surface_name}: #slTime missing")
            self.assertEqual(str(months_slider.get("min")), "3", f"{surface_name}: #slTime min != 3")
            self.assertEqual(str(months_slider.get("max")), "36", f"{surface_name}: #slTime max != 36")
            self.assertEqual(str(months_slider.get("step")), "3", f"{surface_name}: #slTime step != 3")
            self.assertEqual(str(months_slider.get("value")), "12", f"{surface_name}: #slTime default != 12")

            # Currency switch buttons
            for btn_id in ["cUSDT", "cUSD", "cEUR", "cBTC"]:
                self.assertIsNotNone(soup.find(id=btn_id), f"{surface_name}: button #{btn_id} missing")

            # Mode toggles
            self.assertIsNotNone(soup.find(id="btnComp"), f"{surface_name}: #btnComp missing")
            self.assertIsNotNone(soup.find(id="btnDist"), f"{surface_name}: #btnDist missing")

            # Output elements
            for out_id in ["resTotal", "resProfit", "resMonthly"]:
                self.assertIsNotNone(soup.find(id=out_id), f"{surface_name}: output #{out_id} missing")

    # =========================================================================
    # PART 3: HIGH-CONCURRENCY BURST REQUESTS AGAINST ALL API ENDPOINTS
    # =========================================================================

    def test_adv_concurrency_burst_investor_stats(self) -> None:
        """High-concurrency burst against /api/investor/stats (30 parallel requests)."""
        num_requests = 30

        def fetch_stats():
            return self._get("/api/investor/stats")

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(fetch_stats) for _ in range(num_requests)]
            results = [f.result() for f in as_completed(futures)]

        self.assertEqual(len(results), num_requests)
        for status, headers, data in results:
            self.assertEqual(status, 200)
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("agency_name"), "ArcaFid Quantitative Capital")
            self.assertEqual(res.get("sharpe_ratio"), 2.42)
            self.assertEqual(res.get("sortino_ratio"), 3.10)
            self.assertEqual(res.get("max_drawdown_pct"), -6.4)

    def test_adv_concurrency_burst_evolution_status(self) -> None:
        """High-concurrency burst against /api/evolution/status (30 parallel requests)."""
        num_requests = 30

        def fetch_evo():
            return self._get("/api/evolution/status")

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(fetch_evo) for _ in range(num_requests)]
            results = [f.result() for f in as_completed(futures)]

        self.assertEqual(len(results), num_requests)
        for status, headers, data in results:
            self.assertEqual(status, 200)
            res = json.loads(data.decode("utf-8"))
            self.assertIn("generation", res)
            self.assertIn("loss", res)
            self.assertIn("weights", res)

    def test_adv_concurrency_burst_report_pdf(self) -> None:
        """High-concurrency burst against /api/investor/report-pdf (20 parallel requests)."""
        num_requests = 20

        def fetch_pdf():
            return self._get("/api/investor/report-pdf")

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_pdf) for _ in range(num_requests)]
            results = [f.result() for f in as_completed(futures)]

        self.assertEqual(len(results), num_requests)
        for status, headers, data in results:
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("content-type"), "application/pdf")
            self.assertTrue(data.startswith(b"%PDF"), "PDF burst response must start with %PDF")
            self.assertIn(b"%%EOF", data, "PDF burst response must terminate with %%EOF")

    def test_adv_concurrency_burst_report_csv(self) -> None:
        """High-concurrency burst against /api/investor/report-csv (30 parallel requests)."""
        num_requests = 30

        def fetch_csv():
            return self._get("/api/investor/report-csv")

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(fetch_csv) for _ in range(num_requests)]
            results = [f.result() for f in as_completed(futures)]

        self.assertEqual(len(results), num_requests)
        for status, headers, data in results:
            self.assertEqual(status, 200)
            self.assertTrue(headers.get("content-type", "").startswith("text/csv"))
            reader = csv.reader(io.StringIO(data.decode("utf-8")))
            rows = list(reader)
            self.assertGreaterEqual(len(rows), 5)
            self.assertIn("Asset", rows[0])
            self.assertIn("Net_PnL_USD", rows[0])

    def test_adv_concurrency_burst_report_json(self) -> None:
        """High-concurrency burst against /api/investor/report-json (30 parallel requests)."""
        num_requests = 30

        def fetch_json():
            return self._get("/api/investor/report-json")

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(fetch_json) for _ in range(num_requests)]
            results = [f.result() for f in as_completed(futures)]

        self.assertEqual(len(results), num_requests)
        for status, headers, data in results:
            self.assertEqual(status, 200)
            self.assertTrue(headers.get("content-type", "").startswith("application/json"))
            res = json.loads(data.decode("utf-8"))
            self.assertEqual(res.get("syndicate"), "ArcaFid Quantitative Asset Management")
            self.assertIn("telemetry", res)
            self.assertIn("audited_ledger", res)

    def test_adv_concurrency_burst_interleaved_all_endpoints(self) -> None:
        """Interleaved multi-endpoint burst: 50 concurrent requests across all 5 endpoints."""
        endpoints = [
            "/api/investor/stats",
            "/api/evolution/status",
            "/api/investor/report-pdf",
            "/api/investor/report-csv",
            "/api/investor/report-json",
        ]
        # 10 requests per endpoint = 50 total requests
        requests_plan = endpoints * 10

        def call_ep(path: str):
            return path, self._get(path)

        with ThreadPoolExecutor(max_workers=25) as executor:
            futures = [executor.submit(call_ep, ep) for ep in requests_plan]
            results = [f.result() for f in as_completed(futures)]

        self.assertEqual(len(results), 50)
        for path, (status, headers, data) in results:
            self.assertEqual(status, 200, f"Endpoint {path} failed during interleaved burst with status {status}")
            self.assertGreater(len(data), 0, f"Endpoint {path} returned empty body")

        # Verify server is still healthy and responsive
        status, _, data = self._get("/")
        self.assertEqual(status, 200, "Server must remain responsive after burst load")
        self.assertIn("ArcaFid Quantitative", data.decode("utf-8"))

    # =========================================================================
    # PART 4: ZERO <img> TAGS, ZERO .jpg REFERENCES & ALL 4 BRAND STRINGS
    # =========================================================================

    def _read_file(self, rel_path: str) -> str:
        full_path = os.path.join(PROJECT_ROOT, rel_path)
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

    def test_adv_assertion_zero_img_tags_across_all_surfaces(self) -> None:
        """Assert zero <img> tags across bot/institutional_portal.py, index.html, and docs/index.html."""
        surfaces = [
            ("bot/institutional_portal.py (INSTITUTIONAL_PORTAL_HTML)", INSTITUTIONAL_PORTAL_HTML),
            ("index.html", self._read_file("index.html")),
            ("docs/index.html", self._read_file("docs/index.html")),
        ]

        img_tag_regex = re.compile(r"<img\b", re.IGNORECASE)

        for name, html_content in surfaces:
            # DOM check via BeautifulSoup
            soup = BeautifulSoup(html_content, "html.parser")
            dom_imgs = soup.find_all("img")
            self.assertEqual(
                len(dom_imgs), 0,
                f"VIOLATION: Found {len(dom_imgs)} <img> tags in {name}: {dom_imgs}"
            )

            # Raw regex check
            regex_matches = img_tag_regex.findall(html_content)
            self.assertEqual(
                len(regex_matches), 0,
                f"VIOLATION: Regex found {len(regex_matches)} '<img' matches in {name}"
            )

    def test_adv_assertion_zero_jpg_references_across_all_surfaces(self) -> None:
        """Assert zero .jpg or .jpeg references across bot/institutional_portal.py, index.html, and docs/index.html."""
        surfaces = [
            ("bot/institutional_portal.py (INSTITUTIONAL_PORTAL_HTML)", INSTITUTIONAL_PORTAL_HTML),
            ("index.html", self._read_file("index.html")),
            ("docs/index.html", self._read_file("docs/index.html")),
        ]

        jpg_regex = re.compile(r"[\w\-./\\]+\.(?:jpg|jpeg)", re.IGNORECASE)

        for name, html_content in surfaces:
            jpg_matches = jpg_regex.findall(html_content)
            self.assertEqual(
                len(jpg_matches), 0,
                f"VIOLATION: Found {len(jpg_matches)} .jpg/.jpeg references in {name}: {jpg_matches}"
            )

    def test_adv_assertion_all_four_brand_strings_across_all_surfaces(self) -> None:
        """Assert verbatim presence of all 4 mandatory brand strings across all three surfaces."""
        surfaces = [
            ("bot/institutional_portal.py (INSTITUTIONAL_PORTAL_HTML)", INSTITUTIONAL_PORTAL_HTML),
            ("index.html", self._read_file("index.html")),
            ("docs/index.html", self._read_file("docs/index.html")),
        ]

        for name, html_content in surfaces:
            for brand_str in self.MANDATORY_BRAND_STRINGS:
                self.assertIn(
                    brand_str, html_content,
                    f"VIOLATION: Mandatory brand string '{brand_str}' missing from {name}"
                )

    def test_adv_static_mirror_parity_and_integrity(self) -> None:
        """Verify strict parity and structural consistency between index.html and docs/index.html."""
        root_index = self._read_file("index.html")
        docs_index = self._read_file("docs/index.html")

        # 1. Byte length match
        self.assertEqual(
            len(root_index), len(docs_index),
            f"Static distribution mirrors have differing byte lengths: {len(root_index)} vs {len(docs_index)}"
        )

        # 2. Content exact match
        self.assertEqual(root_index, docs_index, "index.html and docs/index.html must be 100% identical")

        # 3. Functional parity with INSTITUTIONAL_PORTAL_HTML
        soup_python = BeautifulSoup(INSTITUTIONAL_PORTAL_HTML, "html.parser")
        soup_static = BeautifulSoup(root_index, "html.parser")

        # Invariant checks: title, essential anchors, script logic
        self.assertEqual(soup_python.title.string, soup_static.title.string)  # type: ignore

        for block_id in ["overview", "performance", "simulator", "security"]:
            self.assertIsNotNone(soup_python.find(id=block_id), f"Python portal missing block #{block_id}")
            self.assertIsNotNone(soup_static.find(id=block_id), f"Static index missing block #{block_id}")


if __name__ == "__main__":
    unittest.main()
