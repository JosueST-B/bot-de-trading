from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import Any

import pandas as pd

from bot.backtester import Backtester
from bot.config import BotConfig
from bot.optimizer import optimize_config
from bot.risk import RiskManager
from bot.strategy import HybridStrategy


def _finite_or_zero(value: float) -> float:
    return value if isfinite(value) else 0.0


def _run_backtest(cfg: BotConfig, df: pd.DataFrame) -> dict[str, Any]:
    strategy = HybridStrategy(cfg)
    risk = RiskManager(cfg)
    bt = Backtester(cfg, strategy, risk)
    return bt.run(df)


def run_walkforward(
    base_cfg: BotConfig,
    df: pd.DataFrame,
    train_size: int = 360,
    test_size: int = 180,
    step_size: int = 180,
    top_n: int = 1,
    fast_grid: bool = True,
) -> dict[str, Any]:
    if train_size < 120:
        raise ValueError("train_size must be >= 120.")
    if test_size < 120:
        raise ValueError("test_size must be >= 120.")
    if step_size <= 0:
        raise ValueError("step_size must be > 0.")
    if len(df) < (train_size + test_size):
        raise ValueError(
            f"Not enough data for walk-forward. Need at least {train_size + test_size} rows."
        )

    wf_grid = None
    if fast_grid:
        wf_grid = {
            "strategy_mode": ["auto", "trend", "breakout", "pullback_trend", "mean_reversion"],
            "use_regime_filter": [True, False],
            "use_multi_timeframe": [True, False],
            "stop_atr_mult": [1.0, 1.6, 1.8, 2.2],
            "take_profit_rr": [0.8, 1.2, 2.0, 2.2],
            "trailing_atr_mult": [1.0],
            "min_confidence": [0.35, 0.5, 0.55, 0.65],
            "min_trend_strength": [0.0005, 0.002],
            "risk_per_trade": [0.005, 0.01],
        }

    folds: list[dict[str, Any]] = []
    start = 0
    fold_idx = 0

    while start + train_size + test_size <= len(df):
        fold_idx += 1
        train_df = df.iloc[start : start + train_size].reset_index(drop=True)
        test_df = df.iloc[start + train_size : start + train_size + test_size].reset_index(
            drop=True
        )

        candidates = optimize_config(
            base_cfg,
            train_df,
            top_n=max(top_n, 1),
            grid=wf_grid,
            min_trades=1,
        )
        if candidates:
            best = candidates[0]
            tuned_cfg = replace(base_cfg, **best["params"])
            selected_params = best["params"]
            train_best_score = best["score"]
        else:
            tuned_cfg = base_cfg
            selected_params = {}
            train_best_score = 0.0

        test_result = _run_backtest(tuned_cfg, test_df)
        test_metrics = test_result["metrics"]

        folds.append(
            {
                "fold": fold_idx,
                "train_rows": int(len(train_df)),
                "test_rows": int(len(test_df)),
                "train_start": str(train_df["open_time"].iloc[0]),
                "train_end": str(train_df["close_time"].iloc[-1]),
                "test_start": str(test_df["open_time"].iloc[0]),
                "test_end": str(test_df["close_time"].iloc[-1]),
                "selected_params": selected_params,
                "train_best_score": train_best_score,
                "test_metrics": test_metrics,
            }
        )

        start += step_size

    if not folds:
        raise ValueError("No walk-forward folds generated. Check sizes and lookback.")

    rois = [float(f["test_metrics"].get("roi_pct", 0.0)) for f in folds]
    dds = [float(f["test_metrics"].get("max_drawdown_pct", 0.0)) for f in folds]
    sharpes = [float(f["test_metrics"].get("sharpe_est", 0.0)) for f in folds]
    trades = [float(f["test_metrics"].get("num_trades", 0.0)) for f in folds]
    win_rates = [float(f["test_metrics"].get("win_rate_pct", 0.0)) for f in folds]

    total_trades = sum(trades)
    weighted_win_rate = (
        sum(w * n for w, n in zip(win_rates, trades)) / total_trades if total_trades > 0 else 0.0
    )

    compounded_balance = base_cfg.initial_balance
    for roi in rois:
        compounded_balance *= 1 + (roi / 100)

    summary = {
        "folds": len(folds),
        "avg_test_roi_pct": sum(rois) / len(rois),
        "median_like_test_roi_pct": sorted(rois)[len(rois) // 2],
        "positive_fold_rate_pct": (sum(1 for x in rois if x > 0) / len(rois)) * 100,
        "worst_test_drawdown_pct": min(dds) if dds else 0.0,
        "avg_test_sharpe_est": sum(_finite_or_zero(x) for x in sharpes) / len(sharpes),
        "total_test_trades": total_trades,
        "weighted_test_win_rate_pct": weighted_win_rate,
        "compounded_balance": compounded_balance,
        "compounded_roi_pct": ((compounded_balance / base_cfg.initial_balance) - 1) * 100,
    }

    return {"summary": summary, "folds_detail": folds}


def run_fixed_walkforward(
    cfg: BotConfig,
    df: pd.DataFrame,
    train_size: int = 500,
    test_size: int = 200,
    step_size: int = 100,
) -> dict[str, Any]:
    if len(df) < (train_size + test_size):
        raise ValueError(
            f"Not enough data for fixed walk-forward. Need at least {train_size + test_size} rows."
        )

    folds: list[dict[str, Any]] = []
    start = 0
    fold_idx = 0
    while start + train_size + test_size <= len(df):
        fold_idx += 1
        test_df = df.iloc[start + train_size : start + train_size + test_size].reset_index(
            drop=True
        )
        result = _run_backtest(cfg, test_df)
        folds.append(
            {
                "fold": fold_idx,
                "test_rows": int(len(test_df)),
                "test_start": str(test_df["open_time"].iloc[0]),
                "test_end": str(test_df["close_time"].iloc[-1]),
                "test_metrics": result["metrics"],
            }
        )
        start += step_size

    rois = [float(f["test_metrics"].get("roi_pct", 0.0)) for f in folds]
    dds = [float(f["test_metrics"].get("max_drawdown_pct", 0.0)) for f in folds]
    trades = [float(f["test_metrics"].get("num_trades", 0.0)) for f in folds]
    win_rates = [float(f["test_metrics"].get("win_rate_pct", 0.0)) for f in folds]
    profit_factors = [float(f["test_metrics"].get("profit_factor", 0.0)) for f in folds]
    gross_profit = sum(float(f["test_metrics"].get("gross_profit", 0.0)) for f in folds)
    gross_loss = sum(float(f["test_metrics"].get("gross_loss", 0.0)) for f in folds)
    aggregate_profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

    total_trades = sum(trades)
    weighted_win_rate = (
        sum(w * n for w, n in zip(win_rates, trades)) / total_trades if total_trades > 0 else 0.0
    )
    compounded_balance = cfg.initial_balance
    for roi in rois:
        compounded_balance *= 1 + (roi / 100)

    summary = {
        "folds": len(folds),
        "avg_test_roi_pct": sum(rois) / len(rois) if rois else 0.0,
        "positive_fold_rate_pct": (sum(1 for x in rois if x > 0) / len(rois) * 100)
        if rois
        else 0.0,
        "worst_test_drawdown_pct": min(dds) if dds else 0.0,
        "total_test_trades": total_trades,
        "weighted_test_win_rate_pct": weighted_win_rate,
        "avg_profit_factor": sum(profit_factors) / len(profit_factors)
        if profit_factors
        else 0.0,
        "aggregate_profit_factor": aggregate_profit_factor,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "compounded_balance": compounded_balance,
        "compounded_roi_pct": ((compounded_balance / cfg.initial_balance) - 1) * 100,
    }
    passed = (
        summary["compounded_roi_pct"] > 0
        and summary["positive_fold_rate_pct"] >= 50
        and summary["aggregate_profit_factor"] >= 1.1
        and summary["total_test_trades"] >= 10
    )
    reasons = []
    if summary["compounded_roi_pct"] <= 0:
        reasons.append("compounded_roi_not_positive")
    if summary["positive_fold_rate_pct"] < 50:
        reasons.append("positive_fold_rate_below_50")
    if summary["aggregate_profit_factor"] < 1.1:
        reasons.append("aggregate_profit_factor_below_1_1")
    if summary["total_test_trades"] < 10:
        reasons.append("too_few_test_trades")
    summary["quality_gate_passed"] = passed
    summary["quality_gate_reasons"] = reasons
    return {"summary": summary, "folds_detail": folds}
