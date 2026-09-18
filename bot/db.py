from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as SQLAlchemySession, declarative_base, sessionmaker

Base = declarative_base()


class OrderState(str, Enum):
    PENDING_SUBMIT = "PENDING_SUBMIT"
    FILLED = "FILLED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNHEDGED_CRITICAL = "UNHEDGED_CRITICAL"


def generate_client_order_id(symbol: str, action: str, timestamp_ms: int | None = None) -> str:
    """Format: AETH_{symbol}_{timestamp_ms}_{action[:4]} (len <= 36)."""
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    clean_sym = symbol.replace("/", "").replace("-", "").replace("_", "").upper()
    act = action[:4].upper()
    client_id = f"AETH_{clean_sym}_{timestamp_ms}_{act}"
    return client_id[:36]


class DBPosition(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    entry_time = Column(DateTime, nullable=False)
    entry_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=False)
    take_profit_price = Column(Float, nullable=False)
    stop_loss_order_id = Column(String(50), nullable=True)
    take_profit_order_id = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    entry_reason = Column(String(100), nullable=True, default="")
    client_order_id = Column(String(50), nullable=True, index=True)
    state = Column(String(30), nullable=False, default="FILLED")
    error_details = Column(Text, nullable=True)


class DBTrade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    entry_time = Column(DateTime, nullable=False)
    exit_time = Column(DateTime, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    pnl = Column(Float, nullable=False)
    pnl_pct = Column(Float, nullable=False)
    reason = Column(String(100), nullable=False)


class DBBotState(Base):
    __tablename__ = "bot_state_orm"

    key = Column(String(100), primary_key=True)
    updated_ts = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    value_json = Column(Text, nullable=False)


class DBUser(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    role = Column(String(50), default="investor", nullable=False)
    status = Column(String(50), default="active", nullable=False)
    created_ts = Column(DateTime, default=datetime.utcnow)


class DBApiCredential(Base):
    __tablename__ = "api_credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    platform = Column(String(50), nullable=False)
    encrypted_api_key = Column(Text, nullable=False)
    encrypted_api_secret = Column(Text, nullable=False)
    ibkr_client_id = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_ts = Column(DateTime, default=datetime.utcnow)


class DBUserAllocation(Base):
    __tablename__ = "user_allocations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    symbol = Column(String(50), nullable=False)
    allocated_pct = Column(Float, nullable=False, default=1.0)
    allocated_cap = Column(Float, nullable=False, default=1000.0)


class DatabaseSession(SQLAlchemySession):
    """
    Enhanced SQLAlchemy Session context manager supporting automatic rollback on exception
    and clean close on exit.
    """

    def __enter__(self) -> DatabaseSession:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            if exc_type is not None:
                self.rollback()
        finally:
            self.close()


@contextmanager
def session_scope(db_path: str | None = None):
    """Context manager yielding an active database session with automatic commit and rollback."""
    session = get_db_session(db_path)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def normalize_database_url(db_url: str) -> str:
    if db_url.startswith("postgres://"):
        return db_url.replace("postgres://", "postgresql://", 1)
    return db_url


_ENGINES: dict[str, Engine] = {}
_MIGRATED_ENGINES: set[str] = set()


def create_db_engine(db_path: str = "bot_events.sqlite3") -> Engine:
    db_url = os.getenv("DATABASE_URL", "").strip()
    if db_url:
        return create_engine(
            normalize_database_url(db_url),
            echo=False,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=5,
            max_overflow=5,
            pool_timeout=20,
            hide_parameters=True,
        )

    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA busy_timeout=30000;")
            cursor.close()
        except Exception:
            pass

    return engine


def get_engine(db_path: str = "bot_events.sqlite3") -> Engine:
    target_path = os.getenv("DATABASE_URL", "").strip() or db_path
    if not target_path.startswith("sqlite://") and not os.getenv("DATABASE_URL"):
        cache_key = os.path.abspath(target_path)
    else:
        cache_key = target_path

    if cache_key not in _ENGINES:
        _ENGINES[cache_key] = create_db_engine(target_path)
    return _ENGINES[cache_key]


def get_db_session(db_path: str | None = None) -> DatabaseSession:
    if db_path is None:
        db_path = os.getenv("EVENT_DB_PATH", "bot_events.sqlite3")

    engine = get_engine(db_path)
    cache_key = (
        os.path.abspath(db_path)
        if not db_path.startswith("sqlite://") and not os.getenv("DATABASE_URL")
        else db_path
    )

    if cache_key not in _MIGRATED_ENGINES:
        Base.metadata.create_all(engine)
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(engine)
            if "positions" in inspector.get_table_names():
                columns = [c["name"] for c in inspector.get_columns("positions")]
                with engine.begin() as conn:
                    if "entry_reason" not in columns:
                        conn.execute(text("ALTER TABLE positions ADD COLUMN entry_reason VARCHAR(100)"))
                    if "client_order_id" not in columns:
                        conn.execute(text("ALTER TABLE positions ADD COLUMN client_order_id VARCHAR(50)"))
                    if "state" not in columns:
                        conn.execute(text("ALTER TABLE positions ADD COLUMN state VARCHAR(30) DEFAULT 'FILLED'"))
                    if "error_details" not in columns:
                        conn.execute(text("ALTER TABLE positions ADD COLUMN error_details TEXT"))
        except Exception as e:
            import logging
            logging.warning(f"Error al verificar/migrar base de datos positions: {e}")
        _MIGRATED_ENGINES.add(cache_key)

    SessionFactory = sessionmaker(bind=engine, class_=DatabaseSession, expire_on_commit=False)
    return SessionFactory()

