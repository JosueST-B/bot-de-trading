# TEST INFRASTRUCTURE & VERIFICATION SPECIFICATION
# Quantitative Trading Engine Hardening (R1, R2, R3, R4)

- **Platform**: Aethelgard Quantitative Asset Management — Autonomous Trading Engine
- **Test Architecture**: 4-Tier Automated Opaque-Box E2E Hardening & Stress Verification Suite
- **Authoritative Specifications**: `ORIGINAL_REQUEST.md` (§ Follow-up 2026-09-17T18:29:25Z), `PROJECT.md`
- **Primary Test Runner**: `.\venv\Scripts\python.exe -m unittest tests.test_engine_hardening`
- **Full Discovery Command**: `.\venv\Scripts\python.exe -m unittest discover tests`
- **Baseline Test Count**: 117 tests passing (zero regressions invariant)
- **Integrity Level**: Full Opaque-Box Hardening / Fault Injection / Concurrency & Crash Recovery

---

## 1. Core Testing Philosophy & Architecture

The Quantitative Trading Engine Hardening milestone fortifies the live algorithmic trading core against unpredictable production environments: abrupt OS socket drops (Windows TCP 10054 `ConnectionResetError`), TLS/SSL handshake failures, exchange rate-limiting (HTTP 429 `Retry-After`), server gateway errors (HTTP 500/502), state desynchronization, orphan positions, extreme slippage, and volatility spikes.

The verification architecture is structured around four foundational tenets:

1. **Opaque-Box Fault Injection & Deterministic Oracles**:
   Tests exercise the engine's resilience interfaces (`FiduciaryRetryPolicy`, `MirrorManager`, `RiskManager`, `OrderState`, `LiveTrader`) via simulated external conditions (socket resets, mock network timeouts, dropped HTTP responses, clock drift). Every expected result is derived directly from mathematical closed forms and explicit interface contracts in `PROJECT.md`.
2. **Progressive Testability & Contract-Driven Verification**:
   Tests are specified against the authoritative interface contracts defined in `PROJECT.md`. Each test incorporates specification reference harness oracles, enabling rigorous verification of logic, bounds, and state machines across all milestones while immediately binding to production implementations as each milestone lands.
3. **Zero Regression Guarantee on Existing Test Baseline**:
   The engine's existing 117 unit/integration tests must continue to pass with 100% success. The new hardening suite runs seamlessly alongside existing tests within `unittest discover tests`.
4. **Idempotency, Atomicity & Fiduciary Capital Preservation**:
   All state transitions, order tracking keys (`newClientOrderId`), two-phase commits, and risk circuit breakers (-6.4% fiduciary drawdown lock, pre-trade slippage guard, $3\times ATR$ flash crash rejection) are verified under adversarial and boundary conditions.

```
+-------------------------------------------------------------------------------+
|                    TIER 4: REAL-WORLD ADVERSARIAL SCENARIOS                   |
|  - T4.1 Simulated Live Loop Cycle Surviving Transient Network Drops (No Crash)|
|  - T4.2 Post-Buy Crash & Startup Reconciler Recovery (No Orphan Positions)    |
|  - T4.3 Sequential Multi-Symbol Cycle Isolation Under Individual Asset Failure|
+-------------------------------------------------------------------------------+
                                       ^
+-------------------------------------------------------------------------------+
|                 TIER 3: CROSS-FEATURE INTERACTIONS & PIPELINES                |
|  - T3.1 Network Timeout During 2PC Order Submission & Idempotent Query Recovery|
|  - T3.2 Dynamic Mirror Failover During Live Market Buy Execution              |
|  - T3.3 Timestamp Clock Desync (-1021 recvWindow) Auto-Resync & Order Retry   |
|  - T3.4 Global Circuit Breaker Lock Freezing All Subsequent Buy Dispatches    |
+-------------------------------------------------------------------------------+
                                       ^
+-------------------------------------------------------------------------------+
|                  TIER 2: BOUNDARY CONDITIONS & CORNER CASES                   |
|  - T2.1 Windows Socket Reset Interception (TCP 10054 ConnectionResetError)   |
|  - T2.2 SSL Handshake & Certificate Verification Exception Re-Connection      |
|  - T2.3 HTTP 429 Rate Limit Parsing with Dynamic Retry-After Sleep Duration   |
|  - T2.4 Full Mirror Degradation & Exhaustion Safety Escalation Exception      |
|  - T2.5 Non-Positive & Catastrophic Negative Equity Handling (ZeroDivision)   |
|  - T2.6 Zero-Fill Order Payload Handling & Multi-Fill VWAP Edge Calculation   |
+-------------------------------------------------------------------------------+
                                       ^
+-------------------------------------------------------------------------------+
|                    TIER 1: CORE FEATURE & CONTRACT COVERAGE                   |
|  - T1.1 FiduciaryRetryPolicy Exponential Backoff Formula & Decorrelated Jitter|
|  - T1.2 MirrorManager Endpoint Health Scoring, Degradation & Auto-Rotation    |
|  - T1.3 Deterministic Client Order ID Generation (AETH_{sym}_{ts}_{act} <=36) |
|  - T1.4 OrderState State Machine & Disallowed State Transition Enforcement    |
|  - T1.5 Pre-Trade Order Book Depth Inspection & Slippage Limit Validation     |
|  - T1.6 Fiduciary Drawdown Hard Lock at Exactly -6.4% Equity Contraction      |
|  - T1.7 Extreme Volatility Flash Spike Rejection (ATR > 3x Historical Baseline)|
+-------------------------------------------------------------------------------+
```

---

## 2. Feature Inventory Coverage Matrix (Features 1–25)

| # | Feature Name | Tier | Test Method in `test_engine_hardening.py` | Authoritative Spec Source |
|---|---|---|---|---|
| 1 | Guarded Lease Acquisition & Heartbeat | Tier 4 | `test_t4_03_per_symbol_cycle_isolation_and_lease` | `PROJECT.md` Feature 1, R1 |
| 2 | Graceful Signal Trapping & Teardown | Tier 4 | `test_t4_01_simulated_live_loop_network_drop_resilience` | `PROJECT.md` Feature 2, R1 |
| 3 | Per-Symbol Cycle Isolation | Tier 4 | `test_t4_03_per_symbol_cycle_isolation_and_lease` | `PROJECT.md` Feature 3, R1 |
| 4 | Bounded LRU Kline Cache | Tier 2 | `test_t2_04_mirror_exhaustion_and_cache_fallback` | `PROJECT.md` Feature 4, R1 |
| 5 | Scoped SQLite Session Context | Tier 3 | `test_t3_01_network_timeout_during_2pc_order_submit` | `PROJECT.md` Feature 5, R1 |
| 6 | Jittered Exponential Backoff Policy | Tier 1 | `test_t1_01_fiduciary_retry_policy_delay_and_jitter` | `PROJECT.md` Feature 6, R2 |
| 7 | TCP 10054 Transport Reset Interceptor | Tier 2 | `test_t2_01_windows_tcp_10054_connection_reset_retry` | `PROJECT.md` Feature 7, R2 |
| 8 | SSL Handshake & Certificate Error Handler | Tier 2 | `test_t2_02_ssl_error_retry_and_adapter_recovery` | `PROJECT.md` Feature 8, R2 |
| 9 | HTTP 429 & Rate-Limit Backoff Engine | Tier 2 | `test_t2_03_http_429_retry_after_sleep` | `PROJECT.md` Feature 9, R2 |
| 10 | Multi-Mirror Dynamic Failover Router | Tier 1 & 3 | `test_t1_02_mirror_manager_health_and_rotation`, `test_t3_02_mirror_failover_during_market_buy` | `PROJECT.md` Feature 10, R2 |
| 11 | Resilient Signed Call Wrapper | Tier 3 | `test_t3_03_clock_desync_1021_retry` | `PROJECT.md` Feature 11, R2 |
| 12 | Kline Cache Fallback Recovery | Tier 2 | `test_t2_04_mirror_exhaustion_and_cache_fallback` | `PROJECT.md` Feature 12, R2 |
| 13 | Deterministic Idempotency Key Generation | Tier 1 | `test_t1_03_client_order_id_generation` | `PROJECT.md` Feature 13, R3 |
| 14 | Two-Phase Order Commitment in SQLite | Tier 1 & 3 | `test_t1_04_order_state_transitions`, `test_t3_01_network_timeout_during_2pc_order_submit` | `PROJECT.md` Feature 14, R3 |
| 15 | Anti-Orphan Protective Fallback Pipeline | Tier 4 | `test_t4_02_crash_and_reboot_recovery` | `PROJECT.md` Feature 15, R3 |
| 16 | Startup Order & State Reconciler | Tier 4 | `test_t4_02_crash_and_reboot_recovery` | `PROJECT.md` Feature 16, R3 |
| 17 | VWAP Multi-Fill Calculation Fix | Tier 2 | `test_t2_06_zero_fill_and_vwap_multi_fill_order` | `PROJECT.md` Feature 17, R3 |
| 18 | Pre-Trade Slippage Depth Inspection | Tier 1 | `test_t1_05_pre_trade_slippage_validation` | `PROJECT.md` Feature 18, R4 |
| 19 | Post-Fill Slippage Auditor | Tier 1 | `test_t1_05_pre_trade_slippage_validation` | `PROJECT.md` Feature 19, R4 |
| 20 | Extreme Volatility & Flash Spike Rejection | Tier 1 | `test_t1_07_atr_volatility_spike_rejection` | `PROJECT.md` Feature 20, R4 |
| 21 | Fiduciary Drawdown Lock (-6.4%) | Tier 1 & 3 | `test_t1_06_fiduciary_drawdown_lock`, `test_t3_04_circuit_breaker_freezes_buys` | `PROJECT.md` Feature 21, R4 |
| 22 | Circuit Breaker Test Bypass Purge & DB State | Tier 1 & 3 | `test_t1_06_fiduciary_drawdown_lock`, `test_t3_04_circuit_breaker_freezes_buys` | `PROJECT.md` Feature 22, R4 |
| 23 | Network Fault Injection Harness | Tier 2 & 4 | `test_t2_01_windows_tcp_10054_connection_reset_retry`, `test_t4_01_simulated_live_loop_network_drop_resilience` | `PROJECT.md` Feature 23 |
| 24 | Concurrency & Idempotency Harness | Tier 1 & 3 | `test_t1_03_client_order_id_generation`, `test_t3_01_network_timeout_during_2pc_order_submit` | `PROJECT.md` Feature 24 |
| 25 | Crash & Restart Reconciliation Harness | Tier 4 | `test_t4_02_crash_and_reboot_recovery` | `PROJECT.md` Feature 25 |

---

## 3. Test Derivation Methodology & Authoritative Oracles

### 3.1 Mathematical Oracles

#### A. Jittered Exponential Backoff Delay
$$t = \min(t_{\max}, t_{\text{base}} \cdot 2^{\text{attempt}}) \cdot U(0.8, 1.2)$$
- $t_{\text{base}} = 0.5\text{ s}$, $t_{\max} = 10.0\text{ s}$, jitter $\in [0.8, 1.2]$.
- Attempt 0: $0.5 \cdot 2^0 = 0.5\text{ s} \implies t \in [0.40, 0.60]\text{ s}$.
- Attempt 1: $0.5 \cdot 2^1 = 1.0\text{ s} \implies t \in [0.80, 1.20]\text{ s}$.
- Attempt 2: $0.5 \cdot 2^2 = 2.0\text{ s} \implies t \in [1.60, 2.40]\text{ s}$.
- Attempt 3: $0.5 \cdot 2^3 = 4.0\text{ s} \implies t \in [3.20, 4.80]\text{ s}$.
- Attempt 4: $0.5 \cdot 2^4 = 8.0\text{ s} \implies t \in [6.40, 9.60]\text{ s}$.
- Attempt 5+: $\min(10.0, 16.0) = 10.0\text{ s} \implies t \in [8.00, 12.00]\text{ s}$.

#### B. Fiduciary Drawdown Hard Lock Oracle
$$\text{Drawdown} = \frac{\text{Current Equity} - \text{Start Equity}}{\text{Start Equity}}$$
- Boundary threshold: $\le -0.064$ ($-6.4\%$).
- If $E_{\text{start}} = \$10,000$, threshold trigger occurs at $E_{\text{current}} = \$9,360.00$.
- For $E_{\text{current}} \le \$9,360.00$, the circuit breaker transitions to `CircuitBreakerStatus.LOCKED` (or `LOCKED_DEFENSIVE`), cancelling non-protective open orders and permanently disallowing new buy entries.

#### C. Pre-Trade Slippage Depth Inspection Oracle
$$\text{Projected Slippage} = \frac{\text{Effective Ask Price} - \text{Expected Price}}{\text{Expected Price}}$$
- If $\text{Projected Slippage} > \text{max\_allowed\_slippage}$ (e.g. $0.05\% = 0.0005$), the order is rejected prior to dispatch with `projected_slippage_exceeded`.

#### D. Volatility Flash Spike Rejection Oracle
$$\text{Volatility Ratio} = \frac{ATR_{\text{current}}}{ATR_{\text{baseline}}}$$
- If $\text{Volatility Ratio} > 3.0$ ($3\times$ expansion), new entries are rejected with `extreme_volatility_rejected`.

#### E. Volume-Weighted Average Price (VWAP) Multi-Fill Oracle
$$\text{VWAP} = \frac{\sum_{i=1}^{N} (p_i \cdot q_i)}{\sum_{i=1}^{N} q_i}$$
- An order filled in slices ($q_1=0.1$ at $p_1=\$60,000$; $q_2=0.4$ at $p_2=\$60,500$) yields:
  $$\text{VWAP} = \frac{(0.1 \times 60000) + (0.4 \times 60500)}{0.1 + 0.4} = \frac{6000 + 24200}{0.5} = \frac{30200}{0.5} = \$60,400.00$$
- The erroneous `fills[0]` approach would yield $\$60,000.00$ (a $\$400.00$ accounting error).

---

## 4. Test Execution & Quality Gates

### 4.1 Test Execution Commands
- **Run Hardening Test Suite**:
  ```powershell
  .\venv\Scripts\python.exe -m unittest tests.test_engine_hardening
  ```
- **Run Full Discovery Suite**:
  ```powershell
  .\venv\Scripts\python.exe -m unittest discover tests
  ```

### 4.2 Quality Gates
1. **Zero Failures, Zero Errors**: Both commands exit with status code `0`.
2. **Zero Regressions**: Total test count must equal $117 + N_{\text{hardening}}$, with 100% of previous 117 tests passing.
3. **Execution Runtime**: `tests.test_engine_hardening` executes in $< 5$ seconds using non-blocking mocked sleeps.
4. **Clean Resource Teardown**: Sockets, mock HTTP adapters, and SQLite temporary databases are closed cleanly without unhandled file lock exceptions.
