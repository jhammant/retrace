"""HTTP API tests via FastAPI TestClient (native helpers not invoked)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from retrace.api.app import app
from retrace.db import session_scope
from retrace.models import Capture, utcnow


def _seed_capture(settings, with_thumb=True):
    day = "2026-06-16"
    thumb_rel = None
    if with_thumb:
        d = settings.thumb_dir_for_day(day)
        (d / "shot.jpg").write_bytes(b"\xff\xd8\xff\xe0JPEGDATA")
        thumb_rel = f"{day}/shot.jpg"
    with session_scope(settings) as s:
        row = Capture(
            captured_at=utcnow(), app_name="Safari", bundle_id="com.apple.Safari",
            window_title="Apple", url="https://apple.com", text="hello timeline world",
            text_len=20, text_source="accessibility", caption="Browsing apple.com",
            content_hash="h1", thumb_path=thumb_rel,
        )
        s.add(row)
        s.flush()
        return row.id


def test_health(settings):
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"] is True


def test_status_toggle_persists(settings):
    with TestClient(app) as c:
        assert c.get("/capture/status").json()["enabled"] is False
        assert c.post("/capture/start").json()["enabled"] is True
        assert c.get("/capture/status").json()["enabled"] is True
        c.post("/capture/stop")
        assert c.get("/capture/status").json()["enabled"] is False


def test_recent_detail_and_image(settings):
    cap_id = _seed_capture(settings)
    with TestClient(app) as c:
        recent = c.get("/capture/recent").json()
        assert recent["count"] == 1
        assert recent["captures"][0]["app_name"] == "Safari"
        assert "snippet" in recent["captures"][0]

        detail = c.get(f"/capture/{cap_id}").json()
        assert detail["text"] == "hello timeline world"
        assert detail["image_url"] == f"/capture/{cap_id}/image"

        img = c.get(f"/capture/{cap_id}/image")
        assert img.status_code == 200
        assert img.headers["content-type"] == "image/jpeg"

        assert c.get("/capture/99999").status_code == 404


def test_recent_filters(settings):
    _seed_capture(settings, with_thumb=False)
    with TestClient(app) as c:
        assert c.get("/capture/recent?app=Safari").json()["count"] == 1
        assert c.get("/capture/recent?app=Nope").json()["count"] == 0
        assert c.get("/capture/recent?q=timeline").json()["count"] == 1
        assert c.get("/capture/recent?q=zzz").json()["count"] == 0


def test_config_get_and_update(settings):
    with TestClient(app) as c:
        cfg = c.get("/config").json()
        assert "retention_days" in cfg["config"]
        c.post("/config", json={"retention_days": 12})
        assert c.get("/config").json()["config"]["retention_days"] == 12
        assert c.post("/config", json={"home": "/x"}).status_code == 400


def test_permissions(settings, monkeypatch):
    monkeypatch.setattr(
        "retrace.native.permissions.get_presence",
        lambda *a, **k: {"ok": True, "screen_recording": True, "accessibility": True},
    )
    with TestClient(app) as c:
        body = c.get("/permissions").json()
        assert body["permissions"]["screen_recording"]["state"] == "granted"
        assert "all_required_granted" in body


def test_web_index_served(settings):
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "Retrace" in r.text


def test_pause_resume_hidden_mode(settings):
    with TestClient(app) as c:
        c.post("/capture/pause")
        assert c.get("/capture/status").json()["snooze_until"] == "indefinite"
        c.post("/capture/resume")
        assert c.get("/capture/status").json()["snooze_until"] is None
        # timed pause sets a future timestamp
        body = c.post("/capture/pause?minutes=30").json()
        assert body["snooze_until"] not in (None, "indefinite")


def test_capture_html_endpoint(settings):
    import gzip
    from retrace.models import CaptureHtml

    cap_id = _seed_capture(settings, with_thumb=False)
    with session_scope(settings) as s:
        s.add(CaptureHtml(capture_id=cap_id, length=11,
                          html_gz=gzip.compress(b"<html>hi</html>")))
    with TestClient(app) as c:
        r = c.get(f"/capture/{cap_id}/html")
        assert r.status_code == 200
        assert "html" in r.headers["content-type"] or "plain" in r.headers["content-type"]
        assert "<html>hi</html>" in r.text
        # detail flags presence
        assert c.get(f"/capture/{cap_id}").json()["has_html"] is True
        # missing html -> 404
        assert c.get("/capture/424242/html").status_code == 404
