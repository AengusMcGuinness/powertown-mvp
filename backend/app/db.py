"""
Database wiring for the Powertown Prospecting MVP.

- Uses SQLite by default (file-based, no external service required).
- Exposes:
    - engine: SQLAlchemy Engine
    - SessionLocal: session factory
    - get_db(): FastAPI dependency that yields a session
    - init_db(): optional helper to create tables (MVP-friendly)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Where should the SQLite DB live?
# Default: backend/app/app.db
_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "app.db"

# Allow override via environment variable (nice for tests later)
DB_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")

# SQLite needs check_same_thread=False for typical FastAPI usage
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
# Ensure the parent directory exists for the sqlite file
if DB_URL.startswith("sqlite:///"):
    _DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(DB_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency: yields a SQLAlchemy session and ensures it's closed.
    Usage:
        def route(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """
    MVP helper: create tables if they don't exist.
    Call this once on startup (or from a script) after models are defined.
    Note: This requires backend.app.models to import Base + model classes.
    """
    from backend.app.models import Base  # local import to avoid circular deps
    Base.metadata.create_all(bind=engine)
