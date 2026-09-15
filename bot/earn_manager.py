"""Gestor de productos Earn de Binance (Fase 11).

Automatiza:
- Simple Earn Flexible: suscribe saldo ocioso y lo redime al vuelo cuando
  el bot necesita liquidez para operar.
- Simple Earn Locked: bloquea una porcion configurable del capital que
  supere un umbral minimo (mayor APR, sin liquidez inmediata).
- Dust a BNB: convierte polvo de monedas no operadas a BNB.
- Reporte de posiciones, APR y recompensas via Telegram.

Todo es tolerante a fallos: un error en Earn nunca debe tumbar el loop
de trading. Cada operacion se puede simular con dry_run=True.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from bot.config import BotConfig

log = logging.getLogger("earn")

STABLE_QUOTES = ("USDT", "USDC", "FDUSD", "TUSD")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class EarnManager:
    cfg: BotConfig
    client: Any  # binance.client.Client (misma sesion firmada del bot)
    dry_run: bool = False
    _last_sweep: datetime | None = field(default=None, init=False)
    _last_news_check: datetime | None = field(default=None, init=False)
    _flexible_products_cache: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    NEWS_KEYWORDS = ("launchpool", "hodler", "megadrop", "airdrop")
    NEWS_SEEN_PATH = Path("earn_seen_announcements.json")
    NEWS_ENDPOINTS = (
        "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
        "?catalogId=48&pageNo=1&pageSize=20",
        "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query"
        "?type=1&pageNo=1&pageSize=20",
    )

    # ------------------------------------------------------------------
    # Capa de acceso SAPI (compatible con varias versiones de python-binance)
    # ------------------------------------------------------------------
    def _sapi(self, method: str, path: str, **params: Any) -> Any:
        """Llama un endpoint /sapi/v1 firmado."""
        return self.client._request_margin_api(method, path, signed=True, data=params)

    # ------------------------------------------------------------------
    # Simple Earn Flexible
    # ------------------------------------------------------------------
    def flexible_product_for(self, asset: str) -> dict[str, Any] | None:
        asset = asset.upper()
        if asset in self._flexible_products_cache:
            return self._flexible_products_cache[asset]
        try:
            res = self._sapi("get", "simple-earn/flexible/list", asset=asset, size=100)
        except Exception as exc:
            log.warning("No se pudo listar productos flexibles de %s: %s", asset, exc)
            return None
        rows = res.get("rows", []) if isinstance(res, dict) else []
        candidates = [r for r in rows if not r.get("isSoldOut")]
        if not candidates:
            return None
        best = max(candidates, key=lambda r: _f(r.get("latestAnnualPercentageRate")))
        self._flexible_products_cache[asset] = best
        return best

    def flexible_positions(self, asset: str | None = None) -> list[dict[str, Any]]:
        try:
            params: dict[str, Any] = {"size": 100}
            if asset:
                params["asset"] = asset.upper()
            res = self._sapi("get", "simple-earn/flexible/position", **params)
        except Exception as exc:
            log.warning("No se pudieron leer posiciones flexibles: %s", exc)
            return []
        return res.get("rows", []) if isinstance(res, dict) else []

    def flexible_balance(self, asset: str) -> float:
        return sum(_f(p.get("totalAmount")) for p in self.flexible_positions(asset))

    def subscribe_flexible(self, asset: str, amount: float) -> dict[str, Any]:
        product = self.flexible_product_for(asset)
        if not product:
            return {"ok": False, "reason": f"no_flexible_product:{asset}"}
        min_amount = _f(product.get("minPurchaseAmount"), 0.1)
        if amount < min_amount:
            return {"ok": False, "reason": f"below_min:{asset}:{min_amount}"}
        if self.dry_run:
            return {"ok": True, "dry_run": True, "asset": asset, "amount": amount}
        try:
            res = self._sapi(
                "post",
                "simple-earn/flexible/subscribe",
                productId=product.get("productId"),
                amount=f"{amount:.8f}",
                autoSubscribe="true",  # auto-compound de recompensas
                sourceAccount="SPOT",
            )
            log.info("Earn: suscrito %.8f %s a flexible (APR %s)", amount, asset,
                     product.get("latestAnnualPercentageRate"))
            return {"ok": bool(res.get("success", True)), "asset": asset, "amount": amount}
        except Exception as exc:
            log.warning("Earn: fallo al suscribir %s: %s", asset, exc)
            return {"ok": False, "reason": str(exc)}

    def redeem_flexible(self, asset: str, amount: float | None = None) -> dict[str, Any]:
        """Redime `amount` (o todo si None) del asset desde flexible."""
        positions = self.flexible_positions(asset)
        if not positions:
            return {"ok": False, "reason": f"no_position:{asset}"}
        remaining = amount
        redeemed = 0.0
        for pos in positions:
            avail = _f(pos.get("totalAmount"))
            if avail <= 0:
                continue
            take = avail if remaining is None else min(avail, remaining)
            if take <= 0:
                break
            if self.dry_run:
                redeemed += take
            else:
                try:
                    params: dict[str, Any] = {"productId": pos.get("productId")}
                    if remaining is None or take >= avail:
                        params["redeemAll"] = "true"
                    else:
                        params["amount"] = f"{take:.8f}"
                    self._sapi("post", "simple-earn/flexible/redeem", **params)
                    redeemed += take
                except Exception as exc:
                    log.warning("Earn: fallo al redimir %s: %s", asset, exc)
                    continue
            if remaining is not None:
                remaining -= take
                if remaining <= 0:
                    break
        if redeemed > 0:
            log.info("Earn: redimido %.8f %s de flexible", redeemed, asset)
        return {"ok": redeemed > 0, "asset": asset, "redeemed": redeemed}

    def ensure_quote_available(self, asset: str, needed: float) -> float:
        """Garantiza `needed` libre en spot redimiendo de flexible si falta.

        Devuelve cuanto se redimio (0 si no hizo falta o no se pudo).
        """
        if not self.cfg.earn_enabled or needed <= 0:
            return 0.0
        try:
            bal = self.client.get_asset_balance(asset=asset.upper()) or {}
            free = _f(bal.get("free"))
        except Exception as exc:
            log.warning("Earn: no se pudo leer balance de %s: %s", asset, exc)
            return 0.0
        if free >= needed:
            return 0.0
        shortfall = needed - free
        res = self.redeem_flexible(asset, shortfall * 1.001)  # margen por redondeo
        return _f(res.get("redeemed"))

    # ------------------------------------------------------------------
    # Simple Earn Locked
    # ------------------------------------------------------------------
    def maybe_subscribe_locked(self, asset: str, free_amount: float) -> dict[str, Any]:
        """Bloquea una porcion del capital solo si supera el umbral configurado."""
        if not self.cfg.earn_locked_enabled:
            return {"ok": False, "reason": "locked_disabled"}
        if free_amount < self.cfg.earn_locked_min_free:
            return {"ok": False, "reason": "below_locked_threshold"}
        amount = free_amount * self.cfg.earn_locked_max_pct
        try:
            res = self._sapi("get", "simple-earn/locked/list", asset=asset.upper(), size=100)
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}
        rows = res.get("rows", []) if isinstance(res, dict) else []
        best = None
        best_apr = 0.0
        for row in rows:
            detail = row.get("detail", {})
            quota = row.get("quota", {})
            duration = int(_f(detail.get("duration")))
            if duration > self.cfg.earn_locked_max_duration_days:
                continue
            if detail.get("status") and detail.get("status") != "PURCHASING":
                continue
            apr = _f(detail.get("apr"))
            min_amt = _f(quota.get("minimum"), 0.0)
            if amount < min_amt:
                continue
            if apr > best_apr:
                best_apr = apr
                best = detail
        if not best:
            return {"ok": False, "reason": "no_locked_product_fits"}
        if self.dry_run:
            return {"ok": True, "dry_run": True, "asset": asset, "amount": amount, "apr": best_apr}
        try:
            self._sapi(
                "post",
                "simple-earn/locked/subscribe",
                projectId=best.get("projectId"),
                amount=f"{amount:.8f}",
            )
            log.info("Earn: bloqueado %.4f %s por %s dias (APR %.2f%%)",
                     amount, asset, best.get("duration"), best_apr * 100)
            return {"ok": True, "asset": asset, "amount": amount, "apr": best_apr}
        except Exception as exc:
            log.warning("Earn: fallo locked subscribe %s: %s", asset, exc)
            return {"ok": False, "reason": str(exc)}

    # ------------------------------------------------------------------
    # On-Chain Yields (staking on-chain locked, APR mayor)
    # ------------------------------------------------------------------
    def maybe_subscribe_onchain(self, asset: str, free_amount: float) -> dict[str, Any]:
        """Suscribe On-Chain Yields locked solo si supera el umbral configurado.

        Mismo gating que locked: porcentaje maximo, duracion maxima y
        renovacion automatica (autoSubscribe) activada.
        """
        if not self.cfg.earn_onchain_enabled:
            return {"ok": False, "reason": "onchain_disabled"}
        if free_amount < self.cfg.earn_onchain_min_free:
            return {"ok": False, "reason": "below_onchain_threshold"}
        amount = free_amount * self.cfg.earn_onchain_max_pct
        try:
            res = self._sapi("get", "onchain-yields/locked/list", asset=asset.upper(), size=100)
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}
        rows = res.get("rows", []) if isinstance(res, dict) else []
        best = None
        best_apr = 0.0
        for row in rows:
            detail = row.get("detail", {})
            quota = row.get("quota", {})
            if detail.get("isSoldOut"):
                continue
            if detail.get("status") and detail.get("status") != "PURCHASING":
                continue
            duration = int(_f(detail.get("duration")))
            if duration > self.cfg.earn_onchain_max_duration_days:
                continue
            if amount < _f(quota.get("minimum")):
                continue
            apr = _f(detail.get("apr"))
            if apr > best_apr:
                best_apr = apr
                best = row
        if not best:
            return {"ok": False, "reason": "no_onchain_product_fits"}
        project_id = best.get("projectId") or best.get("detail", {}).get("projectId")
        if self.dry_run:
            return {"ok": True, "dry_run": True, "asset": asset, "amount": amount,
                    "apr": best_apr, "project": project_id}
        try:
            self._sapi(
                "post",
                "onchain-yields/locked/subscribe",
                projectId=project_id,
                amount=f"{amount:.8f}",
                autoSubscribe="true",
            )
            log.info("Earn: on-chain %.4f %s en %s (APR %.2f%%)",
                     amount, asset, project_id, best_apr * 100)
            return {"ok": True, "asset": asset, "amount": amount,
                    "apr": best_apr, "project": project_id}
        except Exception as exc:
            log.warning("Earn: fallo on-chain subscribe %s: %s", asset, exc)
            return {"ok": False, "reason": str(exc)}

    # ------------------------------------------------------------------
    # Monitor de anuncios (Launchpool / HODLer / Megadrop)
    # ------------------------------------------------------------------
    def _load_seen_news(self) -> set[str]:
        try:
            return set(json.loads(self.NEWS_SEEN_PATH.read_text(encoding="utf-8")))
        except Exception:
            return set()

    def _save_seen_news(self, seen: set[str]) -> None:
        try:
            self.NEWS_SEEN_PATH.write_text(
                json.dumps(sorted(seen)[-200:]), encoding="utf-8"
            )
        except Exception as exc:
            log.warning("Earn: no se pudo guardar estado de anuncios: %s", exc)

    @staticmethod
    def _extract_articles(payload: Any) -> list[dict[str, Any]]:
        """Busca recursivamente listas de articulos {title, code/id} en el JSON."""
        found: list[dict[str, Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("title") and (node.get("code") or node.get("id")):
                    found.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return found

    def check_announcements(self, telemetry: Any | None = None) -> dict[str, Any]:
        """Vigila anuncios publicos de Binance y alerta eventos de airdrop.

        Solo observabilidad (sin API oficial de eventos): si falla, no pasa nada.
        """
        if not self.cfg.earn_news_enabled:
            return {"ok": False, "reason": "news_disabled"}
        now = datetime.now(timezone.utc)
        if (
            self._last_news_check is not None
            and (now - self._last_news_check).total_seconds()
            < self.cfg.earn_news_interval_minutes * 60
        ):
            return {"ok": False, "reason": "throttled"}
        self._last_news_check = now
        articles: list[dict[str, Any]] = []
        for url in self.NEWS_ENDPOINTS:
            try:
                resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                articles = self._extract_articles(resp.json())
                if articles:
                    break
            except Exception as exc:
                log.debug("Earn: endpoint de anuncios fallo (%s): %s", url, exc)
        if not articles:
            return {"ok": False, "reason": "no_articles"}
        seen = self._load_seen_news()
        new_events: list[dict[str, str]] = []
        for art in articles:
            title = str(art.get("title", ""))
            key = str(art.get("code") or art.get("id"))
            if key in seen:
                continue
            seen.add(key)
            if any(kw in title.lower() for kw in self.NEWS_KEYWORDS):
                link = (
                    f"https://www.binance.com/en/support/announcement/{art.get('code')}"
                    if art.get("code") else "https://www.binance.com/en/support/announcement"
                )
                new_events.append({"title": title, "url": link})
        self._save_seen_news(seen)
        if new_events and telemetry is not None:
            import html
            lines = ["<b>Earn</b> nuevo evento de airdrop detectado:"]
            for ev in new_events[:5]:
                lines.append(f"- {html.escape(ev['title'], quote=False)}")
                lines.append(f"  {html.escape(ev['url'], quote=False)}")
            lines.append("Tu BNB en flexible ya cuenta para Launchpool/HODLer.")
            try:
                telemetry.alert("\n".join(lines))
            except Exception:
                pass
        if new_events:
            log.info("Earn: %d anuncios de airdrop nuevos detectados", len(new_events))
        return {"ok": True, "new_events": new_events}

    # ------------------------------------------------------------------
    # Soft Staking (rendimiento sobre saldo spot elegible, sin bloqueo)
    # ------------------------------------------------------------------
    def ensure_soft_staking(self) -> dict[str, Any]:
        """Activa Soft Staking si esta apagado. No bloquea fondos."""
        if not self.cfg.earn_soft_staking_enabled:
            return {"ok": False, "reason": "soft_staking_disabled"}
        try:
            res = self._sapi("get", "soft-staking/list", size=100)
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}
        if isinstance(res, dict) and res.get("status"):
            return {"ok": True, "already_enabled": True}
        if self.dry_run:
            return {"ok": True, "dry_run": True, "action": "enable_soft_staking"}
        try:
            self._sapi("get", "soft-staking/set", softStaking="true")
            log.info("Earn: Soft Staking activado.")
            return {"ok": True, "enabled": True}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    # ------------------------------------------------------------------
    # Stack BNB (elegibilidad Launchpool / HODLer Airdrops)
    # ------------------------------------------------------------------
    def build_bnb_stack(self, usdt_free: float) -> dict[str, Any]:
        """Convierte un % del USDT ocioso a BNB (queda en flexible via sweep).

        BNB en Simple Earn Flexible cuenta automaticamente para
        Launchpool y HODLer Airdrops.
        """
        pct = self.cfg.earn_bnb_stack_pct
        if pct <= 0:
            return {"ok": False, "reason": "bnb_stack_disabled"}
        amount = usdt_free * pct
        if amount < 5.0:  # min notional tipico de BNBUSDT
            return {"ok": False, "reason": f"below_min_notional:{amount:.2f}"}
        if self.dry_run:
            return {"ok": True, "dry_run": True, "quote_spent": amount}
        try:
            order = self.client.create_order(
                symbol="BNBUSDT",
                side="BUY",
                type="MARKET",
                quoteOrderQty=f"{amount:.2f}",
            )
            executed = _f(order.get("executedQty"))
            log.info("Earn: stack BNB +%.6f BNB (%.2f USDT)", executed, amount)
            return {"ok": True, "bnb_bought": executed, "quote_spent": amount}
        except Exception as exc:
            log.warning("Earn: fallo stack BNB: %s", exc)
            return {"ok": False, "reason": str(exc)}

    # ------------------------------------------------------------------
    # Dual Investment (buy-low, reglas estrictas)
    # ------------------------------------------------------------------
    def dual_positions_exposure(self) -> float:
        """Suma USDT comprometido en posiciones dual pendientes."""
        try:
            res = self._sapi("get", "dci/product/positions", pageSize=100)
        except Exception as exc:
            log.warning("Earn: no se pudieron leer posiciones dual: %s", exc)
            return 0.0
        total = 0.0
        rows = res.get("list", []) if isinstance(res, dict) else []
        for pos in rows:
            if str(pos.get("purchaseStatus", "")) in {"PURCHASE_SUCCESS", "PENDING"} and (
                str(pos.get("investCoin", "")).upper() == "USDT"
            ):
                total += _f(pos.get("subscriptionAmount"))
        return total

    def maybe_subscribe_dual(self, usdt_free: float) -> dict[str, Any]:
        """Suscribe Dual Investment BUY-LOW solo si cumple TODAS las reglas:

        - strike al menos `earn_dual_min_discount` debajo del spot
        - duracion <= `earn_dual_max_duration_days`
        - APR >= `earn_dual_min_apr`
        - exposicion total dual <= `earn_dual_max_pct` del USDT disponible
        """
        if not self.cfg.earn_dual_enabled:
            return {"ok": False, "reason": "dual_disabled"}
        budget_cap = usdt_free * self.cfg.earn_dual_max_pct
        current = self.dual_positions_exposure()
        budget = budget_cap - current
        if budget < 0.1:
            return {"ok": False, "reason": f"no_dual_budget (usado {current:.2f})"}
        best = None
        best_apr = 0.0
        best_asset = None
        for asset in self.cfg.earn_dual_assets_list:
            try:
                ticker = self.client.get_symbol_ticker(symbol=f"{asset}USDT")
                spot = _f(ticker.get("price"))
                if spot <= 0:
                    continue
                res = self._sapi(
                    "get",
                    "dci/product/list",
                    optionType="PUT",
                    exercisedCoin=asset,
                    investCoin="USDT",
                    pageSize=100,
                )
            except Exception as exc:
                log.warning("Earn: dual list %s fallo: %s", asset, exc)
                continue
            max_strike = spot * (1 - self.cfg.earn_dual_min_discount)
            for prod in res.get("list", []) if isinstance(res, dict) else []:
                if not prod.get("canPurchase"):
                    continue
                if int(_f(prod.get("duration"))) > self.cfg.earn_dual_max_duration_days:
                    continue
                strike = _f(prod.get("strikePrice"))
                if strike <= 0 or strike > max_strike:
                    continue
                apr = _f(prod.get("apr"))
                if apr < self.cfg.earn_dual_min_apr:
                    continue
                if _f(prod.get("minAmount")) > budget:
                    continue
                if apr > best_apr:
                    best_apr = apr
                    best = prod
                    best_asset = asset
        if not best:
            return {"ok": False, "reason": "no_dual_product_fits_rules"}
        deposit = min(budget, _f(best.get("maxAmount"), budget))
        if self.dry_run:
            return {
                "ok": True, "dry_run": True, "asset": best_asset,
                "amount": deposit, "apr": best_apr,
                "strike": best.get("strikePrice"), "duration": best.get("duration"),
            }
        try:
            self._sapi(
                "post",
                "dci/product/subscribe",
                id=best.get("id"),
                orderId=best.get("orderId"),
                depositAmount=f"{deposit:.8f}",
                autoCompoundPlan="NONE",
            )
            log.info(
                "Earn: dual BUY-LOW %s strike %s dur %sd APR %.2f%% con %.2f USDT",
                best_asset, best.get("strikePrice"), best.get("duration"),
                best_apr * 100, deposit,
            )
            return {
                "ok": True, "asset": best_asset, "amount": deposit,
                "apr": best_apr, "strike": best.get("strikePrice"),
                "duration": best.get("duration"),
            }
        except Exception as exc:
            log.warning("Earn: fallo dual subscribe: %s", exc)
            return {"ok": False, "reason": str(exc)}

    # ------------------------------------------------------------------
    # Dust -> BNB
    # ------------------------------------------------------------------
    def convert_dust(self) -> dict[str, Any]:
        if not self.cfg.earn_dust_enabled:
            return {"ok": False, "reason": "dust_disabled"}
        active_bases = set()
        for sym in self.cfg.symbols_to_trade:
            for quote in STABLE_QUOTES + ("BNB", "BTC", "ETH"):
                if sym.upper().endswith(quote) and len(sym) > len(quote):
                    active_bases.add(sym.upper()[: -len(quote)])
                    break
        try:
            res = self._sapi("post", "asset/dust-btc")
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}
        details = res.get("details", []) if isinstance(res, dict) else []
        assets = [
            d.get("asset")
            for d in details
            if d.get("asset")
            and d.get("asset") not in active_bases
            and d.get("asset") not in STABLE_QUOTES
            and d.get("asset") != "BNB"
        ]
        if not assets:
            return {"ok": False, "reason": "no_dust"}
        if self.dry_run:
            return {"ok": True, "dry_run": True, "assets": assets}
        try:
            self._sapi("post", "asset/dust", asset=",".join(assets))
            log.info("Earn: dust convertido a BNB: %s", assets)
            return {"ok": True, "assets": assets}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    # ------------------------------------------------------------------
    # Barrido principal
    # ------------------------------------------------------------------
    def sweep_idle_balances(self) -> list[dict[str, Any]]:
        """Envia a Earn el saldo spot ocioso configurado.

        Orden para USDT: reserva -> Dual Investment -> stack BNB -> locked -> flexible.
        Para el resto de activos: locked -> flexible.
        """
        results: list[dict[str, Any]] = []
        for asset in self.cfg.earn_sweep_assets_list:
            try:
                bal = self.client.get_asset_balance(asset=asset) or {}
                free = _f(bal.get("free"))
            except Exception as exc:
                log.warning("Earn: balance de %s fallo: %s", asset, exc)
                continue
            investable = free - self.cfg.earn_reserve_usdt if asset in STABLE_QUOTES else free
            if investable < self.cfg.earn_min_subscribe:
                continue
            if asset == "USDT":
                dual_res = self.maybe_subscribe_dual(investable)
                if dual_res.get("ok"):
                    results.append({"action": "dual_buy_low", **dual_res})
                    investable -= _f(dual_res.get("amount"))
                bnb_res = self.build_bnb_stack(max(investable, 0.0))
                if bnb_res.get("ok"):
                    results.append({"action": "bnb_stack", **bnb_res})
                    investable -= _f(bnb_res.get("quote_spent"))
            if investable < self.cfg.earn_min_subscribe:
                continue
            onchain_res = self.maybe_subscribe_onchain(asset, investable)
            if onchain_res.get("ok"):
                results.append({"action": "onchain", **onchain_res})
                investable -= _f(onchain_res.get("amount"))
            locked_res = self.maybe_subscribe_locked(asset, investable)
            if locked_res.get("ok"):
                results.append({"action": "locked", **locked_res})
                investable -= _f(locked_res.get("amount"))
            if investable >= self.cfg.earn_min_subscribe:
                res = self.subscribe_flexible(asset, investable)
                if res.get("ok"):
                    results.append({"action": "flexible", **res})
        return results

    def sweep_cycle(self, telemetry: Any | None = None) -> dict[str, Any]:
        """Ciclo completo: anuncios -> throttle -> sweep -> dust. Nunca lanza excepciones."""
        try:
            self.check_announcements(telemetry)  # throttle propio (mas frecuente)
        except Exception as exc:
            log.warning("Earn: monitor de anuncios fallo: %s", exc)
        now = datetime.now(timezone.utc)
        if (
            self._last_sweep is not None
            and (now - self._last_sweep).total_seconds()
            < self.cfg.earn_sweep_interval_minutes * 60
        ):
            return {"event": "earn_skip", "reason": "throttled"}
        self._last_sweep = now
        summary: dict[str, Any] = {"event": "earn_sweep", "time": now.isoformat()}
        try:
            soft = self.ensure_soft_staking()
            if soft.get("enabled"):
                summary["soft_staking"] = soft
            moves = self.sweep_idle_balances()
            summary["moves"] = moves
            dust = self.convert_dust()
            if dust.get("ok"):
                summary["dust"] = dust
            if moves and telemetry is not None:
                lines = ["<b>Earn</b> barrido automatico"]
                for m in moves:
                    lines.append(
                        f"{m.get('action')}: {m.get('amount', 0):.4f} {m.get('asset')}"
                    )
                try:
                    telemetry.alert("\n".join(lines))
                except Exception:
                    pass
        except Exception as exc:
            log.warning("Earn: sweep_cycle fallo: %s", exc)
            summary["error"] = str(exc)
        return summary

    # ------------------------------------------------------------------
    # Reporte
    # ------------------------------------------------------------------
    def build_report(self) -> str:
        lines = ["<b>Reporte Earn</b>"]
        try:
            account = self._sapi("get", "simple-earn/account")
            lines.append(
                f"Total flexible: {account.get('totalFlexibleAmountInUSDT', '0')} USDT"
            )
            lines.append(
                f"Total locked: {account.get('totalLockedInUSDT', '0')} USDT"
            )
        except Exception as exc:
            lines.append(f"(cuenta Earn no disponible: {exc})")
        positions = self.flexible_positions()
        if positions:
            lines.append("")
            lines.append("Posiciones flexibles:")
            for pos in positions:
                apr = _f(pos.get("latestAnnualPercentageRate")) * 100
                lines.append(
                    f"- {pos.get('asset')}: {_f(pos.get('totalAmount')):.6f} "
                    f"(APR {apr:.2f}%, recompensa acum. "
                    f"{_f(pos.get('cumulativeTotalRewards')):.8f})"
                )
        else:
            lines.append("Sin posiciones flexibles activas.")
        try:
            soft = self._sapi("get", "soft-staking/list", size=100)
            if isinstance(soft, dict):
                estado = "activo" if soft.get("status") else "inactivo"
                lines.append("")
                lines.append(
                    f"Soft Staking: {estado} "
                    f"(recompensas acum. {soft.get('totalRewardsUsdt', '0')} USDT)"
                )
                for row in soft.get("rows", []) or []:
                    staked = _f(row.get("stakedAmount"))
                    if staked > 0:
                        lines.append(
                            f"- {row.get('asset')}: {staked:.6f} "
                            f"(APR {_f(row.get('apr')) * 100:.2f}%)"
                        )
        except Exception:
            pass
        try:
            onchain = self._sapi("get", "onchain-yields/locked/position", size=100)
            rows = onchain.get("rows", []) if isinstance(onchain, dict) else []
            if rows:
                lines.append("")
                lines.append("On-Chain Yields:")
                for pos in rows:
                    lines.append(
                        f"- {pos.get('asset')}: {_f(pos.get('amount')):.6f} "
                        f"({pos.get('projectId')}, APR {_f(pos.get('apr')) * 100:.2f}%)"
                    )
        except Exception:
            pass
        try:
            dual = self._sapi("get", "dci/product/positions", pageSize=100)
            rows = dual.get("list", []) if isinstance(dual, dict) else []
            active = [r for r in rows if str(r.get("purchaseStatus", "")) == "PURCHASE_SUCCESS"]
            if active:
                lines.append("")
                lines.append("Dual Investment activo:")
                for pos in active:
                    lines.append(
                        f"- {pos.get('investCoin')}->{pos.get('exercisedCoin')} "
                        f"{_f(pos.get('subscriptionAmount')):.2f} strike {pos.get('strikePrice')} "
                        f"(APR {_f(pos.get('apr')) * 100:.1f}%, {pos.get('duration')}d)"
                    )
        except Exception:
            pass
        return "\n".join(lines)


def build_earn_manager(cfg: BotConfig, client: Any, dry_run: bool = False) -> EarnManager | None:
    if not cfg.earn_enabled:
        return None
    try:
        return EarnManager(cfg=cfg, client=client, dry_run=dry_run)
    except Exception as exc:
        log.warning("No se pudo inicializar EarnManager: %s", exc)
        return None
