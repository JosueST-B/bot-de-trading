from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from math import sqrt
from typing import Any

import pandas as pd

from bot.config import BotConfig
from bot.models import Position, Trade
from bot.regime import classify_market_row, resample_ohlcv
from bot.risk import RiskManager
from bot.strategy import HybridStrategy


def _interval_to_minutes(interval: str) -> int:
    value = int(interval[:-1])
    unit = interval[-1].lower()
    if unit == "m":
        return value
    if unit == "h":
        return value * 60
    if unit == "d":
        return value * 60 * 24
    raise ValueError(f"Unsupported interval: {interval}")


class Backtester:
    def __init__(self, cfg: BotConfig, strategy: HybridStrategy, risk: RiskManager) -> None:
        self.cfg = cfg
        self.strategy = strategy
        self.risk = risk

    def run(self, df: pd.DataFrame) -> dict[str, Any]:
        if len(df) < 120:
            raise ValueError("Not enough data for backtest. Use a larger lookback.")

        work = self.strategy._build_features(df.copy().reset_index(drop=True))
        work = work.dropna().reset_index(drop=True)
        higher_work = None
        if self.cfg.use_multi_timeframe:
            higher_work = self.strategy._build_features(
                resample_ohlcv(work, self.cfg.higher_interval)
            ).dropna().reset_index(drop=True)
        higher_idx = -1
        macro_work = None
        if self.cfg.use_btc_macro_filter:
            macro_work = self.strategy._build_features(
                resample_ohlcv(work, self.cfg.macro_interval)
            ).dropna().reset_index(drop=True)
        macro_idx = -1

        cash = self.cfg.initial_balance
        position: Position | None = None
        entry_fee_paid = 0.0
        trades: list[Trade] = []
        equity_curve: list[dict[str, Any]] = []

        for idx in range(90, len(work)):
            row = work.iloc[idx]
            now: datetime = row["close_time"].to_pydatetime()
            close = float(row["close"])
            high = float(row["high"])
            low = float(row["low"])
            atr_value = float(row["atr"])
            if higher_work is not None and not higher_work.empty:
                while (
                    higher_idx + 1 < len(higher_work)
                    and higher_work.iloc[higher_idx + 1]["close_time"] <= row["close_time"]
                ):
                    higher_idx += 1
                regime_row = higher_work.iloc[higher_idx] if higher_idx >= 0 else row
            else:
                regime_row = row
            regime = classify_market_row(regime_row, self.cfg)
            macro_context = {"enabled": False, "allow_long": True, "score": 1.0}
            if macro_work is not None and not macro_work.empty:
                while (
                    macro_idx + 1 < len(macro_work)
                    and macro_work.iloc[macro_idx + 1]["close_time"] <= row["close_time"]
                ):
                    macro_idx += 1
                if macro_idx >= 0:
                    macro_context = self.strategy._macro_context(
                        macro_work.iloc[: macro_idx + 1]
                    )

            if position is None:
                equity = cash
                allowed, _ = self.risk.can_trade(
                    now,
                    equity,
                    regime_name=regime.name,
                    interval=self.cfg.interval,
                )
                if allowed:
                    signal = self.strategy.generate_from_features(
                        row,
                        regime,
                        macro_context=macro_context,
                        in_position=False,
                    )
                    if signal.action == "buy":
                        entry = close * (1 + self.cfg.slippage)
                        stop = entry - (atr_value * self.cfg.stop_atr_mult)
                        take = entry + (entry - stop) * self.cfg.take_profit_rr
                        qty = self.risk.position_size(
                            equity=equity,
                            entry_price=entry,
                            stop_price=stop,
                            fee_rate=self.cfg.fee_rate,
                        )
                        max_affordable = cash / (entry * (1 + self.cfg.fee_rate))
                        qty = min(qty, max_affordable)
                        if qty > 0:
                            cost = qty * entry
                            entry_fee_paid = cost * self.cfg.fee_rate
                            cash -= cost + entry_fee_paid
                            position = Position(
                                entry_time=now,
                                entry_price=entry,
                                quantity=qty,
                                stop_price=stop,
                                take_profit_price=take,
                                entry_reason=signal.reason,
                            )
                            self.risk.register_entry(now)

            else:
                trailing_stop = max(
                    position.stop_price,
                    close - (atr_value * self.cfg.trailing_atr_mult),
                )
                position.stop_price = trailing_stop

                exit_price = None
                reason = "hold"
                if low <= position.stop_price:
                    exit_price = position.stop_price * (1 - self.cfg.slippage)
                    reason = "stop_or_trailing"
                elif high >= position.take_profit_price:
                    exit_price = position.take_profit_price * (1 - self.cfg.slippage)
                    reason = "take_profit"
                else:
                    signal = self.strategy.generate_from_features(
                        row,
                        regime,
                        macro_context=macro_context,
                        in_position=True,
                        entry_reason=position.entry_reason,
                    )
                    if signal.action == "exit":
                        exit_price = close * (1 - self.cfg.slippage)
                        reason = signal.reason

                if exit_price is not None:
                    gross = position.quantity * exit_price
                    exit_fee = gross * self.cfg.fee_rate
                    cash += gross - exit_fee

                    pnl = (
                        (exit_price - position.entry_price) * position.quantity
                        - entry_fee_paid
                        - exit_fee
                    )
                    invested = position.entry_price * position.quantity
                    pnl_pct = pnl / invested if invested > 0 else 0.0

                    trade = Trade(
                        entry_time=position.entry_time,
                        exit_time=now,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        quantity=position.quantity,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        reason=reason,
                    )
                    trades.append(trade)
                    self.risk.register_trade_result(pnl, now=now)
                    position = None
                    entry_fee_paid = 0.0

            equity = cash + ((position.quantity * close) if position else 0.0)
            equity_curve.append({"time": now, "equity": equity})

        if position is not None:
            row = work.iloc[-1]
            now = row["close_time"].to_pydatetime()
            final_close = float(row["close"]) * (1 - self.cfg.slippage)
            gross = position.quantity * final_close
            exit_fee = gross * self.cfg.fee_rate
            cash += gross - exit_fee
            pnl = (
                (final_close - position.entry_price) * position.quantity
                - entry_fee_paid
                - exit_fee
            )
            invested = position.entry_price * position.quantity
            pnl_pct = pnl / invested if invested > 0 else 0.0
            trades.append(
                Trade(
                    entry_time=position.entry_time,
                    exit_time=now,
                    entry_price=position.entry_price,
                    exit_price=final_close,
                    quantity=position.quantity,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    reason="forced_end_of_data",
                )
            )
            self.risk.register_trade_result(pnl, now=now)
            equity_curve.append({"time": now, "equity": cash})

        metrics = self._metrics(equity_curve, trades)
        pnl_pcts = [t.pnl_pct for t in trades]
        mc_metrics = self.run_monte_carlo(pnl_pcts, float(self.cfg.initial_balance))
        metrics.update(mc_metrics)
        return {
            "metrics": metrics,
            "trades": [asdict(t) for t in trades],
            "equity_curve": equity_curve,
        }

    def run_monte_carlo(self, pnl_pcts: list[float], initial_balance: float, iterations: int = 1000) -> dict[str, float]:
        """Realiza simulaciones de Montecarlo barajando los retornos porcentuales de las operaciones."""
        if not pnl_pcts:
            return {"median_drawdown_pct": 0.0, "p95_drawdown_pct": 0.0, "risk_of_ruin_pct": 0.0}
            
        import random
        
        drawdowns = []
        ruin_count = 0
        
        for _ in range(iterations):
            shuffled = pnl_pcts.copy()
            random.shuffle(shuffled)
            
            balance = initial_balance
            equity_curve = [balance]
            for ret in shuffled:
                balance = balance * (1.0 + ret)
                equity_curve.append(balance)
                
            equity_series = pd.Series(equity_curve)
            peak = equity_series.cummax()
            dd_series = (equity_series / peak) - 1.0
            max_dd = abs(float(dd_series.min()))
            drawdowns.append(max_dd)
            
            # Umbral de ruina técnica del 15%
            if max_dd >= 0.15:
                ruin_count += 1
                
        drawdowns.sort()
        median_dd = drawdowns[len(drawdowns) // 2]
        p95_dd = drawdowns[int(len(drawdowns) * 0.95)]
        
        return {
            "median_drawdown_pct": float(median_dd * 100.0),
            "p95_drawdown_pct": float(p95_dd * 100.0),
            "risk_of_ruin_pct": float((ruin_count / iterations) * 100.0)
        }

    def _metrics(self, curve: list[dict[str, Any]], trades: list[Trade]) -> dict[str, float]:
        if not curve:
            return {}

        equity = pd.Series([x["equity"] for x in curve], dtype=float)
        initial = float(self.cfg.initial_balance)
        final_equity = float(equity.iloc[-1])

        peak = equity.cummax()
        drawdowns = (equity / peak) - 1
        max_drawdown = float(drawdowns.min()) if len(drawdowns) else 0.0

        returns = equity.pct_change().dropna()
        minutes = _interval_to_minutes(self.cfg.interval)
        periods_per_year = (365 * 24 * 60) / minutes
        sharpe = 0.0
        if not returns.empty and returns.std() > 0:
            sharpe = float((returns.mean() / returns.std()) * sqrt(periods_per_year))

        pnl_values = [t.pnl for t in trades]
        wins = [x for x in pnl_values if x > 0]
        losses = [x for x in pnl_values if x < 0]
        gross_profit = float(sum(wins))
        gross_loss = float(abs(sum(losses)))
        avg_win = gross_profit / len(wins) if wins else 0.0
        avg_loss = gross_loss / len(losses) if losses else 0.0
        payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = 999.0
        else:
            profit_factor = 0.0
        win_rate = (len(wins) / len(trades)) if trades else 0.0

        return {
            "initial_balance": initial,
            "final_balance": final_equity,
            "roi_pct": ((final_equity / initial) - 1) * 100,
            "max_drawdown_pct": max_drawdown * 100,
            "num_trades": float(len(trades)),
            "win_rate_pct": win_rate * 100,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": float(profit_factor),
            "avg_win": float(avg_win),
            "avg_loss": float(avg_loss),
            "payoff_ratio": float(payoff_ratio),
            "sharpe_est": sharpe,
        }
