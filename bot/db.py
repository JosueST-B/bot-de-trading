from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


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



def normalize_database_url(db_url: str) -> str:
    if db_url.startswith("postgres://"):
        return db_url.replace("postgres://", "postgresql://", 1)
    return db_url


def create_db_engine(db_path: str) -> Engine:
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

    return create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 30},
    )


def get_db_session(db_path: str):
    engine = create_db_engine(db_path)
    Base.metadata.create_all(engine)
    
    # Migración automática para la columna entry_reason si falta
    try:
        from sqlalchemy import inspect
        inspector = inspect(engine)
        columns = [c["name"] for c in inspector.get_columns("positions")]
        if "entry_reason" not in columns:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE positions ADD COLUMN entry_reason VARCHAR(100)"))
    except Exception as e:
        import logging
        logging.warning(f"Error al verificar/migrar base de datos positions: {e}")
        
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return Session()
