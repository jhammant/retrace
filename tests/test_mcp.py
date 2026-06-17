"""MCP server: read/search tools work, and there are NO write/destructive tools."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from retrace.db import session_scope
from retrace.mcp import server as srv
from retrace.models import ActivityEvent, Capture, utcnow


def _seed(settings):
    with session_scope(settings) as s:
        cap = Capture(
            captured_at=utcnow(), app_name="Safari", bundle_id="com.apple.Safari",
            window_title="Quantum", url="https://example.com",
            text="quantum computing research notes", text_len=31,
            text_source="accessibility", caption="Reading about quantum computing",
            content_hash="h1",
        )
        s.add(cap)
        s.add(ActivityEvent(source="active", app="Safari", url="",
                            start_at=utcnow() - timedelta(minutes=5), end_at=utcnow(),
                            seconds=300, day="2026-06-16"))
        s.flush()
        return cap.id


def test_tools_are_read_only(settings):
    tools = asyncio.run(srv.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "retrace_search", "retrace_timeline", "retrace_get_capture",
        "retrace_what_was_i_doing", "retrace_stats", "retrace_now", "retrace_list_apps",
    }
    # No destructive verbs anywhere.
    forbidden = ("start", "stop", "purge", "tick", "delete", "pause", "resume", "config", "write")
    assert not any(any(v in n for v in forbidden) for n in names)


def test_now_and_get_capture(settings):
    cap_id = _seed(settings)
    now = srv.retrace_now()
    assert now["app_name"] == "Safari"
    full = srv.retrace_get_capture(cap_id)
    assert full["text"] == "quantum computing research notes"
    assert srv.retrace_get_capture(999999)["error"] == "not found"


def test_timeline_and_list_apps(settings):
    _seed(settings)
    tl = srv.retrace_timeline("2026-01-01", "2030-01-01")
    assert tl["count"] == 1
    apps = srv.retrace_list_apps()
    assert apps["apps"][0]["app"] == "Safari"


def test_search_text_mode(settings):
    _seed(settings)
    res = srv.retrace_search("quantum", mode="text", limit=5)
    assert res["count"] == 1
    assert "quantum" in res["results"][0]["snippet"].lower()


def test_what_was_i_doing(settings):
    _seed(settings)
    out = srv.retrace_what_was_i_doing(utcnow().isoformat() + "Z", window_minutes=60)
    assert out["count"] == 1
    assert out["closest"]["app_name"] == "Safari"
    assert "Safari" in out["apps_seen"]


def test_stats_groupings(settings):
    _seed(settings)
    by_app = srv.retrace_stats("2026-06-16", "2026-06-16", group_by="app")
    assert by_app["group_by"] == "app"
    assert any(a["app"] == "Safari" for a in by_app["apps"])
    by_day = srv.retrace_stats("2026-06-10", "2026-06-16", group_by="day")
    assert "days" in by_day
