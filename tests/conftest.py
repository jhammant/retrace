"""Pytest fixtures with strict isolation from any real Retrace installation.

Every test runs against a throwaway ``RETRACE_HOME`` under pytest's tmp dir. A
guard refuses to proceed if the resolved home is the real ``~/.retrace``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

REAL_HOME = (Path.home() / ".retrace").resolve()

# Defensive: ensure that even a test which forgets the `settings` fixture cannot
# touch the real installation. Point RETRACE_HOME at a temp dir at import time.
if not os.environ.get("RETRACE_HOME") or Path(os.environ["RETRACE_HOME"]).resolve() == REAL_HOME:
    os.environ["RETRACE_HOME"] = tempfile.mkdtemp(prefix="retrace-tests-")

# Never spin up the real capture daemon (Swift compile + live screen capture)
# during the test suite.
os.environ["RETRACE_DISABLE_DAEMON"] = "1"


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    """An isolated, initialized Retrace home + database for one test."""
    home = tmp_path / "retrace-home"
    monkeypatch.setenv("RETRACE_HOME", str(home))

    from retrace import config as cfg
    from retrace import db as dbmod

    s = cfg.reload_settings()

    # Hard guard — never operate against the real installation.
    assert s.home.resolve() != REAL_HOME, "refusing to run tests against the real ~/.retrace"
    assert str(s.home.resolve()).startswith(str(tmp_path.resolve()))

    dbmod.reset_engine_cache()
    s.ensure_dirs()
    dbmod.init_db(s)
    try:
        yield s
    finally:
        dbmod.reset_engine_cache()
        cfg.reload_settings()


@pytest.fixture()
def session(settings):
    """A committed-on-exit ORM session bound to the isolated database."""
    from retrace.db import session_scope

    with session_scope(settings) as s:
        yield s


@pytest.fixture()
def ledger(settings):
    from retrace.status import StatusLedger

    return StatusLedger(settings)


@pytest.fixture(autouse=True)
def _neutralize_native(monkeypatch):
    """By default, never invoke real Swift / Foundation Models helpers in tests.

    Tests that specifically exercise these paths re-patch them with their own
    fakes (monkeypatch stacks, last-set wins for the duration of the test).
    """
    monkeypatch.setattr("retrace.capture.caption_native.native_caption", lambda **k: None)
    monkeypatch.setattr("retrace.search.service._embed_helper", lambda *a, **k: None)
