# TEST_READY: Monochrome Terminal Platform Redesign E2E Suite

- **Component**: Institutional Platform ("Monochrome Terminal" Redesign)
- **Author**: Test Writer Subagent (Track A - E2E Verification)
- **Status**: **READY & OPERATIONAL** (Harness Deployed, 35 Tests Executed)
- **Date**: 2026-09-15T20:50:00Z
- **Primary Suite File**: `tests/test_e2e_monochrome.py`
- **Execution Command**: `.\venv\Scripts\python.exe -m unittest tests/test_e2e_monochrome.py`
- **Full Discovery Command**: `.\venv\Scripts\python.exe -m unittest discover tests`

---

## 1. Executive Summary

The automated opaque-box End-to-End (E2E) test harness for the Monochrome Terminal platform redesign has been implemented, validated, and integrated into the project's test suite.

The test harness runs in the project virtual environment (`.\venv\Scripts\python.exe`) using standard `unittest`. It launches an isolated, live `ThreadingHTTPServer` daemon on port `8796` and performs true black-box HTTP probing, DOM tree inspection via `BeautifulSoup`, computed CSS token validation, exact mathematical actuarial auditing, and cryptographic file inspection (%PDF, RFC-4180 CSV, RFC-8259 JSON).

---

## 2. 4-Tier Test Suite Architecture & Coverage Matrix

The suite in `tests/test_e2e_monochrome.py` comprises **35 automated test cases** structured across 4 rigorous tiers:

### Tier 1: Feature Coverage & DOM Contracts (19 Tests)
- `test_t1_01_dom_zero_image_purge`: Validates complete elimination of decorative stock .jpg/.png images from disk and DOM (REQ-MT-01).
- `test_t1_02_monochrome_palette_tokens`: Validates zinc/carbon design tokens, 1px hairline borders (`rgba(255, 255, 255, 0.08)`), and muted numeric delta colors (REQ-MT-02).
- `test_t1_03_tabular_figures_dual_typography`: Validates dual typography (`Inter` + `JetBrains Mono`) and CSS `font-feature-settings: "tnum" 1, "zero" 1` (REQ-MT-04).
- `test_t1_04_four_block_navigation_anchors`: Validates sequential 4-block architecture and navigation anchors (#overview, #performance, #simulator/#calculator, #security/#audit) (REQ-MT-05).
- `test_t1_05_audited_executive_kpis`: Validates certified quantitative metrics in DOM and `/api/investor/stats`: Sharpe 2.42, Sortino 3.10, Drawdown -6.4% (REQ-MT-06).
- `test_t1_06_live_streaming_ticker_tape`: Validates live ticker tape for BTC, ETH, SOL, NVDA, AAPL with 24/7 session indicators (REQ-MT-06).
- `test_t1_07_finbert_rl_telemetry`: Validates FinBERT sentiment + Bandit RL auto-evolution telemetry and interactive train-step endpoint (REQ-MT-06).
- `test_t1_08_vector_equity_chart`: Validates vector canvas financial chart and timeframe selector buttons (1M, 3M, 6M, 1Y, ALL) (REQ-MT-07).
- `test_t1_09_drawdown_lock_baseline`: Validates fiduciary drawdown lock indicator at -6.4% baseline (REQ-MT-07).
- `test_t1_10_actuarial_simulator_inputs`: Validates actuarial simulator sliders (`#slCap`, `#slTime`), mode toggles, and output containers (REQ-MT-08).
- `test_t1_11_multi_currency_switcher_controls`: Validates multi-currency switcher buttons (`#cUSDT`, `#cUSD`, `#cEUR`, `#cBTC`) and FX conversion rates (REQ-MT-08).
- `test_t1_12_fiduciary_fee_schedule`: Validates explicit fee structure disclosure: 0% Management Fee, 5% Hurdle Rate, 20% High-Water Mark (REQ-MT-08).
- `test_t1_13_non_custodial_api_connection`: Validates non-custodial API connection form and AES-256 submission endpoint (REQ-MT-09).
- `test_t1_14_2fa_protected_withdrawals`: Validates 2FA TOTP protected fund liquidation dispatch with ticket ID generation (REQ-MT-09).
- `test_t1_15_interactive_ledger_and_filter`: Validates interactive ledger table (`#tradeBody`), search bar (`#tradeSearch`), and asset filter pills (REQ-MT-09).
- `test_t1_16_pdf_audit_report_endpoint`: Validates GET `/api/investor/report-pdf` returns HTTP 200 OK and `%PDF` binary stream (REQ-MT-09).
- `test_t1_17_csv_ledger_export_endpoint`: Validates GET `/api/investor/report-csv` returns HTTP 200 OK and RFC-4180 CSV headers (REQ-MT-09).
- `test_t1_18_json_audit_export_endpoint`: Validates GET `/api/investor/report-json` returns HTTP 200 OK and valid JSON schema (REQ-MT-09).
- `test_t1_19_server_routes_and_spa_fallback`: Validates primary routes and universal SPA router fallback returning HTTP 200 OK (REQ-MT-10).

### Tier 2: Boundary Conditions & Corner Cases (9 Tests)
- `test_t2_01_btc_fractional_precision`: Verifies BTC fractional conversion formatting requiring 4 decimal places (`0.0304 BTC`) to prevent rounding truncation.
- `test_t2_02_rapid_currency_toggling_stability`: Verifies mathematical invariance and non-drift during rapid sequential currency cycles (USDT $\to$ EUR $\to$ BTC $\to$ USD $\to$ USDT).
- `test_t2_03_simulator_slider_boundaries`: Verifies actuarial simulator calculations at extreme boundaries ($1k min at 1 mo: +$52.00; $100k max at 36 mo: +$520,249.80).
- `test_t2_04_canvas_mouseleave_boundary`: Verifies chart mouseleave listener resets mouse coordinates and hides tooltip cleanly.
- `test_t2_05_drawdown_lock_alert_threshold`: Verifies drawdown threshold styling switching to red warning state when drawdown exceeds -5.0%.
- `test_t2_06_ledger_search_special_regex_characters`: Verifies search logic uses substring `.includes()` to prevent syntax errors on regex meta-characters (`[`, `]`, `*`, `+`, `?`, `\`).
- `test_t2_07_ledger_filter_zero_matches`: Verifies filtering by non-existent symbol (e.g. `XRP`) yields 0 rows gracefully without DOM error.
- `test_t2_08_withdrawal_amount_minimum_validation`: Verifies client/server rejection when liquidation amount is below $50 USD.
- `test_t2_09_api_connection_input_sanitization`: Verifies `.trim()` sanitization of API credentials before transmission and encryption.

### Tier 3: Cross-Feature Interactions & Stress (4 Tests)
- `test_t3_01_currency_switch_and_simulator_sync`: Verifies currency conversion and compounding simulator math synchronize across currencies (USD +$8,373.37 $\leftrightarrow$ EUR +€7,701.67).
- `test_t3_02_timeframe_selector_and_chart_recalibration`: Verifies all 6 timeframes (`1M`, `3M`, `6M`, `1Y`, `YTD`, `ALL`) contain synchronized series and maintain the -6.4% drawdown baseline.
- `test_t3_03_rl_evolution_step_and_ticker_coexistence`: Verifies concurrent RL evolution step and stats polling execute without race conditions or locks.
- `test_t3_04_combined_ledger_filter_and_search`: Verifies compound filtering intersecting active symbol button (`SOL`) with search query text (`Spot Momentum`).

### Tier 4: Real-World Institutional Scenarios (3 Tests)
- `test_t4_01_full_investor_allocation_journey`: Simulates complete investor onboarding journey (landing $\to$ KPI audit $\to$ API connection $\to$ PDF download).
- `test_t4_02_full_audit_export_inspection`: Deep inspection of PDF (%PDF bytes), CSV (RFC-4180 columns), and JSON (syndicate, telemetry, circuit breakers, trade hashes).
- `test_t4_03_github_pages_static_demo_parity`: Validates `docs/index.html` static distribution file, verifying client-side script engine and mandatory brand invariants.

---

## 3. Baseline Test Execution Results

When executed via `.\venv\Scripts\python.exe -m unittest tests/test_e2e_monochrome.py`:
- **Total Tests Executed**: 35
- **Passed**: 34 (97.1%)
- **Failures**: 1 (2.9%)
- **Execution Time**: 1.85 seconds

When executed via full discovery `.\venv\Scripts\python.exe -m unittest discover tests`:
- **Total Tests Executed**: 61 (26 unit tests + 35 E2E tests)
- **Passed**: 60 (98.4%)
- **Failures**: 1 (1.6%)
- **Execution Time**: 11.95 seconds

---

## 4. Defect Escalation Report for Milestone 1 Implementer

The single failing test is **`test_t1_01_dom_zero_image_purge`**, which accurately catches the in-progress state of Milestone 1:

- **Defect Identified**: 8 photographic `<img>` tags remain in `INSTITUTIONAL_PORTAL_HTML` within `bot/institutional_portal.py` (lines 1034, 1062, 1089, 1202, 1221, 1240, 1609, 1653).
- **Physical Disk State**: Physical `.jpg` files were already successfully purged from `static/images/` and `docs/static/images/`, and `bot/app_dashboard.py` was successfully updated.
- **Remediation Action Required for Worker Milestone 1**: Replace the 8 remaining `<img>` tags in `INSTITUTIONAL_PORTAL_HTML` with technical typography, monospace parameter blocks, or vector SVG badges as specified in `survey_report.md`.
- **Expected Outcome Upon Remediation**: 100% of the 35 E2E tests and 100% of the 61 total tests will pass cleanly.
