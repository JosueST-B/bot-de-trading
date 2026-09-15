"""Servidor del panel unificado (Fase 13).

Sirve una pagina HTML que junta Binance trading + Earn + IBKR con PnL
consolidado y autorefresco. Independiente del dashboard clasico.

Uso:
    python main.py panel --host 127.0.0.1 --port 8770
Luego abrir http://127.0.0.1:8770
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from bot.config import BotConfig
from bot.telemetry import EventStore
from bot.unified import build_unified_summary

HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Panel Unificado - Bot</title>
<style>
  :root { --bg:#0b0e14; --card:#141a24; --line:#232c3a; --txt:#e6edf3; --dim:#8b98a9;
          --green:#3fb950; --red:#f85149; --blue:#58a6ff; --amber:#d29922; --purple:#bc8cff; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt);
         font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
  header { padding:18px 22px; border-bottom:1px solid var(--line); display:flex;
           justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; }
  header h1 { font-size:18px; margin:0; font-weight:650; }
  .muted { color:var(--dim); font-size:12px; }
  .wrap { padding:18px 22px; max-width:1200px; margin:0 auto; }
  .kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:18px; }
  .kpi { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
  .kpi .label { color:var(--dim); font-size:12px; margin-bottom:6px; }
  .kpi .value { font-size:24px; font-weight:680; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:14px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 18px; }
  .card h2 { font-size:14px; margin:0 0 12px; display:flex; align-items:center; gap:8px; }
  .dot { width:9px; height:9px; border-radius:50%; display:inline-block; }
  .row { display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid var(--line); font-size:13px; }
  .row:last-child { border-bottom:none; }
  .row .k { color:var(--dim); }
  .pos { color:var(--green); } .neg { color:var(--red); }
  .pill { font-size:11px; padding:2px 8px; border-radius:20px; border:1px solid var(--line); color:var(--dim); }
  .pill.on { color:var(--green); border-color:var(--green); }
  .pill.off { color:var(--dim); }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th,td { text-align:left; padding:5px 6px; border-bottom:1px solid var(--line); }
  th { color:var(--dim); font-weight:500; }
  .err { color:var(--amber); font-size:11px; margin-top:8px; }
  .foot { color:var(--dim); font-size:11px; margin-top:18px; text-align:center; }
</style>
</head>
<body>
<header>
  <h1>Panel Unificado · Bot de Trading</h1>
  <div class="muted" id="updated">cargando…</div>
</header>
<div class="wrap">
  <div class="kpis" id="kpis"></div>
  <div class="grid">
    <div class="card" id="cardBinance"></div>
    <div class="card" id="cardEarn"></div>
    <div class="card" id="cardIbkr"></div>
  </div>
  <div class="foot" id="notes"></div>
</div>
<script>
const fmt = (v, d=2) => (v===null||v===undefined) ? "—" : Number(v).toLocaleString("es",{maximumFractionDigits:d});
const cls = v => (v>0?"pos":(v<0?"neg":""));
const dot = c => `<span class="dot" style="background:${c}"></span>`;
const pill = (on,txt) => `<span class="pill ${on?'on':'off'}">${txt}</span>`;

function kpi(label, value, extra="") {
  return `<div class="kpi"><div class="label">${label}</div><div class="value">${value}</div><div class="muted">${extra}</div></div>`;
}

async function load() {
  let d;
  try { d = await (await fetch("/api/unified")).json(); }
  catch(e) { document.getElementById("updated").textContent = "error de conexion"; return; }

  const b = d.binance_trading, e = d.earn, k = d.ibkr, c = d.consolidated;
  document.getElementById("updated").textContent = "actualizado " + new Date(d.generated_at).toLocaleString("es");

  // KPIs consolidados
  document.getElementById("kpis").innerHTML =
    kpi("PnL realizado Binance", `<span class="${cls(c.binance_realized_pnl)}">${fmt(c.binance_realized_pnl)} USDT</span>`, `${b.sells} trades cerrados`) +
    kpi("Saldo en Earn", e.total_usdt===null? "—" : `${fmt(e.total_usdt)} USDT`, e.total_usdt===null? "sin snapshot live" : `flex ${fmt(e.flexible_usdt)} · lock ${fmt(e.locked_usdt)}`) +
    kpi("IBKR patrimonio", k.net_liquidation===null? "—" : `$${fmt(k.net_liquidation)}`, k.connected? "conectado" : "sin conexion") +
    kpi("Win rate Binance", `${fmt(b.win_rate_pct,1)}%`, `${b.buys} entradas`);

  // Binance
  let perSym = Object.entries(b.per_symbol||{}).map(([s,v])=>
    `<tr><td>${s}</td><td>${v.buys}</td><td>${v.sells}</td><td class="${cls(v.pnl)}">${fmt(v.pnl)}</td></tr>`).join("");
  document.getElementById("cardBinance").innerHTML =
    `<h2>${dot("#58a6ff")} Binance · Trading</h2>
     <div class="row"><span class="k">Entradas / Salidas</span><span>${b.buys} / ${b.sells}</span></div>
     <div class="row"><span class="k">PnL realizado</span><span class="${cls(b.realized_pnl)}">${fmt(b.realized_pnl)} USDT</span></div>
     <div class="row"><span class="k">Win rate</span><span>${fmt(b.win_rate_pct,1)}%</span></div>
     <div class="row"><span class="k">Pausas por riesgo</span><span>${b.risk_pauses}</span></div>
     <div class="row"><span class="k">Errores</span><span>${b.errors}</span></div>
     ${perSym? `<table style="margin-top:10px"><tr><th>Par</th><th>Comp</th><th>Vent</th><th>PnL</th></tr>${perSym}</table>`:""}`;

  // Earn
  let moves = (e.recent_moves||[]).map(m=>
    `<tr><td>${m.action}</td><td>${m.asset||"—"}</td><td>${fmt(m.amount,4)}</td></tr>`).join("");
  let posE = (e.positions||[]).map(p=>
    `<tr><td>${p.asset}</td><td>${fmt(p.amount,4)}</td><td>${fmt(p.apr_pct,2)}%</td></tr>`).join("");
  document.getElementById("cardEarn").innerHTML =
    `<h2>${dot("#3fb950")} Binance · Earn ${pill(e.enabled,e.enabled?"activo":"off")}</h2>
     <div class="row"><span class="k">Saldo total</span><span>${e.total_usdt===null?"—":fmt(e.total_usdt)+" USDT"}</span></div>
     <div class="row"><span class="k">Flexible / Locked</span><span>${fmt(e.flexible_usdt)} / ${fmt(e.locked_usdt)}</span></div>
     <div class="row"><span class="k">Barridos</span><span>${e.sweeps}</span></div>
     ${posE? `<table style="margin-top:10px"><tr><th>Activo</th><th>Monto</th><th>APR</th></tr>${posE}</table>`:""}
     ${moves? `<div class="muted" style="margin-top:10px">Movimientos recientes</div><table><tr><th>Accion</th><th>Activo</th><th>Monto</th></tr>${moves}</table>`:""}
     ${e.live_error? `<div class="err">snapshot live no disponible: ${e.live_error}</div>`:""}`;

  // IBKR
  let posK = (k.positions||[]).map(p=>
    `<tr><td>${p.symbol}</td><td>${fmt(p.qty,0)}</td></tr>`).join("");
  document.getElementById("cardIbkr").innerHTML =
    `<h2>${dot("#bc8cff")} Interactive Brokers ${pill(k.enabled,k.enabled?"activo":"off")}</h2>
     <div class="row"><span class="k">Patrimonio neto</span><span>${k.net_liquidation===null?"—":"$"+fmt(k.net_liquidation)}</span></div>
     <div class="row"><span class="k">Efectivo</span><span>${k.cash_usd===null?"—":"$"+fmt(k.cash_usd)}</span></div>
     <div class="row"><span class="k">Compras / Errores</span><span>${k.buys} / ${k.errors}</span></div>
     <div class="row"><span class="k">Ultimo evento</span><span>${k.last_event_name||"—"}</span></div>
     ${posK? `<table style="margin-top:10px"><tr><th>Simbolo</th><th>Cantidad</th></tr>${posK}</table>`:'<div class="muted" style="margin-top:8px">Sin posiciones abiertas</div>'}
     ${k.live_error? `<div class="err">IB Gateway no conectado: ${k.live_error}</div>`:""}`;

  document.getElementById("notes").textContent = c.notes;
}
load();
setInterval(load, 20000);
</script>
</body>
</html>"""


def _html(handler: BaseHTTPRequestHandler) -> None:
    body = HTML.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _json(handler: BaseHTTPRequestHandler, payload: Any) -> None:
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(cfg: BotConfig, store: EventStore):
    class PanelHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                _html(self)
                return
            if path == "/api/unified":
                _json(self, build_unified_summary(cfg, store))
                return
            self.send_response(404)
            self.end_headers()

    return PanelHandler


def run_unified_dashboard(cfg: BotConfig, host: str = "127.0.0.1", port: int = 8770) -> None:
    store = EventStore(cfg.event_db_path)
    server = ThreadingHTTPServer((host, port), make_handler(cfg, store))
    print(f"Panel unificado en http://{host}:{port}")
    server.serve_forever()
