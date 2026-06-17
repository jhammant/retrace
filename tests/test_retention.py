"""Content hashing, dedup window, and retention purge (rows + thumbnails)."""

from __future__ import annotations

from datetime import timedelta

from retrace.capture.retention import (
    clean_tmp,
    content_hash,
    is_duplicate,
    purge_older_than,
)
from retrace.db import session_scope
from retrace.models import Capture, utcnow
from retrace.status import StatusLedger


def test_content_hash_is_stable_and_whitespace_insensitive():
    a = content_hash("hello   world", "Safari", "Apple")
    b = content_hash("hello world", "Safari", "Apple")
    c = content_hash("hello world", "Safari", "Different")
    assert a == b
    assert a != c


def test_is_duplicate_uses_ledger_window(settings):
    led = StatusLedger(settings)
    h = content_hash("some text", "App", "Win")
    led.record_capture(content_hash=h, app="App", window="Win", stored=True)
    assert is_duplicate(led, h, window_s=300) is True
    assert is_duplicate(led, content_hash("other", "App", "Win"), window_s=300) is False


def test_purge_removes_old_rows_and_thumbnails(settings):
    # An old capture (40 days) with a thumbnail file, and a fresh one.
    old_day = "2000-01-01"
    thumb_dir = settings.thumb_dir_for_day(old_day)
    thumb_file = thumb_dir / "old.jpg"
    thumb_file.write_bytes(b"JPEGDATA")

    with session_scope(settings) as s:
        s.add(Capture(
            captured_at=utcnow() - timedelta(days=40),
            app_name="Old", text="old", text_len=3,
            content_hash="old", thumb_path=f"{old_day}/old.jpg",
        ))
        s.add(Capture(captured_at=utcnow(), app_name="New", text="new", text_len=3))

    result = purge_older_than(30, settings)
    assert result["captures_deleted"] == 1
    assert result["thumbs_deleted"] == 1
    assert not thumb_file.exists()

    with session_scope(settings) as s:
        remaining = s.query(Capture).all()
    assert len(remaining) == 1
    assert remaining[0].app_name == "New"


def test_clean_tmp_removes_stray_frames(settings):
    stray = settings.tmp_dir / "leftover.png"
    stray.write_bytes(b"PNG")
    assert clean_tmp(settings) == 1
    assert not stray.exists()
