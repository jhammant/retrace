"""Text (FTS5), semantic (cosine), and hybrid search."""

from __future__ import annotations

import pytest

from retrace.db import session_scope
from retrace.models import Capture, CaptureEmbedding, utcnow
from retrace.search import service

VOCAB = ["quantum", "computing", "research", "cooking", "pasta",
         "recipe", "physics", "budget", "invoice"]


def _fake_embed(text, **kw):
    t = (text or "").lower()
    return {"ok": True, "model": "test", "dim": len(VOCAB),
            "vec": [1.0 if w in t else 0.0 for w in VOCAB]}


@pytest.fixture(autouse=True)
def _patch_embed(monkeypatch):
    monkeypatch.setattr(service, "_embed_helper", _fake_embed)


def _seed(settings, items):
    ids = []
    with session_scope(settings) as s:
        for app, txt in items:
            c = Capture(captured_at=utcnow(), app_name=app, text=txt, text_len=len(txt),
                        caption=txt[:40], content_hash=txt[:8])
            s.add(c)
            s.flush()
            ids.append(c.id)
    return ids


DOCS = [
    ("Safari", "quantum computing research roadmap and notes"),
    ("Notes", "cooking pasta recipe with tomato sauce"),
    ("Excel", "quarterly budget invoice spreadsheet"),
]


def test_text_search_matches_keywords(settings):
    _seed(settings, DOCS)
    res = service.search("quantum", mode="text", settings=settings)
    assert res["count"] == 1
    assert "quantum" in res["results"][0]["snippet"]


def test_backfill_creates_embeddings(settings):
    _seed(settings, DOCS)
    out = service.backfill_embeddings(settings=settings)
    assert out["embedded"] == 3
    with session_scope(settings) as s:
        assert s.query(CaptureEmbedding).count() == 3


def test_semantic_ranks_by_meaning(settings):
    _seed(settings, DOCS)
    service.backfill_embeddings(settings=settings)
    res = service.search("quantum physics", mode="semantic", settings=settings)
    # The quantum-computing doc should top the ranking (shares 'quantum').
    assert res["results"][0]["app_name"] == "Safari"
    # cooking doc shares nothing with the query -> not the top result
    assert res["results"][0]["score"] > 0


def test_hybrid_combines_both(settings):
    _seed(settings, DOCS)
    service.backfill_embeddings(settings=settings)
    res = service.search("budget invoice", mode="hybrid", settings=settings)
    assert res["results"][0]["app_name"] == "Excel"


def test_empty_query_returns_nothing(settings):
    _seed(settings, DOCS)
    assert service.search("", mode="hybrid", settings=settings)["results"] == []


def test_pipeline_stores_embedding_when_enabled(settings, monkeypatch):
    # Capturing with semantic search on should persist an embedding.
    from retrace.capture import pipeline
    from retrace.status import StatusLedger
    import retrace.search.service as ss

    monkeypatch.setattr(ss, "_embed_helper", _fake_embed)
    monkeypatch.setattr(pipeline, "get_presence", lambda *a, **k: {"ok": True, "present": True})
    monkeypatch.setattr(pipeline, "read_context", lambda settings=None, **k: {
        "ok": True, "app_name": "Safari", "bundle_id": "com.apple.Safari",
        "window_title": "Q", "text": "quantum computing research notes here",
        "text_source": "accessibility", "private_browsing": False,
    })

    def _cap(*, frame_path, thumb_path, **k):
        from pathlib import Path
        Path(frame_path).write_bytes(b"x"); Path(thumb_path).write_bytes(b"y")
        return {"ok": True, "frame_path": frame_path, "thumb_path": thumb_path}

    monkeypatch.setattr(pipeline, "capture_frame", _cap)
    StatusLedger(settings).set_enabled(True)
    res = pipeline.capture_once(settings=settings, force=True)
    assert res.status == "stored"
    with session_scope(settings) as s:
        assert s.query(CaptureEmbedding).count() == 1
