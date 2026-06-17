"""Activity ingest: idempotent upsert + idle-aware active sampling."""

from __future__ import annotations

from datetime import datetime

from retrace.activity import service
from retrace.db import session_scope
from retrace.models import ActivityEvent


def _fake_kc(_cutoff):
    return [{
        "source": "knowledgec", "app": "com.apple.Safari", "url": "", "title": None,
        "start_at": datetime(2026, 6, 16, 10, 0, 0), "end_at": datetime(2026, 6, 16, 10, 5, 0),
        "seconds": 300.0, "day": "2026-06-16", "detail": None,
    }]


def test_scan_is_idempotent(settings, monkeypatch):
    monkeypatch.setattr(service, "read_knowledgec", _fake_kc)
    monkeypatch.setattr(service, "read_safari", lambda c: [])
    monkeypatch.setattr(service, "read_chrome", lambda c: [])

    r1 = service.scan_and_upsert(full=True, settings=settings)
    r2 = service.scan_and_upsert(full=True, settings=settings)
    assert r1["upserted"] == 1
    assert r2["upserted"] == 0  # same identity -> no duplicate

    with session_scope(settings) as s:
        assert s.query(ActivityEvent).count() == 1


def test_upsert_handles_many_rows(settings, monkeypatch):
    # More rows than the chunk size must all persist (regression for the
    # "too many SQL variables" bulk-insert limit).
    from datetime import timedelta

    base = datetime(2026, 6, 16, 0, 0, 0)
    many = [{
        "source": "chrome", "app": "com.google.Chrome",
        "url": f"https://example.com/{i}", "title": None,
        "start_at": base + timedelta(minutes=i), "end_at": None,
        "seconds": 0.0, "day": "2026-06-16", "detail": None,
    } for i in range(250)]
    monkeypatch.setattr(service, "read_knowledgec", lambda c: [])
    monkeypatch.setattr(service, "read_safari", lambda c: [])
    monkeypatch.setattr(service, "read_chrome", lambda c: many)

    result = service.scan_and_upsert(full=True, settings=settings)
    assert result["upserted"] == 250
    with session_scope(settings) as s:
        assert s.query(ActivityEvent).count() == 250


def test_active_sample_skips_when_away(settings, monkeypatch):
    monkeypatch.setattr(service, "get_presence",
                        lambda *a, **k: {"ok": True, "present": False, "idle_seconds": 999})
    assert service.record_active_sample(45, app="Safari", settings=settings) is False
    with session_scope(settings) as s:
        assert s.query(ActivityEvent).count() == 0


def test_active_sample_records_when_present(settings, monkeypatch):
    monkeypatch.setattr(service, "get_presence",
                        lambda *a, **k: {"ok": True, "present": True, "idle_seconds": 3})
    assert service.record_active_sample(45, app="Safari", settings=settings) is True
    with session_scope(settings) as s:
        rows = s.query(ActivityEvent).all()
        assert len(rows) == 1
        assert rows[0].source == "active"
        assert rows[0].seconds == 45
        assert rows[0].app == "Safari"


def test_activity_status_counts(settings, monkeypatch):
    monkeypatch.setattr(service, "read_knowledgec", _fake_kc)
    monkeypatch.setattr(service, "read_safari", lambda c: [])
    monkeypatch.setattr(service, "read_chrome", lambda c: [])
    service.scan_and_upsert(full=True, settings=settings)
    status = service.activity_status(settings)
    assert status["rows_by_source"].get("knowledgec") == 1
    assert "knowledgec" in status["sources_available"]
