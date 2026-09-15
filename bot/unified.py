"""Agregador de panel unificado (Fase 13).

Junta en una sola vista los tres frentes del sistema:
- Binance trading direccional (eventos live_buy / live_sell)
- Binance Earn (barridos + snapshot real de cuenta Earn si hay credenciales)
- Interactive Brokers (eventos ibkr_buy / ibkr_error + net liquidation si hay conexion)

Todo lo "live" (Earn account, IBKR) es opcional y va envuelto en try/except:
si falla, el panel sigue mostrando lo que hay en la base de eventos.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from bot.config import BotConfig
from bot.telemetry import EventStore

log = logging.getLogger("unified")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _binance_trading_block(events: list[dict[str, Any]]) -> dict[str, Any]:
    live_buys = [e for e in events if e["event"] == "live_buy"]
    live_sells = [e for e in events if e["event"] == "live_sell"]
    risk_pauses = [e for e in events if e["event"] == "risk_pause"]
    errors = [e for e in events if e["event"] == "live_error"]

    pnls = [_num(e["payload"].get("pnl")) for e in live_sells if e["payload"].get("pnl") is not None]
    wins = sum(1 for p in pnls if p > 0)
    realized = sum(pnls)
    win_rate = (wins / len(pnls) * 100) if pnls else 0.0

    per_symbol: dict[str, dict[str, Any]] = {}
    for e in live_buys + live_sells:
        sym = e.get("symbol", "?")
        per_symbol.setdefault(sym, {"buys": 0, "sells": 0, "pnl": 0.0})
        if e["event"] == "live_buy":
            per_symbol[sym]["buys"] += 1
        else:
            per_symbol[sym]["sells"] += 1
            per_symbol[sym]["pnl"] += _num(e["payload"].get("pnl"))

    last_event = events[0] if events else None
    return {
        "buys": len(live_buys),
        "sells": len(live_sells),
        "realized_pnl": round(realized, 4),
        "win_rate_pct": round(win_rate, 1),
        "risk_pauses": len(risk_pauses),
        "errors": len(errors),
        "per_symbol": per_symbol,
        "last_activity": last_event["ts"] if last_event else None,
    }


def _earn_block(events: list[dict[str, Any]], cfg: BotConfig) -> dict[str, Any]:
    sweeps = [e for e in events if e["event"] == "earn_sweep"]
    last_sweep = sweeps[0] if sweeps else None
    recent_moves: list[dict[str, Any]] = []
    for sweep in sweeps[:5]:
        for move in sweep["payload"].get("moves", []) or []:
            recent_moves.append(
                {
                    "ts": sweep["ts"],
                    "action": move.get("action"),
                    "asset": move.get("asset"),
                    "amount": move.get("amount"),
                }
            )

    block: dict[str, Any] = {
        "enabled": cfg.earn_enabled,
        "sweeps": len(sweeps),
        "last_sweep": last_sweep["ts"] if last_sweep else None,
        "recent_moves": recent_moves[:8],
        "flexible_usdt": None,
        "locked_usdt": None,
        "total_usdt": None,
        "live_error": None,
    }

    # Enriquecimiento opcional: snapshot real de la cuenta Earn
    if cfg.earn_enabled and not cfg.use_testnet:
        try:
            from bot.binance_client import BinanceExecutionClient
            from bot.earn_manager import EarnManager

            exec_client = BinanceExecutionClient(cfg)
            earn = EarnManager(cfg=cfg, client=exec_client.client)
            account = earn._sapi("get", "simple-earn/account")
            flex = _num(account.get("totalFlexibleAmountInUSDT"))
            lock = _num(account.get("totalLockedInUSDT"))
            block["flexible_usdt"] = round(flex, 2)
            block["locked_usdt"] = round(lock, 2)
            block["total_usdt"] = round(flex + lock, 2)
            positions = earn.flexible_positions()
            block["positions"] = [
                {
                    "asset": p.get("asset"),
                    "amount": round(_num(p.get("totalAmount")), 6),
                    "apr_pct": round(_num(p.get("latestAnnualPercentageRate")) * 100, 2),
                }
                for p in positions[:10]
            ]
        except Exception as exc:
            block["live_error"] = str(exc)
    return block


def _ibkr_block(events: list[dict[str, Any]], cfg: BotConfig) -> dict[str, Any]:
    ibkr_events = [e for e in events if e.get("mode") == "ibkr"]
    buys = [e for e in ibkr_events if e["event"] == "ibkr_buy"]
    errors = [e for e in ibkr_events if e["event"] == "ibkr_error"]
    last_event = ibkr_events[0] if ibkr_events else None

    block: dict[str, Any] = {
        "enabled": cfg.ibkr_enabled,
        "buys": len(buys),
        "errors": len(errors),
        "last_activity": last_event["ts"] if last_event else None,
        "last_event_name": last_event["event"] if last_event else None,
        "net_liquidation": None,
        "cash_usd": None,
        "positions": None,
        "live_error": None,
    }

    # Enriquecimiento opcional: net liquidation real desde IB Gateway
    if cfg.ibkr_enabled:
        client = None
        try:
            from bot.ibkr_client import IBKRClient

            client = IBKRClient(cfg)
            client.connect()
            block["net_liquidation"] = round(client.net_liquidation(), 2)
            block["cash_usd"] = round(client.account_cash_usd(), 2)
            positions = []
            for sym in cfg.ibkr_symbols_list:
                qty = client.position_qty(sym)
                if qty != 0:
                    positions.append({"symbol": sym, "qty": qty})
            block["positions"] = positions
            block["connected"] = True
        except Exception as exc:
            block["live_error"] = str(exc)
            block["connected"] = False
        finally:
            if client is not None:
                client.disconnect()
    return block


def build_unified_summary(cfg: BotConfig, store: EventStore) -> dict[str, Any]:
    """Ensambla el panel unificado. Nunca lanza excepciones al servidor."""
    try:
        events = store.recent_events(1000)
    except Exception as exc:
        log.warning("No se pudieron leer eventos: %s", exc)
        events = []

    binance = _binance_trading_block(events)
    earn = _earn_block(events, cfg)
    ibkr = _ibkr_block(events, cfg)

    # PnL consolidado realizado (lo que ya se cerro, no incluye posiciones abiertas)
    consolidated_realized = _num(binance.get("realized_pnl"))
    earn_total = earn.get("total_usdt")
    ibkr_net = ibkr.get("net_liquidation")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "binance_trading": binance,
        "earn": earn,
        "ibkr": ibkr,
        "consolidated": {
            "binance_realized_pnl": round(consolidated_realized, 2),
            "earn_balance_usdt": earn_total,
            "ibkr_net_liquidation": ibkr_net,
            "notes": "PnL realizado = trades cerrados. Earn e IBKR muestran saldo actual.",
        },
    }
