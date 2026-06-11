from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, create_engine
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


def get_db_session(db_path: str):
    import os
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        engine = create_engine(db_url, echo=False)
    else:
        engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()
