# Project: Telegram Notification Integration Expansion

## Architecture
- **Configuration (`bot.config.BotConfig`)**: Responsible for reading environmental configuration (from `.env` and `os.environ`) and exposing settings. It will be expanded with four new boolean options: `TELEGRAM_NOTIFY_BUYS`, `TELEGRAM_NOTIFY_SELLS`, `TELEGRAM_NOTIFY_AUTOTUNE`, `TELEGRAM_NOTIFY_ERRORS`.
- **Telemetry (`bot.telemetry.TelegramNotifier` & `bot.telemetry.Telemetry`)**: Responsible for sending messages to the Telegram API. `TelegramNotifier` will be upgraded to support `parse_mode="HTML"`, escape dynamic inputs properly to prevent HTML parse errors, and filter messages based on the configured environment variables.
- **Main Loop (`bot.main` or integration points)**: Triggers alerts for buys, sells, daily/recalibration auto-tuning, and critical errors (with traceback blocks). Message contents will be enriched with institutional Binance Square-like style formatting.

## Code Layout
- `bot/config.py`: Configuration class definition and environment variable parsing.
- `bot/telemetry.py`: Telegram notifier definition, message builders (DailyReporter, etc.), and escaping utility.
- `bot/main.py`: Main process loop where purchases, sales, autotuning, and error handlings are orchestrated and alerts are dispatched.
- `tests/`: Directory containing E2E and unit tests.
- `tests/mock_telegram.py`: Mocking infrastructure for the Telegram API (since actual network access is blocked).

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | E2E Testing Track | Create `TEST_INFRA.md`, E2E test suite (Tiers 1-4 with mock server, 38+ cases), and publish `TEST_READY.md`. | None | DONE |
| 2 | Configuration & Telemetry | Implement config variables, escape utility, HTML notifier, and routing filters in `bot/config.py` and `bot/telemetry.py`. | M1 | DONE |
| 3 | Core Integration | Upgrade calls in `bot/main.py` to send HTML alerts (for buys/sells/tuning/errors) and format traceback blocks. | M2 | DONE |
| 4 | Test & Verify | Execute tests and fix issues until 100% of unit and E2E tests pass. | M3 | DONE |
| 5 | Adversarial Hardening | Add Tier 5 tests to find sanitization/escaping edge cases (like complex HTML-like sequences in news/errors). | M4 | DONE |
| 6 | Forensic Audit | Verify compliance and check integrity constraints using the Forensic Auditor. | M5 | DONE |

## Interface Contracts
### `bot.telemetry.TelegramNotifier`
- `escape_html(text: str) -> str`: Static/utility method to escape `<` to `&lt;`, `>` to `&gt;`, and `&` to `&amp;`.
- `send(text: str, category: str = "general") -> bool`: Upgraded message sender. Checks configuration for the given `category` (one of `"buys"`, `"sells"`, `"autotune"`, `"errors"`, `"general"`) before sending. If configuration disables that category, returns `False` immediately without sending.
- `parse_mode` is always set to `"HTML"`.

### `bot.config.BotConfig`
- `telegram_notify_buys: bool` (default: `True`)
- `telegram_notify_sells: bool` (default: `True`)
- `telegram_notify_autotune: bool` (default: `True`)
- `telegram_notify_errors: bool` (default: `True`)
