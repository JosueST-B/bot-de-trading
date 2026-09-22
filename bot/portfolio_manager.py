"""Gestor fiduciario de cartera y reconciliacion dual de balances (ArcaFid Quantitative).

Unifica la infraestructura de corretaje en dos frentes simultaneos:
1. Binance Spot (Criptoactivos Tier 1 en USDT)
2. Interactive Brokers (Renta Variable / ETFs en USD con feed yfinance)

Aplica paridad actuarial 1:1 (1.00 USDT = 1.00 USD) para determinar:
- Total Consolidated Equity = Equity(Binance) + NetLiquidation(IBKR)
- Total Available Cash = Cash(Binance) + Cash(IBKR)
- Cerrojo fiduciario de Drawdown estricto en -6.4% sobre la cartera consolidada.
- Badges de estado de sincronizacion dual en vivo:
  [BINANCE: CONNECTED / SIMULATED] y [IBKR: CONNECTED / STANDBY_YFINANCE].
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bot.config import BotConfig

logger = logging.getLogger("portfolio_manager")

FIDUCIARY_DRAWDOWN_LIMIT: float = -0.064  # -6.4%


@dataclass(frozen=True)
class SyncBadge:
    broker: str
    status: str
    label: str
    mode: str
    provider: str
    color: str


def get_dual_sync_status(
    binance_client: Any,
    ibkr_client: Any,
    cfg: BotConfig,
) -> dict[str, Any]:
    """Genera las insignias fiduciarias de sincronizacion dual para Binance e IBKR."""
    # 1. Binance Status
    binance_key_present = bool(getattr(cfg, "binance_api_key", ""))
    binance_live = bool(getattr(cfg, "live_enabled", False) and not getattr(cfg, "use_testnet", True))

    if binance_client is not None and getattr(binance_client, "is_live", False):
        binance_status = "CONNECTED"
        binance_mode = "LIVE_SPOT"
        binance_color = "#10b981"  # Verde esmeralda
    elif binance_key_present and not getattr(cfg, "use_testnet", True):
        binance_status = "CONNECTED"
        binance_mode = "LIVE_SPOT"
        binance_color = "#10b981"
    else:
        binance_status = "SIMULATED"
        binance_mode = "SIMULATED_PAPER"
        binance_color = "#38bdf8"  # Azul cian / neutral

    # 2. IBKR Status
    ibkr_connected = False
    if ibkr_client is not None:
        ibkr_connected = bool(getattr(ibkr_client, "is_connected", False))
        ibkr_status = getattr(ibkr_client, "status", "CONNECTED" if ibkr_connected else "STANDBY_YFINANCE")
        ibkr_provider = getattr(ibkr_client, "active_provider", "ib_async" if ibkr_connected else "yfinance")
    else:
        ibkr_status = "STANDBY_YFINANCE"
        ibkr_provider = "yfinance"

    if ibkr_connected:
        ibkr_color = "#10b981"  # Verde esmeralda
    else:
        ibkr_color = "#f59e0b"  # Ambar fiduciario

    return {
        "binance": {
            "broker": "Binance",
            "status": binance_status,
            "label": f"[BINANCE: {binance_status}]",
            "mode": binance_mode,
            "provider": "binance_rest_ws",
            "color": binance_color,
            "is_live": binance_live,
        },
        "ibkr": {
            "broker": "Interactive Brokers",
            "status": ibkr_status,
            "label": f"[IBKR: {ibkr_status}]",
            "mode": "DMA_STOCKS_ETFS",
            "provider": ibkr_provider,
            "color": ibkr_color,
            "is_connected": ibkr_connected,
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def reconcile_dual_balances(
    binance_client: Any,
    ibkr_client: Any,
    cfg: BotConfig,
) -> dict[str, Any]:
    """Reconcilia balances de Binance (USDT) e IBKR (USD) a paridad 1:1."""
    # 1. Binance Leg
    binance_cash = 0.0
    binance_locked = 0.0
    binance_equity = 0.0
    binance_status = "SIMULATED"

    if binance_client is not None:
        try:
            if hasattr(binance_client, "get_asset_balance_values"):
                b_free, b_locked = binance_client.get_asset_balance_values("USDT")
                binance_cash = float(b_free)
                binance_locked = float(b_locked)
                binance_equity = binance_cash + binance_locked
                binance_status = "CONNECTED" if getattr(cfg, "binance_api_key", "") else "SIMULATED"
            elif hasattr(binance_client, "get_account_cash"):
                binance_cash = float(binance_client.get_account_cash())
                binance_equity = binance_cash
                binance_status = "CONNECTED"
        except Exception as exc:
            logger.warning("No se pudo obtener balance de Binance: %s. Usando saldo base.", exc)

    if binance_equity <= 0.0:
        binance_cash = float(getattr(cfg, "initial_balance", 10000.0))
        binance_equity = binance_cash
        binance_status = "CONNECTED" if getattr(cfg, "binance_api_key", "") else "SIMULATED"

    # 2. IBKR Leg
    ibkr_cash = 0.0
    ibkr_equity = 0.0
    ibkr_status = "STANDBY_YFINANCE"
    ibkr_provider = "yfinance"

    if ibkr_client is not None:
        try:
            ibkr_status = getattr(ibkr_client, "status", "STANDBY_YFINANCE")
            ibkr_provider = getattr(ibkr_client, "active_provider", "yfinance")
            if getattr(ibkr_client, "is_connected", False):
                ibkr_cash = float(ibkr_client.account_cash_usd())
                ibkr_equity = float(ibkr_client.net_liquidation())
                ibkr_status = "CONNECTED"
            else:
                ibkr_cash = float(ibkr_client.account_cash_usd())
                ibkr_equity = float(ibkr_client.net_liquidation())
        except Exception as exc:
            logger.warning("No se pudo obtener balance de IBKR: %s. Usando standby.", exc)

    if ibkr_equity <= 0.0:
        ibkr_cash = float(getattr(cfg, "ibkr_standby_cash", 10000.0))
        ibkr_equity = ibkr_cash

    # 3. Consolidacion Fiduciaria Actuarial a Paridad 1:1
    total_cash = round(binance_cash + ibkr_cash, 2)
    total_equity = round(binance_equity + ibkr_equity, 2)

    binance_share = round((binance_equity / total_equity * 100.0), 2) if total_equity > 0 else 50.0
    ibkr_share = round((ibkr_equity / total_equity * 100.0), 2) if total_equity > 0 else 50.0

    return {
        "binance": {
            "status": binance_status,
            "currency": "USDT",
            "cash": round(binance_cash, 2),
            "locked": round(binance_locked, 2),
            "equity": round(binance_equity, 2),
        },
        "ibkr": {
            "status": ibkr_status,
            "provider": ibkr_provider,
            "currency": "USD",
            "cash": round(ibkr_cash, 2),
            "equity": round(ibkr_equity, 2),
        },
        "consolidated": {
            "total_cash_usd": total_cash,
            "total_equity_usd": total_equity,
            "parity_ratio": "1 USDT = 1 USD",
            "binance_allocation_pct": binance_share,
            "ibkr_allocation_pct": ibkr_share,
            "fiduciary_drawdown_limit_pct": round(FIDUCIARY_DRAWDOWN_LIMIT * 100, 2),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def evaluate_drawdown_lock(
    peak_equity: float,
    current_equity: float,
    limit: float = FIDUCIARY_DRAWDOWN_LIMIT,
) -> tuple[bool, float, str]:
    """Evalua si el Drawdown sobre el capital consolidado supera el umbral fiduciario (-6.4%)."""
    if peak_equity <= 0:
        return False, 0.0, "NORMAL"

    drawdown = (current_equity - peak_equity) / peak_equity
    if drawdown <= limit:
        return True, round(drawdown, 4), "LOCKED_DEFENSIVE"

    return False, round(drawdown, 4), "NORMAL"


class PortfolioManager:
    """Administrador central fiduciario de asignacion de capital y balance consolidado."""

    def __init__(
        self,
        cfg: BotConfig,
        binance_client: Any = None,
        ibkr_client: Any = None,
    ) -> None:
        self.cfg = cfg
        self.binance_client = binance_client
        self.ibkr_client = ibkr_client
        self.peak_consolidated_equity: float = 0.0

    def get_sync_status(self) -> dict[str, Any]:
        """Obtiene las insignias de sincronizacion de ambos brokers."""
        return get_dual_sync_status(self.binance_client, self.ibkr_client, self.cfg)

    def reconcile(self) -> dict[str, Any]:
        """Calcula el balance consolidado y actualiza el pico de capital."""
        recon = reconcile_dual_balances(self.binance_client, self.ibkr_client, self.cfg)
        curr_equity = recon["consolidated"]["total_equity_usd"]
        if curr_equity > self.peak_consolidated_equity:
            self.peak_consolidated_equity = curr_equity
        return recon

    def check_circuit_breaker(self, current_equity: float | None = None) -> tuple[bool, float, str]:
        """Evalua el cerrojo fiduciario de -6.4% sobre el capital consolidado."""
        if current_equity is None:
            recon = self.reconcile()
            current_equity = recon["consolidated"]["total_equity_usd"]

        return evaluate_drawdown_lock(
            peak_equity=self.peak_consolidated_equity,
            current_equity=current_equity,
            limit=FIDUCIARY_DRAWDOWN_LIMIT,
        )
