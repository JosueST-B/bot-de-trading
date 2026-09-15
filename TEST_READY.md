# Test Ready — Telegram Notification Integration Test Suite

The comprehensive E2E and unit test suite for the expanded Telegram notifications is fully operational and passing.

## Test Runner Command

To execute the test suite, run the following command from the project root directory:

```powershell
.\venv\Scripts\python.exe tests/run_tests.py
```

## Coverage Summary Table

| Tier | Category | Minimum Target | Implemented Cases | Status | Description |
|---|---|---|---|---|---|
| **Tier 1** | Feature Coverage | 15 | 15 | **PASS** | Validates basic buy, sell, autotune, and error formatted alerts, HTML escape functionality, and configuration flag routing. |
| **Tier 2** | Boundary & Corner Cases | 15 | 15 | **PASS** | Tests extreme values, long messages, empty parameters, unclosed tags, environment variable parsing case sensitivity, and invalid types. |
| **Tier 3** | Cross-Feature Combinations | 3 | 3 | **PASS** | Evaluates combination behaviors (e.g. HTML traceback formatting combined with disabled routing; sanitisation on buy/autotune alerts). |
| **Tier 4** | Real-World Scenarios | 5 | 6 | **PASS** | Emulates exceptions caught in the live trading loop, DailyReporter build sequence, news sentiment HTML airdrop extraction, and paper event logs. |
| **Total** | **All Tiers Combined** | **38** | **39** | **PASS** | **All 39 tests executed and passed without network calls or warnings.** |
