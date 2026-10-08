"""Activity ingest: idempotent upsert + idle-aware active sampling."""

from __future__ import annotations

from datetime import datetime, timedelta
import os
import time

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


def test_active_sample_uses_elapsed_seconds(settings, monkeypatch):
    monkeypatch.setattr(service, "get_presence", lambda *a, **k: {"ok": True, "present": True, "idle_seconds": 0})
    service.record_active_sample(45, app="Safari", settings=settings, elapsed_s=72)
    with session_scope(settings) as s:
        row = s.query(ActivityEvent).one()
        assert row.seconds == 72
        assert row.end_at - row.start_at == timedelta(seconds=72)


def test_active_sample_caps_long_elapsed_gap(settings, monkeypatch):
    monkeypatch.setattr(service, "get_presence", lambda *a, **k: {"ok": True, "present": True, "idle_seconds": 0})
    service.record_active_sample(45, app="Safari", settings=settings, elapsed_s=151)
    with session_scope(settings) as s:
        row = s.query(ActivityEvent).one()
        assert row.seconds == 45


def test_active_sample_uses_utc_start_and_local_day_in_bst(settings, monkeypatch):
    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()
    monkeypatch.setattr(service, "utcnow", lambda: datetime(2026, 10, 7, 23, 30))
    monkeypatch.setattr(service, "get_presence", lambda *a, **k: {"ok": True, "present": True, "idle_seconds": 0})
    try:
        service.record_active_sample(45, app="Safari", settings=settings)
        with session_scope(settings) as s:
            row = s.query(ActivityEvent).one()
            assert row.start_at == datetime(2026, 10, 7, 23, 29, 15)
            assert row.end_at - row.start_at == timedelta(seconds=45)
            assert row.day == "2026-10-08"
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_idle_meeting_app_is_recorded(settings, monkeypatch):
    monkeypatch.setattr(service, "get_presence", lambda *a, **k: {"ok": True, "present": False, "idle_seconds": 300})
    assert service.record_active_sample(45, app="Microsoft Teams", settings=settings)
    with session_scope(settings) as s:
        row = s.query(ActivityEvent).one()
        assert row.detail == {"idle_seconds": 300, "meeting": True}


def test_idle_browser_meeting_title_is_recorded(settings, monkeypatch):
    monkeypatch.setattr(service, "get_presence", lambda *a, **k: {"ok": True, "present": False, "idle_seconds": 300})
    assert service.record_active_sample(45, app="Google Chrome", settings=settings,
                                        window_title="Meet - Planning")


def test_idle_meeting_stops_after_three_hours(settings, monkeypatch):
    monkeypatch.setattr(service, "get_presence", lambda *a, **k: {"ok": True, "present": False, "idle_seconds": 10801})
    assert service.record_active_sample(45, app="Microsoft Teams", settings=settings) is False


def test_locked_meeting_app_is_skipped(settings, monkeypatch):
    monkeypatch.setattr(service, "get_presence", lambda *a, **k: {
        "ok": True, "present": False, "idle_seconds": 300, "screen_locked": True,
    })
    assert service.record_active_sample(45, app="Microsoft Teams", settings=settings) is False


def test_repair_recredits_and_rebuckets_idempotently(settings, monkeypatch, capsys):
    from retrace.cli import main

    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()
    try:
        with session_scope(settings) as s:
            for end in (datetime(2026, 10, 7, 23, 30), datetime(2026, 10, 7, 23, 31, 12)):
                s.add(ActivityEvent(source="active", app="Safari", url="", title=None,
                                    start_at=end - timedelta(hours=1, seconds=45), end_at=end,
                                    seconds=45, day="2026-10-07"))
        args = ["activity", "repair", "--db", str(settings.db_path), "--recredit"]
        assert main(args) == 0
        first = capsys.readouterr().out
        assert "2026-10-08" in first
        with session_scope(settings) as s:
            rows = s.query(ActivityEvent).order_by(ActivityEvent.end_at).all()
            assert [r.seconds for r in rows] == [45, 72]
            assert [r.day for r in rows] == ["2026-10-08", "2026-10-08"]
            assert all(r.end_at - r.start_at == timedelta(seconds=r.seconds) for r in rows)
        assert main(args) == 0
        with session_scope(settings) as s:
            assert [r.seconds for r in s.query(ActivityEvent).order_by(ActivityEvent.end_at)] == [45, 72]
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_repair_dry_run_does_not_change_rows(settings, monkeypatch):
    from retrace.cli import main

    monkeypatch.setenv("TZ", "Europe/London")
    time.tzset()
    try:
        with session_scope(settings) as s:
            s.add(ActivityEvent(source="active", app="Safari", url="", title=None,
                                start_at=datetime(2026, 10, 7, 22, 29, 15),
                                end_at=datetime(2026, 10, 7, 23, 30), seconds=45,
                                day="2026-10-07"))
        assert main(["activity", "repair", "--db", str(settings.db_path), "--dry-run"]) == 0
        with session_scope(settings) as s:
            row = s.query(ActivityEvent).one()
            assert row.day == "2026-10-07"
            assert row.start_at == datetime(2026, 10, 7, 22, 29, 15)
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()
