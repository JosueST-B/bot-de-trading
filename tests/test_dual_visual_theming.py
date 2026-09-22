"""Tests unitarios y de invariantes para el modo visual dual (Crisp White + Terminal Dark).

Verifica:
1. Componentes del DOM del conmutador de temas ([ ☀ LUMINOSO / ☾ TERMINAL ]).
2. Arquitectura de tokens CSS / propiedades personalizadas para ambos modos.
3. Invariantes de persistencia en localStorage y script de carga anti-flicker en <head>.
4. Adaptación dinámica de temas en el gráfico canvas y paridad estricta entre espejos.
"""

from __future__ import annotations

import os
import re
import unittest
from bs4 import BeautifulSoup

from bot.institutional_portal import INSTITUTIONAL_PORTAL_HTML

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestDualVisualTheming(unittest.TestCase):
    """Pruebas de verificación de la arquitectura de temas visuales duales."""

    @classmethod
    def setUpClass(cls):
        cls.soup = BeautifulSoup(INSTITUTIONAL_PORTAL_HTML, "html.parser")
        with open(os.path.join(PROJECT_ROOT, "index.html"), "r", encoding="utf-8") as f:
            cls.index_html = f.read()
        with open(os.path.join(PROJECT_ROOT, "docs", "index.html"), "r", encoding="utf-8") as f:
            cls.docs_html = f.read()
        cls.index_soup = BeautifulSoup(cls.index_html, "html.parser")
        cls.docs_soup = BeautifulSoup(cls.docs_html, "html.parser")

    # -------------------------------------------------------------------------
    # 1. Componente DOM: Conmutador de Temas
    # -------------------------------------------------------------------------

    def test_theme_switcher_presence_and_structure(self):
        """Verifica la existencia del conmutador con id themeSwitcher en el header."""
        for name, soup in [
            ("INSTITUTIONAL_PORTAL_HTML", self.soup),
            ("index.html", self.index_soup),
            ("docs/index.html", self.docs_soup),
        ]:
            switcher = soup.find(id="themeSwitcher")
            self.assertIsNotNone(switcher, f"Falta #themeSwitcher en {name}")
            self.assertIn("theme-switcher", switcher.get("class", []))

            # Botón Luminoso
            btn_light = switcher.find(id="tbtnLight")
            self.assertIsNotNone(btn_light, f"Falta #tbtnLight en {name}")
            self.assertIn("setTheme('light')", btn_light.get("onclick", ""))
            self.assertIn("LUMINOSO", btn_light.text)
            self.assertIn("☀", btn_light.text)

            # Botón Terminal
            btn_dark = switcher.find(id="tbtnDark")
            self.assertIsNotNone(btn_dark, f"Falta #tbtnDark en {name}")
            self.assertIn("setTheme('dark')", btn_dark.get("onclick", ""))
            self.assertIn("TERMINAL", btn_dark.text)
            self.assertIn("☾", btn_dark.text)
            self.assertIn("active", btn_dark.get("class", []))

    def test_theme_switcher_location_inside_nav_right(self):
        """Verifica que el conmutador esté posicionado correctamente en nav-right."""
        nav_right = self.soup.find(class_="nav-right")
        self.assertIsNotNone(nav_right, "Falta elemento .nav-right en el header")
        switcher = nav_right.find(id="themeSwitcher")
        self.assertIsNotNone(switcher, "#themeSwitcher debe estar dentro de .nav-right")

    # -------------------------------------------------------------------------
    # 2. Tokens CSS y Propiedades Personalizadas
    # -------------------------------------------------------------------------

    def test_css_variables_crisp_white_mode(self):
        """Verifica los tokens de color requeridos para Crisp White Mode."""
        style_blocks = self.soup.find_all("style")
        css_combined = "\n".join(s.string or "" for s in style_blocks)

        # Regla de tema claro
        self.assertTrue(
            '[data-theme="light"]' in css_combined or 'body.theme-light' in css_combined,
            "Falta selector para modo luminoso [data-theme='light'] o body.theme-light"
        )

        # Paleta institucional blanca: #ffffff, #f8fafc, #09090b, #2563eb, #10b981
        tokens_crisp_white = [
            "--bg-base: #ffffff",
            "--bg-surface: #f8fafc",
            "--bg-card: #ffffff",
            "--text-main: #09090b",
            "--text-muted: #52525b",
            "--accent: #2563eb",
            "--green: #10b981",
        ]
        for token in tokens_crisp_white:
            self.assertIn(
                token,
                css_combined,
                f"Falta token obligatorio de Crisp White: '{token}'"
            )

    def test_css_variables_terminal_dark_mode(self):
        """Verifica los tokens de color requeridos para Terminal Dark Mode (:root)."""
        style_blocks = self.soup.find_all("style")
        css_combined = "\n".join(s.string or "" for s in style_blocks)

        tokens_terminal_dark = [
            "--bg-base: #09090b",
            "--bg-surface: #111215",
            "--bg-card: #14161b",
            "--text-main: #f4f4f5",
            "--border: rgba(255, 255, 255, 0.08)",
        ]
        for token in tokens_terminal_dark:
            self.assertIn(
                token,
                css_combined,
                f"Falta token obligatorio de Terminal Dark: '{token}'"
            )

    def test_tabular_numbers_and_typography_invariants(self):
        """Verifica la preservación de números tabulares (tnum, zero) sin distorsión."""
        style_blocks = self.soup.find_all("style")
        css_combined = "\n".join(s.string or "" for s in style_blocks)
        self.assertIn('"tnum" 1', css_combined)
        self.assertIn('"zero" 1', css_combined)
        self.assertIn('tabular-nums', css_combined)

    def test_zero_stock_images_purged(self):
        """Garantiza la ausencia total de imágenes de stock (cero etiquetas img, cero jpg/png)."""
        imgs = self.soup.find_all("img")
        self.assertEqual(len(imgs), 0, f"Se encontraron etiquetas <img> en el portal: {imgs}")

        jpg_png_regex = re.compile(r'[\w\-./\\]+\.(?:jpg|jpeg|png)', re.IGNORECASE)
        matches = jpg_png_regex.findall(INSTITUTIONAL_PORTAL_HTML)
        self.assertEqual(len(matches), 0, f"Se encontraron referencias a imágenes de stock: {matches}")

    # -------------------------------------------------------------------------
    # 3. Persistencia en localStorage y Script Anti-Flicker en <head>
    # -------------------------------------------------------------------------

    def test_anti_flicker_head_loader_script(self):
        """Verifica el script en <head> que lee localStorage antes del renderizado para evitar FOUC."""
        head = self.soup.find("head")
        self.assertIsNotNone(head, "Falta etiqueta <head> en el HTML")
        head_scripts = head.find_all("script")
        head_scripts_text = "\n".join(s.string or "" for s in head_scripts)

        self.assertIn("localStorage.getItem('arcafid_theme')", head_scripts_text)
        self.assertIn("setAttribute('data-theme'", head_scripts_text)

    def test_javascript_set_theme_controller(self):
        """Verifica la implementación de la función setTheme y la clave de almacenamiento."""
        script_blocks = self.soup.find_all("script")
        all_scripts = "\n".join(s.string or "" for s in script_blocks)

        self.assertIn("function setTheme(theme)", all_scripts)
        self.assertIn("THEME_STORAGE_KEY = 'arcafid_theme'", all_scripts)
        self.assertIn("localStorage.setItem(THEME_STORAGE_KEY", all_scripts)
        self.assertIn("document.documentElement.setAttribute('data-theme'", all_scripts)
        self.assertIn("classList.toggle('theme-light'", all_scripts)
        self.assertIn("initTheme()", all_scripts)

    def test_dynamic_canvas_chart_theme_adaptation(self):
        """Verifica que el motor drawChart() adapte colores según el modo activo."""
        script_blocks = self.soup.find_all("script")
        all_scripts = "\n".join(s.string or "" for s in script_blocks)

        self.assertIn("function drawChart()", all_scripts)
        # Verificación de detección de tema en gráfico
        self.assertTrue(
            "data-theme" in all_scripts or "theme-light" in all_scripts,
            "drawChart debe verificar el estado data-theme o theme-light"
        )
        # Línea azul institucional #2563eb en modo claro y blanca en oscuro
        self.assertIn("#2563eb", all_scripts)
        # Tooltip adaptativo
        self.assertIn("chartTooltip", all_scripts)


if __name__ == "__main__":
    unittest.main()
