"""Poll-hook plugins: Spotify track logging, system stats, daemon gating."""

from __future__ import annotations

from retrace.db import session_scope
from retrace.models import ActivityEvent, Capture
from retrace.status import StatusLedger


def test_spotify_logs_only_track_changes(settings, monkeypatch):
    from retrace.plugins.builtin import spotify as sp

    track = {"id": "spotify:track:1", "name": "Song A", "artist": "Artist", "album": "Album"}
    monkeypatch.setattr(sp, "_now_playing", lambda: track)
    p = sp.SpotifyPlugin()
    p.poll(settings)
    p.poll(settings)  # same track still playing -> no new row
    with session_scope(settings) as s:
        assert s.query(Capture).filter(Capture.bundle_id == "com.spotify.client").count() == 1
        row = s.query(Capture).filter(Capture.bundle_id == "com.spotify.client").one()
        assert row.caption == "🎵 Song A — Artist"
        assert row.text_source == "plugin"

    monkeypatch.setattr(sp, "_now_playing", lambda: {**track, "id": "spotify:track:2", "name": "Song B"})
    p.poll(settings)
    with session_scope(settings) as s:
        assert s.query(Capture).filter(Capture.bundle_id == "com.spotify.client").count() == 2


def test_spotify_noop_when_not_playing(settings, monkeypatch):
    from retrace.plugins.builtin import spotify as sp

    monkeypatch.setattr(sp, "_now_playing", lambda: None)
    sp.SpotifyPlugin().poll(settings)
    with session_scope(settings) as s:
        assert s.query(Capture).filter(Capture.bundle_id == "com.spotify.client").count() == 0


def test_system_stats_records_cpu_mem(settings):
    from retrace.plugins.builtin.system_stats import SystemStatsPlugin

    SystemStatsPlugin().poll(settings)
    with session_scope(settings) as s:
        row = s.query(ActivityEvent).filter(ActivityEvent.source == "system").first()
        assert row is not None
        assert "cpu_percent" in row.detail
        assert "mem_percent" in row.detail


def test_daemon_poll_gated_on_enabled(settings):
    from retrace.capture import daemon as D

    calls = []

    class FakePoller:
        name = "fake"

        def poll(self, s):
            calls.append(1)

    d = D.CaptureDaemon(settings, enable_watcher=False, enable_fallback=False)
    d._pollers = [FakePoller()]

    d._poll_plugins()  # capture disabled -> skip
    assert calls == []

    StatusLedger(settings).set_enabled(True)
    d._poll_plugins()
    assert calls == [1]

    # Hidden mode also suppresses polling.
    StatusLedger(settings).set_snooze_indefinite()
    d._poll_plugins()
    assert calls == [1]


def test_daemon_active_sample_uses_last_app(settings, monkeypatch):
    from retrace.activity import service
    from retrace.capture import daemon as D

    seen = {}
    monkeypatch.setattr(D, "capture_once", lambda **k: None)
    monkeypatch.setattr(service, "get_presence", lambda *a, **k: {"ok": True, "present": True})

    d = D.CaptureDaemon(settings, enable_watcher=False, enable_fallback=False)
    d._last_app = "Google Chrome"
    d._record_active(30)
    with session_scope(settings) as s:
        row = s.query(ActivityEvent).filter(ActivityEvent.source == "active").first()
        assert row is not None
        assert row.app == "Google Chrome"  # not "unknown"
