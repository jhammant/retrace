"""Daemon gating, power throttle, trigger serialization, and clean lifecycle."""

from __future__ import annotations

from dataclasses import dataclass

from retrace.capture import daemon as D


@dataclass
class FakeResult:
    status: str = "stored"
    app: str = "Safari"


def test_power_state_parsing(monkeypatch):
    class P:
        def __init__(self, out):
            self.stdout = out

    def fake_run(cmd, **kw):
        if cmd[:3] == ["pmset", "-g", "batt"]:
            return P("Now drawing from 'Battery Power'\n -InternalBattery 80%")
        return P(" lowpowermode         1\n")

    monkeypatch.setattr(D.subprocess, "run", fake_run)
    ps = D.power_state()
    assert ps["on_battery"] is True
    assert ps["low_power"] is True


def test_effective_interval_throttles_on_battery(settings, monkeypatch):
    d = D.CaptureDaemon(settings, enable_watcher=False, enable_fallback=False)
    settings_interval = settings.capture_interval_s

    monkeypatch.setattr(D, "power_state", lambda: {"on_battery": False, "low_power": False})
    assert d._effective_interval() == settings_interval

    monkeypatch.setattr(D, "power_state", lambda: {"on_battery": True, "low_power": False})
    assert d._effective_interval() == settings_interval * 1.5

    monkeypatch.setattr(D, "power_state", lambda: {"on_battery": True, "low_power": True})
    assert d._effective_interval() == settings_interval * 2.0


def test_trigger_calls_capture_once(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(D, "capture_once", lambda **kw: calls.append(kw) or FakeResult())
    d = D.CaptureDaemon(settings, enable_watcher=False, enable_fallback=False)
    res = d._trigger("event")
    assert res.status == "stored"
    assert calls and calls[0]["reason"] == "event"


def test_trigger_skips_when_busy(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(D, "capture_once", lambda **kw: calls.append(kw) or FakeResult())
    d = D.CaptureDaemon(settings, enable_watcher=False, enable_fallback=False)
    d._busy.acquire()  # simulate a capture already in flight
    try:
        assert d._trigger("event") is None
        assert calls == []
    finally:
        d._busy.release()


def test_start_stop_is_clean(settings, monkeypatch):
    monkeypatch.setattr(D, "capture_once", lambda **kw: FakeResult())
    # No real watcher subprocess; fallback interval is long so no captures fire.
    d = D.CaptureDaemon(settings, enable_watcher=False, enable_fallback=True)
    d.start()
    assert d._started is True
    d.stop()
    assert d._started is False
    # idempotent
    d.stop()


def test_maybe_daily_jobs_runs_once_per_day(settings, monkeypatch):
    runs = []
    monkeypatch.setattr("retrace.capture.retention.purge_older_than", lambda *a, **k: runs.append(1))
    monkeypatch.setattr("retrace.plugins.registry.run_collectors", lambda *a, **k: [])
    d = D.CaptureDaemon(settings, enable_watcher=False, enable_fallback=False)
    d._maybe_daily_jobs()
    d._maybe_daily_jobs()  # same day -> no second purge
    assert len(runs) == 1
