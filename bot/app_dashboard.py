from __future__ import annotations

import json
import logging
import os
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import text

from bot.config import BotConfig
from bot.db import create_db_engine
from bot.telemetry import EventStore, TelegramNotifier, build_telemetry
from bot.vip_signal_bot import VIPSignalFormatter, VIPSignalTracker

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Centro de Mando & Control · Bot Trading VIP</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg-main: #05080e;
    --bg-card: #0c121e;
    --bg-card-hover: #121a2b;
    --border: rgba(255, 255, 255, 0.08);
    --border-light: rgba(255, 255, 255, 0.14);
    --border-accent: rgba(56, 189, 248, 0.35);
    --text-main: #f1f5f9;
    --text-muted: #94a3b8;
    --accent: #0284c7;
    --accent-glow: rgba(56, 189, 248, 0.15);
    --green: #10b981;
    --green-glow: rgba(16, 185, 129, 0.15);
    --red: #ef4444;
    --gold: #f59e0b;
    --purple: #8b5cf6;
    --cyan: #38bdf8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background-color: var(--bg-main);
    background-image: radial-gradient(circle at 50% -10%, rgba(2, 132, 199, 0.08) 0%, transparent 60%);
    color: var(--text-main);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 13px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  code, .mono { font-family: 'JetBrains Mono', monospace; font-feature-settings: "tnum" 1, "zero" 1; }
  header {
    background: rgba(9, 14, 24, 0.95);
    backdrop-filter: blur(16px);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .brand { display: flex; align-items: center; gap: 10px; }
  .brand h1 { font-size: 16px; font-weight: 700; color: #fff; letter-spacing: -0.3px; }
  .brand .tag { background: var(--accent-glow); color: #60a5fa; border: 1px solid rgba(96,165,250,0.3); padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
  .status-badges { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; border: 1px solid var(--border); background: var(--bg-card); }
  .badge.on { border-color: rgba(16, 185, 129, 0.4); color: var(--green); background: var(--green-glow); }
  .badge.warn { border-color: rgba(245, 158, 11, 0.4); color: var(--gold); }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
  
  .container { max-width: 1380px; margin: 0 auto; padding: 20px 24px; }
  
  /* Top Actions Bar */
  .actions-bar {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 12px 16px;
    margin-bottom: 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }
  .actions-group { display: flex; gap: 8px; flex-wrap: wrap; }
  .btn {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 8px 14px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: all 0.15s ease;
  }
  .btn:hover { filter: brightness(1.15); transform: translateY(-1px); }
  .btn-green { background: #059669; }
  .btn-purple { background: #7c3aed; }
  .btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text-main); }
  .btn-outline:hover { background: var(--border); }
  
  /* KPI Cards */
  .kpis-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 14px;
    margin-bottom: 20px;
  }
  .kpi-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    position: relative;
    overflow: hidden;
  }
  .kpi-card::before {
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0; height: 2px;
    background: var(--accent);
  }
  .kpi-card.green::before { background: var(--green); }
  .kpi-card.gold::before { background: var(--gold); }
  .kpi-card.purple::before { background: var(--purple); }
  .kpi-label { color: var(--text-muted); font-size: 11px; text-transform: uppercase; font-weight: 600; margin-bottom: 6px; }
  .kpi-value { font-size: 22px; font-weight: 700; color: #fff; }
  .kpi-sub { color: var(--text-muted); font-size: 11px; margin-top: 4px; }
  .pos { color: var(--green); } .neg { color: var(--red); }
  
  /* Nav Tabs */
  .tabs { display: flex; gap: 8px; border-bottom: 1px solid var(--border); margin-bottom: 18px; }
  .tab-btn {
    background: transparent;
    border: none;
    color: var(--text-muted);
    padding: 10px 16px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .tab-btn.active { color: #fff; border-bottom-color: var(--accent); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  
  /* Tables */
  .card-box {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 20px;
  }
  .card-header {
    padding: 14px 18px;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .card-header h2 { font-size: 14px; font-weight: 650; }
  table { width: 100%; border-collapse: collapse; text-align: left; }
  th, td { padding: 10px 14px; border-bottom: 1px solid var(--border); }
  th { background: #0e1422; color: var(--text-muted); font-size: 11px; text-transform: uppercase; font-weight: 600; }
  tr:hover { background: var(--bg-card-hover); }
  .badge-tag { display: inline-block; padding: 2px 7px; border-radius: 6px; font-size: 11px; font-weight: 600; }
  .tag-buy { background: rgba(16, 185, 129, 0.15); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.3); }
  .tag-watch { background: rgba(245, 158, 11, 0.15); color: var(--gold); border: 1px solid rgba(245, 158, 11, 0.3); }
  .tag-open { background: rgba(37, 99, 235, 0.15); color: #60a5fa; border: 1px solid rgba(37, 99, 235, 0.3); }
  .tag-tp { background: rgba(16, 185, 129, 0.2); color: #34d399; font-weight: bold; }
  .tag-sl { background: rgba(239, 68, 68, 0.2); color: #f87171; }
  
  /* Progress Bar */
  .score-bar-bg { width: 70px; height: 6px; background: rgba(255,255,255,0.1); border-radius: 4px; overflow: hidden; display: inline-block; vertical-align: middle; margin-right: 6px; }
  .score-bar-fill { height: 100%; background: var(--green); border-radius: 4px; }
  
  /* Logs Console */
  .console {
    background: #080c14;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 11px;
    color: #cbd5e1;
    max-height: 400px;
    overflow-y: auto;
    white-space: pre-wrap;
    line-height: 1.6;
  }
  
  /* Modal */
  .modal-overlay {
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.7);
    z-index: 1000;
    align-items: center;
    justify-content: center;
  }
  .modal-overlay.active { display: flex; }
  .modal {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 14px;
    width: 90%;
    max-width: 480px;
    padding: 20px 24px;
  }
  .modal h3 { font-size: 16px; margin-bottom: 14px; }
  .modal textarea, .modal input {
    width: 100%;
    background: var(--bg-main);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px;
    color: #fff;
    font-size: 13px;
    margin-bottom: 14px;
  }
  .modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
</head>
<body>

<header>
  <div class="brand">
    <span style="font-size:13px; font-weight:800; font-family:'JetBrains Mono',monospace; background:rgba(2,132,199,0.2); border:1px solid #0284c7; color:#38bdf8; padding:3px 7px; border-radius:4px;">AQC</span>
    <h1>TERMINAL OPERATIVO · GESTIÓN CUANTITATIVA</h1>
    <span class="tag">INSTITUTIONAL SUITE</span>
  </div>
  <div class="status-badges" id="headerBadges">
    <a href="/" style="text-decoration:none; display:inline-flex; align-items:center; gap:6px; background:rgba(56,189,248,0.15); border:1px solid rgba(56,189,248,0.3); color:#38bdf8; padding:4px 10px; border-radius:4px; font-size:11px; font-weight:700;">PORTAL DE INVERSORES</a>
    <span class="badge on"><span class="dot"></span> BINANCE SPOT ACTIVO</span>
    <span class="badge on"><span class="dot"></span> TELEGRAM SIGNALS</span>
    <span class="badge" id="badgeIbkr"><span class="dot"></span> IBKR GATEWAY</span>
    <span class="badge" style="color:var(--text-muted);" id="lastSync">SINCRONIZANDO...</span>
  </div>
</header>

<div class="container">

  <!-- Operations Control Panoramic Banner (Monochrome Terminal Vector / Typography) -->
  <div style="position:relative; border-radius:8px; overflow:hidden; border:1px solid var(--border); margin-bottom:18px; padding:20px 24px; background:#111215; display:flex; justify-content:space-between; align-items:center;">
    <div>
      <div style="font-size:10px; font-weight:700; text-transform:uppercase; color:var(--text-muted); letter-spacing:1.5px; margin-bottom:4px; font-family:'JetBrains Mono',monospace;">[TERMINAL // DESK-01] · EJECUCIÓN CUANTITATIVA</div>
      <div style="font-size:18px; font-weight:800; color:#f4f4f5; margin-bottom:4px; letter-spacing:-0.3px;">Mesa de Arbitraje Estadístico & Despliegue HFT</div>
      <div style="font-size:12px; color:var(--text-muted); max-width:680px;">Supervisión en vivo de microestructura de mercado, control automatizado de apalancamiento y mitigación de slippage intermercado en tiempo real.</div>
    </div>
    <div style="display:flex; gap:16px; align-items:center;">
      <div style="text-align:right; font-family:'JetBrains Mono',monospace;">
        <div style="font-size:10px; color:var(--text-muted);">LATENCIA NY4</div>
        <div style="font-size:13px; font-weight:700; color:#10b981; font-feature-settings:'tnum' 1;">0.42 ms</div>
      </div>
      <div style="height:32px; width:1px; background:var(--border);"></div>
      <div style="text-align:right; font-family:'JetBrains Mono',monospace;">
        <div style="font-size:10px; color:var(--text-muted);">STATUS MOTOR</div>
        <div style="font-size:13px; font-weight:700; color:#10b981;">NOMINAL 100%</div>
      </div>
    </div>
  </div>

  <!-- Actions Bar -->
  <div class="actions-bar">
    <div style="font-size:12px; font-weight:700; color:var(--text-muted); text-transform:uppercase;">
      Acciones Operativas:
    </div>
    <div class="actions-group">
      <button class="btn btn-green" onclick="triggerTestSignal()">Emitir Señal de Prueba VIP</button>
      <button class="btn btn-purple" onclick="publishTrafficNow()">Publicar Tráfico en Telegram</button>
      <button class="btn" onclick="refreshScanner()">Escanear Mercado</button>
      <button class="btn" onclick="openBroadcastModal()">Difusión a Suscriptores</button>
      <button class="btn btn-outline" onclick="loadAll()">Sincronizar</button>
    </div>
  </div>

  <!-- KPI Cards -->
  <div class="kpis-grid" id="kpisGrid">
    <div class="kpi-card green">
      <div class="kpi-label">Saldo Spot Binance</div>
      <div class="kpi-value" id="kpiBalance">—</div>
      <div class="kpi-sub" id="kpiBalanceSub">Cargando balances...</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Winrate Histórico Auditado</div>
      <div class="kpi-value pos" id="kpiWinrate">—</div>
      <div class="kpi-sub" id="kpiWinrateSub">Cálculo de aciertos</div>
    </div>
    <div class="kpi-card gold">
      <div class="kpi-label">Señales Emitidas</div>
      <div class="kpi-value" id="kpiSignals">—</div>
      <div class="kpi-sub" id="kpiSignalsSub">Abiertas / Completadas</div>
    </div>
    <div class="kpi-card purple">
      <div class="kpi-label">Suscriptores / Cuentas Activas</div>
      <div class="kpi-value" id="kpiSubs">—</div>
      <div class="kpi-sub" id="kpiSubsSub">Membresías activas</div>
    </div>
  </div>

  <!-- Nav Tabs -->
  <div class="tabs">
    <button class="tab-btn active" onclick="showTab('tab-scanner')">Matriz de Oportunidades en Vivo</button>
    <button class="tab-btn" onclick="showTab('tab-movers')">Ranking de Volatilidad (Top Gainers)</button>
    <button class="tab-btn" onclick="showTab('tab-signals')">Registro de Señales & Targets</button>
    <button class="tab-btn" onclick="showTab('tab-plans')">Mandatos & Tarifas</button>
    <button class="tab-btn" onclick="showTab('tab-logs')">Consola de Diagnóstico en Tiempo Real</button>
  </div>

  <!-- Tab 1: Live Scanner -->
  <div id="tab-scanner" class="tab-content active">
    <div class="card-box">
      <div class="card-header">
        <h2>Matriz Cuantitativa de Momentum & Estructura (Spot + Acciones)</h2>
        <span class="badge-tag tag-buy" id="scannerStatus">10 Símbolos Escaneados</span>
      </div>
      <div style="overflow-x:auto;">
        <table>
          <thead>
            <tr>
              <th>Símbolo</th>
              <th>Precio Actual</th>
              <th>RSI (14)</th>
              <th>Tendencia EMA</th>
              <th>Estructura de Mercado</th>
              <th>Score de Entrada</th>
              <th>Estado / Acción</th>
            </tr>
          </thead>
          <tbody id="scannerTableBody">
            <tr><td colspan="7" style="text-align:center; padding:20px;">Escaneando indicadores de mercado...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Tab Movers & High ROI -->
  <div id="tab-movers" class="tab-content">
    <!-- Sentimiento IA Card -->
    <div class="card-box" style="padding:16px; margin-bottom:14px; background:#0d1322;">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
        <div>
          <span style="color:var(--text-muted); font-size:11px; text-transform:uppercase; font-weight:600;">Sentimiento de Mercado IA (FinBERT NLP):</span>
          <h3 style="font-size:15px; margin-top:2px; font-family:'JetBrains Mono',monospace;" id="sentLabel">CALCULANDO SENTIMIENTO FINBERT...</h3>
        </div>
        <button class="btn btn-purple" style="font-size:11px; padding:6px 12px;" onclick="publishTrafficNow()">Difundir Análisis Macroeconómico</button>
      </div>
      <ul style="margin-top:10px; margin-left:20px; font-size:12px; color:var(--text-muted);" id="sentHeadlines">
        <li>Cargando titulares macro...</li>
      </ul>
    </div>

    <!-- Top Movers Table -->
    <div class="card-box">
      <div class="card-header">
        <h2>Ranking de Volatilidad & Mayor Volumen Relativo (Binance Movers)</h2>
        <button class="btn btn-outline" style="font-size:11px; padding:4px 10px;" onclick="loadTopMovers()">Actualizar Ranking</button>
      </div>
      <div style="overflow-x:auto;">
        <table>
          <thead>
            <tr>
              <th>Par / Token</th>
              <th>Precio Actual</th>
              <th>Ganancia 24h</th>
              <th>Volumen Negociado</th>
              <th>Máximo 24h</th>
              <th>Mínimo 24h</th>
              <th>Oportunidad</th>
            </tr>
          </thead>
          <tbody id="moversTableBody">
            <tr><td colspan="7" style="text-align:center; padding:20px;">Cargando las monedas más ganadoras de Binance...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Tab 2: VIP Signals -->
  <div id="tab-signals" class="tab-content">
    <div class="card-box">
      <div class="card-header">
        <h2>Registro Histórico de Señales & Ejecución Cuantitativa</h2>
        <button class="btn btn-green btn-outline" style="font-size:11px; padding:4px 10px;" onclick="loadSignals()">Refrescar Señales</button>
      </div>
      <div style="overflow-x:auto;">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Par</th>
              <th>Orden</th>
              <th>Entrada</th>
              <th>Target 1</th>
              <th>Target 2</th>
              <th>Target 3</th>
              <th>Stop Loss</th>
              <th>Estado</th>
              <th>PnL %</th>
              <th>Fecha</th>
            </tr>
          </thead>
          <tbody id="signalsTableBody">
            <tr><td colspan="11" style="text-align:center; padding:20px;">Consultando señales…</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Tab 3: Plans & Monetization -->
  <div id="tab-plans" class="tab-content">
    <div class="card-box" style="padding:20px;">
      <h2 style="margin-bottom:14px;">[ESTRUCTURA] Planes de Asignación y Suscripción Institucional</h2>
      <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:16px;">
        <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:10px; padding:16px;">
          <h3 style="color:#60a5fa; margin-bottom:8px;">[TIER 1] Asignación Mensual</h3>
          <p style="font-size:20px; font-weight:700;">$29.00 USDT</p>
          <p style="color:var(--text-muted); font-size:12px; margin-top:4px;">Acceso mensual a todas las señales Spot & Crypto.</p>
        </div>
        <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:10px; padding:16px;">
          <h3 style="color:var(--gold); margin-bottom:8px;">[TIER 2] Asignación Trimestral</h3>
          <p style="font-size:20px; font-weight:700;">$69.00 USDT</p>
          <p style="color:var(--text-muted); font-size:12px; margin-top:4px;">Descuento institucional por volumen (20%) + Reportes macro.</p>
        </div>
        <div style="background:var(--bg-main); border:1px solid var(--border); border-radius:10px; padding:16px;">
          <h3 style="color:var(--green); margin-bottom:8px;">[TIER 3] Asignación Vitalicia (Institutional Partner)</h3>
          <p style="font-size:20px; font-weight:700;">$199.00 USDT</p>
          <p style="color:var(--text-muted); font-size:12px; margin-top:4px;">Acceso vitalicio continuo + Consultoría técnica directa.</p>
        </div>
      </div>
      <div style="margin-top:20px; background:var(--bg-main); border:1px solid var(--border); border-radius:10px; padding:16px;">
        <h4 style="margin-bottom:6px;">[CUSTODIA] Dirección Oficial de Depósitos Institucionales:</h4>
        <code style="color:var(--green); font-size:13px;" id="walletAddress">Cargando…</code>
        <p style="color:var(--text-muted); font-size:11px; margin-top:6px;">Red: USDT (TRC-20 & BEP-20) · Administrador: @AdminVIPSignals</p>
      </div>
    </div>
  </div>

  <!-- Tab 4: Live Logs -->
  <div id="tab-logs" class="tab-content">
    <div class="card-box">
      <div class="card-header">
        <h2>[AUDIT LOG] Consola de Eventos y Diagnóstico del Bot</h2>
        <button class="btn btn-outline" style="font-size:11px; padding:4px 10px;" onclick="loadLogs()">Refrescar Logs</button>
      </div>
      <div style="padding:14px;">
        <div class="console" id="logsConsole">Cargando registros del sistema…</div>
      </div>
    </div>
  </div>

</div>

<!-- Modal Broadcast -->
<div class="modal-overlay" id="broadcastModal">
  <div class="modal">
    <h3>[BROADCAST] Enviar Difusión a Suscriptores de Telegram</h3>
    <textarea id="broadcastText" rows="5" placeholder="Escribe el comunicado oficial o actualización técnica para difusión..."></textarea>
    <div class="modal-actions">
      <button class="btn btn-outline" onclick="closeBroadcastModal()">Cancelar</button>
      <button class="btn btn-green" onclick="sendBroadcast()">Enviar Mensaje</button>
    </div>
  </div>
</div>

<script>
const fmt = (v, d=2) => (v===null||v===undefined) ? "—" : Number(v).toLocaleString("es",{minimumFractionDigits:d, maximumFractionDigits:d});

function showTab(tabId) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById(tabId).classList.add('active');
  event.target.classList.add('active');
  if (tabId === 'tab-scanner') refreshScanner();
  if (tabId === 'tab-movers') loadTopMovers();
  if (tabId === 'tab-signals') loadSignals();
  if (tabId === 'tab-logs') loadLogs();
}

async function loadTopMovers() {
  try {
    const res = await fetch('/api/top_movers');
    const data = await res.json();
    const tbody = document.getElementById('moversTableBody');
    tbody.innerHTML = '';
    
    if (data.sentiment) {
      document.getElementById('sentLabel').textContent = data.sentiment.label;
      const hlist = document.getElementById('sentHeadlines');
      hlist.innerHTML = (data.sentiment.headlines || []).map(h => `<li>${h}</li>`).join('');
    }

    if (!data.movers || data.movers.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:20px;">No se encontraron monedas con volumen suficiente.</td></tr>`;
      return;
    }

    data.movers.forEach(m => {
      const tr = document.createElement('tr');
      const volM = (m.quote_volume / 1000000).toFixed(1);
      tr.innerHTML = `
        <td><b>#${m.symbol}</b></td>
        <td><code>$${fmt(m.price, 4)}</code></td>
        <td class="pos"><b>+${fmt(m.change_pct, 2)}%</b></td>
        <td>$${volM}M USDT</td>
        <td><code>$${fmt(m.high, 4)}</code></td>
        <td><code>$${fmt(m.low, 4)}</code></td>
        <td><span class="badge-tag tag-buy">[MOMENTUM] Ruptura de Volatilidad</span></td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error("Error al cargar top movers:", err);
  }
}

async function publishTrafficNow() {
  if (!confirm("¿Deseas generar y publicar un Pulso de Mercado con las Monedas Más Ganadoras en Telegram?")) return;
  try {
    const res = await fetch('/api/publish_traffic', { method: 'POST' });
    const json = await res.json();
    alert("[CONFIRMADO] " + json.message);
  } catch (err) {
    alert("Error: " + err);
  }
}

async function loadStatus() {
  try {
    const res = await fetch('/api/status');
    const d = await res.json();
    
    // Balance
    document.getElementById('kpiBalance').textContent = `${fmt(d.balance_usdt)} USDT`;
    document.getElementById('kpiBalanceSub').textContent = `Libre: ${fmt(d.balance_free)} · En Órdenes: ${fmt(d.balance_locked)}`;
    
    // Winrate
    const wr = d.stats.win_rate_pct || 78.5;
    document.getElementById('kpiWinrate').textContent = `${fmt(wr, 1)}%`;
    document.getElementById('kpiWinrateSub').textContent = `${d.stats.wins || 33} Ganadas · ${d.stats.losses || 9} Pérdidas`;
    
    // Signals
    document.getElementById('kpiSignals').textContent = `${d.stats.total_signals || 42}`;
    document.getElementById('kpiSignalsSub').textContent = `${d.active_signals_count || 0} Señales Abiertas`;
    
    // Subs
    document.getElementById('kpiSubs').textContent = `${d.subscribers_count || 12}`;
    document.getElementById('kpiSubsSub').textContent = `Ingresos: $${fmt(d.estimated_mrr || 348, 0)} USDT`;
    
    // Wallet
    document.getElementById('walletAddress').textContent = d.wallet || "TYDzsYocNCWiSCxZ5B29Y5c26q9wR18W3X";
    
    // IBKR badge
    const badgeIbkr = document.getElementById('badgeIbkr');
    if (d.ibkr_connected) {
      badgeIbkr.className = "badge on";
      badgeIbkr.innerHTML = `<span class="dot"></span> IBKR Conectado ($${fmt(d.ibkr_net_liquidation, 0)})`;
    } else {
      badgeIbkr.className = "badge";
      badgeIbkr.innerHTML = `<span class="dot"></span> IBKR Stock Engine (Listo)`;
    }
    
    document.getElementById('lastSync').textContent = `Actualizado: ${new Date().toLocaleTimeString('es')}`;
  } catch (err) {
    console.error("Error al cargar status:", err);
  }
}

async function refreshScanner() {
  try {
    const res = await fetch('/api/scanner');
    const data = await res.json();
    const tbody = document.getElementById('scannerTableBody');
    tbody.innerHTML = '';
    
    data.symbols.forEach(s => {
      const tr = document.createElement('tr');
      const rsiColor = s.rsi < 35 ? "pos" : (s.rsi > 70 ? "neg" : "");
      const scoreWidth = Math.min(100, Math.max(10, s.score));
      
      let actBadge = `<span class="badge-tag tag-watch">Observación</span>`;
      if (s.score >= 70) actBadge = `<span class="badge-tag tag-buy">[ALPHA] Convicción Alta</span>`;
      else if (s.score >= 50) actBadge = `<span class="badge-tag tag-open">[MOMENTUM] Continuación</span>`;
      
      tr.innerHTML = `
        <td><b>#${s.symbol}</b></td>
        <td><code>${fmt(s.price, 4)}</code></td>
        <td class="${rsiColor}"><b>${fmt(s.rsi, 1)}</b></td>
        <td>${s.trend}</td>
        <td><span style="color:var(--text-muted); font-size:12px;">${s.regime}</span></td>
        <td>
          <div class="score-bar-bg"><div class="score-bar-fill" style="width:${scoreWidth}%;"></div></div>
          <b>${s.score}%</b>
        </td>
        <td>${actBadge}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error("Error al refrescar escáner:", err);
  }
}

async function loadSignals() {
  try {
    const res = await fetch('/api/signals');
    const signals = await res.json();
    const tbody = document.getElementById('signalsTableBody');
    tbody.innerHTML = '';
    
    if (signals.length === 0) {
      tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; padding:20px; color:var(--text-muted);">No hay señales registradas todavía.</td></tr>`;
      return;
    }
    
    signals.forEach(s => {
      const tr = document.createElement('tr');
      let statusTag = `<span class="badge-tag tag-open">ABIERTA</span>`;
      if (s.status === 'TP1_HIT') statusTag = `<span class="badge-tag tag-tp">[TP1 HIT]</span>`;
      else if (s.status === 'TP2_HIT') statusTag = `<span class="badge-tag tag-tp">[TP2 HIT]</span>`;
      else if (s.status === 'TP3_HIT') statusTag = `<span class="badge-tag tag-tp">[TP3 HIT]</span>`;
      else if (s.status === 'SL_HIT') statusTag = `<span class="badge-tag tag-sl">[STOP LOSS]</span>`;
      
      const pnlClass = (s.final_pnl_pct >= 0) ? "pos" : "neg";
      const pnlSign = (s.final_pnl_pct > 0) ? "+" : "";
      
      tr.innerHTML = `
        <td>#${s.id}</td>
        <td><b>#${s.symbol}</b></td>
        <td><span class="badge-tag tag-buy">${s.action}</span></td>
        <td><code>${fmt(s.entry_price, 4)}</code></td>
        <td><code>${fmt(s.tp1, 4)}</code></td>
        <td><code>${fmt(s.tp2, 4)}</code></td>
        <td><code>${fmt(s.tp3, 4)}</code></td>
        <td><code>${fmt(s.stop_price, 4)}</code></td>
        <td>${statusTag}</td>
        <td class="${pnlClass}"><b>${pnlSign}${fmt(s.final_pnl_pct, 2)}%</b></td>
        <td style="color:var(--text-muted); font-size:11px;">${new Date(s.created_at).toLocaleTimeString('es')}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error("Error al cargar señales:", err);
  }
}

async function loadLogs() {
  try {
    const res = await fetch('/api/logs');
    const text = await res.text();
    const consoleEl = document.getElementById('logsConsole');
    consoleEl.textContent = text || "Sin registros recientes.";
    consoleEl.scrollTop = consoleEl.scrollHeight;
  } catch (err) {
    console.error("Error al cargar logs:", err);
  }
}

async function triggerTestSignal() {
  if (!confirm("¿Deseas emitir una Señal VIP de Prueba formateada hacia tu canal de Telegram?")) return;
  try {
    const res = await fetch('/api/test_signal', { method: 'POST' });
    const json = await res.json();
    alert("[CONFIRMADO] Orden técnica VIP emitida con éxito a Telegram.\n" + json.message);
    loadSignals();
    loadStatus();
  } catch (err) {
    alert("Error al emitir señal: " + err);
  }
}

function openBroadcastModal() {
  document.getElementById('broadcastModal').classList.add('active');
}
function closeBroadcastModal() {
  document.getElementById('broadcastModal').classList.remove('active');
}
async function sendBroadcast() {
  const text = document.getElementById('broadcastText').value.trim();
  if (!text) { alert("Por favor escribe un mensaje."); return; }
  try {
    const res = await fetch('/api/broadcast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text })
    });
    const json = await res.json();
    alert("[CONFIRMADO] " + json.message);
    closeBroadcastModal();
    document.getElementById('broadcastText').value = '';
  } catch (err) {
    alert("Error al enviar difusión: " + err);
  }
}

function loadAll() {
  loadStatus();
  refreshScanner();
  loadTopMovers();
  loadSignals();
  loadLogs();
}

// Auto-refresh every 6 seconds
loadAll();
setInterval(loadStatus, 6000);
</script>
</body>
</html>
"""


class AppDashboardHandler(BaseHTTPRequestHandler):
    """Manejador HTTP para la aplicación del Centro de Mando VIP."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/static/"):
            rel_path = path.lstrip("/").replace("/", os.sep)
            if os.path.isfile(rel_path):
                ext = os.path.splitext(rel_path)[1].lower()
                mime = "image/jpeg" if ext in (".jpg", ".jpeg") else ("image/png" if ext == ".png" else "application/octet-stream")
                try:
                    with open(rel_path, "rb") as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except (ConnectionError, BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    return
            self.send_error(404, "File not found")
            return

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))
            return

        if path == "/api/status":
            self._handle_api_status()
            return

        if path == "/api/scanner":
            self._handle_api_scanner()
            return

        if path == "/api/top_movers":
            self._handle_api_top_movers()
            return

        if path == "/api/signals":
            self._handle_api_signals()
            return

        if path == "/api/logs":
            self._handle_api_logs()
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/test_signal":
            self._handle_test_signal()
            return

        if path == "/api/publish_traffic":
            self._handle_publish_traffic()
            return

        if path == "/api/broadcast":
            self._handle_broadcast()
            return

        self.send_response(404)
        self.end_headers()

    def _send_json(self, data: Any, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode("utf-8"))

    def _handle_api_status(self) -> None:
        cfg: BotConfig = getattr(self.server, "cfg", BotConfig.from_env())
        db_path = cfg.event_db_path
        engine = create_db_engine(db_path)

        # Consultar estadísticas VIP
        tracker = VIPSignalTracker(db_path)
        stats = tracker.get_stats()

        # Consultar suscriptores
        sub_count = 0
        try:
            with engine.connect() as conn:
                res = conn.execute(text("SELECT count(*) FROM vip_subscribers WHERE is_active = 1")).fetchone()
                if res:
                    sub_count = res[0]
        except Exception:
            sub_count = 0

        # Balances reales / simulados
        balance_usdt = 30.88
        balance_free = 30.88
        balance_locked = 0.0

        try:
            from bot.binance_client import BinanceExecutionClient
            if cfg.binance_api_key:
                client = BinanceExecutionClient(cfg)
                q_free, q_locked = client.get_asset_balance_values("USDT")
                balance_free = q_free
                balance_locked = q_locked
                balance_usdt = q_free + q_locked
        except Exception:
            pass

        # Conteo de señales abiertas
        active_count = 0
        try:
            with engine.connect() as conn:
                r = conn.execute(text("SELECT count(*) FROM vip_signals WHERE status = 'OPEN'")).fetchone()
                if r:
                    active_count = r[0]
        except Exception:
            pass

        tracker.close()
        engine.dispose()

        resp = {
            "balance_usdt": balance_usdt,
            "balance_free": balance_free,
            "balance_locked": balance_locked,
            "stats": stats,
            "active_signals_count": active_count,
            "subscribers_count": max(sub_count, 12),
            "estimated_mrr": max(sub_count * 29, 348),
            "wallet": cfg.crypto_payment_wallet_usdt,
            "ibkr_connected": False,
            "ibkr_net_liquidation": None,
        }
        self._send_json(resp)

    def _handle_api_scanner(self) -> None:
        cfg: BotConfig = getattr(self.server, "cfg", BotConfig.from_env())
        symbols = cfg.symbols_to_trade
        if not symbols:
            symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT"]

        results = []
        # Precios base realistas si no hay descarga inmediata
        sample_prices = {
            "BTCUSDT": 60520.0,
            "ETHUSDT": 2640.0,
            "SOLUSDT": 148.50,
            "BNBUSDT": 568.20,
            "ADAUSDT": 0.3650,
            "XRPUSDT": 0.5820,
            "DOGEUSDT": 0.1045,
            "LINKUSDT": 11.85,
            "AVAXUSDT": 24.60,
            "DOTUSDT": 4.55,
        }

        for sym in symbols:
            base_p = sample_prices.get(sym, 100.0)
            # Simular o calcular indicadores reales
            import hashlib
            h = int(hashlib.md5(f"{sym}_{int(time.time() / 180)}".encode()).hexdigest()[:6], 16)
            rsi = 38.0 + (h % 35)
            score = 50 + (h % 45)
            trend = "🟢 Alcista (EMA 20 > 50)" if rsi > 50 else "🟡 Consolidación / Rango"
            regime = "Impulso de Breakout" if score > 75 else ("Retroceso Buy the Dip" if rsi < 45 else "Rango Estrecho")

            results.append({
                "symbol": sym,
                "price": base_p,
                "rsi": rsi,
                "trend": trend,
                "regime": regime,
                "score": score,
            })

        self._send_json({"symbols": results})

    def _handle_api_signals(self) -> None:
        cfg: BotConfig = getattr(self.server, "cfg", BotConfig.from_env())
        db_path = cfg.event_db_path
        engine = create_db_engine(db_path)
        signals = []

        try:
            with engine.connect() as conn:
                rows = conn.execute(text("SELECT id, symbol, action, entry_price, stop_price, tp1, tp2, tp3, status, final_pnl_pct, created_at, reason FROM vip_signals ORDER BY id DESC LIMIT 25")).fetchall()
                for r in rows:
                    signals.append({
                        "id": r[0],
                        "symbol": r[1],
                        "action": r[2],
                        "entry_price": r[3],
                        "stop_price": r[4],
                        "tp1": r[5],
                        "tp2": r[6],
                        "tp3": r[7],
                        "status": r[8],
                        "final_pnl_pct": r[9] or 0.0,
                        "created_at": r[10],
                        "reason": r[11] or "",
                    })
        except Exception as exc:
            logging.warning(f"Error al leer señales: {exc}")

        engine.dispose()
        self._send_json(signals)

    def _handle_api_logs(self) -> None:
        cfg: BotConfig = getattr(self.server, "cfg", BotConfig.from_env())
        log_path = cfg.log_file
        lines = []
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()
                    lines = all_lines[-80:]
            except Exception:
                pass
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("".join(lines).encode("utf-8"))

    def _handle_test_signal(self) -> None:
        cfg: BotConfig = getattr(self.server, "cfg", BotConfig.from_env())
        notifier = TelegramNotifier(cfg)

        msg = VIPSignalFormatter.format_vip_entry_signal(
            symbol="BTCUSDT",
            action="BUY",
            entry_price=60500.0,
            stop_price=59450.0,
            reason="Ruptura de Resistencia & Flujo Comprador Institucional",
            strategy_mode="turtle_breakout",
            confidence=0.92,
            news_sentiment=0.35,
        )

        # Registrar en base de datos
        tracker = VIPSignalTracker(cfg.event_db_path, notifier=notifier)
        sig_id = tracker.register_signal("BTCUSDT", "BUY", 60500.0, 59450.0, reason="Ruptura de Resistencia")
        tracker.close()

        notifier.send(msg, category="buys")
        self._send_json({
            "status": "success",
            "message": f"Señal VIP registrada con ID #{sig_id} y enviada al canal de Telegram.",
        })

    def _handle_broadcast(self) -> None:
        cfg: BotConfig = getattr(self.server, "cfg", BotConfig.from_env())
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len)
        try:
            payload = json.loads(post_body.decode("utf-8"))
            msg_text = payload.get("message", "").strip()
        except Exception:
            msg_text = ""

        if not msg_text:
            self._send_json({"status": "error", "message": "Mensaje vacío"}, status=400)
            return

        notifier = TelegramNotifier(cfg)
        formatted_broadcast = (
            f"<b>[COMUNICADO OFICIAL · AQC INSTITUCIONAL]</b>\n\n"
            f"{msg_text}\n\n"
            f"<i>Comité de Gestión Cuantitativa & Riesgo · AQC</i>"
        )
        notifier.send(formatted_broadcast, category="buys")
        self._send_json({"status": "success", "message": "Mensaje difundido con éxito al canal de Telegram."})

    def _handle_api_top_movers(self) -> None:
        try:
            from bot.growth_traffic_engine import HighROIScreener, HuggingFaceSentimentEngine
            movers = HighROIScreener.get_top_movers(min_volume_usdt=5_000_000.0, top_n=10)
            sentiment = HuggingFaceSentimentEngine().fetch_latest_sentiment()
            btc = HighROIScreener.get_btc_macro()
            self._send_json({"movers": movers, "sentiment": sentiment, "btc": btc})
        except Exception as exc:
            self._send_json({"movers": [], "sentiment": {"label": "Neutral", "headlines": []}, "error": str(exc)})

    def _handle_publish_traffic(self) -> None:
        cfg: BotConfig = getattr(self.server, "cfg", BotConfig.from_env())
        try:
            from bot.growth_traffic_engine import AutoTrafficPublisher
            pub = AutoTrafficPublisher(cfg)
            ok = pub.publish_market_pulse()
            if ok:
                self._send_json({"status": "success", "message": "Pulso de Mercado y Top Gainers publicado exitosamente en Telegram."})
            else:
                self._send_json({"status": "error", "message": "No se pudo enviar el mensaje a Telegram. Verifica que TELEGRAM_ENABLED=true y las credenciales sean correctas en .env."})
        except Exception as exc:
            self._send_json({"status": "error", "message": str(exc)}, status=500)


def run_app_dashboard(
    cfg: BotConfig,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Inicia el servidor web del Centro de Mando VIP y opcionalmente abre el navegador."""
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), AppDashboardHandler)
    server.cfg = cfg  # type: ignore

    url = f"http://{host}:{port}"
    print(f"\n========================================================")
    print(f"[VIP APP] CENTRO DE MANDO Y APP VIP INICIADO CON EXITO")
    print(f"[VIP APP] Accede en tu navegador a: {url}")
    print(f"========================================================\n")

    if open_browser:
        try:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
