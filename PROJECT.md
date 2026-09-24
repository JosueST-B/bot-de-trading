# Project: Binance Square Autonomous Publisher & Growth Traffic Engine

## Architecture
- **Layer 1: Content Generation & Formatting Engine (`bot/growth_traffic_engine.py`, `bot/binance_square.py`)**:
  Generates 3 institutional content archetypes:
  1. Real-Time Quantitative Setup Alerts ($S_{composite} \ge 0.72$, Entry, TP1/2/3, SL, R:R $\ge 1:2.5$).
  2. Daily Macro & Market Reports (BTC summary, Top gainers, FinBERT sentiment, institutional flow).
  3. Audited Performance & Transparency Reports (Win Rate 78.5%, Profit Factor 2.65, -6.4% Drawdown lock, ledger verification).
  Enforces clean Markdown, zero unsupported HTML tags (`<b>`, `<code>`, `<pre>`), strict length bounds (< 2,000 chars), and dual-pillar conversion CTAs (Telegram VIP `@AdminVIPSignals` + `/subscribe`, Institutional Web Portal `https://josuest-b.github.io/bot-de-trading/`).
- **Layer 2: Resilient Transport, Rate Limiting & Audit Persistence (`bot/binance_square.py`, `bot/config.py`)**:
  Encapsulates OpenAPI interaction with `X-Square-OpenAPI-Key` validation, jittered exponential backoff retries on HTTP 429/500/timeouts, hybrid sliding window + priority cooldown anti-spam rate limiter, atomic rate-limit state persistence in SQLite `bot_state`, and comprehensive audit event telemetry in SQLite `events` table (`bot_events.sqlite3`).
- **Layer 3: Asynchronous Non-Blocking Execution & Live Loop Integration (`bot/main.py`, `bot/growth_traffic_engine.py`)**:
  Decouples publishing from the 24/7 live trading loop via a thread-safe `queue.Queue` producer-consumer model and background daemon thread (`BinanceSquareWorker`). Replaces synchronous 15s blocking HTTP calls in `bot/main.py` with sub-millisecond enqueueing, preserving real-time tick execution and order safety.
- **Layer 4: Verification, Adversarial Hardening & Forensic Audit Suite (`tests/test_binance_square_publisher.py`)**:
  29-method core test suite + 28 adversarial tests covering archetype rendering, character bounds, HTML hygiene, CTA presence, high-fidelity HTTP mocks (200, 400, 401, 429, 500, timeouts), rate limiting, graceful degradation, and SQLite logging, ensuring zero regressions on the 246 existing tests.

---

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Quantitative Setup Alert Generator | Renders setups when $S_{composite} \ge 0.72$ with Entry, TP1, TP2, TP3, SL, and R:R $\ge 1:2.5$ | M1 | ORIGINAL_REQUEST §R1.1 |
| 2 | Daily Macro & Market Report Generator | Renders BTC executive summary, Top Gainers ranking, FinBERT sentiment, and institutional thesis | M1 | ORIGINAL_REQUEST §R1.2 |
| 3 | Audited Performance Report Generator | Renders Win Rate, Profit Factor, -6.4% Drawdown lock, and public ledger audit invitation | M1 | ORIGINAL_REQUEST §R1.3 |
| 4 | Markdown Hygiene & HTML Stripper | Sanitizes text removing disallowed HTML tags while preserving mathematical inequalities (`< -6.4%`) | M1 | ORIGINAL_REQUEST §R2 |
| 5 | Character Bounds & Truncation Guard | Enforces strict character limit (< 2,000 chars, target < 1,950 chars) without severing CTAs | M1 | ORIGINAL_REQUEST §R2 |
| 6 | Institutional Conversion CTA Embedder | Injects `@AdminVIPSignals`, `/subscribe`, and Web Portal URL + strategic hashtags in every post | M1 | ORIGINAL_REQUEST §R2 |
| 7 | OpenAPI Request & Key Validation | Formats JSON payload `{"bodyTextOnly": text}` with headers `X-Square-OpenAPI-Key` & `clienttype` | M2 | ORIGINAL_REQUEST §R2, §R3 |
| 8 | Resilient Network Retries with Backoff | Retries on HTTP 429/500/502/timeouts with exponential backoff and jitter | M2 | ORIGINAL_REQUEST §AC4 |
| 9 | Graceful Degradation / Standby Mode | Safe dry-run / simulation mode when API key is missing or `BINANCE_SQUARE_ENABLED=false` | M2 | ORIGINAL_REQUEST §AC5 |
| 10 | Sliding Window & Priority Rate Limiter | Enforces hourly limits (max 5/h), cooldowns (15m standard, 3m for setups), and content deduplication | M2 | ORIGINAL_REQUEST §R3 |
| 11 | SQLite State & Telemetry Persistence | Logs published, failed, throttled, and simulated posts to `events` table in `bot_events.sqlite3` | M2 | ORIGINAL_REQUEST §R3 |
| 12 | Configuration Expansion | Adds `BINANCE_SQUARE_POST_INTERVAL_HOURS`, `RATE_LIMIT`, `MIN_COOLDOWN`, `MIN_SCORE`, `DRY_RUN` | M2 | Explorer 3 Report |
| 13 | Asynchronous Producer-Consumer Queue | Thread-safe `queue.Queue` eliminates 15s blocking calls in the live trading loop | M3 | ORIGINAL_REQUEST §R3 |
| 14 | Background Daemon Worker | `BinanceSquareWorker` drains queue and handles dual-cadence scheduling (periodic + immediate) | M3 | ORIGINAL_REQUEST §R3 |
| 15 | Live Loop Non-Blocking Signal Hook | Enqueues high-conviction signals ($S_{composite} \ge 0.72$) in `bot/main.py` in < 0.001 ms | M3 | ORIGINAL_REQUEST §R1.1, §R3 |
| 16 | E2E & Unit Verification Suite | 29 test cases in `tests/test_binance_square_publisher.py` with mock network and isolated SQLite | E2E | ORIGINAL_REQUEST §AC6 |
| 17 | Zero-Regression Verification Gate | Verifies 100% pass of existing 246 baseline tests + new tests; Reviewer, Challenger, and Audit | M4 | ORIGINAL_REQUEST §AC7 |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| E2E | E2E Testing Track | Implement comprehensive test suite in `tests/test_binance_square_publisher.py` covering archetypes, limits, CTAs, mocks, degradation, rate limiting, and SQLite | none | DONE |
| M1 | Dynamic Multi-Format Content Generator | Features 1, 2, 3, 4, 5, 6: `bot/growth_traffic_engine.py`, `bot/binance_square.py` (3 archetypes, variables, Markdown sanitization, length bounds, CTAs, hashtags) | none | DONE |
| M2 | Resilient Client, Rate Limiter & SQLite Telemetry | Features 7, 8, 9, 10, 11, 12: `bot/binance_square.py`, `bot/config.py`, `.env.example` (API key validation, retries, rate limiting, SQLite events & state) | M1 | DONE |
| M3 | Asynchronous Queue Worker & Live Loop Integration | Features 13, 14, 15: `bot/growth_traffic_engine.py`, `bot/main.py` (`queue.Queue` worker, dual-cadence scheduler, live loop non-blocking hook) | M2 | DONE |
| M4 | Full Verification, Review, Challenger & Forensic Audit | Feature 17: Pass all E2E tests, verify 0 regressions on 246 tests, Reviewer APPROVE, Challenger verified, Forensic Auditor CLEAN | E2E, M3 | DONE |

---

## Code Layout
- `bot/binance_square.py`:
  * `BinanceSquarePublisher`: Core client with OpenAPI payload formatting, key validation, retries, rate-limiting, and SQLite `events` logging.
  * `SquareRateLimiter`: Sliding window and cooldown limiter with SQLite `bot_state` persistence and content deduplication.
  * `sanitize_for_square()`: Markdown hygiene and HTML stripping utility enforcing `< 2,000` chars and preserving mathematical inequalities.
  * `BinanceSquareContentGenerator`: Renders Archetype 1 (Quantitative Setups), Archetype 2 (Daily Macro), and Archetype 3 (Audited Performance).
- `bot/growth_traffic_engine.py`:
  * `BinanceSquareWorker`: Daemon thread processing queued posts and managing scheduled cadences.
  * `enqueue_square_post()`: Non-blocking enqueueing function (< 0.001 ms).
  * `AutoTrafficPublisher`: Growth engine managing dual-channel traffic (Telegram + Binance Square).
- `bot/config.py`:
  * Extended `BotConfig` with `binance_square_post_interval_hours`, `binance_square_rate_limit_per_hour`, `binance_square_min_cooldown_minutes`, `binance_square_min_score`, `binance_square_dry_run`.
- `bot/main.py`:
  * Non-blocking signal enqueueing in `run_live_loop` and `_publish_live_event_to_square`.
  * Asynchronous dispatch in `run_auto_tune_cycle`.
- `tests/test_binance_square_publisher.py`:
  * Comprehensive core test suite containing 7 test classes and 29 methods.
- `tests/test_adversarial_binance_square_challenger_1.py`:
  * Adversarial stress testing for bounds, HTML stripping, rate limits (14 methods).
- `tests/test_challenger_binance_square.py`:
  * Empirical fault injection and concurrency testing (14 methods).
