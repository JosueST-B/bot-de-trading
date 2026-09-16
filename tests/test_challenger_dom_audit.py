"""Adversarial DOM and Static Analysis Verifier for Milestone 1.

Authored by Milestone 1 Challenger 1 (challenger_1_m1).
Verifies:
1. Zero <img> tags in bot/institutional_portal.py and bot/app_dashboard.py.
2. Zero references to .jpg or .png files in the HTML of both files.
3. Presence of the 4 block IDs (overview/summary, performance, simulator, security) in institutional_portal.py.
4. Presence of mandatory brand strings in institutional_portal.py.
5. HTML & CSS syntactic integrity (tag closure, brace balancing, valid structure) for both files.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from html.parser import HTMLParser
from typing import List, Tuple

from bs4 import BeautifulSoup

# Ensure bot package is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.app_dashboard import DASHBOARD_HTML
from bot.institutional_portal import INSTITUTIONAL_PORTAL_HTML


class TagBalanceValidator(HTMLParser):
    """Strict HTML parser tracking unclosed tags."""

    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr", "!doctype",
    }

    def __init__(self) -> None:
        super().__init__()
        self.stack: List[Tuple[str, int]] = []
        self.unclosed: List[Tuple[str, int]] = []
        self.mismatched: List[Tuple[str, str, int]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower not in self.VOID_TAGS:
            line, _ = self.getpos()
            self.stack.append((tag_lower, line))

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.VOID_TAGS:
            return

        line, _ = self.getpos()
        if not self.stack:
            self.mismatched.append(("", tag_lower, line))
            return

        # Pop matching tag or find nearest match
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag_lower:
                # Any tags above this on the stack were unclosed
                for unclosed_tag, u_line in self.stack[i + 1:]:
                    self.unclosed.append((unclosed_tag, u_line))
                self.stack = self.stack[:i]
                return

        # Tag had no opening tag on stack
        self.mismatched.append(("", tag_lower, line))

    def finish(self) -> None:
        for tag, line in self.stack:
            self.unclosed.append((tag, line))


class TestMilestone1ChallengerDOM(unittest.TestCase):
    """Adversarial stress-test for DOM structure, image purge, and branding."""

    MANDATORY_BRAND_STRINGS = [
        "Aethelgard Quantitative",
        "Mandatos & Estructuras de Inversión",
        "Simulador Cuantitativo de Retornos",
        "Ratio Sharpe",
    ]

    IMAGE_REGEX = re.compile(r'[\w\-./\\]+\.(?:jpg|jpeg|png)', re.IGNORECASE)

    # -------------------------------------------------------------------------
    # 1. Image Purge Verification
    # -------------------------------------------------------------------------

    def test_institutional_portal_zero_img_tags(self) -> None:
        """Assert zero <img> tags in bot/institutional_portal.py."""
        soup = BeautifulSoup(INSTITUTIONAL_PORTAL_HTML, "html.parser")
        imgs = soup.find_all("img")
        self.assertEqual(
            len(imgs), 0,
            f"Expected zero <img> tags in INSTITUTIONAL_PORTAL_HTML, found: {imgs}"
        )

    def test_app_dashboard_zero_img_tags(self) -> None:
        """Assert zero <img> tags in bot/app_dashboard.py."""
        soup = BeautifulSoup(DASHBOARD_HTML, "html.parser")
        imgs = soup.find_all("img")
        self.assertEqual(
            len(imgs), 0,
            f"Expected zero <img> tags in DASHBOARD_HTML, found: {imgs}"
        )

    def test_institutional_portal_zero_jpg_png_references(self) -> None:
        """Assert zero references to .jpg or .png files in bot/institutional_portal.py."""
        matches = self.IMAGE_REGEX.findall(INSTITUTIONAL_PORTAL_HTML)
        self.assertEqual(
            len(matches), 0,
            f"Expected zero .jpg/.png references in INSTITUTIONAL_PORTAL_HTML, found: {matches}"
        )

    def test_app_dashboard_zero_jpg_png_references(self) -> None:
        """Assert zero references to .jpg or .png files in bot/app_dashboard.py."""
        matches = self.IMAGE_REGEX.findall(DASHBOARD_HTML)
        self.assertEqual(
            len(matches), 0,
            f"Expected zero .jpg/.png references in DASHBOARD_HTML, found: {matches}"
        )

    # -------------------------------------------------------------------------
    # 2. 4-Block IDs Verification
    # -------------------------------------------------------------------------

    def test_institutional_portal_four_block_ids(self) -> None:
        """Assert presence of the 4 block IDs: overview/summary, performance, simulator, security."""
        soup = BeautifulSoup(INSTITUTIONAL_PORTAL_HTML, "html.parser")

        # Block 1: overview or summary
        b1_overview = soup.find(id="overview")
        b1_summary = soup.find(id="summary")
        self.assertTrue(
            b1_overview is not None or b1_summary is not None,
            "Block 1 (overview or summary) must be present in DOM"
        )
        self.assertTrue(
            soup.find(id="overview") is not None,
            "Expected id='overview' anchor in DOM"
        )
        self.assertTrue(
            soup.find(id="summary") is not None,
            "Expected id='summary' section in DOM"
        )

        # Block 2: performance
        b2_perf = soup.find(id="performance")
        self.assertIsNotNone(
            b2_perf,
            "Block 2 (id='performance') must be present in DOM"
        )

        # Block 3: simulator (or calculator)
        b3_sim = soup.find(id="simulator")
        self.assertIsNotNone(
            b3_sim,
            "Block 3 (id='simulator') must be present in DOM"
        )
        self.assertIsNotNone(
            soup.find(id="calculator"),
            "Expected id='calculator' in DOM"
        )

        # Block 4: security (or audit)
        b4_sec = soup.find(id="security")
        self.assertIsNotNone(
            b4_sec,
            "Block 4 (id='security') must be present in DOM"
        )
        self.assertIsNotNone(
            soup.find(id="audit"),
            "Expected id='audit' in DOM"
        )

    # -------------------------------------------------------------------------
    # 3. Mandatory Brand Strings Verification
    # -------------------------------------------------------------------------

    def test_institutional_portal_mandatory_brand_strings(self) -> None:
        """Assert presence of mandatory brand strings in bot/institutional_portal.py."""
        for brand in self.MANDATORY_BRAND_STRINGS:
            self.assertIn(
                brand,
                INSTITUTIONAL_PORTAL_HTML,
                f"Mandatory brand string '{brand}' missing from INSTITUTIONAL_PORTAL_HTML"
            )

    # -------------------------------------------------------------------------
    # 4. CSS and HTML Syntactic Hygiene
    # -------------------------------------------------------------------------

    def test_css_brace_balancing(self) -> None:
        """Assert that all <style> blocks have balanced curly braces without truncation."""
        for name, html_text in [("institutional_portal", INSTITUTIONAL_PORTAL_HTML), ("app_dashboard", DASHBOARD_HTML)]:
            soup = BeautifulSoup(html_text, "html.parser")
            style_tags = soup.find_all("style")
            self.assertGreater(len(style_tags), 0, f"{name} must contain at least one <style> tag")
            for idx, style in enumerate(style_tags):
                css = style.string or ""
                # Strip out strings and comments to count structural braces
                clean_css = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
                clean_css = re.sub(r'["\'].*?["\']', '', clean_css)
                open_braces = clean_css.count("{")
                close_braces = clean_css.count("}")
                self.assertEqual(
                    open_braces, close_braces,
                    f"{name} style tag #{idx} has unbalanced braces: {open_braces} open vs {close_braces} close"
                )

    def test_html_tag_closure_hygiene(self) -> None:
        """Verify HTML tag closure integrity in institutional portal and app dashboard."""
        for name, html_text in [("institutional_portal", INSTITUTIONAL_PORTAL_HTML), ("app_dashboard", DASHBOARD_HTML)]:
            parser = TagBalanceValidator()
            parser.feed(html_text)
            parser.finish()

            critical_tags = {"div", "section", "table", "tbody", "thead", "tr", "header", "footer", "canvas", "script", "style"}
            critical_unclosed = [t for t in parser.unclosed if t[0] in critical_tags]
            self.assertEqual(
                critical_unclosed, [],
                f"Found unclosed critical tags in {name}: {critical_unclosed}"
            )


if __name__ == "__main__":
    unittest.main()
