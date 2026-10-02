"""temp_copy must not leak file descriptors -- the bug that silently killed capture.

Regression for the Errno 24 outage: the old inline ``mkstemp`` + ``write_bytes``
leaked one descriptor per Chrome-history scan until the daemon hit RLIMIT_NOFILE.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from retrace.tmpcopy import temp_copy


def _open_fds() -> int:
    """Open descriptors (POSIX) or handles (Windows) for this process."""
    from retrace.capture.daemon import fd_usage

    return fd_usage()[0]


# A Windows handle count also moves with unrelated threads and events; a real leak
# is one handle per call (hundreds here), so allow a little noise there.
_NOISE = 8 if sys.platform == "win32" else 0


@pytest.fixture()
def src_db(tmp_path: Path) -> Path:
    p = tmp_path / "History"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    return p


def test_temp_copy_yields_readable_copy_and_unlinks(src_db: Path):
    with temp_copy(src_db, suffix=".x.db") as tmp:
        assert tmp.exists() and tmp.suffix == ".db"
        conn = sqlite3.connect(tmp)
        assert conn.execute("SELECT x FROM t").fetchone() == (1,)
        conn.close()
    assert not tmp.exists()


def test_temp_copy_does_not_leak_fds(src_db: Path):
    with temp_copy(src_db):
        pass
    before = _open_fds()
    for _ in range(2000):
        with temp_copy(src_db):
            pass
    after = _open_fds()
    assert after - before <= _NOISE, f"fd count grew {before} -> {after}"


def test_temp_copy_missing_source_raises_without_leak(tmp_path: Path):
    before = _open_fds()
    for _ in range(200):
        with pytest.raises(OSError):
            with temp_copy(tmp_path / "nope"):
                pass
    assert _open_fds() - before <= _NOISE


def test_read_chrome_does_not_leak_fds(src_db: Path, monkeypatch):
    """The real caller, end to end: many scans, flat descriptor count."""
    from retrace.activity import service

    conn = sqlite3.connect(src_db)
    conn.executescript(
        "CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT);"
        "CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER);"
        "INSERT INTO urls VALUES (1, 'https://example.com', 'Example');"
        "INSERT INTO visits VALUES (1, 1, 13400000000000000);"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(service, "_CHROME", src_db)
    assert service.read_chrome(None)
    before = _open_fds()
    for _ in range(500):
        service.read_chrome(None)
    assert _open_fds() - before <= _NOISE
