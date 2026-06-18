"""Stats rollups: source selection, AFK scaling, domain apportionment."""

from __future__ import annotations

from datetime import datetime

from retrace.db import session_scope
from retrace.models import ActivityEvent
from retrace.stats import service as stats

WIDE_START = datetime(2026, 6, 1)
WIDE_END = datetime(2026, 7, 1)


def _add(settings, **kw):
    kw.setdefault("url", "")
    kw.setdefault("day", "2026-06-16")
    kw.setdefault("start_at", datetime(2026, 6, 16, 12, 0, 0))
    with session_scope(settings) as s:
        s.add(ActivityEvent(**kw))


def test_time_per_app_knowledgec_only(settings):
    _add(settings, source="knowledgec", app="com.apple.Safari", seconds=3600)
    res = stats.time_per_app(WIDE_START, WIDE_END, settings)
    assert res["source"] == "knowledgec"
    assert res["total_seconds"] == 3600
    assert res["apps"][0]["app"] == "Safari"  # _pretty_bundle


def test_time_per_app_scaled_by_active_coverage(settings):
    # knowledgeC says 3600s focused, but idle-aware active samples cover only 1800s.
    _add(settings, source="knowledgec", app="com.apple.Safari", seconds=3600)
    _add(settings, source="active", app="Safari", seconds=1800)
    res = stats.time_per_app(WIDE_START, WIDE_END, settings)
    assert res["source"] == "knowledgec+active"
    # 3600 scaled by 1800/3600 = 1800 (AFK time excluded)
    assert abs(res["total_seconds"] - 1800) < 1e-6


def test_time_per_app_active_only(settings):
    _add(settings, source="active", app="Code", seconds=600)
    res = stats.time_per_app(WIDE_START, WIDE_END, settings)
    assert res["source"] == "active"
    assert res["apps"][0]["app"] == "Code"


def test_time_per_domain_apportions_browser_focus(settings):
    _add(settings, source="knowledgec", app="com.apple.Safari", seconds=1000)
    _add(settings, source="safari", app="com.apple.Safari", url="https://news.example.com/a")
    _add(settings, source="safari", app="com.apple.Safari", url="https://news.example.com/b")
    _add(settings, source="safari", app="com.apple.Safari", url="https://other.example.org/x")
    res = stats.time_per_domain(WIDE_START, WIDE_END, settings)
    by_domain = {d["domain"]: d for d in res["domains"]}
    assert by_domain["news.example.com"]["visits"] == 2
    # 1000s browser focus split 2:1 -> ~667 to news
    assert abs(by_domain["news.example.com"]["seconds"] - (1000 * 2 / 3)) < 1.0


def test_system_series(settings):
    day = "2026-06-16"
    _add(settings, source="system", app="system",
         detail={"cpu_percent": 12.5, "mem_percent": 41.0, "load_1m": 1.2})
    out = stats.system_series(day, settings)
    assert out["count"] == 1
    assert out["series"][0]["cpu"] == 12.5
    assert out["cpu_max"] == 12.5
    assert out["mem_max"] == 41.0


def test_top_and_weekly_shape(settings):
    _add(settings, source="active", app="Safari", seconds=300)
    top = stats.top("2026-06-10", "2026-06-20", settings)
    assert "apps" in top and "domains" in top and top["total_seconds"] == 300
    wk = stats.weekly("2026-06-16", settings)
    assert len(wk["days"]) == 7
    assert "top_apps" in wk
