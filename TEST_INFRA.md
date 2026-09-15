# Test Infrastructure — Telegram Notification Integration

This document outlines the testing strategy, tools, mocking mechanisms, and test execution details for the expanded Telegram notifications.

## 1. Core Testing Philosophy
We employ automated end-to-end (E2E) and unit testing using Python's built-in `unittest` module. All tests are designed to run fully isolated, without external network dependencies, and without mutating any production state (databases, state stores, etc.).

## 2. Mocking Infrastructure
To satisfy the network isolation mandate and capture notification requests:
- **`requests.Session.post` Interception**: We patch `requests.Session.post` to intercept outgoing calls to `https://api.telegram.org/bot<TOKEN>/sendMessage`.
- **Request Parameter Capture**: For every outgoing alert, we capture:
  - `url`: Destination Telegram endpoint.
  - `token`: Extract bot token from the URL.
  - `chat_id`: Target Telegram chat identifier.
  - `text`: Message payload (including HTML formatting).
  - `parse_mode`: Confirmed to be `"HTML"`.
  - `timeout`: Confirmed execution timeout (15 seconds).
- **Mock Response**: Returns a mock `requests.Response` with HTTP status `200` and `{"ok": true}` to simulate a successful API transmission.

## 3. Database Isolation
Many real-world E2E scenarios interact with the SQLite event database (`bot_events.sqlite3`):
- **Temporary DB Instance**: During `setUp()`, each test class creates a isolated temporary SQLite database file using `tempfile.NamedTemporaryFile`.
- **Cleanup**: During `tearDown()`, the temporary database file is closed and unlinked from the system to ensure no residue is left behind.

## 4. Test Design & Tiers
The E2E test suite under `tests/run_tests.py` consists of 39 distinct test cases structured across four tiers:

### Tier 1: Feature Coverage (15 test cases)
Verifies core operations of the three features under test:
- **F1 (HTML Format)**: Formatting checks for buy, sell, error tracebacks, and autotune alerts.
- **F2 (HTML Escaping)**: Basic entity replacement checks for `<`, `>`, `&`.
- **F3 (Config Routing)**: Checking configuration variables `telegram_notify_buys/sells/autotune/errors` for routing.

### Tier 2: Boundary & Corner Cases (15 test cases)
Tests the limits and edge cases of the features:
- Extreme numbers, empty strings, very long texts.
- Nested or unclosed HTML tags, preventing double escaping.
- Environment variables case insensitivity (e.g. `true`, `FALSE`, `1`, `0`, `yes`, `no`) and invalid types.
- Disabling notifier taking precedence.

### Tier 3: Cross-Feature Combinations (3 test cases)
Tests the interaction between features:
- HTML formatting + config routing (errors formatted but blocked if routing disabled).
- Escaping + config routing (escaped reasons routed successfully).
- Autotune escaping + routing.

### Tier 4: Real-World Scenarios (6 test cases)
End-to-end integration flows:
- Exception handling in the continuous live loop (formats traceback using `<code>`).
- Daily reporter report generation and telemetry database inspection.
- News sentiment analyzer airdrop alerts parsing.
- Paper trading event dispatch (`_record_paper_event`).
- Live loop buy/sell execution flow.
- Manual CLI report generation.
