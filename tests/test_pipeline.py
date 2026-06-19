"""Capture pipeline integration tests with the native helpers mocked.

The critical assertion: the raw frame is deleted in every path — success,
capture failure, and exception — proving the core privacy invariant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from retrace.capture import pipeline
from retrace.db import session_scope
from retrace.models import Capture
from retrace.status import StatusLedger


# --- fakes ------------------------------------------------------------------

def fake_present(*_a, **_k):
    return {"ok": True, "present": True, "idle_seconds": 1.0}


def fake_context_factory(app="Safari", bundle="com.apple.Safari", window="Apple News",
                         text="", url=None, private=False, ok=True):
    def _ctx(settings=None, **_k):
        return {
            "ok": ok,
            "app_name": app,
            "bundle_id": bundle,
            "window_title": window,
            "url": url,
            "doc_path": None,
            "text": text,
            "text_source": "accessibility" if text else "none",
            "private_browsing": private,
            "ax_trusted": True,
        }
    return _ctx


def fake_capture_factory(ok=True, write_frame=True, write_thumb=True, error=None):
    def _cap(*, frame_path, thumb_path, max_edge, jpeg_quality, exclude_bundle_ids,
             settings=None, **_k):
        if write_frame:
            Path(frame_path).write_bytes(b"RAWPNGBYTES")
        if write_thumb:
            Path(thumb_path).write_bytes(b"JPEGBYTES")
        if ok:
            return {"ok": True, "frame_path": frame_path, "thumb_path": thumb_path,
                    "width": 100, "height": 80, "excluded": len(exclude_bundle_ids)}
        return {"ok": False, "error": error or "capture failed"}
    return _cap


def fake_ocr_factory(text="ocr recovered some on-screen words from the frame"):
    def _ocr(path, settings=None, **_k):
        return {"ok": True, "text": text, "line_count": 3}
    return _ocr


def _patch(monkeypatch, *, present=fake_present, context=None, capture=None, ocr=None,
           sensitivity=None):
    monkeypatch.setattr(pipeline, "get_presence", present)
    monkeypatch.setattr(pipeline, "read_context", context or fake_context_factory(
        text="A reasonably long accessibility text blob about quantum computing research."))
    monkeypatch.setattr(pipeline, "capture_frame", capture or fake_capture_factory())
    monkeypatch.setattr(pipeline, "ocr_image", ocr or fake_ocr_factory())
    # Default: image sensitivity analysis unavailable (no-op).
    monkeypatch.setattr(pipeline, "analyze_sensitivity",
                        sensitivity or (lambda *a, **k: {"available": False, "sensitive": False}))
    # Don't invoke the real embedding helper in capture-pipeline tests.
    monkeypatch.setattr("retrace.search.service.store_capture_embedding", lambda *a, **k: False)


def _no_raw_frames(settings) -> bool:
    return not any(settings.tmp_dir.glob("*.png"))


# --- tests ------------------------------------------------------------------

def test_stored_path_persists_row_and_deletes_frame(settings, monkeypatch):
    _patch(monkeypatch)
    StatusLedger(settings).set_enabled(True)

    result = pipeline.capture_once(settings=settings)

    assert result.status == "stored"
    assert result.capture_id is not None
    assert result.text_source == "accessibility"
    assert result.frame_deleted is True
    assert _no_raw_frames(settings), "raw frame must be deleted"

    # The thumbnail persists; the row exists.
    assert result.thumb_path is not None
    assert (settings.thumbs_dir / result.thumb_path).exists()
    with session_scope(settings) as s:
        rows = s.query(Capture).all()
        assert len(rows) == 1
        assert rows[0].app_name == "Safari"


def test_capture_failure_still_deletes_frame(settings, monkeypatch):
    # Capture wrote a partial raw frame then reported failure.
    _patch(monkeypatch, capture=fake_capture_factory(ok=False, write_frame=True))
    StatusLedger(settings).set_enabled(True)

    result = pipeline.capture_once(settings=settings)

    assert result.status == "error"
    assert result.reason == "capture-failed"
    assert result.frame_deleted is True
    assert _no_raw_frames(settings), "raw frame must be deleted even on capture failure"
    with session_scope(settings) as s:
        assert s.query(Capture).count() == 0


def test_exception_during_persist_still_deletes_frame(settings, monkeypatch):
    _patch(monkeypatch)

    def boom(**_k):
        raise RuntimeError("simulated failure after frame grab")

    monkeypatch.setattr(pipeline, "make_caption", boom)
    StatusLedger(settings).set_enabled(True)

    result = pipeline.capture_once(settings=settings)

    assert result.status == "error"
    assert result.reason == "exception"
    assert result.frame_deleted is True
    assert _no_raw_frames(settings), "raw frame must be deleted even when persist raises"


def test_denylisted_app_never_grabs_a_frame(settings, monkeypatch):
    called = {"n": 0}

    def tracking_capture(**kw):
        called["n"] += 1
        return fake_capture_factory()(**kw)

    _patch(
        monkeypatch,
        context=fake_context_factory(app="1Password", bundle="com.1password.1password",
                                     text="secret vault contents here"),
        capture=tracking_capture,
    )
    StatusLedger(settings).set_enabled(True)

    result = pipeline.capture_once(settings=settings)

    assert result.status == "skipped"
    assert result.reason == "denylist"
    assert called["n"] == 0, "denylisted app must never trigger a frame grab"
    assert _no_raw_frames(settings)


def test_dedup_skips_unchanged_screen(settings, monkeypatch):
    _patch(monkeypatch)
    StatusLedger(settings).set_enabled(True)

    first = pipeline.capture_once(settings=settings)
    assert first.status == "stored"
    second = pipeline.capture_once(settings=settings)
    assert second.status == "skipped"
    assert second.reason == "duplicate"

    with session_scope(settings) as s:
        assert s.query(Capture).count() == 1


def test_disabled_is_skipped_unless_forced(settings, monkeypatch):
    _patch(monkeypatch)
    # capture disabled (default)
    assert pipeline.capture_once(settings=settings).reason == "disabled"
    # force bypasses the gate
    forced = pipeline.capture_once(settings=settings, force=True)
    assert forced.status == "stored"


def test_ocr_fallback_when_ax_text_thin(settings, monkeypatch):
    _patch(monkeypatch, context=fake_context_factory(text=""))  # no AX text
    StatusLedger(settings).set_enabled(True)

    result = pipeline.capture_once(settings=settings)

    assert result.status == "stored"
    assert result.text_source == "ocr"
    assert result.text_len > 0
    assert _no_raw_frames(settings)


def test_idle_user_is_not_captured(settings, monkeypatch):
    _patch(monkeypatch, present=lambda *a, **k: {"ok": True, "present": False, "idle_seconds": 999})
    StatusLedger(settings).set_enabled(True)
    result = pipeline.capture_once(settings=settings)
    assert result.status == "skipped"
    assert result.reason == "idle"
    assert _no_raw_frames(settings)


def test_locked_screen_is_not_captured(settings, monkeypatch):
    _patch(monkeypatch, present=lambda *a, **k: {
        "ok": True, "present": False, "screen_locked": True, "idle_seconds": 5})
    StatusLedger(settings).set_enabled(True)
    result = pipeline.capture_once(settings=settings)
    assert result.reason == "locked"
    assert _no_raw_frames(settings)


def test_pause_when_away_disabled_allows_idle_capture(settings, monkeypatch):
    from retrace import config as cfg
    cfg.update_config({"pause_when_away": False})
    s = cfg.get_settings()
    _patch(monkeypatch, present=lambda *a, **k: {"ok": True, "present": False, "idle_seconds": 999})
    StatusLedger(s).set_enabled(True)
    result = pipeline.capture_once(settings=s)
    assert result.status == "stored"  # gating bypassed by setting
    assert _no_raw_frames(s)


def test_no_context_does_not_capture(settings, monkeypatch):
    _patch(monkeypatch, context=lambda settings=None, **_k: None)
    StatusLedger(settings).set_enabled(True)

    result = pipeline.capture_once(settings=settings)
    assert result.status == "skipped"
    assert result.reason == "no-context"
    assert _no_raw_frames(settings)


def test_hidden_mode_skips(settings, monkeypatch):
    _patch(monkeypatch)
    led = StatusLedger(settings)
    led.set_enabled(True)
    led.set_snooze_indefinite()
    result = pipeline.capture_once(settings=settings)
    assert result.status == "skipped"
    assert result.reason == "hidden"
    assert _no_raw_frames(settings)


def test_sensitive_url_skips(settings, monkeypatch):
    _patch(monkeypatch, context=fake_context_factory(
        app="Safari", bundle="com.apple.Safari", url="https://example.com/nsfw-clip",
        window="clip", text="some text"))
    StatusLedger(settings).set_enabled(True)
    result = pipeline.capture_once(settings=settings)
    assert result.status == "skipped"
    assert result.reason == "sensitive"
    assert _no_raw_frames(settings)


def test_sensitive_image_drops_capture_and_thumb(settings, monkeypatch):
    _patch(monkeypatch, sensitivity=lambda *a, **k: {"available": True, "sensitive": True})
    StatusLedger(settings).set_enabled(True)
    result = pipeline.capture_once(settings=settings)
    assert result.status == "skipped"
    assert result.reason == "sensitive-image"
    assert _no_raw_frames(settings)
    # thumbnail must not be retained
    assert not any(settings.thumbs_dir.rglob("*.jpg"))
    with session_scope(settings) as s:
        assert s.query(Capture).count() == 0


def test_page_text_preferred_and_html_stored(settings, monkeypatch):
    from retrace import config as cfg
    from retrace.models import CaptureHtml

    cfg.update_config({"capture_page_text": True, "capture_page_html": True})
    s = cfg.get_settings()

    def ctx(settings=None, **k):
        return {
            "ok": True, "app_name": "Safari", "bundle_id": "com.apple.Safari",
            "window_title": "Doc", "url": "https://example.com", "text": "short ax",
            "text_source": "accessibility", "private_browsing": False,
            "page_text": "the full page innerText is much richer and longer than ax text",
            "page_html": "<html><body>full</body></html>",
        }

    _patch(monkeypatch, context=ctx)
    StatusLedger(s).set_enabled(True)
    result = pipeline.capture_once(settings=s)
    assert result.status == "stored"
    assert result.text_source == "page"
    with session_scope(s) as sess:
        row = sess.query(Capture).one()
        assert "innerText" in row.text
        assert sess.get(CaptureHtml, row.id) is not None
