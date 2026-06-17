"""SQLite engine, sessions, and schema setup (tables + FTS5 + triggers).

A single file-backed SQLite database holds everything. WAL mode and a busy
timeout let the capture daemon and the API share it safely.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings
from .models import Base

# Engines are cached per database path so tests (which point RETRACE_HOME at a
# temp dir) transparently get an isolated engine.
_engines: dict[str, Engine] = {}
_sessionmakers: dict[str, sessionmaker[Session]] = {}


def _create_engine(db_path: str) -> Engine:
    from sqlalchemy import create_engine

    url = f"sqlite:///{db_path}"
    engine = create_engine(
        url,
        future=True,
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # pragma: no cover - exercised indirectly
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


def get_engine(settings: Settings | None = None) -> Engine:
    s = settings or get_settings()
    s.ensure_dirs()
    key = str(s.db_path)
    if key not in _engines:
        _engines[key] = _create_engine(key)
    return _engines[key]


def get_sessionmaker(settings: Settings | None = None) -> sessionmaker[Session]:
    s = settings or get_settings()
    key = str(s.db_path)
    if key not in _sessionmakers:
        _sessionmakers[key] = sessionmaker(
            bind=get_engine(s), expire_on_commit=False, future=True
        )
    return _sessionmakers[key]


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """Transactional session: commit on success, rollback on error, always close."""
    sm = get_sessionmaker(settings)
    session = sm()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session(settings: Settings | None = None) -> Session:
    """Return a bare session (caller manages lifecycle). Used by FastAPI deps."""
    return get_sessionmaker(settings)()


# --- FTS5 ------------------------------------------------------------------

_FTS_DDL = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS captures_fts USING fts5(
        text, caption, window_title, app_name,
        content='captures', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS captures_ai AFTER INSERT ON captures BEGIN
        INSERT INTO captures_fts(rowid, text, caption, window_title, app_name)
        VALUES (new.id, new.text, new.caption, new.window_title, new.app_name);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS captures_ad AFTER DELETE ON captures BEGIN
        INSERT INTO captures_fts(captures_fts, rowid, text, caption, window_title, app_name)
        VALUES ('delete', old.id, old.text, old.caption, old.window_title, old.app_name);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS captures_au AFTER UPDATE ON captures BEGIN
        INSERT INTO captures_fts(captures_fts, rowid, text, caption, window_title, app_name)
        VALUES ('delete', old.id, old.text, old.caption, old.window_title, old.app_name);
        INSERT INTO captures_fts(rowid, text, caption, window_title, app_name)
        VALUES (new.id, new.text, new.caption, new.window_title, new.app_name);
    END
    """,
]


def init_db(settings: Settings | None = None) -> None:
    """Create all tables, the FTS5 virtual table, and sync triggers (idempotent)."""
    s = settings or get_settings()
    engine = get_engine(s)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for ddl in _FTS_DDL:
            conn.execute(text(ddl))


def rebuild_fts(settings: Settings | None = None) -> None:
    """Rebuild the FTS5 index from the content table (repair after bulk edits)."""
    engine = get_engine(settings)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO captures_fts(captures_fts) VALUES ('rebuild')"))


def reset_engine_cache() -> None:
    """Dispose and drop cached engines/sessionmakers (used by tests between runs)."""
    for eng in _engines.values():
        eng.dispose()
    _engines.clear()
    _sessionmakers.clear()
