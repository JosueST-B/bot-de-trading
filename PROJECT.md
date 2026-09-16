# Project: Institutional Platform Monochrome Terminal Redesign

## Architecture
The platform operates as a dual-surface institutional web application:
1. **Dynamic Python Web Server (`bot/institutional_portal.py` / `main.py portal`)**:
   - Native HTTP server listening on port 8765 (institutional portal) and port 8770 (`main.py panel` / `/admin`).
   - Serves `INSTITUTIONAL_PORTAL_HTML`, handles REST APIs for investor stats, allocations, AES-256 API credentials, 2FA withdrawals, RL evolution telemetry, and PDF/CSV/JSON report exports.
2. **Static Distribution (`index.html` & `docs/index.html`)**:
   - Hosted on GitHub Pages (`https://josuest-b.github.io/bot-de-trading/`).
   - Pure client-side execution with transparent fallbacks (client-side blob report generators, simulated RL updates).
3. **Core Design System**:
   - "Monochrome Terminal" standard (Jane Street / Linear / Vercel).
   - Zero photographic images (.jpg, .png) in the DOM.
   - Zinc/carbon palette: `#09090b` base, `#111215` surface, `#14161b` cards, `1px solid rgba(255, 255, 255, 0.08)` micro-borders, `#f4f4f5` solid white accent button.
   - Dual typography: Inter + JetBrains Mono with `font-feature-settings: "tnum" 1, "zero" 1`.
   - Semantic colors restricted strictly to numeric deltas: `#10b981` (emerald) and `#f43f5e` (coral).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | DOM Image Purge | Complete elimination of photographic stock images (.jpg, .png, <img>) from DOM | M1 | ORIGINAL_REQUEST.md R1 |
| 2 | Monochrome Terminal Palette | Application of precise zinc/carbon palette (#09090b, #111215, #14161b), micro-borders 1px, and #f4f4f5 white button | M1 | ORIGINAL_REQUEST.md R1 |
| 3 | Muted Semantic Accents | Color accents restricted strictly to numeric variation indicators: emerald #10b981 and coral #f43f5e | M1 | ORIGINAL_REQUEST.md R1 |
| 4 | Dual Typography & Tabular Figures | Inter for prose, JetBrains Mono for code/metrics, with CSS font-feature-settings: "tnum" 1, "zero" 1 | M1 | ORIGINAL_REQUEST.md R1 |
| 5 | 4-Block Visual Flow | Sequential 4-block page architecture with anchor navigation (#overview, #performance, #simulator, #security) | M1 | ORIGINAL_REQUEST.md R2 |
| 6 | Audited Executive Metrics | Prominent display of certified track record: Sharpe 2.42, Sortino 3.10, Drawdown -6.4%, Win Rate 78.5%, Profit Factor 2.65, AUM $18.42M | M1 | ORIGINAL_REQUEST.md R2.1 |
| 7 | Live Streaming Ticker Tape | Real-time market ticker across major assets (BTC, ETH, SOL, NVDA, AAPL) with 24/7 session indicators | M1 | ORIGINAL_REQUEST.md R2.1 |
| 8 | Auto-Evolution Telemetry | Continuous reinforcement learning (FinBERT + Thompson Sampling Bandit) telemetry: Generation, Loss, Sentiment, weights | M1 | ORIGINAL_REQUEST.md R2.1 |
| 9 | RL Evolution Step Action | Interactive button to trigger live RL auto-tuning step recalibrating weights | M1 | ORIGINAL_REQUEST.md R2.1 |
| 10 | Vector Equity Chart | High-precision dual-pane financial chart rendering strategy equity vs SPY benchmark (1M, 3M, 6M, 1Y, ALL) | M1 | ORIGINAL_REQUEST.md R2.2 |
| 11 | Crosshair Cursor & Tooltip | Fine interactive crosshair line tracking mouse movement with precise coordinate tooltip | M1 | ORIGINAL_REQUEST.md R2.2 |
| 12 | Fiduciary Drawdown Lock (-6.4%) | Lower chart sub-panel displaying historical drawdowns with horizontal red alert line at -6.4% baseline | M1 | ORIGINAL_REQUEST.md R2.2 |
| 13 | Risk Heatmap & Matrix | Cross-asset correlation and probability density matrix for volatility mitigation | M1 | ORIGINAL_REQUEST.md R2.2 |
| 14 | Actuarial Return Simulator | Compound and simple interest projection model with sliders for initial capital and duration | M1 | ORIGINAL_REQUEST.md R2.3 |
| 15 | Instant Multi-Currency Switcher | Currency converter supporting USDT, USD, EUR (0.92), BTC (0.0000165) | M1 | ORIGINAL_REQUEST.md R2.3 |
| 16 | Fiduciary Fee Structure Disclosure | Explicit institutional fee schedule: 0% Management Fee, 5% Annual Hurdle Rate, 20% Strict High-Water Mark | M1 | ORIGINAL_REQUEST.md R2.3 |
| 17 | Non-Custodial API Connection | Secure credential submission for Binance/Bybit/IBKR with AES-256 PBKDF2 encryption at rest (read/trade only) | M2 | ORIGINAL_REQUEST.md R2.4 |
| 18 | 2FA Protected Fund Liquidations | Withdrawal request dispatch with 2FA TOTP authentication and strict SLA < 24h | M2 | ORIGINAL_REQUEST.md R2.4 |
| 19 | Interactive Ledger Search & Filter | Real-time search filter filtering table rows by asset symbol (BTC, ETH, SOL, NVDA) or arbitrary text | M2 | ORIGINAL_REQUEST.md R2.4 |
| 20 | PDF Audit Report Export | Generation and download of official cryptographic audit report PDF (HTTP 200 OK) | M2 | ORIGINAL_REQUEST.md R2.4 |
| 21 | CSV Ledger Export | Download of complete transaction history in CSV format (HTTP 200 OK) | M2 | ORIGINAL_REQUEST.md R2.4 |
| 22 | JSON Audit Export | Structured JSON export of syndicate telemetry, circuit breakers, and audited trades (HTTP 200 OK) | M2 | ORIGINAL_REQUEST.md R2.4 |
| 23 | Local Institutional Portal Server | Local HTTP server executed via Python CLI (main.py portal --host 127.0.0.1 --port 8765) | M3 | ORIGINAL_REQUEST.md R3 |
| 24 | Operator Workstation (/admin) | Administrative operations terminal and live telemetry panel on port 8770 | M3 | ORIGINAL_REQUEST.md R3 |
| 25 | GitHub Pages Static Parity | Offline/static parity deployment hosted at https://josuest-b.github.io/bot-de-trading/ (docs/index.html) | M3 | ORIGINAL_REQUEST.md R3 |
| 26 | Telegram Buy Spot Notification | Formatted HTML notification sent when spot buy executes | M3 | ORIGINAL_REQUEST.md R1.1 |
| 27 | Telegram Sell Spot Notification | Formatted HTML notification sent when spot sell executes | M3 | ORIGINAL_REQUEST.md R1.1 |
| 28 | Telegram HTML Sanitization | Sanitization utility converting <, >, & to entities | M3 | ORIGINAL_REQUEST.md R1.2 |
| 29 | Selective Notification Config | Selective suppression of notification types via .env flags | M3 | ORIGINAL_REQUEST.md R1.3 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Monochrome Terminal UI & 4-Block Flow | Purge 100% images (.jpg/.png/<img>), apply zinc palette, 1px micro-borders, dual typography with tnum/zero, pure SVG icons, organize 4-block layout, Canvas chart crosshair, DD lock -6.4%, actuarial simulator, multi-currency switcher | Survey Complete | IN_PROGRESS |
| M2 | Security, Audited Ledger & Export Engine | Block 4 (AES-256 API connection, 2FA TOTP liquidations, interactive search/filter by asset, PDF/CSV/JSON report generation 200 OK with client blob fallback) | M1 | PLANNED |
| M3 | Multi-Platform Sync & Regression Verification | Mirror parity between `bot/institutional_portal.py`, `index.html`, `docs/index.html`; verify ports 8765 and 8770; pass 100% existing unit tests | M2 | PLANNED |
| M4 | Final Milestone: 100% E2E Test Suite & Adversarial Hardening | Phase 1: Pass 100% E2E test suite (Tiers 1-4). Phase 2: Tier 5 Adversarial Coverage Hardening & Forensic Audit | M3, E2E Test Track | PLANNED |

## Interface Contracts
### Client UI ↔ Backend Portal API (`bot/institutional_portal.py`)
- `GET /api/investor/stats` -> JSON `{"sharpe": 2.42, "sortino": 3.10, "win_rate": 78.5, "max_drawdown": -6.4, ...}`
- `GET /api/evolution/status` -> JSON `{"generation": 48, "loss": 0.0412, "weights": {"hawkes": 0.28, ...}, ...}`
- `POST /api/evolution/train-step` -> JSON `{"generation": 49, "loss": 0.0385, "weights": {...}}`
- `POST /api/investor/connect-api` -> JSON `{"status": "connected", "platform": "binance", "encrypted": true}`
- `POST /api/investor/withdraw` -> JSON `{"status": "ticket_created", "ticket_id": "AQC-WTH-XXXXX", "sla": "< 24h"}`
- `GET /api/investor/report-pdf` -> Binary stream `application/pdf` (HTTP 200 OK)
- `GET /api/investor/report-csv` -> Text stream `text/csv` (HTTP 200 OK)
- `GET /api/investor/report-json` -> JSON `application/json` (HTTP 200 OK)

### Test Invariant Contract (`tests/test_institutional_portal.py`)
The following mandatory brand strings MUST be preserved verbatim in `INSTITUTIONAL_PORTAL_HTML`, `index.html`, and `docs/index.html`:
1. `"Aethelgard Quantitative"`
2. `"Mandatos & Estructuras de Inversión"`
3. `"Simulador Cuantitativo de Retornos"`
4. `"Ratio Sharpe"`

## Code Layout
- `bot/institutional_portal.py`: Institutional portal HTTP server, SPA router, APIs, and embedded HTML template.
- `index.html`: Root static mirror of the institutional portal.
- `docs/index.html`: GitHub Pages deployment mirror.
- `tests/test_institutional_portal.py`: Unit test suite for portal server and endpoints.
- `tests/test_e2e_monochrome.py`: Automated E2E test suite covering Tiers 1-4.
