from __future__ import annotations

from dataclasses import replace
from itertools import product
from math import isfinite
from typing import Any

import pandas as pd

from bot.backtester import Backtester
from bot.config import BotConfig
from bot.risk import RiskManager
from bot.strategy import HybridStrategy


def _score(metrics: dict[str, float]) -> float:
    roi = metrics.get("roi_pct", 0.0)
    max_dd = abs(metrics.get("max_drawdown_pct", 0.0))
    win_rate = metrics.get("win_rate_pct", 0.0)
    profit_factor = metrics.get("profit_factor", 0.0)
    sharpe = metrics.get("sharpe_est", 0.0)
    trades = metrics.get("num_trades", 0.0)
    if not isfinite(profit_factor):
        profit_factor = 3.0
    profit_factor = min(profit_factor, 3.0)

    score = (roi * 2.0) + (profit_factor * 6.0) + (win_rate * 0.03) + (sharpe * 0.15)
    score -= max_dd * 1.2
    if roi <= 0:
        score -= 8.0
    if profit_factor < 1.0:
        score -= (1.0 - profit_factor) * 8.0
    if trades < 5:
        score -= 5.0
    return score


def optimize_config(
    base_cfg: BotConfig,
    df: pd.DataFrame,
    top_n: int = 5,
    grid: dict[str, list[Any]] | None = None,
    min_trades: int = 4,
) -> list[dict[str, Any]]:
    if grid is None:
        grid = {
            "strategy_mode": ["auto", "trend", "breakout", "pullback_trend", "mean_reversion"],
            "stop_atr_mult": [1.2, 1.8, 2.4],
            "take_profit_rr": [1.4, 2.0, 2.2, 2.8],
            "trailing_atr_mult": [1.0],
            "min_confidence": [0.5, 0.55, 0.6, 0.65],
            "min_entry_quality": [0.68, 0.72, 0.78],
            "min_trend_strength": [0.001, 0.003],
            "risk_per_trade": [0.005, 0.01],
        }

    keys = list(grid.keys())
    values = [grid[k] for k in keys]
    candidates: list[dict[str, Any]] = []

    for combo in product(*values):
        params = dict(zip(keys, combo))
        cfg = replace(base_cfg, **params)
        strategy = HybridStrategy(cfg)
        risk = RiskManager(cfg)
        bt = Backtester(cfg, strategy, risk)
        result = bt.run(df)
        metrics = result["metrics"]

        if metrics.get("num_trades", 0.0) < float(min_trades):
            continue

        score = _score(metrics)
        candidates.append(
            {
                "score": score,
                "params": params,
                "metrics": metrics,
            }
        )

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_n]
