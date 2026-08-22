from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

_engine: Engine | None = None
_lock = threading.Lock()


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def init_engine(database_url: str) -> Engine:
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                _engine = create_engine(
                    normalize_database_url(database_url),
                    pool_pre_ping=True,
                    pool_recycle=300,
                    pool_size=5,
                    max_overflow=10,
                    pool_timeout=10,
                )
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database engine has not been initialized")
    return _engine


@contextmanager
def transaction() -> Iterator[Connection]:
    with get_engine().begin() as connection:
        yield connection


def database_ready() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def close_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
