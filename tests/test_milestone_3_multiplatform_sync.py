"""
Milestone 3 Verification Test Suite: Multi-Platform Sync & Static Parity.

Verifies:
1. Static Distribution Mirrors:
   - Mirror INSTITUTIONAL_PORTAL_HTML into index.html and docs/index.html.
   - Purge 100% of <img> tags and .jpg/.png decorative file references.
   - Validate zinc monochrome palette (#09090b, #111215, #14161b), 1px micro-borders, #f4f4f5 white CTA button.
   - Validate dual typography Inter + JetBrains Mono with font-feature-settings: "tnum" 1, "zero" 1.
   - Validate 4-block architecture (#overview/#summary, #performance, #simulator/#calculator, #security/#audit).
   - Validate client-side blob fallback mechanism exportClientBlob(type) for offline GitHub Pages execution.
   - Validate 4 mandatory brand invariant strings preserved verbatim in both static files.
2. Multi-Platform Server Endpoints:
   - Local institutional portal on port 8765 responds 200 OK.
   - Unified panel / admin terminal on port 8770 responds 200 OK (both / and /admin).
   - Static file server simulating GitHub Pages serving docs/index.html responds 200 OK.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import re
import socket
import sys
import threading
import time
import unittest
from http.client import HTTPConnection
from typing import Optional

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
from bot.app_dashboard import DASHBOARD_HTML


class TestMilestone3MultiPlatformSync(unittest.TestCase):
    """Test suite verifying Milestone 3 Multi-Platform Parity and Server Endpoints."""

    server_8765: Optional[ThreadingHTTPServer] = None
    thread_8765: Optional[threading.Thread] = None
    server_8770: Optional[ThreadingHTTPServer] = None
    thread_8770: Optional[threading.Thread] = None
    server_static: Optional[ThreadingHTTPServer] = None
    thread_static: Optional[threading.Thread] = None
    static_port: int = 0

    MANDATORY_BRAND_STRINGS = [
        "ArcaFid Quantitative",
        "Mandatos & Estructuras de Inversión",
        "Simulador Cuantitativo de Retornos",
        "Ratio Sharpe",
    ]

    @classmethod
    def setUpClass(cls) -> None:
        """Start servers on ports 8765, 8770, and dynamic static serving port if not already running."""
        cls.cfg = BotConfig.from_env()

        # 1. Setup port 8765 server
        if not cls._is_port_in_use(8765):
            ThreadingHTTPServer.allow_reuse_address = True
            cls.server_8765 = ThreadingHTTPServer(("127.0.0.1", 8765), InstitutionalPortalHandler)
            cls.server_8765.cfg = cls.cfg  # type: ignore
            cls.thread_8765 = threading.Thread(target=cls.server_8765.serve_forever, daemon=True)
            cls.thread_8765.start()

        # 2. Setup port 8770 server
        if not cls._is_port_in_use(8770):
            ThreadingHTTPServer.allow_reuse_address = True
            cls.server_8770 = ThreadingHTTPServer(("127.0.0.1", 8770), InstitutionalPortalHandler)
            cls.server_8770.cfg = cls.cfg  # type: ignore
            cls.thread_8770 = threading.Thread(target=cls.server_8770.serve_forever, daemon=True)
            cls.thread_8770.start()

        # 3. Setup static file server simulating GitHub Pages rooted at docs/
        docs_dir = os.path.join(PROJECT_ROOT, "docs")
        handler_cls = functools.partial(http.server.SimpleHTTPRequestHandler, directory=docs_dir)
        cls.server_static = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        cls.static_port = cls.server_static.server_address[1]
        cls.thread_static = threading.Thread(target=cls.server_static.serve_forever, daemon=True)
        cls.thread_static.start()

        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls) -> None:
        """Clean up server daemons and release sockets."""
        if cls.server_8765:
            try:
                cls.server_8765.shutdown()
                cls.server_8765.server_close()
            except Exception:
                pass
        if cls.server_8770:
            try:
                cls.server_8770.shutdown()
                cls.server_8770.server_close()
            except Exception:
                pass
        if cls.server_static:
            try:
                cls.server_static.shutdown()
                cls.server_static.server_close()
            except Exception:
                pass

    @staticmethod
    def _is_port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) == 0

    # -------------------------------------------------------------------------
    # PART 1: Static Distribution Mirror Parity Tests
    # -------------------------------------------------------------------------

    def test_static_mirrors_identical_to_institutional_portal_html(self) -> None:
        """Verify index.html and docs/index.html exist and match INSTITUTIONAL_PORTAL_HTML."""
        index_path = os.path.join(PROJECT_ROOT, "index.html")
        docs_path = os.path.join(PROJECT_ROOT, "docs", "index.html")

        self.assertTrue(os.path.exists(index_path), "Root index.html must exist")
        self.assertTrue(os.path.exists(docs_path), "docs/index.html must exist")

        with open(index_path, "r", encoding="utf-8") as f:
            index_content = f.read()
        with open(docs_path, "r", encoding="utf-8") as f:
            docs_content = f.read()

        self.assertEqual(index_content, INSTITUTIONAL_PORTAL_HTML, "index.html must mirror INSTITUTIONAL_PORTAL_HTML")
        self.assertEqual(docs_content, INSTITUTIONAL_PORTAL_HTML, "docs/index.html must mirror INSTITUTIONAL_PORTAL_HTML")

    def test_static_mirrors_zero_img_tags(self) -> None:
        """Verify 0 <img> tags exist in index.html and docs/index.html."""
        for path in ["index.html", os.path.join("docs", "index.html")]:
            with open(os.path.join(PROJECT_ROOT, path), "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
            imgs = soup.find_all("img")
            self.assertEqual(
                len(imgs), 0,
                f"Expected 0 <img> tags in {path}, found: {imgs}"
            )

    def test_static_mirrors_zero_jpg_references(self) -> None:
        """Verify 0 .jpg references exist in index.html and docs/index.html."""
        jpg_regex = re.compile(r'[\w\-./\\]+\.jpg', re.IGNORECASE)
        for path in ["index.html", os.path.join("docs", "index.html")]:
            with open(os.path.join(PROJECT_ROOT, path), "r", encoding="utf-8") as f:
                content = f.read()
            matches = jpg_regex.findall(content)
            self.assertEqual(
                len(matches), 0,
                f"Expected 0 .jpg references in {path}, found: {matches}"
            )

    def test_static_mirrors_zero_decorative_image_files(self) -> None:
        """Verify deleted stock image files are absent from static asset directories."""
        purged_files = [
            "trading_floor.jpg",
            "hft_datacenter.jpg",
            "stock_exchange.jpg",
            "security_vault.jpg",
            "vip_banner.jpg",
        ]
        for base_dir in ["static", os.path.join("docs", "static")]:
            img_dir = os.path.join(PROJECT_ROOT, base_dir, "images")
            for fname in purged_files:
                target = os.path.join(img_dir, fname)
                self.assertFalse(
                    os.path.exists(target),
                    f"Deleted image {fname} must not exist at {target}"
                )

    def test_static_mirrors_mandatory_brand_strings(self) -> None:
        """Verify all 4 brand invariant strings are preserved verbatim in both static files."""
        for path in ["index.html", os.path.join("docs", "index.html")]:
            with open(os.path.join(PROJECT_ROOT, path), "r", encoding="utf-8") as f:
                content = f.read()
            for brand in self.MANDATORY_BRAND_STRINGS:
                self.assertIn(
                    brand, content,
                    f"Mandatory brand string '{brand}' missing from {path}"
                )

    def test_static_mirrors_zinc_monochrome_design_system(self) -> None:
        """Verify zinc monochrome tokens, micro-borders, white button, and dual typography."""
        for path in ["index.html", os.path.join("docs", "index.html")]:
            with open(os.path.join(PROJECT_ROOT, path), "r", encoding="utf-8") as f:
                content = f.read()

            # Zinc palette tokens
            self.assertIn("#09090b", content, f"--bg-base #09090b missing in {path}")
            self.assertIn("#111215", content, f"--bg-surface #111215 missing in {path}")
            self.assertIn("#14161b", content, f"--bg-card #14161b missing in {path}")

            # Micro-borders 1px
            self.assertIn("rgba(255, 255, 255, 0.08)", content, f"1px micro-border missing in {path}")

            # Solid white CTA accent button
            self.assertIn("#f4f4f5", content, f"Accent #f4f4f5 missing in {path}")

            # Dual typography and tabular figures
            self.assertIn("Inter", content, f"Inter font family missing in {path}")
            self.assertIn("JetBrains Mono", content, f"JetBrains Mono font family missing in {path}")
            self.assertIn('"tnum" 1', content, f'CSS tabular figures "tnum" 1 missing in {path}')
            self.assertIn('"zero" 1', content, f'CSS slashed zero "zero" 1 missing in {path}')

    def test_static_mirrors_four_block_architecture(self) -> None:
        """Verify 4-block layout anchors and sections are present in both static files."""
        for path in ["index.html", os.path.join("docs", "index.html")]:
            with open(os.path.join(PROJECT_ROOT, path), "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f.read(), "html.parser")

            # Block 1: Overview & Executive Summary
            self.assertTrue(
                soup.find(id="overview") is not None and soup.find(id="summary") is not None,
                f"Block 1 (#overview / #summary) missing in {path}"
            )

            # Block 2: Historical Performance & Drawdown Matrix
            self.assertIsNotNone(
                soup.find(id="performance"),
                f"Block 2 (#performance) missing in {path}"
            )

            # Block 3: Actuarial Return Simulator
            self.assertTrue(
                soup.find(id="simulator") is not None or soup.find(id="calculator") is not None,
                f"Block 3 (#simulator / #calculator) missing in {path}"
            )

            # Block 4: Security & Audited Ledger
            self.assertTrue(
                soup.find(id="security") is not None or soup.find(id="audit") is not None,
                f"Block 4 (#security / #audit) missing in {path}"
            )

    def test_static_mirrors_client_blob_fallback_presence(self) -> None:
        """Verify exportClientBlob(type) is present and supports pdf, csv, and json with valid newline bytes."""
        for path in ["index.html", os.path.join("docs", "index.html")]:
            with open(os.path.join(PROJECT_ROOT, path), "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("function exportClientBlob(type)", content, f"exportClientBlob function missing in {path}")
            self.assertIn("application/pdf", content, f"PDF mimeType missing in client blob export in {path}")
            self.assertIn("text/csv", content, f"CSV mimeType missing in client blob export in {path}")
            self.assertIn("application/json", content, f"JSON mimeType missing in client blob export in {path}")
            self.assertIn("URL.createObjectURL(blob)", content, f"Blob object URL creation missing in {path}")
            # Ensure unescaped literal newline sequences are not present in client-side Blob generation
            self.assertNotIn(r".join('\\n')", content, f"Double-escaped newline in CSV join found in {path}")
            self.assertIn(r".join('\n')", content, f"Valid newline in CSV join missing in {path}")
            self.assertNotIn(r"%PDF-1.4\\n1 0 obj", content, f"Double-escaped newline in PDF template found in {path}")
            self.assertIn(r"%PDF-1.4\n1 0 obj", content, f"Valid newline in PDF template missing in {path}")

    # -------------------------------------------------------------------------
    # PART 2: Multi-Platform Server Verification Tests
    # -------------------------------------------------------------------------

    def test_local_institutional_portal_port_8765_responds_200(self) -> None:
        """Verify local institutional portal on port 8765 responds HTTP 200 OK."""
        conn = HTTPConnection("127.0.0.1", 8765, timeout=5)
        conn.request("GET", "/")
        res = conn.getresponse()
        self.assertEqual(res.status, 200, "Port 8765 GET / must respond 200 OK")
        body = res.read().decode("utf-8")
        conn.close()

        for brand in self.MANDATORY_BRAND_STRINGS:
            self.assertIn(brand, body, f"Port 8765 response missing brand string: {brand}")

    def test_unified_panel_and_admin_port_8770_responds_200(self) -> None:
        """Verify unified panel / admin terminal on port 8770 responds HTTP 200 OK."""
        # 1. Main institutional portal view on port 8770
        conn = HTTPConnection("127.0.0.1", 8770, timeout=5)
        conn.request("GET", "/")
        res = conn.getresponse()
        self.assertEqual(res.status, 200, "Port 8770 GET / must respond 200 OK")
        body = res.read().decode("utf-8")
        conn.close()
        self.assertIn("ArcaFid Quantitative", body)

        # 2. Admin workstation terminal view on port 8770 (/admin)
        conn = HTTPConnection("127.0.0.1", 8770, timeout=5)
        conn.request("GET", "/admin")
        res_admin = conn.getresponse()
        self.assertEqual(res_admin.status, 200, "Port 8770 GET /admin must respond 200 OK")
        admin_body = res_admin.read().decode("utf-8")
        conn.close()
        self.assertIn("TERMINAL OPERATIVO", admin_body)

    def test_static_file_serving_docs_index_html_responds_200(self) -> None:
        """Verify static file serving of docs/index.html (GitHub Pages simulation) responds 200 OK."""
        conn = HTTPConnection("127.0.0.1", self.static_port, timeout=5)
        conn.request("GET", "/index.html")
        res = conn.getresponse()
        self.assertEqual(res.status, 200, "Static docs server GET /index.html must respond 200 OK")
        content_type = res.getheader("Content-Type", "")
        self.assertIn("text/html", content_type, f"Expected text/html content-type, got: {content_type}")
        body = res.read().decode("utf-8")
        conn.close()

        for brand in self.MANDATORY_BRAND_STRINGS:
            self.assertIn(brand, body, f"Static docs server response missing brand string: {brand}")
        self.assertIn("exportClientBlob", body, "Static docs server response missing exportClientBlob")
        self.assertNotIn("<img", body, "Static docs server response must not contain <img> tags")


if __name__ == "__main__":
    unittest.main()
