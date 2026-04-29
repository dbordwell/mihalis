"""SQLAlchemy engine + session factory. WAL mode required for Litestream."""

from __future__ import annotations

from contextlib import contextmanager
from sqlite3 import Connection as SQLite3Connection

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import secrets

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _set_sqlite_pragmas(dbapi_conn, _):
    if isinstance(dbapi_conn, SQLite3Connection):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        url = secrets().database_url
        _engine = create_engine(url, future=True, echo=False)
        if url.startswith("sqlite"):
            event.listen(_engine, "connect", _set_sqlite_pragmas)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def session_scope():
    factory = session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
