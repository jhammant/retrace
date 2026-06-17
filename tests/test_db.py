"""Schema setup: tables, FTS5 sync via triggers, and embedding cascade delete."""

from __future__ import annotations

import struct

from sqlalchemy import text

from retrace.db import session_scope
from retrace.models import Capture, CaptureEmbedding


def _add_capture(s, **kw):
    cap = Capture(**kw)
    s.add(cap)
    s.flush()
    return cap


def test_tables_and_fts_exist(settings):
    from retrace.db import get_engine

    eng = get_engine(settings)
    with eng.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
            )
        }
    assert "captures" in names
    assert "activity_events" in names
    assert "capture_embeddings" in names
    assert "captures_fts" in names


def test_fts_sync_on_insert_and_delete(settings):
    with session_scope(settings) as s:
        cap = _add_capture(
            s,
            app_name="Safari",
            window_title="Apple",
            text="quantum computing roadmap notes",
            caption="Reading about quantum computing",
        )
        cap_id = cap.id

    from retrace.db import get_engine

    eng = get_engine(settings)
    with eng.connect() as conn:
        rows = list(
            conn.execute(text("SELECT rowid FROM captures_fts WHERE captures_fts MATCH 'quantum'"))
        )
        assert [r[0] for r in rows] == [cap_id]

    # Delete -> FTS row should disappear.
    with session_scope(settings) as s:
        s.delete(s.get(Capture, cap_id))

    with eng.connect() as conn:
        rows = list(
            conn.execute(text("SELECT rowid FROM captures_fts WHERE captures_fts MATCH 'quantum'"))
        )
        assert rows == []


def test_embedding_cascade_delete(settings):
    vec = struct.pack("<4f", 0.1, 0.2, 0.3, 0.4)
    with session_scope(settings) as s:
        cap = _add_capture(s, app_name="Notes", text="hello world")
        s.add(CaptureEmbedding(capture_id=cap.id, dim=4, model="test", vec=vec))
        cap_id = cap.id

    with session_scope(settings) as s:
        assert s.get(CaptureEmbedding, cap_id) is not None
        s.delete(s.get(Capture, cap_id))

    with session_scope(settings) as s:
        assert s.get(CaptureEmbedding, cap_id) is None
