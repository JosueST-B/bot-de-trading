"""Tests de integracion fiduciaria para IBKR Client, yfinance Engine y Reconciliacion Dual.

Verifica:
1. Motor yfinance (bot/yfinance_engine.py):
   - Normalizacion de velas a esquema cuantitativo ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time'] en UTC.
   - Cache Bounded TTL (60s) anti-429.
   - Cotizaciones en tiempo real (Fast Info).
   - Fallback a cache stale ante fallo de red.
2. Conector IBKR (bot/ibkr_client.py):
   - Sondeo no bloqueante de puertos (4002, 7497, 4001, 7496).
   - Modo Fiduciary Standby (STANDBY_YFINANCE) sin excepciones ni bloqueos.
   - Despacho de velas via yfinance en standby.
   - Resumen financiero de cuenta (net_liquidation, total_cash, buying_power).
   - Simulacion de ordenes bracket y ventas en standby.
3. Reconciliacion Dual y Badges (bot/portfolio_manager.py, bot/unified.py):
   - Paridad 1:1 USDT / USD en capital consolidado.
   - Insignias de sincronizacion [BINANCE: CONNECTED/SIMULATED] y [IBKR: CONNECTED/STANDBY_YFINANCE].
   - Cerrojo fiduciario de Drawdown estricto en -6.4%.
   - Enriquecimiento del resumen unificado en build_unified_summary().
"""

from __future__ import annotations

import socket
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from bot.config import BotConfig
from bot.ibkr_client import IBKRClient, probe_ibkr_port
from bot.portfolio_manager import (
    FIDUCIARY_DRAWDOWN_LIMIT,
    PortfolioManager,
    evaluate_drawdown_lock,
    get_dual_sync_status,
    reconcile_dual_balances,
)
from bot.unified import build_unified_summary
from bot.yfinance_engine import QUANT_COLUMNS, YFinanceDataEngine


class TestYFinanceEngine(unittest.TestCase):
    """Pruebas unitarias y de normalizacion del motor yfinance."""

    def setUp(self):
        self.engine = YFinanceDataEngine(ttl_seconds=60.0, max_cache_size=32)

    def test_normalize_dataframe_structure_and_utc(self):
        """Valida que normalize_dataframe genere exactamente las 7 columnas y marcas UTC."""
        dates = pd.date_range("2026-09-20 09:30:00", periods=5, freq="15min", tz="America/New_York")
        raw_df = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0, 103.0, 104.0],
                "High": [101.0, 102.0, 103.0, 104.0, 105.0],
                "Low": [99.0, 100.0, 101.0, 102.0, 103.0],
                "Close": [100.5, 101.5, 102.5, 103.5, 104.5],
                "Volume": [1000, 1500, 2000, 2500, 3000],
            },
            index=dates,
        )
        raw_df.index.name = "Datetime"

        norm_df = self.engine.normalize_dataframe(raw_df, interval="15m", limit=10)

        # 1. Columnas exactas
        self.assertEqual(list(norm_df.columns), QUANT_COLUMNS)
        self.assertEqual(len(norm_df), 5)

        # 2. Tipos numericos
        for col in ["open", "high", "low", "close", "volume"]:
            self.assertTrue(pd.api.types.is_numeric_dtype(norm_df[col]))

        # 3. Marcas de tiempo en UTC
        self.assertTrue(str(norm_df["open_time"].dt.tz).upper().startswith("UTC"))
        self.assertTrue(str(norm_df["close_time"].dt.tz).upper().startswith("UTC"))

        # 4. Verificacion del intervalo (15m = 900s - 1s = 899s de diferencia)
        diff_seconds = (norm_df["close_time"].iloc[0] - norm_df["open_time"].iloc[0]).total_seconds()
        self.assertEqual(diff_seconds, 899)

    def test_ttl_cache_avoids_repeated_downloads(self):
        """Verifica que el cache TTL acotado responda sin llamadas repetidas a red."""
        mock_df = pd.DataFrame(
            {
                "Open": [200.0],
                "High": [205.0],
                "Low": [199.0],
                "Close": [202.0],
                "Volume": [5000],
            },
            index=pd.date_range("2026-09-21 10:00:00", periods=1, tz="UTC"),
        )
        mock_df.index.name = "Date"

        with patch("yfinance.Ticker") as mock_ticker_cls:
            instance = mock_ticker_cls.return_value
            instance.history.return_value = mock_df

            # Primera llamada: debe invocar yfinance
            df1 = self.engine.get_klines("NVDA", interval="15m", limit=10)
            self.assertEqual(mock_ticker_cls.call_count, 1)
            self.assertEqual(len(df1), 1)

            # Segunda llamada dentro del TTL: debe servirse del cache
            df2 = self.engine.get_klines("NVDA", interval="15m", limit=10)
            self.assertEqual(mock_ticker_cls.call_count, 1)  # No se invoco de nuevo
            self.assertEqual(len(df2), 1)
            self.assertEqual(df1.iloc[0]["close"], df2.iloc[0]["close"])

    def test_stale_cache_fallback_on_network_error(self):
        """Verifica que ante un fallo de red posterior, se sirva la copia previa del cache."""
        mock_df = pd.DataFrame(
            {
                "Open": [150.0],
                "High": [155.0],
                "Low": [149.0],
                "Close": [152.0],
                "Volume": [8000],
            },
            index=pd.date_range("2026-09-21 11:00:00", periods=1, tz="UTC"),
        )

        with patch("yfinance.Ticker") as mock_ticker_cls:
            instance = mock_ticker_cls.return_value
            instance.history.return_value = mock_df

            df1 = self.engine.get_klines("AAPL", interval="1h", limit=5)
            self.assertEqual(len(df1), 1)

            # Forzar expiracion y fallo en la siguiente peticion
            self.engine._kline_cache[("AAPL", "1h", "1mo", 5)].timestamp -= 1000.0
            instance.history.side_effect = ConnectionError("Yahoo Finance Rate Limit 429")

            df_stale = self.engine.get_klines("AAPL", interval="1h", limit=5)
            self.assertEqual(len(df_stale), 1)
            self.assertEqual(df_stale.iloc[0]["close"], 152.0)

    def test_get_latest_quote_fast_info(self):
        """Verifica la obtencion de cotizaciones ultra-rapidas con calculo de variacion."""
        with patch("yfinance.Ticker") as mock_ticker_cls:
            instance = mock_ticker_cls.return_value
            fast_info_mock = MagicMock()
            fast_info_mock.last_price = 560.50
            fast_info_mock.previous_close = 555.00
            fast_info_mock.open = 556.00
            fast_info_mock.day_high = 562.00
            fast_info_mock.day_low = 554.00
            fast_info_mock.last_volume = 1200000.0
            instance.fast_info = fast_info_mock

            quote = self.engine.get_latest_quote("SPY")
            self.assertEqual(quote["symbol"], "SPY")
            self.assertEqual(quote["price"], 560.5)
            self.assertEqual(quote["previous_close"], 555.0)
            self.assertAlmostEqual(quote["change"], 5.5, places=2)
            self.assertGreater(quote["change_pct"], 0)
            self.assertEqual(quote["source"], "yfinance_realtime")


class TestIBKRClient(unittest.TestCase):
    """Pruebas del conector IBKR con sondeo de puertos y Fiduciary Standby Mode."""

    def setUp(self):
        self.cfg = BotConfig(
            ibkr_enabled=True,
            ibkr_port=4002,
            ibkr_symbols="NVDA,AAPL,SPY",
            initial_balance=10000.0,
        )

    def test_probe_ibkr_port_detects_open_and_closed(self):
        """Valida que probe_ibkr_port detecte sockets abiertos y descarte cerrados."""
        # Socket de prueba efimero en localhost
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        assigned_port = server_sock.getsockname()[1]

        try:
            detected = probe_ibkr_port("127.0.0.1", [99999, assigned_port, 4002], timeout=0.2)
            self.assertEqual(detected, assigned_port)
        finally:
            server_sock.close()

    def test_standby_mode_when_desktop_closed(self):
        """Valida que si no hay puertos abiertos, transicione a STANDBY_YFINANCE sin lanzar excepciones."""
        with patch("bot.ibkr_client.probe_ibkr_port", return_value=None):
            client = IBKRClient(self.cfg)
            client.connect()

            self.assertEqual(client.status, "STANDBY_YFINANCE")
            self.assertFalse(client.is_connected)
            self.assertEqual(client.active_provider, "yfinance")
            self.assertIsNone(client.connected_port)

    def test_standby_serves_klines_and_bars_via_yfinance(self):
        """Valida que en Standby las solicitudes de velas se deriven limpiamente a yfinance."""
        with patch("bot.ibkr_client.probe_ibkr_port", return_value=None):
            client = IBKRClient(self.cfg)
            client.connect()

            mock_bars = pd.DataFrame(
                {
                    "open_time": pd.to_datetime(["2026-09-21 12:00:00"], utc=True),
                    "open": [115.0],
                    "high": [118.0],
                    "low": [114.5],
                    "close": [117.2],
                    "volume": [45000.0],
                    "close_time": pd.to_datetime(["2026-09-21 12:14:59"], utc=True),
                }
            )

            with patch.object(client.yfinance_engine, "get_klines", return_value=mock_bars) as mock_get:
                df = client.get_klines("NVDA", "15m", limit=100)
                self.assertEqual(len(df), 1)
                self.assertEqual(df.iloc[0]["close"], 117.2)
                mock_get.assert_called_once_with("NVDA", interval="15m", limit=100)

                # Probar alias get_bars
                df_bars = client.get_bars("NVDA", interval="15m")
                self.assertEqual(len(df_bars), 1)

    def test_standby_account_summary_and_orders(self):
        """Valida que get_account_summary y ordenes bracket operen en standby sin crashear."""
        with patch("bot.ibkr_client.probe_ibkr_port", return_value=None):
            client = IBKRClient(self.cfg)
            client.connect()

            summary = client.get_account_summary()
            self.assertIn("net_liquidation", summary)
            self.assertIn("total_cash", summary)
            self.assertIn("buying_power", summary)
            self.assertEqual(summary["net_liquidation"], 10000.0)

            # Bracket order simulada fiduciariamente
            order_res = client.buy_bracket("MSFT", qty=5, entry_ref=420.0, take_profit=440.0, stop_loss=410.0)
            self.assertTrue(order_res["ok"])
            self.assertEqual(order_res["status"], "standby_simulated")
            self.assertEqual(order_res["provider"], "yfinance")

            # Venta simulada
            sell_res = client.market_sell("MSFT", qty=5)
            self.assertTrue(sell_res["ok"])
            self.assertEqual(sell_res["status"], "standby_simulated")


class TestDualBalanceReconciliation(unittest.TestCase):
    """Pruebas actuariales de reconciliacion de balances y cerrojo de drawdown."""

    def setUp(self):
        self.cfg = BotConfig(
            initial_balance=12500.0,
            binance_api_key="TEST_API_KEY",
            use_testnet=False,
            ibkr_enabled=True,
        )

    def test_reconcile_dual_balances_parity_1_to_1(self):
        """Verifica la agregacion a paridad 1:1 de Binance USDT e IBKR USD."""
        mock_binance = MagicMock()
        mock_binance.get_asset_balance_values.return_value = (5000.0, 2500.0)  # Total 7500 USDT

        mock_ibkr = MagicMock()
        mock_ibkr.is_connected = True
        mock_ibkr.status = "CONNECTED"
        mock_ibkr.account_cash_usd.return_value = 8000.0
        mock_ibkr.net_liquidation.return_value = 12000.0  # Total 12000 USD

        recon = reconcile_dual_balances(mock_binance, mock_ibkr, self.cfg)

        # Binance: 5000 libre + 2500 bloqueado = 7500 USDT
        self.assertEqual(recon["binance"]["equity"], 7500.0)
        self.assertEqual(recon["binance"]["status"], "CONNECTED")

        # IBKR: 12000 Net Liquidation USD
        self.assertEqual(recon["ibkr"]["equity"], 12000.0)
        self.assertEqual(recon["ibkr"]["status"], "CONNECTED")

        # Consolidado: 7500 + 12000 = 19500 Total Equity
        self.assertEqual(recon["consolidated"]["total_equity_usd"], 19500.0)
        self.assertEqual(recon["consolidated"]["total_cash_usd"], 13000.0)

    def test_dual_sync_status_badges(self):
        """Verifica la generacion de badges [BINANCE: CONNECTED] e [IBKR: STANDBY_YFINANCE]."""
        mock_binance = MagicMock()
        mock_binance.is_live = True

        mock_ibkr = MagicMock()
        mock_ibkr.is_connected = False
        mock_ibkr.status = "STANDBY_YFINANCE"
        mock_ibkr.active_provider = "yfinance"

        badges = get_dual_sync_status(mock_binance, mock_ibkr, self.cfg)

        self.assertEqual(badges["binance"]["status"], "CONNECTED")
        self.assertEqual(badges["binance"]["label"], "[BINANCE: CONNECTED]")

        self.assertEqual(badges["ibkr"]["status"], "STANDBY_YFINANCE")
        self.assertEqual(badges["ibkr"]["label"], "[IBKR: STANDBY_YFINANCE]")
        self.assertEqual(badges["ibkr"]["provider"], "yfinance")

    def test_fiduciary_drawdown_lock_at_minus_6_4_pct(self):
        """Verifica que el cerrojo fiduciario de Drawdown en -6.4% se active con precision."""
        peak_equity = 20000.0

        # Caso 1: -4.0% de drawdown -> NORMAL
        current_eq_1 = 19200.0
        locked, dd, status = evaluate_drawdown_lock(peak_equity, current_eq_1)
        self.assertFalse(locked)
        self.assertEqual(status, "NORMAL")
        self.assertAlmostEqual(dd, -0.04, places=3)

        # Caso 2: -6.4% exacto de drawdown -> LOCKED_DEFENSIVE
        current_eq_2 = 20000.0 * (1.0 - 0.064)  # 18720.0
        locked, dd, status = evaluate_drawdown_lock(peak_equity, current_eq_2)
        self.assertTrue(locked)
        self.assertEqual(status, "LOCKED_DEFENSIVE")
        self.assertAlmostEqual(dd, -0.064, places=3)

        # Caso 3: -8.0% de drawdown -> LOCKED_DEFENSIVE
        current_eq_3 = 18400.0
        locked, dd, status = evaluate_drawdown_lock(peak_equity, current_eq_3)
        self.assertTrue(locked)
        self.assertEqual(status, "LOCKED_DEFENSIVE")
        self.assertAlmostEqual(dd, -0.08, places=3)

    def test_portfolio_manager_lifecycle(self):
        """Prueba el ciclo de vida de la clase PortfolioManager."""
        mgr = PortfolioManager(self.cfg)
        status = mgr.get_sync_status()
        self.assertIn("binance", status)
        self.assertIn("ibkr", status)

        recon = mgr.reconcile()
        self.assertGreater(recon["consolidated"]["total_equity_usd"], 0)
        self.assertGreater(mgr.peak_consolidated_equity, 0)

        # Sin caida de capital -> Normal
        locked, dd, state = mgr.check_circuit_breaker()
        self.assertFalse(locked)
        self.assertEqual(state, "NORMAL")

    def test_build_unified_summary_integration(self):
        """Verifica que build_unified_summary() incluya sync_badges y balances reconciliados."""
        mock_store = MagicMock()
        mock_store.recent_events.return_value = []

        summary = build_unified_summary(self.cfg, mock_store)
        self.assertIn("sync_badges", summary)
        self.assertIn("binance", summary["sync_badges"])
        self.assertIn("ibkr", summary["sync_badges"])
        self.assertIn("consolidated", summary)
        self.assertIn("total_consolidated_equity", summary["consolidated"])
        self.assertIn("total_available_cash", summary["consolidated"])
        self.assertIn("fiduciary_drawdown_limit_pct", summary["consolidated"])
        self.assertEqual(summary["consolidated"]["fiduciary_drawdown_limit_pct"], -6.4)


if __name__ == "__main__":
    unittest.main()
