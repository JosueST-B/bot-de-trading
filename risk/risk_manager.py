from __future__ import annotations

from dataclasses import replace

from bot.config import BotConfig
from bot.risk import RiskManager as ModernRiskManager


class RiskManager:
    """
    Legacy-compatible risk manager facade.

    It preserves the old API (`update_balance`, `calculate_position_size`,
    `calculate_sl_tp`) but uses the same configuration basis as the current
    engine in `bot/`.
    """

    def __init__(self, portfolio_balance: float = 1000.0, risk_per_trade: float | None = None):
        self.portfolio_balance = float(portfolio_balance)
        self.cfg = BotConfig.from_env()
        if risk_per_trade is not None:
            self.cfg = replace(self.cfg, risk_per_trade=float(risk_per_trade))
        self.risk_per_trade = self.cfg.risk_per_trade
        self._modern = ModernRiskManager(self.cfg)

    def update_balance(self, new_balance: float) -> None:
        self.portfolio_balance = float(new_balance)

    def calculate_position_size(self, current_price: float, sl_price: float) -> float:
        if current_price <= 0 or sl_price <= 0 or current_price <= sl_price:
            return 0.0
        return self._modern.position_size(
            equity=self.portfolio_balance,
            entry_price=float(current_price),
            stop_price=float(sl_price),
            fee_rate=self.cfg.fee_rate,
        )

    def calculate_sl_tp(
        self,
        current_price: float,
        atr: float,
        sl_atr_multiplier: float = 2.0,
        risk_reward_ratio: float = 2.0,
    ) -> tuple[float, float]:
        if current_price <= 0 or atr <= 0:
            return 0.0, 0.0
        stop_loss_distance = float(atr) * float(sl_atr_multiplier)
        stop_loss_price = float(current_price) - stop_loss_distance
        take_profit_price = float(current_price) + (
            stop_loss_distance * float(risk_reward_ratio)
        )
        return stop_loss_price, take_profit_price


if __name__ == "__main__":
    manager = RiskManager(portfolio_balance=5000)
    precio_actual_btc = 66000.0
    atr_actual = 500.0

    sl_price, tp_price = manager.calculate_sl_tp(
        precio_actual_btc,
        atr_actual,
        sl_atr_multiplier=1.5,
        risk_reward_ratio=2.0,
    )
    size = manager.calculate_position_size(precio_actual_btc, sl_price)

    print(f"Balance Total: ${manager.portfolio_balance}")
    print(f"Riesgo Maximo: ${manager.portfolio_balance * manager.risk_per_trade}")
    print("--- Operacion ---")
    print(f"Precio Entrada: ${precio_actual_btc}")
    print(f"Stop Loss: ${sl_price} (Distancia: ${precio_actual_btc - sl_price})")
    print(f"Take Profit: ${tp_price}")
    print(f"Tamano de Posicion en BTC: {size:.4f} BTC")
    print(f"Capital Invertido: ${size * precio_actual_btc:.2f}")
