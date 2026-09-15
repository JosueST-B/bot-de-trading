import unittest
import os
from bot.auto_evolution import AutonomousEvolutionEngine, HuggingFaceMarketIntelligence, StrategyWeights


class TestAutoEvolution(unittest.TestCase):
    def setUp(self):
        self.test_state_file = "test_evolution_state.json"
        if os.path.exists(self.test_state_file):
            os.remove(self.test_state_file)

    def tearDown(self):
        if os.path.exists(self.test_state_file):
            os.remove(self.test_state_file)

    def test_weights_normalization(self):
        w = StrategyWeights(alpha_hawkes=0.5, momentum_trend=0.5, huggingface_sentiment=0.5, mean_reversion=0.5)
        w.normalize()
        total = round(w.alpha_hawkes + w.momentum_trend + w.huggingface_sentiment + w.mean_reversion, 2)
        self.assertAlmostEqual(total, 1.0, places=2)

    def test_huggingface_sentiment(self):
        hf = HuggingFaceMarketIntelligence()
        res = hf.analyze_market_narrative(["Bitcoin adoption surges with massive ETF inflow"])
        self.assertIn("sentiment_score", res)
        self.assertIn("label", res)
        self.assertEqual(res["label"], "BULLISH")
        self.assertGreater(res["sentiment_score"], 0)

    def test_step_evolution(self):
        eng = AutonomousEvolutionEngine(state_file=self.test_state_file)
        init_gen = eng.generation
        step = eng.step_evolution(trade_pnl_pct=2.45, regime="trend")
        self.assertEqual(step["status"], "success")
        self.assertEqual(step["generation"], init_gen + 1)
        self.assertIn("weights", step)
        self.assertTrue(os.path.exists(self.test_state_file))

    def test_get_status(self):
        eng = AutonomousEvolutionEngine(state_file=self.test_state_file)
        status = eng.get_status()
        self.assertEqual(status["status"], "active")
        self.assertIn("huggingface_nlp", status)
        self.assertIn("drift_detection", status)


if __name__ == "__main__":
    unittest.main()
