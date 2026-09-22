"""Suite de pruebas para la Cartera Híbrida de 14 Activos y el Cerebro Cuantitativo Unificado.

Cubre:
1. Configuración del Universo Híbrido de 14 Activos (8 Cripto + 6 Acciones/ETFs) en BotConfig.
2. Lista blanca (Whitelist) y enrutamiento institucional de activos (AMZN reemplaza a TSLA).
3. Máquina de estados de sesiones de mercado (5 estados: CRYPTO_24_7, US_PRE_MARKET, US_REGULAR, US_POST_MARKET, US_CLOSED)
   y evaluación de señales pre-mercado sin ejecución prematura de órdenes.
4. Cerebro Cuantitativo Unificado de 5 Factores (Trend, Mom, Vol, ML, Sent) con umbral S_composite >= 0.72.
5. Cerrojo fiduciario de Drawdown Consolidado en -6.4% con cancelación coordinada dual-broker (Binance + IBKR).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from bot.config import BotConfig
from bot.main import (
    MarketSession,
    _ibkr_premarket_step,
    get_market_session,
)
from bot.quant_engine import (
    COMPOSITE_THRESHOLD,
    WEIGHT_ML,
    WEIGHT_MOMENTUM,
    WEIGHT_SENTIMENT,
    WEIGHT_TREND,
    WEIGHT_VOLATILITY,
    FactorBreakdown,
    QuantEngine,
    calculate_composite_score,
)
from bot.risk import (
    CircuitBreakerStatus,
    RiskManager,
    trigger_dual_broker_cancellation,
)


class TestHybridPortfolioConfig(unittest.TestCase):
    """Verifica la configuración y partición del universo híbrido de 14 activos."""

    def test_default_14_asset_universe(self):
        """Verifica que BotConfig contenga por defecto 8 criptos y 6 acciones/ETFs."""
        cfg = BotConfig()

        # 8 Criptomonedas insignia
        expected_crypto = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT"]
        self.assertEqual(cfg.symbols_to_trade, expected_crypto)
        self.assertEqual(len(cfg.symbols_to_trade), 8)

        # 6 Acciones / ETFs insignia
        expected_stocks = ["NVDA", "AAPL", "MSFT", "AMZN", "SPY", "QQQ"]
        self.assertEqual(cfg.ibkr_symbols_list, expected_stocks)
        self.assertEqual(len(cfg.ibkr_symbols_list), 6)

        # Total 14 instrumentos
        total_universe = set(cfg.symbols_to_trade) | set(cfg.ibkr_symbols_list)
        self.assertEqual(len(total_universe), 14)

    def test_from_env_fallback_to_14_assets(self):
        """Verifica que variables vacías o strings legadas hagan fallback a los 14 activos."""
        with patch.dict(os.environ, {"ACTIVE_SYMBOLS": "", "IBKR_SYMBOLS": ""}, clear=False):
            cfg = BotConfig.from_env()
            self.assertEqual(len(cfg.symbols_to_trade), 8)
            self.assertEqual(len(cfg.ibkr_symbols_list), 6)


class TestAssetWhitelistAndVenueRouting(unittest.TestCase):
    """Verifica la lista blanca en RiskManager y el enrutamiento de activos."""

    def test_whitelist_contains_amzn_and_excludes_tsla(self):
        """Verifica que el universo de acciones contenga AMZN y excluya TSLA."""
        cfg = BotConfig()
        stocks = cfg.ibkr_symbols_list
        self.assertIn("AMZN", stocks)
        self.assertNotIn("TSLA", stocks)

    def test_quant_engine_asset_classification(self):
        """Verifica que QuantEngine identifique correctamente Cripto vs Acciones."""
        # Criptomonedas
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT"]:
            self.assertTrue(QuantEngine.is_crypto_asset(sym), f"{sym} debe ser identificado como cripto")

        # Acciones / ETFs
        for sym in ["NVDA", "AAPL", "MSFT", "AMZN", "SPY", "QQQ"]:
            self.assertFalse(QuantEngine.is_crypto_asset(sym), f"{sym} debe ser identificado como acción")

    def test_risk_manager_bot_id_tagging(self):
        """Verifica que RiskManager asigne el prefijo de broker correcto (ibkr_ vs binance_)."""
        temp_dir = tempfile.mkdtemp()
        state_file = os.path.join(temp_dir, "test_state.json")

        # Caso Acción: AMZN -> ibkr_AMZN
        cfg_stock = BotConfig(symbol="AMZN")
        rm_stock = RiskManager(cfg_stock, shared_state_path=state_file)
        rm_stock.check_global_circuit_breaker(10000.0, 10000.0)

        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("ibkr_AMZN", data.get("bots", {}))

        # Caso Cripto: BTCUSDT -> binance_BTCUSDT
        cfg_crypto = BotConfig(symbol="BTCUSDT")
        rm_crypto = RiskManager(cfg_crypto, shared_state_path=state_file)
        rm_crypto.check_global_circuit_breaker(10000.0, 10000.0)

        with open(state_file, "r", encoding="utf-8") as f:
            data_crypto = json.load(f)
        self.assertIn("binance_BTCUSDT", data_crypto.get("bots", {}))


class TestMarketSessionStateMachine(unittest.TestCase):
    """Verifica la máquina de 5 estados de sesiones y el ciclo de pre-mercado."""

    def test_market_session_enum_values(self):
        """Valida que existan los 5 estados definidos en la especificación."""
        self.assertEqual(MarketSession.CRYPTO_24_7.value, "CRYPTO_24_7")
        self.assertEqual(MarketSession.US_PRE_MARKET.value, "US_PRE_MARKET")
        self.assertEqual(MarketSession.US_REGULAR.value, "US_REGULAR")
        self.assertEqual(MarketSession.US_POST_MARKET.value, "US_POST_MARKET")
        self.assertEqual(MarketSession.US_CLOSED.value, "US_CLOSED")

    def test_get_market_session_weekday_hours(self):
        """Verifica la resolución horaria en días hábiles (Lunes a Viernes)."""
        tz_ny = ZoneInfo("America/New_York")

        # 1. Pre-Mercado: Miércoles 05:30 ET (09:30 UTC)
        t_pre = datetime(2026, 9, 23, 5, 30, tzinfo=tz_ny)
        self.assertEqual(get_market_session(t_pre), MarketSession.US_PRE_MARKET)

        # 2. Mercado Regular RTH: Miércoles 11:15 ET (15:15 UTC)
        t_reg = datetime(2026, 9, 23, 11, 15, tzinfo=tz_ny)
        self.assertEqual(get_market_session(t_reg), MarketSession.US_REGULAR)

        # 3. Post-Mercado: Miércoles 17:00 ET (21:00 UTC)
        t_post = datetime(2026, 9, 23, 17, 0, tzinfo=tz_ny)
        self.assertEqual(get_market_session(t_post), MarketSession.US_POST_MARKET)

        # 4. Mercado Cerrado Nocturno: Miércoles 22:30 ET (02:30 UTC siguiente)
        t_closed = datetime(2026, 9, 23, 22, 30, tzinfo=tz_ny)
        self.assertEqual(get_market_session(t_closed), MarketSession.US_CLOSED)

    def test_get_market_session_weekends_always_closed(self):
        """Verifica que Sábados y Domingos siempre retornen US_CLOSED independientemente de la hora."""
        tz_ny = ZoneInfo("America/New_York")
        t_sat = datetime(2026, 9, 26, 11, 0, tzinfo=tz_ny)
        t_sun = datetime(2026, 9, 27, 14, 0, tzinfo=tz_ny)
        self.assertEqual(get_market_session(t_sat), MarketSession.US_CLOSED)
        self.assertEqual(get_market_session(t_sun), MarketSession.US_CLOSED)

    def test_ibkr_premarket_step_defers_without_market_order(self):
        """Verifica que _ibkr_premarket_step evalúe setup y no lance órdenes al mercado."""
        # Generar DataFrame sintético alcista de 30 velas
        dates = pd.date_range("2026-09-23 05:00:00", periods=30, freq="15min", tz="UTC")
        prices = np.linspace(100.0, 130.0, 30)
        mock_df = pd.DataFrame({
            "open_time": dates,
            "open": prices * 0.99,
            "high": prices * 1.01,
            "low": prices * 0.98,
            "close": prices,
            "volume": [10000.0] * 30,
            "close_time": dates + pd.Timedelta(minutes=15),
        })

        mock_client = MagicMock()
        mock_client.get_klines.return_value = mock_df

        cfg = BotConfig(symbol="NVDA")
        last_close: dict[str, datetime] = {}

        # Ejecutar premarket step
        result = _ibkr_premarket_step(
            client=mock_client,
            base_cfg=cfg,
            symbol="NVDA",
            strategy=MagicMock(),
            risk=MagicMock(),
            last_close=last_close,
        )

        self.assertIn(result.get("event"), ("pre_market_setup", "pre_market_eval"))
        # Si superó el umbral, debe posponerse a la apertura regular sin orden de compra inmediata
        if result.get("event") == "pre_market_setup":
            self.assertEqual(result.get("status"), "deferred_to_regular_open")
            self.assertGreaterEqual(result.get("composite_score", 0.0), 0.72)
        # El cliente no debe haber llamado a buy_bracket ni market_buy
        self.assertFalse(mock_client.buy_bracket.called)


class TestUnifiedFiveFactorQuantBrain(unittest.TestCase):
    """Pruebas del motor unificado de scoring multi-factor (0.25, 0.20, 0.15, 0.20, 0.20)."""

    def test_factor_weights_sum_to_one(self):
        """Verifica que los ponderadores de los 5 factores sumen exactamente 1.00."""
        total_weight = WEIGHT_TREND + WEIGHT_MOMENTUM + WEIGHT_VOLATILITY + WEIGHT_ML + WEIGHT_SENTIMENT
        self.assertAlmostEqual(total_weight, 1.00, places=6)
        self.assertEqual(COMPOSITE_THRESHOLD, 0.72)

    def test_calculate_composite_score_formula(self):
        """Verifica el cálculo aritmético exacto de S_composite."""
        # 1. Configuración de alta convicción institucional
        # 0.25*0.85 + 0.20*0.80 + 0.15*0.90 + 0.20*0.75 + 0.20*0.70 = 0.2125 + 0.16 + 0.135 + 0.15 + 0.14 = 0.7975
        high_score = calculate_composite_score(trend=0.85, mom=0.80, vol=0.90, ml=0.75, sent=0.70)
        self.assertAlmostEqual(high_score, 0.7975, places=4)
        self.assertGreaterEqual(high_score, COMPOSITE_THRESHOLD)

        # 2. Configuración por debajo del umbral de autorización
        low_score = calculate_composite_score(trend=0.50, mom=0.50, vol=0.50, ml=0.50, sent=0.50)
        self.assertAlmostEqual(low_score, 0.5000, places=4)
        self.assertLess(low_score, COMPOSITE_THRESHOLD)

    def test_quant_engine_evaluate_setup_structure(self):
        """Verifica la estructura y desglose devuelto por QuantEngine.evaluate_setup."""
        dates = pd.date_range("2026-09-20 00:00:00", periods=50, freq="15min", tz="UTC")
        prices = np.linspace(50000.0, 60000.0, 50)
        df = pd.DataFrame({
            "open_time": dates,
            "open": prices * 0.995,
            "high": prices * 1.005,
            "low": prices * 0.990,
            "close": prices,
            "volume": [150.0] * 50,
            "close_time": dates + pd.Timedelta(minutes=15),
        })

        breakdown = QuantEngine.evaluate_setup(symbol="BTCUSDT", df=df)
        self.assertIsInstance(breakdown, FactorBreakdown)
        self.assertEqual(breakdown.symbol, "BTCUSDT")
        self.assertTrue(0.0 <= breakdown.trend <= 1.0)
        self.assertTrue(0.0 <= breakdown.momentum <= 1.0)
        self.assertTrue(0.0 <= breakdown.volatility <= 1.0)
        self.assertTrue(0.0 <= breakdown.ml <= 1.0)
        self.assertTrue(0.0 <= breakdown.sentiment <= 1.0)
        self.assertTrue(0.0 <= breakdown.composite <= 1.0)
        self.assertIsInstance(breakdown.is_buy_authorized, bool)


class TestConsolidatedCircuitBreakerAndCancellation(unittest.TestCase):
    """Verifica el cerrojo de drawdown del -6.4% y la cancelación dual coordinada."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_events.db")
        self.state_path = os.path.join(self.temp_dir, "global_state.json")
        self.cfg = BotConfig(event_db_path=self.db_path)
        self.risk = RiskManager(self.cfg, shared_state_path=self.state_path)

    def test_consolidated_circuit_breaker_threshold_breach(self):
        """Valida que un drawdown consolidado <= -6.4% active LOCKED_DEFENSIVE."""
        baseline = 20000.0

        # Drawdown de -5.0%: Seguro (19000 total)
        allowed_5, reason_5 = self.risk.check_consolidated_circuit_breaker(
            binance_equity=9500.0, ibkr_equity=9500.0, baseline_equity=baseline
        )
        self.assertTrue(allowed_5)
        self.assertEqual(reason_5, "ok")
        self.assertEqual(self.risk.state.circuit_breaker_status, CircuitBreakerStatus.NORMAL)

        # Drawdown de -7.0%: Brecha estricta (18600 total <= 18720)
        allowed_7, reason_7 = self.risk.check_consolidated_circuit_breaker(
            binance_equity=9300.0, ibkr_equity=9300.0, baseline_equity=baseline
        )
        self.assertFalse(allowed_7)
        self.assertIn("fiduciary_drawdown_limit_breached", reason_7)
        self.assertEqual(self.risk.state.circuit_breaker_status, CircuitBreakerStatus.LOCKED_DEFENSIVE)
        self.assertTrue(self.risk.state.circuit_breaker_paused)

    def test_trigger_dual_broker_cancellation_protects_stop_loss(self):
        """Verifica que la cancelación de emergencia cancele compras pero conserve Stop-Loss."""
        mock_binance = MagicMock()
        # Simular 2 órdenes abiertas en Binance: 1 LIMIT BUY (cancelar) y 1 STOP_LOSS_LIMIT (preservar)
        mock_binance.get_open_orders.return_value = [
            {"orderId": 1001, "type": "LIMIT", "side": "BUY"},
            {"orderId": 1002, "type": "STOP_LOSS_LIMIT", "side": "SELL"},
        ]

        mock_ibkr = MagicMock()

        results = trigger_dual_broker_cancellation(
            binance_client=mock_binance,
            ibkr_client=mock_ibkr,
            crypto_symbols=["BTCUSDT"],
            stock_symbols=["NVDA"],
        )

        # Binance: orden 1001 cancelada, 1002 preservada
        mock_binance.cancel_order.assert_called_once_with("BTCUSDT", order_id=1001)
        self.assertIn(1001, results["binance"]["BTCUSDT"])
        self.assertNotIn(1002, results["binance"]["BTCUSDT"])

        # IBKR: reqGlobalCancel invocado
        mock_ibkr.ib.reqGlobalCancel.assert_called_once()
        self.assertTrue(results["ibkr"]["global_cancel"])


if __name__ == "__main__":
    unittest.main()
