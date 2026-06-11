from __future__ import annotations

import json
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from bot.config import BotConfig
from bot.telemetry import EventStore


HTML = r"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Trading Bot Dashboard</title>
  <style>
    :root {
      --bg: #101416;
      --panel: #182024;
      --panel-2: #202a2f;
      --text: #e9f0ed;
      --muted: #93a39c;
      --line: #334247;
      --green: #56d68a;
      --red: #ff6b6b;
      --gold: #e8c15b;
      --cyan: #61d9df;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, Segoe UI, Helvetica, Arial, sans-serif;
      background:
        linear-gradient(135deg, rgba(97,217,223,.08), transparent 36%),
        radial-gradient(circle at 92% 12%, rgba(232,193,91,.10), transparent 28%),
        var(--bg);
      color: var(--text);
    }
    header {
      padding: 28px clamp(18px, 4vw, 52px) 14px;
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 18px;
      border-bottom: 1px solid var(--line);
    }
    h1 { margin: 0; font-size: clamp(26px, 4vw, 46px); letter-spacing: 0; }
    .sub { color: var(--muted); margin-top: 6px; }
    button {
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--text);
      min-height: 40px;
      padding: 0 14px;
      border-radius: 6px;
      cursor: pointer;
    }
    main { padding: 22px clamp(18px, 4vw, 52px) 42px; }
    .grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .card {
      border: 1px solid var(--line);
      background: color-mix(in srgb, var(--panel) 92%, transparent);
      border-radius: 8px;
      padding: 16px;
      min-height: 110px;
    }
    .label { color: var(--muted); font-size: 13px; }
    .value { font-size: clamp(24px, 4vw, 38px); margin-top: 10px; font-weight: 750; }
    .pos { color: var(--green); }
    .neg { color: var(--red); }
    .gold { color: var(--gold); }
    .cyan { color: var(--cyan); }
    .split {
      display: grid;
      grid-template-columns: 1fr 1.2fr 1.5fr;
      gap: 14px;
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      color: var(--muted);
      font-size: 13px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      text-align: left;
      border-bottom: 1px solid var(--line);
      padding: 10px 8px;
      vertical-align: top;
    }
    th { color: var(--muted); font-weight: 650; }
    td.payload { max-width: 460px; color: var(--muted); word-break: break-word; }

    /* Estilos para badges y noticias */
    .badge {
      display: inline-block;
      padding: 2px 6px;
      font-size: 10px;
      font-weight: 700;
      border-radius: 4px;
      text-transform: uppercase;
    }
    .badge-pos {
      background: var(--green);
      color: #101416;
    }
    .badge-neg {
      background: var(--red);
      color: #101416;
    }
    .badge-neu {
      background: var(--line);
      color: var(--text);
    }
    .news-item {
      border-bottom: 1px solid var(--line);
      padding: 10px 0;
    }
    .news-item:last-child {
      border-bottom: none;
      padding-bottom: 0;
    }
    .news-title {
      font-weight: 600;
      font-size: 13px;
      margin-bottom: 4px;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .news-desc {
      font-size: 12px;
      color: var(--muted);
      line-height: 1.4;
    }
    .news-date {
      font-size: 10px;
      color: var(--muted);
      margin-top: 4px;
      text-align: right;
    }

    @media (max-width: 1200px) {
      .grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .split { grid-template-columns: 1fr 1fr; }
      .split > div:nth-child(3) { grid-column: span 2; }
    }
    @media (max-width: 920px) {
      header { align-items: start; flex-direction: column; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .split { grid-template-columns: 1fr; }
      .split > div:nth-child(3) { grid-column: span 1; }
    }
    @media (max-width: 560px) {
      .grid { grid-template-columns: 1fr; }
      table { font-size: 12px; }
      th:nth-child(1), td:nth-child(1) { display: none; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Trading Bot Dashboard</h1>
      <div class="sub" id="updated">Sin datos cargados</div>
    </div>
    <button onclick="refresh()">Actualizar</button>
  </header>
  <main>
    <section class="grid">
      <div class="card"><div class="label">Eventos</div><div class="value cyan" id="events">0</div></div>
      <div class="card"><div class="label">PnL live</div><div class="value" id="pnl">0.0000</div></div>
      <div class="card"><div class="label">Win rate live</div><div class="value gold" id="winrate">0.00%</div></div>
      <div class="card"><div class="label">Risk pauses</div><div class="value" id="risk">0</div></div>
      <div class="card"><div class="label">Sentimiento Noticias</div><div class="value" id="sentiment">Neutral (0.00)</div></div>
    </section>
    <section class="split">
      <div class="card" style="display: flex; flex-direction: column;">
        <div class="label" style="margin-bottom: 10px;">Monitoreo de Activos y Optimización</div>
        <div id="snapshotsContainer" style="flex: 1; overflow-y: auto; max-height: 400px; display: flex; flex-direction: column; gap: 10px;">
          Cargando monitoreo...
        </div>
      </div>
      <div class="card" style="display: flex; flex-direction: column;">
        <div class="label" style="margin-bottom: 10px;">Noticias (Cointelegraph)</div>
        <div id="newsList" style="flex: 1; overflow-y: auto; max-height: 400px; display: flex; flex-direction: column;">
          Cargando noticias...
        </div>
      </div>
      <div class="card">
        <div class="label">Eventos recientes</div>
        <table>
          <thead><tr><th>Hora</th><th>Modo</th><th>Evento</th><th>Payload</th></tr></thead>
          <tbody id="eventsBody"></tbody>
        </table>
      </div>
    </section>
  </main>
  <script>
    const fmt = (n, d = 2) => Number(n || 0).toFixed(d);
    const cssSigned = (el, n) => {
      el.classList.remove("pos", "neg");
      if (Number(n) > 0) el.classList.add("pos");
      if (Number(n) < 0) el.classList.add("neg");
    };

    function analyzeTextLocal(text) {
      const bullWords = [
        "bullish", "surge", "pump", "gain", "rally", "upward", "breakout", "rose", "profit", "growth",
        "adoption", "support", "partner", "partnership", "buy", "buying", "upgrade", "greater", "high",
        "highest", "soar", "soared", "soaring", "green", "positive", "optimism", "optimistic", "accumulation"
      ];
      const bearWords = [
        "bearish", "drop", "dump", "loss", "crash", "downward", "fall", "fallen", "falling", "down",
        "hack", "hacked", "exploit", "exploited", "scam", "regulation", "regulate", "lawsuit", "sec", "fine",
        "fined", "ban", "banned", "banning", "warning", "warned", "panic", "fear", "dip", "dipped",
        "decline", "declined", "red", "negative", "short"
      ];
      let bullCount = 0;
      let bearCount = 0;
      const lower = text.toLowerCase();
      
      bullWords.forEach(w => {
        const regex = new RegExp("\\b" + w + "\\b", "gi");
        const matches = lower.match(regex);
        if (matches) bullCount += matches.length;
      });
      bearWords.forEach(w => {
        const regex = new RegExp("\\b" + w + "\\b", "gi");
        const matches = lower.match(regex);
        if (matches) bearCount += matches.length;
      });
      const total = bullCount + bearCount;
      if (total === 0) return 0.0;
      return (bullCount - bearCount) / total;
    }

    async function refresh() {
      const [summaryRes, eventsRes] = await Promise.all([
        fetch("/api/summary"),
        fetch("/api/events?limit=80")
      ]);
      const summary = await summaryRes.json();
      const events = await eventsRes.json();
      document.getElementById("events").textContent = summary.events ?? 0;
      
      const pnlEl = document.getElementById("pnl");
      pnlEl.textContent = fmt(summary.live_pnl, 4);
      cssSigned(pnlEl, summary.live_pnl);
      
      document.getElementById("winrate").textContent = fmt(summary.live_win_rate_pct, 2) + "%";
      document.getElementById("risk").textContent = summary.risk_pauses ?? 0;
      document.getElementById("updated").textContent = "Ultimo evento: " + (summary.latest_ts || "sin eventos");
      
      const states = summary.states || [];
      const liveStates = states.filter(s => s.key.startsWith("live:"));
      const optimalStates = states.filter(s => s.key.startsWith("optimal_config:"));

      let snapshotsHtml = "";
      
      if (liveStates.length === 0) {
        snapshotsHtml += `<div style="color: var(--muted); font-size: 13px;">No hay estados de trading en vivo en DB.</div>`;
      } else {
        snapshotsHtml += `<h3 style="margin: 0 0 10px 0; font-size: 13px; border-bottom: 1px solid var(--line); padding-bottom: 4px; color: var(--cyan);">Activos en Operación</h3>`;
        liveStates.forEach(s => {
          const parts = s.key.split(":");
          const sym = parts[1] || "";
          const tf = parts[2] || "";
          const val = s.value || {};
          const pos = val.position;
          
          let posText = "Sin posición";
          let posClass = "badge-neu";
          if (pos) {
            posText = `LONG @ ${fmt(pos.entry_price, 2)} (SL: ${fmt(pos.stop_price, 2)})`;
            posClass = "badge-pos";
          }
          
          snapshotsHtml += `
            <div style="margin-bottom: 12px; font-size: 13px; border-bottom: 1px dashed var(--line); padding-bottom: 8px;">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                <strong>${sym} (${tf})</strong>
                <span class="badge ${posClass}">${posText}</span>
              </div>
              <div style="color: var(--muted); font-size: 11px;">
                Saldo Quote: ${fmt(val.cash, 2)} | Trades: ${val.trades?.length || 0}
              </div>
            </div>
          `;
        });
      }

      if (optimalStates.length > 0) {
        snapshotsHtml += `<h3 style="margin: 15px 0 10px 0; font-size: 13px; border-bottom: 1px solid var(--line); padding-bottom: 4px; color: var(--gold);">Auto-Tuning Inteligente (Retroalimentación)</h3>`;
        optimalStates.forEach(s => {
          const sym = s.key.split(":")[1] || "";
          const val = s.value || {};
          const params = val.params || {};
          const metrics = val.metrics || {};
          
          snapshotsHtml += `
            <div style="margin-bottom: 12px; font-size: 13px; line-height: 1.4; border-bottom: 1px dashed var(--line); padding-bottom: 8px;">
              <div style="display: flex; justify-content: space-between; font-weight: 600;">
                <span>${sym}</span>
                <span class="badge badge-pos" style="background: var(--cyan); color: #101416; font-size: 9px; padding: 1px 4px;">Score: ${fmt(val.score, 2)}</span>
              </div>
              <div style="color: var(--muted); font-size: 11px; margin-top: 4px;">
                <strong>Parámetros:</strong> Mode: ${params.strategy_mode}, SL ATR: ${params.stop_atr_mult}, TP R:R: ${params.take_profit_rr}<br>
                <strong>Métricas:</strong> ROI: ${fmt(metrics.roi_pct, 2)}% | Sharpe: ${fmt(metrics.sharpe_est, 2)} | Trades: ${metrics.num_trades}
              </div>
            </div>
          `;
        });
      }
      
      document.getElementById("snapshotsContainer").innerHTML = snapshotsHtml;

      // Sentimiento y noticias
      const newsData = summary.news_sentiment || { score: 0.0, headlines: [] };
      const sentimentVal = newsData.score ?? 0.0;
      const sentimentEl = document.getElementById("sentiment");
      let sentimentText = "Neutral";
      if (sentimentVal > 0.15) sentimentText = "Alcista";
      else if (sentimentVal < -0.15) sentimentText = "Bajista";
      sentimentEl.textContent = `${sentimentText} (${fmt(sentimentVal, 2)})`;
      cssSigned(sentimentEl, sentimentVal);

      const newsListEl = document.getElementById("newsList");
      const headlines = newsData.headlines || [];
      if (headlines.length === 0) {
        newsListEl.textContent = "No hay noticias disponibles.";
      } else {
        newsListEl.innerHTML = headlines.map(h => {
          const textToAnalyze = `${h.title} ${h.description}`;
          const localScore = analyzeTextLocal(textToAnalyze);
          let badgeClass = "badge-neu";
          let badgeText = "Neutral";
          if (localScore > 0.05) {
            badgeClass = "badge-pos";
            badgeText = "Bullish";
          } else if (localScore < -0.05) {
            badgeClass = "badge-neg";
            badgeText = "Bearish";
          }
          return `
            <div class="news-item">
              <div class="news-title">
                <span class="badge ${badgeClass}">${badgeText}</span>
                <span>${h.title}</span>
              </div>
              <div class="news-desc">${h.description}</div>
              <div class="news-date">${h.pub_date || ""}</div>
            </div>
          `;
        }).join("");
      }

      const body = document.getElementById("eventsBody");
      body.innerHTML = events.map(e => `
        <tr>
          <td>${e.ts}</td>
          <td>${e.mode}<br>${e.symbol}</td>
          <td>${e.event}</td>
          <td class="payload">${JSON.stringify(e.payload)}</td>
        </tr>
      `).join("");
    }
    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>"""


def _json_response(handler: BaseHTTPRequestHandler, payload: Any) -> None:
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler) -> None:
    body = HTML.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(store: EventStore):
    class DashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                _html_response(self)
                return

            if parsed.path == "/api/summary":
                _json_response(self, store.dashboard_summary())
                return

            if parsed.path == "/api/events":
                query = parse_qs(parsed.query)
                limit = int(query.get("limit", ["200"])[0])
                _json_response(self, store.recent_events(limit))
                return

            if parsed.path == "/api/day":
                query = parse_qs(parsed.query)
                raw_day = query.get("day", [date.today().isoformat()])[0]
                _json_response(self, store.events_for_day(date.fromisoformat(raw_day)))
                return

            self.send_response(404)
            self.end_headers()

    return DashboardHandler


def run_dashboard(cfg: BotConfig, host: str = "127.0.0.1", port: int = 8765) -> None:
    store = EventStore(cfg.event_db_path)
    server = ThreadingHTTPServer((host, port), make_handler(store))
    print(f"Dashboard running at http://{host}:{port}")
    server.serve_forever()

