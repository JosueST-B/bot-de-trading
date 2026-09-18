# Project: Quantitative Trading Engine Hardening

## Architecture
- **Layer 1: Resilient Transport & Network Fiduciary (`bot/network.py`, `bot/binance_client.py`)**:
  Manages REST communications, mirror health rotation (`api.binance.com`, `api1`, `api2`, `api3`, `data-api.binance.vision`), jittered exponential backoff retry policy, HTTP 429 `Retry-After` parsing, and interception of Windows TCP 10054 resets and SSL errors.
- **Layer 2: Atomic State & Order Lifecycle (`bot/db.py`, `bot/main.py`)**:
  Manages SQLite database with Write-Ahead Logging (`WAL`), ACID transactional outbox, deterministic `client_order_id` idempotency keys, two-phase order commitment (`PENDING_SUBMIT` -> `FILLED`), anti-orphan fallback pipeline, and startup state reconciler.
- **Layer 3: Dynamic Risk Safeguards & Circuit Breakers (`bot/risk.py`, `bot/main.py`)**:
  Enforces fiduciary capital preservation: -6.4% absolute drawdown lock across consolidated equity, pre-trade order book depth slippage guard, post-fill slippage audit, and extreme volatility flash spike rejection ($ATR > 3\times \text{baseline}$).
- **Layer 4: Engine Lifecycle & Concurrency (`bot/main.py`, `bot/telemetry.py`)**:
  Guarded lease mutual exclusion, per-symbol execution isolation, bounded LRU kline caching, scoped DB session management, and graceful SIGINT/SIGTERM teardown.
- **Layer 5: Opaque-Box E2E Hardening & Verification Suite (`tests/`)**:
  Network fault injection, concurrency idempotency, crash recovery, and circuit breaker stress test suites running alongside existing 117 unit/integration tests.

---

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Guarded Lease Acquisition & Heartbeat | Mutex lock in SQLite with owner UUID and TTL refresh; prevents multi-instance execution conflicts without theft | M4 | bot/main.py:1424, R1 |
| 2 | Graceful Signal Trapping & Teardown | Intercepts SIGINT/SIGTERM, cancels tasks, releases lease, and closes DB connections | M4 | bot/main.py:1472, R1 |
| 3 | Per-Symbol Cycle Isolation | Sequential asset loop isolates errors per symbol; prevents single-symbol timeout from breaking cycle | M4 | bot/main.py:1434, R1 |
| 4 | Bounded LRU Kline Cache | LRU cache with TTL eviction for market data; prevents unbounded memory leaks | M1 | bot/binance_client.py:43, R1 |
| 5 | Scoped SQLite Session Context | Context-managed sessions with WAL mode and busy timeout (30s); eliminates unclosed connection leaks | M2 | bot/db.py:110, R1 |
| 6 | Jittered Exponential Backoff Retry Policy | Calculates delay $t = \min(t_{max}, t_{base} \cdot 2^{attempt}) \cdot \text{uniform}(0.8, 1.2)$ on network faults | M1 | ORIGINAL_REQUEST §R2 |
| 7 | TCP 10054 & Transport Reset Interceptor | Specifically catches `ConnectionResetError` (WinError 10054) and reconnects underlying socket session | M1 | ORIGINAL_REQUEST §R2 |
| 8 | SSL Handshake & Certificate Error Handler | Traps `requests.exceptions.SSLError`, rebuilds session adapter pool, and fails over to mirrors | M1 | ORIGINAL_REQUEST §R2 |
| 9 | HTTP 429 & Rate-Limit Backoff Engine | Parses `Retry-After` header on 429; pauses execution safely to prevent IP ban (HTTP 418) | M1 | ORIGINAL_REQUEST §R2 |
| 10 | Multi-Mirror Dynamic Failover Router | Rotates between `api.binance.com`, `data-api.binance.vision`, `api1`, `api2`, `api3` based on endpoint health scores | M1 | ORIGINAL_REQUEST §R2 |
| 11 | Resilient Signed Call Wrapper | Extends `_call_signed` with mirror fallback, backoff retries, and clock re-sync on -1021 | M1 | bot/binance_client.py:200, R2 |
| 12 | Kline Cache Fallback Bug Fix | Fixes `RuntimeError` vs `RequestException` bug in `_get_klines_page` so cached candles serve when offline | M1 | bot/binance_client.py:112, R2 |
| 13 | Deterministic Idempotency Key Generation | Generates unique client order ID (`AETH_{symbol}_{timestamp_ms}_{action}`) passed as `newClientOrderId` | M2 | ORIGINAL_REQUEST §R3 |
| 14 | Two-Phase Order Commitment in SQLite | Records order in SQLite as `PENDING_SUBMIT` before dispatch; updates to `FILLED` or `REJECTED` upon response | M2 | ORIGINAL_REQUEST §R3 |
| 15 | Anti-Orphan Protective Fallback Pipeline | Immediate retry and limit sell fallback if Stop-Loss fails after Buy fill; marks `UNHEDGED_CRITICAL` in DB | M2 | bot/main.py:826, R3 |
| 16 | Startup Order & State Reconciler | Reconciles open/pending orders on reboot via exchange API; eliminates phantom positions and attaches SL/TP | M2 | ORIGINAL_REQUEST §R3 |
| 17 | VWAP Multi-Fill Calculation Fix | Replaces `fills[0]` with volume-weighted average price across all execution fill records | M2 | bot/main.py:794, R3 |
| 18 | Pre-Trade Slippage Depth Inspection | Compares best ask price on order book against last candle close; rejects order if spread > `cfg.slippage` | M3 | ORIGINAL_REQUEST §R4 |
| 19 | Post-Fill Slippage Auditor | Validates effective fill price against expected price; logs violation and pauses trading if anomalous | M3 | bot/main.py:806, R4 |
| 20 | Extreme Volatility & Flash Spike Rejection | Computes $ATR_{14} / \text{close}$; rejects buy orders if market volatility exceeds $3\times$ baseline | M3 | ORIGINAL_REQUEST §R4 |
| 21 | Fiduciary Drawdown Lock (-6.4%) | Absolute equity drawdown lock at -6.4%; cancels non-protective orders, sets `LOCKED_DEFENSIVE` | M3 | ORIGINAL_REQUEST §R4 |
| 22 | Circuit Breaker Test Bypass Purge & DB State | Removes `is_testing` bypass; persists circuit breaker state in SQLite `bot_state` across restarts | M3 | bot/risk.py:48, R4 |
| 23 | Network Fault Injection Harness | Mocks network drops, TCP 10054, timeouts, SSL errors, HTTP 429/500/502 to verify loop resilience | E2E | ORIGINAL_REQUEST §Criteria |
| 24 | Concurrency & Idempotency Harness | Dispatches duplicate execution requests; verifies single buy and exact balance | E2E | ORIGINAL_REQUEST §Criteria |
| 25 | Crash & Restart Reconciliation Harness | Simulates process kill at buy fill; reboots engine and verifies automatic reconciliation | E2E | ORIGINAL_REQUEST §Criteria |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| E2E | E2E Hardening Test Track | Test infrastructure, network fault injection, concurrency idempotency, crash recovery, and circuit breaker stress tests (`tests/test_engine_hardening.py`) | none | DONE |
| M1 | Resilient Network Layer & Mirror Failover | Features 4, 6, 7, 8, 9, 10, 11, 12: `bot/network.py`, `bot/binance_client.py` (jittered backoff, 429 handling, mirror router, TCP 10054 & SSL interceptor, kline cache fix) | none | DONE |
| M2 | Atomic State Reconciliation & Anti-Orphan | Features 5, 13, 14, 15, 16, 17: `bot/db.py`, `bot/main.py` (idempotency keys, WAL mode, 2PC order commitment, anti-orphan fallback, reboot reconciler, VWAP fix) | M1 | DONE |
| M3 | Dynamic Circuit Breakers & Slippage Safeguards | Features 18, 19, 20, 21, 22: `bot/risk.py`, `bot/main.py`, `bot/config.py` (pre-trade slippage guard, ATR spike rejection, -6.4% fiduciary lock, test bypass purge, DB state persistence) | M2 | DONE |
| M4 | Engine Lifecycle & Concurrency Hardening | Features 1, 2, 3: `bot/main.py`, `bot/telemetry.py` (lease theft fix, per-symbol cycle isolation, graceful SIGINT/SIGTERM teardown, connection leak cleanup) | M3 | DONE |
| M5 | 100% E2E and Stress Test Verification | Pass 100% of existing 117 tests + all new hardening tests; Reviewer, Challenger, and Forensic Auditor verification | E2E, M4 | IN_PROGRESS |

---

## Code Layout
- `bot/network.py`: New network resilience module containing `FiduciaryRetryPolicy`, `MirrorManager`, and network exception adapters.
- `bot/binance_client.py`: Hardened `BinanceDataClient` and `BinanceExecutionClient` integrating `bot/network.py`, supporting mirror failovers, jittered retries, `newClientOrderId`, and clock sync.
- `bot/db.py`: SQLite engine with WAL mode, busy timeout, connection pooling, and extended `DBPosition` / `DBOrder` models (`client_order_id`, `state`, `error_details`).
- `bot/risk.py`: Hardened `RiskManager` with real -6.4% fiduciary drawdown lock, volatility spike rejection, pre-trade slippage validation, and test bypass removal.
- `bot/main.py`: `LiveTrader` and `run_live_loop` with 2PC order commitment, startup state reconciliation, anti-orphan fallback, protected lease acquisition, and graceful teardown.
- `tests/test_engine_hardening.py`: Comprehensive test suite containing network fault injection, idempotency, reboot recovery, and circuit breaker stress tests.

---

## Interface Contracts

### Network Fiduciary Layer (`bot/network.py` ↔ `bot/binance_client.py`)
```python
class FiduciaryRetryPolicy:
    max_retries: int = 4
    base_delay: float = 0.5
    max_delay: float = 10.0
    jitter_range: tuple[float, float] = (0.8, 1.2)
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)

    def calculate_delay(self, attempt: int, retry_after: float | None = None) -> float: ...
    def execute(self, callable_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any: ...

class MirrorManager:
    mirrors: list[str]  # ["https://api.binance.com", "https://api1.binance.com", "https://api2.binance.com", "https://api3.binance.com", "https://data-api.binance.vision"]
    def get_active_mirror(self) -> str: ...
    def mark_degraded(self, mirror: str, cooldown: float = 300.0) -> None: ...
    def get_request_url(self, path: str) -> str: ...
```

### Order Lifecycle & State Atomicity (`bot/db.py` ↔ `bot/main.py`)
```python
class OrderState(str, Enum):
    PENDING_SUBMIT = "PENDING_SUBMIT"
    FILLED = "FILLED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

# Deterministic idempotency key:
def generate_client_order_id(symbol: str, action: str, timestamp_ms: int | None = None) -> str:
    # Format: AETH_{symbol}_{timestamp_ms}_{action[:4]} (len <= 36)
```

### Dynamic Safeguards & Circuit Breakers (`bot/risk.py` ↔ `bot/main.py`)
```python
class CircuitBreakerStatus(str, Enum):
    NORMAL = "NORMAL"
    DEFENSIVE = "DEFENSIVE"
    LOCKED = "LOCKED"

class RiskManager:
    FIDUCIARY_DRAWDOWN_LIMIT: float = -0.064  # -6.4%
    MAX_VOLATILITY_RATIO: float = 3.0         # 3x ATR baseline

    def check_global_circuit_breaker(self, current_equity: float, start_equity: float) -> tuple[bool, str]: ...
    def validate_pre_trade_slippage(self, order_book: dict, expected_price: float, max_slippage: float) -> tuple[bool, float, str]: ...
    def validate_volatility_regime(self, klines_df: pd.DataFrame) -> tuple[bool, float, str]: ...
```
