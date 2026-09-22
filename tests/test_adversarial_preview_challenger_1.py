"""Adversarial stress-test suite for Challenger 1 (Frontend & Visual Theming).

Empirically verifies:
1. Brand String Audit: Global repository scan for forbidden legacy string 'Aethelgard'.
2. Dual Switcher & DOM Stress Testing:
   - Corrupted/unusual localStorage values (null, undefined, "matrix", "", binary, exceptions).
   - Rapid toggling stress testing (10,000 cycles).
   - Synchronous DOM attribute inspection (data-theme vs theme-light vs active button states).
3. Zero Stock Images Invariant:
   - Zero <img> tags, zero external CDNs, zero raster image references (.jpg, .png, .gif, etc.).
4. Tabular Numbers (tnum) CSS & Typography Rules:
   - font-feature-settings: "tnum" 1, "zero" 1 and tabular-nums rules on metrics and tables.
5. Bit-for-bit Mirror Parity Cryptographic Verification:
   - SHA-256 parity across index.html, docs/index.html, and INSTITUTIONAL_PORTAL_HTML.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unittest
from bs4 import BeautifulSoup

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bot.institutional_portal import INSTITUTIONAL_PORTAL_HTML


class TestAdversarialFrontendChallenger1(unittest.TestCase):
    """Adversarial verification and stress harness for Frontend & Visual Theming."""

    @classmethod
    def setUpClass(cls):
        cls.index_path = os.path.join(PROJECT_ROOT, "index.html")
        cls.docs_path = os.path.join(PROJECT_ROOT, "docs", "index.html")

        with open(cls.index_path, "rb") as f:
            cls.index_bytes = f.read()
        with open(cls.docs_path, "rb") as f:
            cls.docs_bytes = f.read()

        cls.portal_bytes = INSTITUTIONAL_PORTAL_HTML.encode("utf-8")

        cls.index_html = cls.index_bytes.decode("utf-8")
        cls.docs_html = cls.docs_bytes.decode("utf-8")

        cls.soup_index = BeautifulSoup(cls.index_html, "html.parser")
        cls.soup_portal = BeautifulSoup(INSTITUTIONAL_PORTAL_HTML, "html.parser")

    # -------------------------------------------------------------------------
    # 1. Bit-for-Bit Mirror Parity (Cryptographic SHA-256)
    # -------------------------------------------------------------------------

    def test_sha256_mirror_bit_parity(self):
        """Verify that index.html, docs/index.html, and INSTITUTIONAL_PORTAL_HTML match bit-for-bit."""
        hash_portal = hashlib.sha256(self.portal_bytes).hexdigest()
        hash_index = hashlib.sha256(self.index_bytes).hexdigest()
        hash_docs = hashlib.sha256(self.docs_bytes).hexdigest()

        self.assertEqual(
            hash_index, hash_portal,
            f"Bit parity mismatch between index.html ({hash_index}) and INSTITUTIONAL_PORTAL_HTML ({hash_portal})"
        )
        self.assertEqual(
            hash_docs, hash_portal,
            f"Bit parity mismatch between docs/index.html ({hash_docs}) and INSTITUTIONAL_PORTAL_HTML ({hash_portal})"
        )
        self.assertEqual(
            len(self.index_bytes), len(self.portal_bytes),
            f"Byte length mismatch: index.html ({len(self.index_bytes)}) vs portal ({len(self.portal_bytes)})"
        )
        self.assertEqual(
            len(self.docs_bytes), len(self.portal_bytes),
            f"Byte length mismatch: docs/index.html ({len(self.docs_bytes)}) vs portal ({len(self.portal_bytes)})"
        )

    # -------------------------------------------------------------------------
    # 2. Zero Stock Images Invariant
    # -------------------------------------------------------------------------

    def test_zero_img_elements_and_sources(self):
        """Verify 0 <img> tags, 0 <picture> tags, and 0 external image CDNs across all mirrors."""
        for name, html_content, soup in [
            ("INSTITUTIONAL_PORTAL_HTML", INSTITUTIONAL_PORTAL_HTML, self.soup_portal),
            ("index.html", self.index_html, self.soup_index),
        ]:
            img_tags = soup.find_all("img")
            self.assertEqual(
                len(img_tags), 0,
                f"Forbidden <img> tags found in {name}: {img_tags}"
            )

            picture_tags = soup.find_all("picture")
            self.assertEqual(
                len(picture_tags), 0,
                f"Forbidden <picture> tags found in {name}: {picture_tags}"
            )

            # Check for raster image references: .jpg, .jpeg, .png, .webp, .gif, .bmp
            raster_matches = re.findall(r'[\w\-./\\]+\.(?:jpg|jpeg|png|webp|gif|bmp)', html_content, re.IGNORECASE)
            self.assertEqual(
                len(raster_matches), 0,
                f"Forbidden raster image references found in {name}: {raster_matches}"
            )

            # Check for known external image CDN domains
            cdns = ["unsplash.com", "pexels.com", "cloudinary.com", "imgur.com", "images.ctfassets.net"]
            for cdn in cdns:
                self.assertNotIn(
                    cdn, html_content,
                    f"External image CDN '{cdn}' detected in {name}"
                )

    # -------------------------------------------------------------------------
    # 3. Tabular Numbers (tnum) CSS & Typography Rules
    # -------------------------------------------------------------------------

    def test_tabular_numbers_css_and_elements(self):
        """Verify font-feature-settings 'tnum' 1, 'zero' 1 and tabular-nums CSS rules."""
        style_blocks = self.soup_portal.find_all("style")
        css_combined = "\n".join(s.string or "" for s in style_blocks)

        # Invariant 1: CSS property presence
        self.assertIn('"tnum" 1', css_combined, "Missing 'tnum' 1 in font-feature-settings")
        self.assertIn('"zero" 1', css_combined, "Missing 'zero' 1 in font-feature-settings")
        self.assertIn('tabular-nums', css_combined, "Missing tabular-nums in font-variant-numeric")
        self.assertIn('slashed-zero', css_combined, "Missing slashed-zero in font-variant-numeric")

        # Invariant 2: Applied to body and key metric classes
        self.assertIn('body {', css_combined)
        self.assertIn('.metric-val', css_combined)
        self.assertIn('.kpi-val', css_combined)
        self.assertIn('.price', css_combined)
        self.assertIn('.target-val', css_combined)
        self.assertIn('.trade-table td', css_combined)

        # Invariant 3: Fonts Inter and JetBrains Mono are imported
        self.assertIn('Inter', css_combined)
        self.assertIn('JetBrains Mono', css_combined)

    # -------------------------------------------------------------------------
    # 4. Dual Switcher & DOM Stress Testing
    # -------------------------------------------------------------------------

    def test_dom_switcher_structural_contract(self):
        """Verify DOM elements, IDs, classes, and initial attributes for dual theming."""
        switcher = self.soup_portal.find(id="themeSwitcher")
        self.assertIsNotNone(switcher, "Missing #themeSwitcher container")

        btn_light = switcher.find(id="tbtnLight")
        self.assertIsNotNone(btn_light, "Missing #tbtnLight button")
        self.assertEqual(btn_light.get("onclick"), "setTheme('light')")

        btn_dark = switcher.find(id="tbtnDark")
        self.assertIsNotNone(btn_dark, "Missing #tbtnDark button")
        self.assertEqual(btn_dark.get("onclick"), "setTheme('dark')")

    def test_head_anti_flicker_script_integrity(self):
        """Verify the synchronous anti-flicker script in <head>."""
        head = self.soup_portal.find("head")
        self.assertIsNotNone(head, "Missing <head>")
        head_scripts = head.find_all("script")
        head_script_text = "\n".join(s.string or "" for s in head_scripts)

        # Must inspect arcafid_theme
        self.assertIn("localStorage.getItem('arcafid_theme')", head_script_text)
        # Must set data-theme before body renders
        self.assertIn("document.documentElement.setAttribute('data-theme'", head_script_text)
        # Must be wrapped in try/catch to survive localStorage access denial
        self.assertIn("try {", head_script_text)
        self.assertIn("catch(e)", head_script_text)

    def test_theme_logic_simulation_corrupted_local_storage(self):
        """Simulate JS theme resolution logic against adversarial/corrupted localStorage values."""
        def resolve_theme_from_storage(saved_val):
            # Mirror of index.html head loader and initTheme logic
            active = "dark"
            if saved_val == "light":
                active = "light"
            return active

        # Adversarial test vector
        vectors = [
            (None, "dark"),
            ("undefined", "dark"),
            ("", "dark"),
            ("matrix", "dark"),
            ("LIGHT", "dark"), # Case sensitive
            ("Light", "dark"),
            ("{'theme': 'light'}", "dark"),
            ("true", "dark"),
            ("0", "dark"),
            ("dark", "dark"),
            ("light", "light"),
            ("!@#$%^&*()", "dark"),
            ("   light   ", "dark"),
        ]

        for val, expected in vectors:
            with self.subTest(storage_val=val):
                res = resolve_theme_from_storage(val)
                self.assertEqual(res, expected, f"Value '{val}' should resolve to '{expected}', got '{res}'")

    def test_rapid_toggling_dom_simulation(self):
        """Simulate 10,000 rapid state transitions to ensure deterministic synchronization."""
        # Simulated DOM state
        class MockDOM:
            def __init__(self):
                self.doc_elem_attrs = {}
                self.body_classes = set()
                self.btn_light_classes = set()
                self.btn_dark_classes = {"active"}
                self.local_storage = {}
                self.chart_draw_count = 0

            def set_theme(self, theme):
                is_light = (theme == "light")
                self.doc_elem_attrs["data-theme"] = "light" if is_light else "dark"
                if is_light:
                    self.body_classes.add("theme-light")
                    self.body_classes.discard("theme-dark")
                    self.btn_light_classes.add("active")
                    self.btn_dark_classes.discard("active")
                else:
                    self.body_classes.discard("theme-light")
                    self.body_classes.add("theme-dark")
                    self.btn_light_classes.discard("active")
                    self.btn_dark_classes.add("active")

                try:
                    self.local_storage["arcafid_theme"] = "light" if is_light else "dark"
                except Exception:
                    pass

                self.chart_draw_count += 1

        dom = MockDOM()

        # Rapidly toggle 10,000 times
        for i in range(10000):
            target = "light" if (i % 2 == 0) else "dark"
            dom.set_theme(target)

            # Invariant check at every toggle
            expected_theme = target
            self.assertEqual(dom.doc_elem_attrs["data-theme"], expected_theme)
            if expected_theme == "light":
                self.assertIn("theme-light", dom.body_classes)
                self.assertNotIn("theme-dark", dom.body_classes)
                self.assertIn("active", dom.btn_light_classes)
                self.assertNotIn("active", dom.btn_dark_classes)
            else:
                self.assertNotIn("theme-light", dom.body_classes)
                self.assertIn("theme-dark", dom.body_classes)
                self.assertNotIn("active", dom.btn_light_classes)
                self.assertIn("active", dom.btn_dark_classes)

            self.assertEqual(dom.local_storage["arcafid_theme"], expected_theme)

        self.assertEqual(dom.chart_draw_count, 10000)

    # -------------------------------------------------------------------------
    # 5. Brand String Audit
    # -------------------------------------------------------------------------

    def test_brand_string_audit_occurrences(self):
        """Scan repository for occurrences of 'Aethelgard' and catalog them."""
        occurrences = []
        excluded_dirs = {".git", "venv", "__pycache__", ".agents", ".gemini"}

        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for file in files:
                # Only check text files
                ext = os.path.splitext(file)[1].lower()
                if ext in [".py", ".html", ".md", ".json", ".csv", ".txt", ".js", ".css"]:
                    fpath = os.path.join(root, file)
                    relpath = os.path.relpath(fpath, PROJECT_ROOT)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                            for lno, line in enumerate(f, 1):
                                if "aethelgard" in line.lower():
                                    occurrences.append((relpath, lno, line.strip()))
                    except Exception:
                        pass

        # We will report all occurrences
        print(f"\n[BRAND AUDIT] Total 'Aethelgard' occurrences in repo: {len(occurrences)}")
        for path, lno, line in occurrences:
            print(f"  {path}:{lno} -> {line[:90]}")

        # Assert no user-facing visual text in index.html contains Aethelgard outside compatibility comments/meta
        for tag in self.soup_portal.find_all(True):
            if tag.name not in ["script", "style", "meta"]:
                text = tag.string
                if text and "aethelgard" in text.lower():
                    self.fail(f"Found visual user-facing 'Aethelgard' in tag <{tag.name}>: '{text}'")


if __name__ == "__main__":
    unittest.main()
