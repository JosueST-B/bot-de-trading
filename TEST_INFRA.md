# TEST INFRASTRUCTURE & VERIFICATION SPECIFICATION
# Monochrome Terminal Institutional Redesign

- **Platform**: Aethelgard Quantitative Asset Management — Institutional Platform
- **Test Architecture**: 4-Tier Automated Opaque-Box E2E Test Suite
- **Authoritative Specifications**: `ORIGINAL_REQUEST.md`, `PROJECT.md`, `spec_mining_report.md`
- **Primary Test Runner**: `.\venv\Scripts\python.exe -m unittest tests/test_e2e_monochrome.py`
- **Integrity Level**: Full Opaque-Box Black-Box / Integration Testing

---

## 1. Core Testing Philosophy & Architecture

The Monochrome Terminal platform redesign transitions the institutional web application to a minimal, high-density, high-contrast trading interface adhering to standards established by **Jane Street, Linear, and Vercel**.

The verification architecture is built around three fundamental tenets:
1. **Opaque-Box End-to-End Execution**: Tests interact with the platform exclusively through external HTTP interfaces (native Python HTTP daemon on `127.0.0.1:<port>`), public DOM structures, client-side scripts, and data export payloads. No internal private state is mutated or mocked unless isolating external third-party network gateways (e.g. Telegram API).
2. **Deterministic Expected Output Derivation**: Every test assertion is derived strictly from the authoritative specifications in `ORIGINAL_REQUEST.md`, `PROJECT.md`, and `spec_mining_report.md`. Mathematical calculations (compound interest $P(1+r)^t$, FX conversion rates, Sharpe/Sortino ratios) have explicit closed-form derivations.
3. **Multi-Platform Parity & Zero Regression**: Full functional and structural equivalence is verified across the native Python HTTP server (`main.py portal` on port 8765), the operator control center (`/admin` on port 8770), the root SPA distribution (`index.html`), and the GitHub Pages deployment (`docs/index.html`).

```
+-------------------------------------------------------------------------------+
|                    TIER 4: REAL-WORLD FIDUCIARY SCENARIOS                     |
|  - T4.1 Full Institutional Investor Allocation & Onboarding Journey           |
|  - T4.2 Deep Cryptographic & Structural Audit Export Inspection (PDF/CSV/JSON)|
|  - T4.3 GitHub Pages Offline Static Demonstration Parity Journey              |
+-------------------------------------------------------------------------------+
                                       ^
+-------------------------------------------------------------------------------+
|                  TIER 3: CROSS-FEATURE INTERACTIONS & STRESS                  |
|  - T3.1 Currency Switcher + Actuarial Simulator + Ledger Synchronization     |
|  - T3.2 Timeframe Selector (1M-ALL) + Canvas Crosshair + -6.4% DD Lock       |
|  - T3.3 RL Evolution Step Trigger + Live Market Ticker Influx                 |
|  - T3.4 Ledger Multi-Keyword Search + Symbol Filter Intersection             |
+-------------------------------------------------------------------------------+
                                       ^
+-------------------------------------------------------------------------------+
|                  TIER 2: BOUNDARY CONDITIONS & CORNER CASES                   |
|  - T2.1 BTC High-Precision Fractional Conversion (4 Decimals: 0.0304 BTC)     |
|  - T2.2 Rapid Sequential Currency Toggling Stability (USDT-EUR-BTC-USD)       |
|  - T2.3 Simulator Capital ($1k-$100k) & Duration (1-36 mo) Limits            |
|  - T2.4 Canvas Mouseleave & Out-of-Bounds Crosshair Deactivation              |
|  - T2.5 -6.4% Fiduciary Drawdown Lock Alert Threshold (Normal/Boundary/Breach)|
|  - T2.6 Ledger Search Regex Meta-Character Immunity ([, ], *, +, ?, \)       |
|  - T2.7 Empty / Zero Matching Asset Search Handling                           |
|  - T2.8 Non-Custodial Withdrawal < $50 Minimum SLA Rejection                  |
|  - T2.9 API Credential Whitespace & Special Character Sanitization            |
+-------------------------------------------------------------------------------+
                                       ^
+-------------------------------------------------------------------------------+
|                    TIER 1: FEATURE COVERAGE & DOM CONTRACTS                   |
|  - T1.1 DOM Zero Photographic Image Purge (0 .jpg, 0 .png, 0 stock <img>)     |
|  - T1.2 Monochrome Terminal Zinc Design Tokens (#09090b, #111215, #14161b)    |
|  - T1.3 Dual Typography & Tabular Figures (font-feature-settings: tnum, zero) |
|  - T1.4 4-Block Sequential Architecture & Navigation Anchors                  |
|  - T1.5 Certified Executive KPIs (Sharpe 2.42, Sortino 3.10, Drawdown -6.4%)  |
|  - T1.6 Live Streaming Market Ticker Tape (BTC, ETH, SOL, NVDA, AAPL)        |
|  - T1.7 Hugging Face FinBERT & Bandit RL Auto-Evolution Telemetry             |
|  - T1.8 Vector Canvas Financial Equity Chart with Dual-Pane NAV / SPY         |
|  - T1.9 Fiduciary Drawdown Lock Indicator Baseline (-6.4%)                    |
|  - T1.10 Actuarial Return Simulator Interactive Sliders & Formula Selectors  |
|  - T1.11 Instant Multi-Currency Switcher Controls (USDT, USD, EUR, BTC)       |
|  - T1.12 Fiduciary Fee Structure Disclosure (0% Mgmt, 5% Hurdle, 20% HWM)     |
|  - T1.13 Non-Custodial API Key Connection Form (AES-256 PBKDF2 Storage)       |
|  - T1.14 2FA TOTP Protected Fund Liquidation Request Dispatch (SLA < 24h)     |
|  - T1.15 Interactive Audited Ledger Table, Filter Pills & Search Bar          |
|  - T1.16 PDF Audit Report Generation Endpoint (/api/investor/report-pdf) 200  |
|  - T1.17 CSV Transaction Ledger Export Endpoint (/api/investor/report-csv) 200|
|  - T1.18 JSON Telemetry & Ledger Export Endpoint (/api/investor/report-json)  |
|  - T1.19 Local Python Server REST APIs & Universal SPA Router Fallback        |
+-------------------------------------------------------------------------------+
```

---

## 2. Feature Inventory Coverage Matrix (Features 1–29)

| Feature # | Feature Name | Tier Mapping | Test Method Name in `test_e2e_monochrome.py` | Authoritative Source |
|---|---|---|---|---|
| 1 | DOM Image Purge | Tier 1 | `test_t1_01_dom_zero_image_purge` | `ORIGINAL_REQUEST.md` R1 |
| 2 | Monochrome Palette & Borders | Tier 1 | `test_t1_02_monochrome_palette_tokens` | `ORIGINAL_REQUEST.md` R1 |
| 3 | Muted Semantic Accents | Tier 1 | `test_t1_02_monochrome_palette_tokens` | `ORIGINAL_REQUEST.md` R1 |
| 4 | Dual Typography & Tabular Figures | Tier 1 | `test_t1_03_tabular_figures_dual_typography` | `ORIGINAL_REQUEST.md` R1 |
| 5 | 4-Block Visual Flow | Tier 1 | `test_t1_04_four_block_navigation_anchors` | `ORIGINAL_REQUEST.md` R2 |
| 6 | Audited Executive Metrics | Tier 1 | `test_t1_05_audited_executive_kpis` | `ORIGINAL_REQUEST.md` R2.1 |
| 7 | Live Streaming Ticker Tape | Tier 1 | `test_t1_06_live_streaming_ticker_tape` | `ORIGINAL_REQUEST.md` R2.1 |
| 8 | Auto-Evolution Telemetry | Tier 1 | `test_t1_07_finbert_rl_telemetry` | `ORIGINAL_REQUEST.md` R2.1 |
| 9 | RL Evolution Step Action | Tier 1 & 3 | `test_t1_07_finbert_rl_telemetry`, `test_t3_03_rl_step_and_ticker` | `ORIGINAL_REQUEST.md` R2.1 |
| 10 | Vector Equity Chart | Tier 1 | `test_t1_08_vector_equity_chart` | `ORIGINAL_REQUEST.md` R2.2 |
| 11 | Crosshair Cursor & Tooltip | Tier 1 & 2 | `test_t1_08_vector_equity_chart`, `test_t2_04_canvas_mouseleave` | `ORIGINAL_REQUEST.md` R2.2 |
| 12 | Fiduciary Drawdown Lock (-6.4%) | Tier 1 & 2 | `test_t1_09_drawdown_lock_baseline`, `test_t2_05_dd_lock_threshold`| `ORIGINAL_REQUEST.md` R2.2 |
| 13 | Risk Heatmap & Matrix | Tier 1 | `test_t1_08_vector_equity_chart` | `PROJECT.md` Feature 13 |
| 14 | Actuarial Return Simulator | Tier 1 & 2 | `test_t1_10_actuarial_simulator_inputs`, `test_t2_03_simulator_limits`| `ORIGINAL_REQUEST.md` R2.3 |
| 15 | Instant Multi-Currency Switcher | Tier 1, 2, 3 | `test_t1_11_currency_switcher`, `test_t2_01_btc_precision`, `test_t3_01_currency_simulator_sync` | `ORIGINAL_REQUEST.md` R2.3 |
| 16 | Fiduciary Fee Structure Disclosure | Tier 1 | `test_t1_12_fiduciary_fee_schedule` | `ORIGINAL_REQUEST.md` R2.3 |
| 17 | Non-Custodial API Connection | Tier 1, 2, 4 | `test_t1_13_non_custodial_api_connection`, `test_t2_09_api_sanitization`, `test_t4_01_investor_journey` | `ORIGINAL_REQUEST.md` R2.4 |
| 18 | 2FA Protected Withdrawals | Tier 1 & 2 | `test_t1_14_2fa_withdrawals`, `test_t2_08_withdrawal_min_validation` | `ORIGINAL_REQUEST.md` R2.4 |
| 19 | Interactive Ledger Search & Filter | Tier 1, 2, 3 | `test_t1_15_ledger_search_filter`, `test_t2_06_regex_search`, `test_t3_04_combined_ledger_filter` | `ORIGINAL_REQUEST.md` R2.4 |
| 20 | PDF Audit Report Export | Tier 1 & 4 | `test_t1_16_pdf_export_endpoint`, `test_t4_02_export_inspection` | `ORIGINAL_REQUEST.md` R2.4 |
| 21 | CSV Ledger Export | Tier 1 & 4 | `test_t1_17_csv_export_endpoint`, `test_t4_02_export_inspection` | `ORIGINAL_REQUEST.md` R2.4 |
| 22 | JSON Audit Export | Tier 1 & 4 | `test_t1_18_json_export_endpoint`, `test_t4_02_export_inspection` | `ORIGINAL_REQUEST.md` R2.4 |
| 23 | Local Institutional Portal Server | Tier 1 | `test_t1_19_server_routes_and_spa_fallback` | `ORIGINAL_REQUEST.md` R3 |
| 24 | Operator Workstation (/admin) | Tier 1 | `test_t1_19_server_routes_and_spa_fallback` | `ORIGINAL_REQUEST.md` R3 |
| 25 | GitHub Pages Static Parity | Tier 4 | `test_t4_03_github_pages_static_parity` | `ORIGINAL_REQUEST.md` R3 |
| 26 | Telegram Buy Spot Notification | Legacy | `tests/run_tests.py` | `ORIGINAL_REQUEST.md` R1.1 |
| 27 | Telegram Sell Spot Notification| Legacy | `tests/run_tests.py` | `ORIGINAL_REQUEST.md` R1.1 |
| 28 | Telegram HTML Sanitization | Legacy | `tests/run_tests.py` | `ORIGINAL_REQUEST.md` R1.2 |
| 29 | Selective Notification Config | Legacy | `tests/run_tests.py` | `ORIGINAL_REQUEST.md` R1.3 |

---

## 3. Test Derivation Methodology & Authoritative Oracles

### 3.1 Mathematical Oracles for Actuarial Simulator
- **Compounding Mode Formula**:
  $$V_{\text{final}} = P \times (1 + r)^t$$
  where $P$ is principal, $r = 0.052$ monthly rate (62.4% APR), $t$ is duration in months.
  $$\text{Net Profit} = V_{\text{final}} - P$$
  $$\text{Monthly Average} = \frac{\text{Net Profit}}{t}$$
- **Distribution Mode Formula**:
  $$V_{\text{final}} = P \times (1 + r \times t)$$
  $$\text{Net Profit} = P \times r \times t$$
- **Oracle Values**:
  * $10,000 USD for 12 months (Compound):
    $$V_{\text{final}} = 10000 \times (1.052)^{12} = 10000 \times 1.837138 = \$18,371.38$$
    $$\text{Net Profit} = \$8,371.38$$
  * $1,000 USD for 1 month: Net Profit = $\$52.00$
  * $100,000 USD for 36 months (Compound): Net Profit = $\$523,903.88$

### 3.2 Currency Conversion Oracle
- **Exchange Rates**:
  * USDT: $1.0$ (Baseline peg)
  * USD: $1.0$ (Parity)
  * EUR: $0.92$ (1 USD = 0.92 EUR)
  * BTC: $0.0000165$ (1 USD = 0.0000165 BTC $\rightarrow$ BTC = ~$60,606.06 USD)
- **Formatting Constraints**:
  * Standard currencies (USDT, USD, EUR): 2 decimal places.
  * BTC: Minimum 4 decimal places (e.g. `0.0304 BTC`) to avoid catastrophic rounding truncation to `0.00 BTC`.

### 3.3 Cryptographic & Export Oracles
- **PDF Export**:
  * HTTP Status: `200 OK`
  * Content-Type: `application/pdf`
  * Magic Byte Signature: `b"%PDF-1."`
  * Content-Disposition: contains `filename=`
- **CSV Export**:
  * HTTP Status: `200 OK`
  * Content-Type: `text/csv; charset=utf-8`
  * Header Line: `ID,Timestamp,Asset,Market,Side,Entry_Price,Exit_Price,Size_USD,Net_PnL_USD,Return_Pct,Tx_Hash_Verification`
  * Row Format: RFC-4180 compliant CSV lines.
- **JSON Export**:
  * HTTP Status: `200 OK`
  * Content-Type: `application/json; charset=utf-8`
  * JSON Schema: Requires `syndicate`, `telemetry` (with `sharpe_ratio`, `circuit_breakers`), and `audited_ledger` array with verified transaction hashes.

---

## 4. Test Execution & Pass Thresholds

### 4.1 Test Execution Command
The test suite is fully self-contained and executed using Python's standard `unittest` framework within the project virtual environment:

```powershell
.\venv\Scripts\python.exe -m unittest tests/test_e2e_monochrome.py
```

To run the complete platform test suite (unit + E2E):
```powershell
.\venv\Scripts\python.exe -m unittest discover tests
```

### 4.2 Pass/Fail Criteria & Quality Gates
1. **0 Failures, 0 Errors**: Exit code must be `0`.
2. **Execution Time**: The full E2E test suite must execute within 15 seconds.
3. **No Unclosed Resources**: Clean tearDown of HTTP server daemon, sockets, and memory buffers.
4. **Zero Photographic Assets**: HTML DOM inspection must assert 0 `.jpg` or `.png` references.
5. **Brand Invariant Preservation**: Verbatim preservation of required brand strings (`Aethelgard Quantitative`, `Mandatos & Estructuras de Inversión`, `Simulador Cuantitativo de Retornos`, `Ratio Sharpe`).
