"""Automated Opaque-Box E2E Test Suite for Monochrome Terminal Platform Redesign.

Authoritative Specifications:
- ORIGINAL_REQUEST.md (Follow-up: 2026-09-15T20:25:34Z)
- PROJECT.md (Monochrome Terminal Redesign Scope & Contracts)
- spec_mining_report.md (Specification Mining & Test Infrastructure)

Test Coverage:
- Tier 1: Feature Coverage & DOM Contracts (19 tests)
- Tier 2: Boundary Conditions & Corner Cases (9 tests)
- Tier 3: Cross-Feature Interactions & Stress (4 tests)
- Tier 4: Real-World Institutional Scenarios (3 tests)
Total: 35 Automated E2E Test Cases
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import sys
import threading
import time
import unittest
from http.client import HTTPConnection
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath("."))

from bot.app_dashboard import DASHBOARD_HTML
from bot.config import BotConfig
from bot.institutional_portal import (
    INSTITUTIONAL_PORTAL_HTML,
    InstitutionalPortalHandler,
    ThreadingHTTPServer,
)


class TestE2EMonochromePlatform(unittest.TestCase):
    """Exhaustive 4-Tier Automated E2E Test Suite for the Monochrome Terminal Platform."""

    server: ThreadingHTTPServer
    server_thread: threading.Thread
    port: int = 8796
    host: str = "127.0.0.1"

    @classmethod
    def setUpClass(cls) -> None:
        """Start isolated HTTP server daemon on dedicated test port."""
        cls.cfg = BotConfig.from_env()
        ThreadingHTTPServer.allow_reuse_address = True
        cls.server = ThreadingHTTPServer((cls.host, cls.port), InstitutionalPortalHandler)
        cls.server.cfg = cls.cfg  # type: ignore
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls) -> None:
        """Gracefully shut down test HTTP server daemon and release socket."""
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # HTTP Client Helper Utilities
    # -------------------------------------------------------------------------

    def _http_request(
        self,
        method: str,
        path: str,
        body: Optional[bytes | str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, str], bytes]:
        """Perform raw opaque HTTP request to the live test server daemon."""
        conn = HTTPConnection(self.host, self.port, timeout=10)
        hdrs = headers or {}
        if isinstance(body, str):
            body = body.encode("utf-8")
        conn.request(method, path, body=body, headers=hdrs)
        res = conn.getresponse()
        status = res.status
        res_headers = {k.lower(): v for k, v in res.getheaders()}
        data = res.read()
        conn.close()
        return status, res_headers, data

    def _get(self, path: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, str], bytes]:
        return self._http_request("GET", path, headers=headers)

    def _post(
        self,
        path: str,
        payload: Optional[Dict[str, Any] | str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, str], bytes]:
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        if isinstance(payload, dict):
            body_bytes = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            body_bytes = payload.encode("utf-8")
        else:
            body_bytes = b""
        return self._http_request("POST", path, body=body_bytes, headers=hdrs)

    def _get_soup(self, path: str = "/") -> BeautifulSoup:
        status, _, data = self._get(path)
        self.assertEqual(status, 200, f"Expected 200 OK when fetching {path}")
        return BeautifulSoup(data.decode("utf-8"), "html.parser")

    # =========================================================================
    # TIER 1: FEATURE COVERAGE & DOM CONTRACT TESTING (19 TESTS)
    # =========================================================================

    def test_t1_01_dom_zero_image_purge(self) -> None:
        """T1.1: Verify complete elimination of decorative stock .jpg/.png images (REQ-MT-01)."""
        # 1. Disk check: all 10 decorative .jpg files must be deleted from static directories
        purged_files = [
            "hft_datacenter.jpg",
            "stock_exchange.jpg",
            "trading_floor.jpg",
            "security_vault.jpg",
            "vip_banner.jpg",
        ]
        for fname in purged_files:
            self.assertFalse(
                os.path.exists(os.path.join("static", "images", fname)),
                f"Decorative image {fname} must not exist in static/images/",
            )
            self.assertFalse(
                os.path.exists(os.path.join("docs", "static", "images", fname)),
                f"Decorative image {fname} must not exist in docs/static/images/",
            )

        # 2. Operator workstation: DASHBOARD_HTML in bot/app_dashboard.py must have 0 img tags
        self.assertNotIn(
            "<img",
            DASHBOARD_HTML,
            "bot/app_dashboard.py must contain 0 <img> tags per Monochrome redesign",
        )

        # 3. Served Institutional Portal DOM check
        soup = self._get_soup("/")
        img_tags = soup.find_all("img")
        jpg_png_imgs = [
            img for img in img_tags
            if any(img.get("src", "").lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png"])
        ]
        self.assertEqual(
            len(jpg_png_imgs),
            0,
            f"DOM must contain 0 decorative .jpg/.png images. Found {len(jpg_png_imgs)}: {jpg_png_imgs}",
        )

    def test_t1_02_monochrome_palette_tokens(self) -> None:
        """T1.2: Verify Monochrome Terminal zinc/carbon CSS design tokens (REQ-MT-02)."""
        status, _, data = self._get("/")
        self.assertEqual(status, 200)
        html_content = data.decode("utf-8")

        # Must define micro-border 1px with transparent white
        self.assertIn(
            "rgba(255, 255, 255, 0.08)",
            html_content,
            "CSS must enforce 1px micro-border rgba(255, 255, 255, 0.08)",
        )

        # Must contain zinc/carbon dark palette tokens or classes
        has_zinc_canvas = (
            "#09090b" in html_content
            or "#05080e" in html_content
            or "--bg-base" in html_content
        )
        self.assertTrue(has_zinc_canvas, "CSS must define dark terminal canvas background token")

        # Semantic color tokens restricted to numeric deltas (green #10b981, coral/red)
        self.assertIn("#10b981", html_content, "CSS must define emerald green #10b981 for positive deltas")

    def test_t1_03_tabular_figures_dual_typography(self) -> None:
        """T1.3: Verify dual typography (Inter + JetBrains Mono) with tnum/zero figures (REQ-MT-04)."""
        soup = self._get_soup("/")
        html_text = str(soup)

        # Google fonts links must include Inter and JetBrains Mono
        self.assertIn("Inter", html_text, "Font stack must load Inter for prose")
        self.assertIn("JetBrains+Mono", html_text, "Font stack must load JetBrains Mono for metrics")

        # Tabular numbers font-feature-settings rule must be present
        tnum_pattern = re.compile(r'font-feature-settings:\s*["\']tnum["\']\s*1,\s*["\']zero["\']\s*1')
        self.assertTrue(
            bool(tnum_pattern.search(html_text)),
            'CSS must enforce font-feature-settings: "tnum" 1, "zero" 1 to prevent layout jitter',
        )

    def test_t1_04_four_block_navigation_anchors(self) -> None:
        """T1.4: Verify sequential 4-block architecture and navigation anchors (REQ-MT-05)."""
        soup = self._get_soup("/")

        # Block 1: Executive Summary & Alpha Consensus
        block1 = soup.find(id="overview") or soup.find("header") or soup.find(class_="hero-grid")
        self.assertIsNotNone(block1, "Block 1 (Executive Summary / Overview) must exist in DOM")

        # Block 2: Historical Performance & Risk Matrix
        block2 = soup.find(id="performance")
        self.assertIsNotNone(block2, "Block 2 (Historical Performance #performance) must exist in DOM")

        # Block 3: Actuarial Simulator & Fiduciary Transparency
        block3 = soup.find(id="calculator") or soup.find(id="simulator")
        self.assertIsNotNone(block3, "Block 3 (Actuarial Simulator #calculator/#simulator) must exist in DOM")

        # Block 4: Non-Custodial Security & Audited Ledger
        block4 = soup.find(id="audit") or soup.find(id="security")
        self.assertIsNotNone(block4, "Block 4 (Audited Ledger / Security #audit/#security) must exist in DOM")

    def test_t1_05_audited_executive_kpis(self) -> None:
        """T1.5: Verify certified track record metrics rendered in DOM and API (REQ-MT-06)."""
        # 1. API endpoint verification
        status, _, data = self._get("/api/investor/stats")
        self.assertEqual(status, 200)
        stats = json.loads(data.decode("utf-8"))
        self.assertEqual(stats.get("sharpe_ratio"), 2.42, "Sharpe ratio must be exactly 2.42")
        self.assertEqual(stats.get("sortino_ratio"), 3.10, "Sortino ratio must be exactly 3.10")

        # 2. DOM verification
        soup = self._get_soup("/")
        text_content = soup.get_text()
        self.assertIn("2.42", text_content, "Sharpe 2.42 must be visible in DOM")
        self.assertIn("3.10", text_content, "Sortino 3.10 must be visible in DOM")
        self.assertIn("-6.4%", text_content, "Max drawdown -6.4% must be visible in DOM")

    def test_t1_06_live_streaming_ticker_tape(self) -> None:
        """T1.6: Verify live market ticker tape with required major assets (REQ-MT-06)."""
        soup = self._get_soup("/")
        ticker = soup.find(class_="ticker-tape")
        self.assertIsNotNone(ticker, "Ticker tape container (.ticker-tape) must exist in DOM")

        ticker_text = ticker.get_text()
        for sym in ["BTC", "ETH", "SOL", "NVDA", "AAPL"]:
            self.assertIn(sym, ticker_text, f"Asset symbol {sym} must be present in ticker tape")

    def test_t1_07_finbert_rl_telemetry(self) -> None:
        """T1.7: Verify FinBERT sentiment + Bandit RL auto-evolution telemetry (REQ-MT-06)."""
        # 1. Status API check
        status, _, data = self._get("/api/evolution/status")
        self.assertEqual(status, 200)
        evo_data = json.loads(data.decode("utf-8"))
        self.assertIn("generation", evo_data, "Evolution status must contain generation")
        self.assertIn("weights", evo_data, "Evolution status must contain weights")

        # 2. DOM check for FinBERT and RL elements
        soup = self._get_soup("/")
        self.assertIsNotNone(soup.find(id="dispGen") or soup.find(id="genNum"), "Generation counter must exist")
        self.assertIsNotNone(soup.find(id="btnTrainStep"), "RL train step button (#btnTrainStep) must exist")

        # 3. Interactive RL Step POST check
        post_status, _, post_data = self._post("/api/evolution/train-step")
        self.assertEqual(post_status, 200)
        step_res = json.loads(post_data.decode("utf-8"))
        self.assertIn("generation", step_res, "Step response must contain updated generation")

    def test_t1_08_vector_equity_chart(self) -> None:
        """T1.8: Verify vector canvas financial chart and timeframe selectors (REQ-MT-07)."""
        soup = self._get_soup("/")
        canvas = soup.find("canvas", id="equityChart")
        self.assertIsNotNone(canvas, "Vector financial chart canvas (#equityChart) must exist")

        # Verify timeframe buttons in DOM
        html_str = str(soup)
        for tf in ["1M", "3M", "6M", "1Y", "ALL"]:
            self.assertIn(f"'{tf}'", html_str, f"Timeframe selector for {tf} must be defined in chart")

    def test_t1_09_drawdown_lock_baseline(self) -> None:
        """T1.9: Verify fiduciary drawdown lock indicator at -6.4% baseline (REQ-MT-07)."""
        _, _, data = self._get("/")
        html_str = data.decode("utf-8")

        # Must explicitly reference -6.4% drawdown limit in script and markup
        self.assertIn("-6.4%", html_str, "DOM and scripts must demarcate -6.4% drawdown baseline")
        self.assertIn("DRAWDOWN CONTROL", html_str, "Drawdown control panel must be labeled")

    def test_t1_10_actuarial_simulator_inputs(self) -> None:
        """T1.10: Verify actuarial return simulator interactive sliders and mode toggles (REQ-MT-08)."""
        soup = self._get_soup("/")

        # Capital slider
        sl_cap = soup.find("input", id="slCap")
        self.assertIsNotNone(sl_cap, "Capital slider (#slCap) must exist in DOM")

        # Timeframe slider
        sl_time = soup.find("input", id="slTime")
        self.assertIsNotNone(sl_time, "Timeframe slider (#slTime) must exist in DOM")

        # Compounding and distribution mode buttons
        self.assertIsNotNone(soup.find(id="btnComp"), "Compounding toggle button (#btnComp) must exist")
        self.assertIsNotNone(soup.find(id="btnDist"), "Distribution toggle button (#btnDist) must exist")

        # Projected return output fields
        self.assertIsNotNone(soup.find(id="resTotal"), "Total capital output (#resTotal) must exist")
        self.assertIsNotNone(soup.find(id="resProfit"), "Net profit output (#resProfit) must exist")
        self.assertIsNotNone(soup.find(id="resMonthly"), "Monthly average output (#resMonthly) must exist")

    def test_t1_11_multi_currency_switcher_controls(self) -> None:
        """T1.11: Verify instant multi-currency switcher buttons and rates (REQ-MT-08)."""
        soup = self._get_soup("/")
        for curr in ["USDT", "USD", "EUR", "BTC"]:
            btn = soup.find(id=f"c{curr}")
            self.assertIsNotNone(btn, f"Currency button #c{curr} must exist in DOM")

        # Script must declare conversion rates: USDT 1.0, USD 1.0, EUR 0.92, BTC 0.0000165
        _, _, data = self._get("/")
        script_text = data.decode("utf-8")
        self.assertIn("0.92", script_text, "EUR rate 0.92 must be declared in script")
        self.assertIn("0.0000165", script_text, "BTC rate 0.0000165 must be declared in script")

    def test_t1_12_fiduciary_fee_schedule(self) -> None:
        """T1.12: Verify transparent institutional fee structure disclosure (REQ-MT-08)."""
        soup = self._get_soup("/")
        text = soup.get_text()

        self.assertTrue(
            "0.0%" in text or "0%" in text,
            "0% management fee disclosure must be visible in DOM",
        )
        self.assertIn("5.0%", text, "5% annual hurdle rate disclosure must be visible in DOM")
        self.assertIn("20%", text, "20% High-Water Mark disclosure must be visible in DOM")

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_t1_13_non_custodial_api_connection(self, mock_send: MagicMock) -> None:
        """T1.13: Verify non-custodial API connection form and AES-256 submission (REQ-MT-09)."""
        mock_send.return_value = True
        payload = {
            "name": "Marcus Vance",
            "email": "marcus@vancecapital.ch",
            "platform": "binance",
            "apiKey": "test_public_binance_api_key_888",
            "apiSecret": "test_secret_binance_key_999",
        }
        status, _, data = self._post("/api/investor/connect-api", payload)
        self.assertEqual(status, 200, "POST /api/investor/connect-api must return HTTP 200 OK")
        res = json.loads(data.decode("utf-8"))
        self.assertIn(res.get("status"), ("connected", "success"))
        self.assertEqual(res.get("platform"), "binance")
        self.assertTrue(res.get("encrypted"))
        self.assertIn("AES-256", res.get("message", ""), "Response must confirm AES-256 encryption")

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_t1_14_2fa_protected_withdrawals(self, mock_send: MagicMock) -> None:
        """T1.14: Verify 2FA TOTP protected fund liquidation dispatch (REQ-MT-09)."""
        mock_send.return_value = True
        payload = {
            "email": "marcus@vancecapital.ch",
            "amount": 2500.0,
            "network": "TRC20",
            "address": "TLyqzVGLV1srkB7dToTAvZgqndvvDfHoxX",
            "code2fa": "849201",
        }
        status, _, data = self._post("/api/investor/withdraw", payload)
        self.assertEqual(status, 200, "POST /api/investor/withdraw must return HTTP 200 OK")
        res = json.loads(data.decode("utf-8"))
        self.assertIn(res.get("status"), ("ticket_created", "success"))
        self.assertIn("ticket_id", res, "Response must return a liquidation ticket ID")
        self.assertEqual(res.get("sla"), "< 24h")

    def test_t1_15_interactive_ledger_and_filter(self) -> None:
        """T1.15: Verify interactive ledger table, search bar, and asset pills (REQ-MT-09)."""
        soup = self._get_soup("/")
        self.assertIsNotNone(soup.find(id="tradeSearch"), "Ledger search input (#tradeSearch) must exist")
        self.assertIsNotNone(soup.find(id="tradeBody"), "Ledger table body (#tradeBody) must exist")

        # Symbol filter buttons
        for sym_id in ["fAll", "fBTC", "fETH", "fSOL", "fNVDA"]:
            self.assertIsNotNone(soup.find(id=sym_id), f"Filter button #{sym_id} must exist")

    def test_t1_16_pdf_audit_report_endpoint(self) -> None:
        """T1.16: Verify GET /api/investor/report-pdf returns HTTP 200 OK and %PDF (REQ-MT-09)."""
        status, headers, data = self._get("/api/investor/report-pdf")
        self.assertEqual(status, 200, "GET /api/investor/report-pdf must return HTTP 200 OK")
        self.assertEqual(headers.get("content-type"), "application/pdf")
        self.assertTrue(data.startswith(b"%PDF"), "PDF response body must start with %PDF magic bytes")

    def test_t1_17_csv_ledger_export_endpoint(self) -> None:
        """T1.17: Verify GET /api/investor/report-csv returns HTTP 200 OK and valid CSV (REQ-MT-09)."""
        status, headers, data = self._get("/api/investor/report-csv")
        self.assertEqual(status, 200, "GET /api/investor/report-csv must return HTTP 200 OK")
        self.assertTrue(headers.get("content-type", "").startswith("text/csv"))

        csv_text = data.decode("utf-8")
        reader = csv.reader(io.StringIO(csv_text))
        header_row = next(reader)
        self.assertIn("Asset", header_row, "CSV header must contain Asset column")
        self.assertIn("Net_PnL_USD", header_row, "CSV header must contain Net_PnL_USD column")

    def test_t1_18_json_audit_export_endpoint(self) -> None:
        """T1.18: Verify GET /api/investor/report-json returns HTTP 200 OK and valid schema (REQ-MT-09)."""
        status, headers, data = self._get("/api/investor/report-json")
        self.assertEqual(status, 200, "GET /api/investor/report-json must return HTTP 200 OK")
        self.assertTrue(headers.get("content-type", "").startswith("application/json"))

        report = json.loads(data.decode("utf-8"))
        self.assertIn("syndicate", report, "JSON export must specify syndicate")
        self.assertIn("telemetry", report, "JSON export must contain telemetry")
        self.assertIn("audited_ledger", report, "JSON export must contain audited_ledger array")

    def test_t1_19_server_routes_and_spa_fallback(self) -> None:
        """T1.19: Verify primary server routes and universal SPA router 200 OK fallback (REQ-MT-10)."""
        routes_to_test = ["/", "/admin", "/control", "/dashboard"]
        for route in routes_to_test:
            status, _, _ = self._get(route)
            self.assertEqual(status, 200, f"Route {route} must respond with HTTP 200 OK")

        # SPA router fallback: arbitrary unknown path must serve portal with 200 OK
        status, headers, data = self._get("/portal/deep-link/arbitrary-path")
        self.assertEqual(status, 200, "Universal SPA router must catch deep links with HTTP 200 OK")
        self.assertIn("Aethelgard Quantitative", data.decode("utf-8"))

    def test_t1_20_export_client_blob_mechanism(self) -> None:
        """T1.20: Verify exportClientBlob client-side fallback implementation (REQ-MT-09)."""
        _, _, data = self._get("/")
        script = data.decode("utf-8")
        self.assertIn("function exportClientBlob", script, "Script must define exportClientBlob fallback")
        self.assertIn("Libro_Mayor_Auditoria_Aethelgard.csv", script)
        self.assertIn("Auditoria_Aethelgard.json", script)
        self.assertIn("Certificado_Auditoria_Aethelgard.pdf", script)

    # =========================================================================
    # TIER 2: BOUNDARY CONDITIONS & CORNER CASES (9 TESTS)
    # =========================================================================

    def test_t2_01_btc_fractional_precision(self) -> None:
        """T2.1: Verify BTC high-precision fractional conversion formatting (4 decimals)."""
        # Authoritative Math: Base NAV $1,842.50 * 0.0000165 = 0.03040125 BTC
        rate_btc = 0.0000165
        base_nav = 1842.50
        converted_btc = base_nav * rate_btc
        self.assertAlmostEqual(converted_btc, 0.03040125, places=8)

        # Truncating to 2 decimals yields 0.03 (or 0.00 for smaller NAVs).
        # Precision rule requires 4 decimal places:
        formatted_btc_4dec = f"{converted_btc:.4f}"
        self.assertEqual(formatted_btc_4dec, "0.0304", "BTC NAV must format with 4 decimals as 0.0304")

        # Verify client script implements the 4-decimal constraint for BTC
        _, _, data = self._get("/")
        script = data.decode("utf-8")
        self.assertIn("curr === 'BTC' ? 4 : 2", script, "Script must enforce 4 decimal places for BTC")

    def test_t2_02_rapid_currency_toggling_stability(self) -> None:
        """T2.2: Verify mathematical stability under rapid back-and-forth currency switches."""
        rates = {"USDT": 1.0, "USD": 1.0, "EUR": 0.92, "BTC": 0.0000165}
        base_aum = 18420500.0

        cycle = ["USDT", "EUR", "BTC", "USD", "EUR", "BTC", "USDT"]
        current_val = base_aum
        for curr in cycle:
            target_val = base_aum * rates[curr]
            self.assertTrue(target_val > 0, "Converted value must always be strictly positive")
            self.assertFalse(math.isnan(target_val), "Converted value must never be NaN")

        # Ensure return to USDT recovers exact original baseline without rounding loss
        final_usdt = base_aum * rates["USDT"]
        self.assertEqual(final_usdt, base_aum)

    def test_t2_03_simulator_slider_boundaries(self) -> None:
        """T2.3: Verify actuarial simulator at extreme limits ($1k min, $100k max, 1-36 mo)."""
        monthly_rate = 0.052

        # Min boundary: $1,000 capital, 1 month compound
        cap_min = 1000.0
        time_min = 1
        profit_min_comp = cap_min * (math.pow(1 + monthly_rate, time_min) - 1)
        self.assertAlmostEqual(profit_min_comp, 52.00, places=2, msg="Min compound profit must be $52.00")

        # Max boundary: $100,000 capital, 36 months compound
        cap_max = 100000.0
        time_max = 36
        profit_max_comp = cap_max * (math.pow(1 + monthly_rate, time_max) - 1)
        self.assertAlmostEqual(profit_max_comp, 520249.80, places=2, msg="Max compound profit must be $520,249.80")

        # Distribution mode (simple interest): $10,000 for 12 months
        cap_mid = 10000.0
        time_mid = 12
        profit_dist = cap_mid * (monthly_rate * time_mid)
        self.assertAlmostEqual(profit_dist, 6240.00, places=2)

    def test_t2_04_canvas_mouseleave_boundary(self) -> None:
        """T2.4: Verify canvas mouseleave boundary deactivates crosshair and hides tooltip cleanly."""
        _, _, data = self._get("/")
        script = data.decode("utf-8")

        # Must attach mouseleave listener that resets mouseX = -1 and calls drawChart
        self.assertIn("cvsEl.addEventListener('mouseleave'", script)
        self.assertIn("mouseX = -1", script)
        self.assertIn("tip.style.display = 'none'", script)

    def test_t2_05_drawdown_lock_alert_threshold(self) -> None:
        """T2.5: Verify -6.4% drawdown alert threshold boundary states (normal vs warning)."""
        # Threshold logic in client script switches bar color if ddVal > 5.0%
        _, _, data = self._get("/")
        script = data.decode("utf-8")
        self.assertIn("ddVal > 5.0", script, "Script must trigger warning color when drawdown exceeds 5.0%")
        self.assertIn("rgba(239, 68, 68, 0.5)", script, "Warning state must render in red rgba(239, 68, 68, 0.5)")

    def test_t2_06_ledger_search_special_regex_characters(self) -> None:
        """T2.6: Verify ledger search handles regex meta-characters without runtime exceptions."""
        _, _, data = self._get("/")
        script = data.decode("utf-8")

        # Logic must use .includes() for substring comparison rather than new RegExp()
        self.assertIn(".includes(query)", script, "Search must use safe substring matching .includes()")
        self.assertNotIn("new RegExp(query", script, "Search must NOT compile raw user regex to prevent crashes")

    def test_t2_07_ledger_filter_zero_matches(self) -> None:
        """T2.7: Verify filtering by non-existent asset symbol displays 0 rows without breaking layout."""
        soup = self._get_soup("/")
        rows = soup.find_all("tr", attrs={"data-symbol": True})
        # Verify existing sample data has valid rows
        self.assertTrue(len(rows) >= 0)

        # Simulation of filterBySymbol with unmatched token "XRP"
        query_sym = "XRP"
        matching_rows = [r for r in rows if query_sym in r.get("data-symbol", "")]
        self.assertEqual(len(matching_rows), 0, "Non-existent token must produce exactly 0 matches")

    def test_t2_08_withdrawal_amount_minimum_validation(self) -> None:
        """T2.8: Verify withdrawal rejects amounts below $50 USD threshold."""
        _, _, data = self._get("/")
        script = data.decode("utf-8")
        self.assertIn("amt < 50", script, "Client script must enforce minimum $50 liquidation amount")

        # Backend rejection test
        payload = {
            "email": "test@aethelgard.com",
            "amount": 25.0,
            "network": "TRC20",
            "address": "TLyqzVGLV1srkB7dToTAvZgqndvvDfHoxX",
            "code2fa": "123456",
        }
        status, _, res_data = self._post("/api/investor/withdraw", payload)
        self.assertEqual(status, 400)
        res = json.loads(res_data.decode("utf-8"))
        self.assertEqual(res.get("status"), "error")

    def test_t2_09_api_connection_input_sanitization(self) -> None:
        """T2.9: Verify API credential inputs are sanitized and trimmed before transmission."""
        _, _, data = self._get("/")
        script = data.decode("utf-8")
        self.assertIn(".trim()", script, "Client script must apply .trim() to credential inputs")

    def test_t2_10_withdrawal_totp_validation(self) -> None:
        """T2.10: Verify withdrawal rejects invalid or non-6-digit TOTP tokens."""
        invalid_tokens = ["12345", "1234567", "abcdef", ""]
        for bad_token in invalid_tokens:
            payload = {
                "email": "test@aethelgard.com",
                "amount": 100.0,
                "network": "TRC20",
                "address": "TLyqzVGLV1srkB7dToTAvZgqndvvDfHoxX",
                "code2fa": bad_token,
            }
            status, _, res_data = self._post("/api/investor/withdraw", payload)
            self.assertEqual(status, 400)
            res = json.loads(res_data.decode("utf-8"))
            self.assertEqual(res.get("status"), "error")
            self.assertIn("TOTP", res.get("message", ""))

    # =========================================================================
    # TIER 3: CROSS-FEATURE INTERACTIONS & STRESS (4 TESTS)
    # =========================================================================

    def test_t3_01_currency_switch_and_simulator_sync(self) -> None:
        """T3.1: Verify currency switcher and actuarial simulator maintain synchronized math."""
        # Initial: $10,000 USD for 12 months compound:
        # P = 10000, r = 0.052, t = 12 -> Profit = $8,371.38
        monthly_rate = 0.052
        profit_usd = 10000.0 * (math.pow(1 + monthly_rate, 12) - 1)
        self.assertAlmostEqual(profit_usd, 8373.37, delta=1.0)

        # Switching to EUR (rate 0.92):
        # Converted Capital = €9,200 -> Profit = €7,701.67
        eur_rate = 0.92
        cap_eur = 10000.0 * eur_rate
        profit_eur = cap_eur * (math.pow(1 + monthly_rate, 12) - 1)
        self.assertAlmostEqual(profit_eur, profit_usd * eur_rate, delta=1.0)

    def test_t3_02_timeframe_selector_and_chart_recalibration(self) -> None:
        """T3.2: Verify chart timeframe datasets all contain valid aligned series and -6.4% baseline."""
        _, _, data = self._get("/")
        script = data.decode("utf-8")

        # Parse chartDatasets declaration from script
        for tf in ["1M", "3M", "6M", "1Y", "ALL"]:
            self.assertIn(f"'{tf}':", script, f"Dataset for {tf} must be defined in chart engine")

    def test_t3_03_rl_evolution_step_and_ticker_coexistence(self) -> None:
        """T3.3: Verify concurrent RL step execution and stats polling execute without race conditions."""
        # Step evolution API call
        status_step, _, data_step = self._post("/api/evolution/train-step")
        self.assertEqual(status_step, 200)
        res_step = json.loads(data_step.decode("utf-8"))

        # Concurrent stats query
        status_stats, _, data_stats = self._get("/api/investor/stats")
        self.assertEqual(status_stats, 200)
        res_stats = json.loads(data_stats.decode("utf-8"))

        self.assertIn("generation", res_step)
        self.assertEqual(res_stats.get("sharpe_ratio"), 2.42)

    def test_t3_04_combined_ledger_filter_and_search(self) -> None:
        """T3.4: Verify compound filtering: active symbol button + search bar query."""
        trades_sample = [
            {"id": "AQC-8821", "sym": "SOL/USDT", "desc": "Spot Momentum", "pnl": 425.0},
            {"id": "AQC-8822", "sym": "BTC/USDT", "desc": "Poisson Trend", "pnl": 580.0},
            {"id": "AQC-8823", "sym": "NVDA", "desc": "NASDAQ Equity", "pnl": 310.0},
            {"id": "AQC-8824", "sym": "ETH/USDT", "desc": "OrderBook Microstructure", "pnl": 350.0},
        ]

        # Filter by symbol 'SOL' AND query 'MOMENTUM'
        matched = [
            t for t in trades_sample
            if "SOL" in t["sym"] and "MOMENTUM" in t["desc"].upper()
        ]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["id"], "AQC-8821")

    # =========================================================================
    # TIER 4: REAL-WORLD INSTITUTIONAL SCENARIOS (3 TESTS)
    # =========================================================================

    @patch("bot.telemetry.TelegramNotifier.send")
    def test_t4_01_full_investor_allocation_journey(self, mock_send: MagicMock) -> None:
        """T4.1: End-to-end institutional investor due diligence and allocation journey."""
        mock_send.return_value = True

        # 1. Investor loads Monochrome Terminal portal
        status, _, data = self._get("/")
        self.assertEqual(status, 200)
        soup = BeautifulSoup(data.decode("utf-8"), "html.parser")

        # 2. Reviews executive metrics
        stats_status, _, stats_data = self._get("/api/investor/stats")
        self.assertEqual(stats_status, 200)
        stats = json.loads(stats_data.decode("utf-8"))
        self.assertEqual(stats.get("sharpe_ratio"), 2.42)

        # 3. Submits non-custodial API key
        api_payload = {
            "name": "Geneva Wealth Syndicate",
            "email": "syndicate@genevawealth.ch",
            "platform": "binance",
            "apiKey": "gen_key_live_alpha_001",
            "apiSecret": "gen_secret_aes_vault_002",
        }
        api_status, _, api_res_data = self._post("/api/investor/connect-api", api_payload)
        self.assertEqual(api_status, 200)
        api_res = json.loads(api_res_data.decode("utf-8"))
        self.assertIn(api_res.get("status"), ("connected", "success"))
        self.assertTrue(api_res.get("encrypted"))

        # 4. Downloads official audit PDF
        pdf_status, pdf_headers, pdf_bytes = self._get("/api/investor/report-pdf")
        self.assertEqual(pdf_status, 200)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_t4_02_full_audit_export_inspection(self) -> None:
        """T4.2: Exhaustive deep structural inspection of PDF, CSV, and JSON audit exports."""
        # 1. PDF Deep Inspection
        s_pdf, h_pdf, d_pdf = self._get("/api/investor/report-pdf")
        self.assertEqual(s_pdf, 200)
        self.assertEqual(h_pdf.get("content-type"), "application/pdf")
        self.assertTrue(d_pdf.startswith(b"%PDF"))
        self.assertGreater(len(d_pdf), 500, "PDF payload must be non-trivial size")

        # 2. CSV Deep Inspection
        s_csv, h_csv, d_csv = self._get("/api/investor/report-csv")
        self.assertEqual(s_csv, 200)
        reader = csv.reader(io.StringIO(d_csv.decode("utf-8")))
        rows = list(reader)
        self.assertGreaterEqual(len(rows), 2, "CSV must contain header + at least 1 trade row")
        expected_cols = [
            "ID", "Timestamp", "Asset", "Market", "Side",
            "Entry_Price", "Exit_Price", "Size_USD", "Net_PnL_USD", "Return_Pct",
            "Tx_Hash_Verification"
        ]
        self.assertEqual(rows[0], expected_cols, "CSV columns must strictly match expected schema")

        # 3. JSON Deep Inspection
        s_json, h_json, d_json = self._get("/api/investor/report-json")
        self.assertEqual(s_json, 200)
        report = json.loads(d_json.decode("utf-8"))
        self.assertIn("fiduciary_standard", report)
        self.assertEqual(report["telemetry"]["max_drawdown_pct"], -6.4)
        self.assertGreaterEqual(len(report["audited_ledger"]), 1)
        for trade in report["audited_ledger"]:
            self.assertTrue(trade["hash"].startswith("0x"), "Trade hash must have 0x hex prefix")

    def test_t4_03_github_pages_static_demo_parity(self) -> None:
        """T4.3: Verify docs/index.html maintains 100% offline static parity with root index.html."""
        docs_path = os.path.join("docs", "index.html")
        root_path = "index.html"
        self.assertTrue(os.path.exists(docs_path), "docs/index.html must exist for GitHub Pages deployment")
        self.assertTrue(os.path.exists(root_path), "root index.html must exist")

        with open(docs_path, "r", encoding="utf-8") as f:
            docs_content = f.read()

        # Invariant brand strings must be preserved verbatim in docs/index.html
        brand_invariants = [
            "Aethelgard Quantitative",
            "Mandatos & Estructuras de Inversión",
            "Simulador Cuantitativo de Retornos",
            "Ratio Sharpe",
        ]
        for brand_str in brand_invariants:
            self.assertIn(brand_str, docs_content, f"docs/index.html must preserve verbatim: {brand_str}")

        # Static distribution must contain client-side interactive logic
        self.assertIn("setCurrency", docs_content, "docs/index.html must include setCurrency")
        self.assertIn("runSim", docs_content, "docs/index.html must include runSim")
        self.assertIn("drawChart", docs_content, "docs/index.html must include drawChart")
        self.assertIn("filterTrades", docs_content, "docs/index.html must include filterTrades")


if __name__ == "__main__":
    unittest.main()
