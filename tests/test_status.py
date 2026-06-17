"""Status ledger: enable persistence, counters, day rollover, dedup window."""

from __future__ import annotations

import json

from retrace.status import StatusLedger


def test_enabled_defaults_off_and_persists(settings):
    led = StatusLedger(settings)
    assert led.is_enabled() is False
    led.set_enabled(True)
    # A fresh ledger reads the persisted file.
    assert StatusLedger(settings).is_enabled() is True
    led.set_enabled(False)
    assert StatusLedger(settings).is_enabled() is False


def test_counters_bump_and_capture(settings):
    led = StatusLedger(settings)
    led.record_capture(content_hash="abc", app="Safari", window="Apple", stored=True)
    led.record_skip("dupe")
    led.record_skip("denylist")
    snap = led.snapshot()
    assert snap["counters"]["captured"] == 1
    assert snap["counters"]["stored"] == 1
    assert snap["counters"]["skipped_dupe"] == 1
    assert snap["counters"]["skipped_denylist"] == 1
    assert snap["last_app"] == "Safari"
    assert snap["last_hash"] == "abc"


def test_day_rollover_resets_counters(settings):
    led = StatusLedger(settings)
    led.record_capture(content_hash="x", app="A", window="W", stored=True)
    # Simulate a stale day in the persisted file.
    data = json.loads(settings.status_path.read_text())
    data["counters"]["day"] = "2000-01-01"
    data["counters"]["captured"] = 42
    settings.status_path.write_text(json.dumps(data))
    snap = StatusLedger(settings).snapshot()
    assert snap["counters"]["captured"] == 0  # reset for today
    assert snap["counters"]["day"] != "2000-01-01"


def test_snooze_hidden_mode(settings):
    from datetime import datetime, timedelta, timezone

    led = StatusLedger(settings)
    assert led.is_snoozed() is False
    # indefinite
    led.set_snooze_indefinite()
    assert StatusLedger(settings).is_snoozed() is True
    # resume
    led.set_snooze(None)
    assert led.is_snoozed() is False
    # future window
    led.set_snooze(datetime.now(timezone.utc) + timedelta(minutes=30))
    assert led.is_snoozed() is True
    # past window -> not snoozed
    led.set_snooze(datetime.now(timezone.utc) - timedelta(minutes=1))
    assert led.is_snoozed() is False


def test_last_hash_within_window(settings):
    led = StatusLedger(settings)
    led.record_capture(content_hash="hh", app="A", window="W", stored=True)
    assert led.last_hash_within("hh", window_s=300) is True
    assert led.last_hash_within("different", window_s=300) is False
    assert led.last_hash_within("hh", window_s=0) is False  # zero window => stale
