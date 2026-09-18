from __future__ import annotations

import atexit
import json
import logging
import sqlite3
import weakref
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import text

from bot.config import BotConfig
from bot.db import create_db_engine, get_engine


_ACTIVE_SQLITE_CONNS: weakref.WeakSet[sqlite3.Connection] = weakref.WeakSet()
_orig_sqlite_connect = sqlite3.connect


def _safe_sqlite_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
    conn = _orig_sqlite_connect(*args, **kwargs)
    try:
        _ACTIVE_SQLITE_CONNS.add(conn)
    except Exception:
        pass
    return conn


sqlite3.connect = _safe_sqlite_connect


def _cleanup_db_resources() -> None:
    for conn in list(_ACTIVE_SQLITE_CONNS):
        try:
            conn.close()
        except Exception:
            pass
    _ACTIVE_SQLITE_CONNS.clear()
    try:
        from bot.db import _ENGINES
        for eng in list(_ENGINES.values()):
            try:
                eng.dispose()
            except Exception:
                pass
        _ENGINES.clear()
    except Exception:
        pass


atexit.register(_cleanup_db_resources)


def setup_logging(cfg: BotConfig) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if root.handlers:
        return

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = logging.FileHandler(cfg.log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    root.addHandler(file_handler)
    root.addHandler(stream_handler)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return json.dumps(asdict(value), default=_json_default)
    return str(value)


class EventStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.engine = get_engine(db_path)
        self.is_sqlite = self.engine.dialect.name == "sqlite"
        self._init_db()

    def close(self) -> None:
        if hasattr(self, "engine") and self.engine is not None:
            try:
                self.engine.dispose()
            except Exception:
                pass

    def __enter__(self) -> EventStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _init_db(self) -> None:
        with self.engine.begin() as conn:
            if self.is_sqlite:
                conn.execute(text(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        event TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                ))
            else:
                conn.execute(text(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        id SERIAL PRIMARY KEY,
                        ts VARCHAR(50) NOT NULL,
                        mode VARCHAR(20) NOT NULL,
                        symbol VARCHAR(20) NOT NULL,
                        event VARCHAR(50) NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                ))
            
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_events_event ON events(event)"))
            
            if self.is_sqlite:
                conn.execute(text(
                    """
                    CREATE TABLE IF NOT EXISTS bot_state (
                        key TEXT PRIMARY KEY,
                        updated_ts TEXT NOT NULL,
                        value_json TEXT NOT NULL
                    )
                    """
                ))
            else:
                conn.execute(text(
                    """
                    CREATE TABLE IF NOT EXISTS bot_state (
                        key VARCHAR(100) PRIMARY KEY,
                        updated_ts VARCHAR(50) NOT NULL,
                        value_json TEXT NOT NULL
                    )
                    """
                ))

    def record(self, mode: str, symbol: str, event: str, payload: dict[str, Any]) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload, default=_json_default, ensure_ascii=False)
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO events (ts, mode, symbol, event, payload_json)
                VALUES (:ts, :mode, :symbol, :event, :payload_json)
                """),
                {"ts": ts, "mode": mode, "symbol": symbol, "event": event, "payload_json": payload_json}
            )

    def set_state(self, key: str, value: dict[str, Any]) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        value_json = json.dumps(value, default=_json_default, ensure_ascii=False)
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO bot_state (key, updated_ts, value_json)
                VALUES (:key, :updated_ts, :value_json)
                ON CONFLICT(key) DO UPDATE SET
                    updated_ts = EXCLUDED.updated_ts,
                    value_json = EXCLUDED.value_json
                """),
                {"key": key, "updated_ts": ts, "value_json": value_json}
            )

    def get_state(self, key: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("""
                SELECT value_json
                FROM bot_state
                WHERE key = :key
                """),
                {"key": key}
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def delete_state(self, key: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM bot_state WHERE key = :key"), {"key": key})

    def acquire_lease(self, key: str, owner: str, ttl_seconds: int) -> bool:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=max(1, ttl_seconds))
        owner_payload = json.dumps({"owner": owner}, ensure_ascii=False)
        with self.engine.begin() as conn:
            current = conn.execute(
                text("SELECT updated_ts, value_json FROM bot_state WHERE key = :key"),
                {"key": key},
            ).fetchone()
            if current is None:
                conn.execute(
                    text("INSERT INTO bot_state (key, updated_ts, value_json) VALUES (:key, :updated_ts, :value_json)"),
                    {"key": key, "updated_ts": now.isoformat(), "value_json": owner_payload},
                )
                return True

            curr_ts_str, curr_val_str = current
            try:
                curr_dt = datetime.fromisoformat(curr_ts_str)
                if curr_dt.tzinfo is None:
                    curr_dt = curr_dt.replace(tzinfo=timezone.utc)
            except Exception:
                curr_dt = datetime.min.replace(tzinfo=timezone.utc)

            curr_owner = None
            try:
                curr_val = json.loads(curr_val_str)
                if isinstance(curr_val, dict):
                    curr_owner = curr_val.get("owner")
            except Exception:
                pass

            is_expired = curr_dt < cutoff
            is_same_owner = (curr_owner == owner)

            if is_expired or is_same_owner:
                conn.execute(
                    text("UPDATE bot_state SET updated_ts = :updated_ts, value_json = :value_json WHERE key = :key"),
                    {"key": key, "updated_ts": now.isoformat(), "value_json": owner_payload},
                )
                return True
            return False

    def refresh_lease(self, key: str, owner: str) -> bool:
        now = datetime.now(timezone.utc)
        owner_payload = json.dumps({"owner": owner}, ensure_ascii=False)
        with self.engine.begin() as conn:
            current = conn.execute(
                text("SELECT updated_ts, value_json FROM bot_state WHERE key = :key"),
                {"key": key},
            ).fetchone()
            if current is None:
                return False
            _, curr_val_str = current
            curr_owner = None
            try:
                curr_val = json.loads(curr_val_str)
                if isinstance(curr_val, dict):
                    curr_owner = curr_val.get("owner")
            except Exception:
                pass
            if curr_owner != owner:
                return False
            conn.execute(
                text("UPDATE bot_state SET updated_ts = :updated_ts, value_json = :value_json WHERE key = :key"),
                {"key": key, "updated_ts": now.isoformat(), "value_json": owner_payload},
            )
            return True

    def release_lease(self, key: str, owner: str) -> None:
        with self.engine.begin() as conn:
            current = conn.execute(
                text("SELECT value_json FROM bot_state WHERE key = :key"),
                {"key": key},
            ).fetchone()
            if current is None:
                return
            curr_owner = None
            try:
                curr_val = json.loads(current[0])
                if isinstance(curr_val, dict):
                    curr_owner = curr_val.get("owner")
            except Exception:
                pass
            if curr_owner == owner:
                conn.execute(text("DELETE FROM bot_state WHERE key = :key"), {"key": key})

    def states(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                SELECT key, updated_ts, value_json
                FROM bot_state
                ORDER BY updated_ts DESC
                """)
            ).fetchall()
        return [
            {"key": key, "updated_ts": updated_ts, "value": json.loads(value_json)}
            for key, updated_ts, value_json in rows
        ]

    def latest_paper_state(self) -> dict[str, Any] | None:
        states = self.states()
        for state in states:
            if str(state.get("key", "")).startswith("paper:"):
                return state
        return None

    def events_for_day(self, target_day: date) -> list[dict[str, Any]]:
        start = datetime.combine(target_day, datetime.min.time(), tzinfo=timezone.utc)
        end = datetime.combine(target_day, datetime.max.time(), tzinfo=timezone.utc)
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                SELECT ts, mode, symbol, event, payload_json
                FROM events
                WHERE ts BETWEEN :start AND :end
                ORDER BY ts ASC
                """),
                {"start": start.isoformat(), "end": end.isoformat()}
            ).fetchall()

        out = []
        for ts, mode, symbol, event, payload_json in rows:
            out.append(
                {
                    "ts": ts,
                    "mode": mode,
                    "symbol": symbol,
                    "event": event,
                    "payload": json.loads(payload_json),
                }
            )
        return out

    def recent_events(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(f"""
                SELECT ts, mode, symbol, event, payload_json
                FROM events
                ORDER BY ts DESC
                LIMIT {safe_limit}
                """)
            ).fetchall()

        out = []
        for ts, mode, symbol, event, payload_json in rows:
            out.append(
                {
                    "ts": ts,
                    "mode": mode,
                    "symbol": symbol,
                    "event": event,
                    "payload": json.loads(payload_json),
                }
            )
        return out

    def dashboard_summary(self) -> dict[str, Any]:
        events = self.recent_events(1000)
        live_buys = [e for e in events if e["event"] == "live_buy"]
        live_sells = [e for e in events if e["event"] == "live_sell"]
        risk_pauses = [e for e in events if e["event"] == "risk_pause"]
        paper_summaries = [e for e in events if e["event"] == "paper_summary"]
        backtests = [e for e in events if e["event"] == "backtest_metrics"]
        walkforwards = [e for e in events if e["event"] == "walkforward_summary"]
        states = self.states()
        latest_paper = paper_summaries[0]["payload"] if paper_summaries else None

        paper_states = [
            s
            for s in states
            if isinstance(s.get("key"), str) and str(s["key"]).startswith("paper:")
        ]
        if paper_states:
            newest_state = paper_states[0]
            state_payload = newest_state.get("value", {})
            state_summary = {
                "trades": len(state_payload.get("trades", [])),
                "wins": sum(1 for t in state_payload.get("trades", []) if t.get("pnl", 0) > 0),
                "losses": sum(1 for t in state_payload.get("trades", []) if t.get("pnl", 0) < 0),
                "win_rate_pct": 0.0,
                "total_pnl": sum(float(t.get("pnl", 0.0)) for t in state_payload.get("trades", [])),
                "balance": float(state_payload.get("cash", 0.0)),
                "roi_pct": 0.0,
                "position_open": state_payload.get("position") is not None,
                "position": state_payload.get("position"),
                "trades_detail": state_payload.get("trades", []),
            }
            trade_count = state_summary["trades"]
            if trade_count:
                state_summary["win_rate_pct"] = (state_summary["wins"] / trade_count) * 100
            initial_balance = float(state_payload.get("initial_balance", 10000.0))
            if initial_balance > 0:
                state_summary["roi_pct"] = (
                    (state_summary["balance"] / initial_balance) - 1
                ) * 100

            if (
                latest_paper is None
                or newest_state["updated_ts"]
                >= next(
                    (event["ts"] for event in paper_summaries[:1]),
                    "",
                )
            ):
                latest_paper = state_summary

        pnl_values = []
        for event in live_sells:
            pnl = event["payload"].get("pnl")
            if isinstance(pnl, (int, float)):
                pnl_values.append(float(pnl))

        wins = sum(1 for pnl in pnl_values if pnl > 0)
        win_rate = (wins / len(pnl_values) * 100) if pnl_values else 0.0

        # Cargar sentimiento de noticias de bot_state_orm
        news_sentiment = {"score": 0.0, "headlines": []}
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    text("SELECT value_json FROM bot_state_orm WHERE key = 'news_sentiment'")
                ).fetchone()
                if row:
                    news_sentiment = json.loads(row[0])
        except Exception:
            pass

        return {
            "events": len(events),
            "latest_ts": events[0]["ts"] if events else None,
            "live_buys": len(live_buys),
            "live_sells": len(live_sells),
            "risk_pauses": len(risk_pauses),
            "live_pnl": sum(pnl_values),
            "live_win_rate_pct": win_rate,
            "latest_paper": latest_paper,
            "latest_backtest": backtests[0]["payload"] if backtests else None,
            "latest_walkforward": walkforwards[0]["payload"] if walkforwards else None,
            "states": states,
            "news_sentiment": news_sentiment,
        }



class TelegramNotifier:
    def __init__(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self.enabled = (
            cfg.telegram_enabled
            and bool(cfg.telegram_bot_token)
            and bool(cfg.telegram_chat_id)
        )
        self.token = cfg.telegram_bot_token
        self.chat_id = cfg.telegram_chat_id
        self.session = requests.Session()
        self.session.trust_env = False

    @staticmethod
    def escape_html(text: str) -> str:
        if not isinstance(text, str):
            text = str(text)
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def send(self, text: str, category: str = "general") -> bool:
        if not self.enabled:
            return False

        cat = category.lower()
        if cat == "general":
            text_lower = text.lower()
            if "buy" in text_lower:
                cat = "buys"
            elif "sell" in text_lower:
                cat = "sells"
            elif "error" in text_lower or "exception" in text_lower or "traceback" in text_lower:
                cat = "errors"
            elif "tune" in text_lower or "recalibration" in text_lower:
                cat = "autotune"

        if cat == "buys" and not getattr(self.cfg, "telegram_notify_buys", True):
            return False
        if cat == "sells" and not getattr(self.cfg, "telegram_notify_sells", True):
            return False
        if cat == "autotune" and not getattr(self.cfg, "telegram_notify_autotune", True):
            return False
        if cat == "errors" and not getattr(self.cfg, "telegram_notify_errors", True):
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        response = self.session.post(
            url,
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        response.raise_for_status()
        return True

    def close(self) -> None:
        if hasattr(self, "session") and self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass


class DailyReporter:
    def __init__(self, store: EventStore) -> None:
        self.store = store

    def build(self, target_day: date) -> str:
        events = self.store.events_for_day(target_day)
        trade_events = [e for e in events if e["event"] in {"live_sell", "paper_summary"}]
        live_buys = [e for e in events if e["event"] == "live_buy"]
        live_sells = [e for e in events if e["event"] == "live_sell"]
        risk_pauses = [e for e in events if e["event"] == "risk_pause"]

        pnl_values = []
        for event in live_sells:
            pnl = event["payload"].get("pnl")
            if isinstance(pnl, (int, float)):
                pnl_values.append(float(pnl))

        total_pnl = sum(pnl_values)
        wins = sum(1 for pnl in pnl_values if pnl > 0)
        losses = sum(1 for pnl in pnl_values if pnl < 0)
        win_rate = (wins / len(pnl_values) * 100) if pnl_values else 0.0

        lines = [
            f"Reporte diario {target_day.isoformat()}",
            f"Eventos registrados: {len(events)}",
            f"Compras live: {len(live_buys)}",
            f"Ventas live: {len(live_sells)}",
            f"Risk pauses: {len(risk_pauses)}",
            f"PnL live: {total_pnl:.4f}",
            f"Win rate live: {win_rate:.2f}%",
        ]

        if trade_events:
            lines.append("Ultimos eventos relevantes:")
            for event in trade_events[-5:]:
                payload = event["payload"]
                if event["event"] == "paper_summary":
                    keys = ("trades", "wins", "losses", "balance", "roi_pct")
                else:
                    keys = ("event", "price", "qty", "pnl", "pnl_pct", "reason")
                summary = {k: payload.get(k) for k in keys if k in payload}
                lines.append(f"- {event['ts']} {event['symbol']} {event['event']} {summary}")

        return "\n".join(lines)


def build_paper_report(
    store: EventStore,
    windows: list[int | None] | None = None,
) -> dict[str, Any]:
    paper_state = store.latest_paper_state()
    if paper_state is None:
        return {
            "status": "no_paper_state",
            "windows": [],
        }

    if windows is None:
        windows = [1, 7, 30, None]

    state_value = paper_state.get("value", {})
    trades = state_value.get("trades", [])
    balance = float(state_value.get("cash", 0.0))
    initial_balance = float(state_value.get("initial_balance", 10000.0))
    updated_ts = paper_state.get("updated_ts")
    updated_dt = datetime.fromisoformat(updated_ts) if updated_ts else None
    recent_events = store.recent_events(2000)
    now = datetime.now(timezone.utc)

    def window_metrics(days: int | None) -> dict[str, Any]:
        selected = trades
        label = "all" if days is None else f"{days}d"
        if days is not None and updated_dt is not None:
            cutoff = updated_dt - timedelta(days=days)
            selected = [
                trade
                for trade in trades
                if datetime.fromisoformat(str(trade.get("exit_time"))) >= cutoff
            ]

        pnl_values = [float(t.get("pnl", 0.0)) for t in selected]
        wins = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        num_trades = len(selected)
        win_rate = (len(wins) / num_trades * 100) if num_trades else 0.0
        avg_pnl = (sum(pnl_values) / num_trades) if num_trades else 0.0
        avg_win = (gross_profit / len(wins)) if wins else 0.0
        avg_loss = (gross_loss / len(losses)) if losses else 0.0
        payoff = (avg_win / avg_loss) if avg_loss > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        expectancy = avg_pnl
        best_trade = max(pnl_values) if pnl_values else 0.0
        worst_trade = min(pnl_values) if pnl_values else 0.0

        return {
            "window": label,
            "num_trades": num_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": win_rate,
            "total_pnl": sum(pnl_values),
            "avg_pnl": avg_pnl,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": profit_factor,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "payoff_ratio": payoff,
            "expectancy": expectancy,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
        }

    failures_24h = [
        event
        for event in recent_events
        if event["event"] in {"data_error", "paper_error", "paper_process_exit"}
        and (
            (ts := event.get("ts")) is not None
            and datetime.fromisoformat(ts) >= now - timedelta(hours=24)
        )
    ]

    return {
        "status": "ok",
        "symbol": state_value.get("symbol"),
        "interval": state_value.get("interval"),
        "updated_ts": updated_ts,
        "balance": balance,
        "initial_balance": initial_balance,
        "roi_pct": ((balance / initial_balance) - 1) * 100 if initial_balance > 0 else 0.0,
        "position_open": state_value.get("position") is not None,
        "recent_failures_24h": len(failures_24h),
        "windows": [window_metrics(days) for days in windows],
    }


class Telemetry:
    def __init__(self, cfg: BotConfig) -> None:
        setup_logging(cfg)
        self.cfg = cfg
        self.store = EventStore(cfg.event_db_path)
        self.notifier = TelegramNotifier(cfg)
        self.reporter = DailyReporter(self.store)

    def record(self, mode: str, symbol: str, event: str, payload: dict[str, Any]) -> None:
        logging.info("%s %s %s %s", mode, symbol, event, payload)
        self.store.record(mode, symbol, event, payload)

    def alert(self, text: str, category: str = "general") -> None:
        try:
            sent = self.notifier.send(text, category=category)
            if sent:
                logging.info("Telegram alert sent.")
        except Exception as exc:
            logging.warning("Telegram alert failed: %s", exc)

    def close(self) -> None:
        if hasattr(self, "store") and self.store is not None:
            try:
                self.store.close()
            except Exception:
                pass
        if hasattr(self, "notifier") and hasattr(self.notifier, "close"):
            try:
                self.notifier.close()
            except Exception:
                pass

    def __enter__(self) -> Telemetry:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


def build_telemetry(cfg: BotConfig) -> Telemetry:
    return Telemetry(cfg)
