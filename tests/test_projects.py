"""Time per project, and its HTTP routes, over a synthetic fortnight."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from retrace.insights.projects import project_activity, project_key, project_patterns, project_time
from retrace.models import Capture

NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
ITERM, CHROME, CODE = "com.googlecode.iterm2", "com.google.Chrome", "com.anthropic.claude-code"
ACME, WORKER, OTHER = "/Users/sam/dev/acme", "/Users/sam/dev/acme/worker", "/Users/sam/dev/other"
SECRET = "rotate the staging password"


def _at(days_ago: float, minutes: float = 0):
    return (NOW - timedelta(days=days_ago) + timedelta(minutes=minutes)).replace(tzinfo=None)


def _prompt(s, at, cwd, session, prompt):
    s.add(Capture(captured_at=at, app_name="Claude Code", bundle_id=CODE, window_title=cwd.rsplit("/", 1)[-1],
                  doc_path=cwd, caption=session, text=f"You: {prompt}\n\nClaude: Done.", text_len=20,
                  text_source="plugin"))


def _screen(s, at, **kw):
    kw.setdefault("text_source", "accessibility")
    s.add(Capture(captured_at=at, text=SECRET, text_len=len(SECRET), **kw))


@pytest.fixture()
def fortnight(settings):
    from retrace.db import session_scope

    with session_scope(settings) as s:
        # Yesterday: two prompts in one 5-minute block, one ten minutes later, and machine noise.
        _prompt(s, _at(1), ACME, "Billing export", f"please {SECRET} and keep going")
        _prompt(s, _at(1, 2), ACME, "Billing export", "keep going")
        _prompt(s, _at(1, 10), ACME, "Billing export", "keep going")
        _prompt(s, _at(1, 11), ACME, "Billing export", "This session is being continued from a previous one")
        # The terminal tab showing that session, 30 minutes later (a block of its own).
        _screen(s, _at(1, 30), app_name="iTerm2", bundle_id=ITERM, window_title="◐ Billing export")
        # The repo on GitHub and a file in the folder, three days ago.
        _screen(s, _at(3), app_name="Google Chrome", bundle_id=CHROME, window_title="acme · PR",
                url="https://github.com/sam/acme/pull/4")
        _screen(s, _at(3, 20), app_name="Preview", bundle_id="com.apple.Preview", window_title="spec.pdf",
                doc_path=f"{ACME}/docs/spec.pdf")
        # A subfolder of acme with its own session, ten days ago.
        _prompt(s, _at(10), WORKER, "Queue worker", "fix the retry loop")
        # A title two projects share, and a generic one: neither says which project you were on.
        _prompt(s, _at(2), OTHER, "Shared title", "look at this")
        _prompt(s, _at(2, 1), ACME, "Shared title", "and this")
        _prompt(s, _at(2, 2), OTHER, "New session", "hello")
        _screen(s, _at(2, 40), app_name="iTerm2", bundle_id=ITERM, window_title="✳ Shared title")
        _screen(s, _at(2, 50), app_name="iTerm2", bundle_id=ITERM, window_title="✳ New session")
        # Seven weeks ago: only the last-active date should see it.
        _prompt(s, _at(50), OTHER, "Old work", "start the other project")
    return settings


def _end():
    return NOW.astimezone()


def test_project_key_ignores_case_and_separators():
    assert project_key("TaxiRL") == project_key("taxi-rl") == project_key("taxi_RL") == "taxirl"


def test_time_is_counted_in_blocks_of_your_own_activity(fortnight):
    rows = {r["project"]: r for r in project_time(fortnight, days=7, end=_end())["projects"]}
    acme = rows["acme"]
    # Blocks: yesterday :00 (2 prompts) + :10 + :30 (terminal tab), 2 days ago :00, 3 days ago :00 and :20.
    assert acme["minutes"] == 30
    assert acme["prompts"] == 4, "the machine-written prompt is not counted"
    assert acme["evidence"] == {"prompts": 4, "terminal_captures": 1, "page_and_file_captures": 2}
    assert acme["active_days"] == 3
    assert "worker" not in rows, "ten days ago is outside the week"
    assert rows["other"]["evidence"]["terminal_captures"] == 0, "shared and generic titles are not evidence"


def test_one_project_takes_in_its_subfolders(fortnight):
    a = project_activity("ACME", fortnight, end=_end())
    assert a["project"] == "acme" and a["found"] is True
    assert (a["minutes_7d"], a["prompts_7d"]) == (30, 4)
    assert (a["minutes_28d"], a["prompts_28d"]) == (35, 5), "acme/worker counts towards acme"
    assert a["days_since_active"] == 1
    worker = project_time(fortnight, days=28, end=_end())["projects"]
    assert {r["project"] for r in worker} == {"acme", "worker", "other"}, "the list keeps folders separate"


def test_last_active_looks_further_back_and_unknown_projects_are_not_found(fortnight):
    other = project_activity("other", fortnight, end=_end())
    assert other["days_since_active"] == 2
    gone = project_activity("nothing-here", fortnight, end=_end())
    assert gone["found"] is False and gone["last_active"] is None and gone["minutes_28d"] == 0
    # Only the 50-day-old prompt: nothing in 28 days, but still found.
    from retrace.db import session_scope

    with session_scope(fortnight) as s:
        _prompt(s, _at(50), "/Users/sam/dev/dormant", "Old idea", "sketch the dormant idea")
    dormant = project_activity("dormant", fortnight, end=_end())
    assert dormant["found"] is True and dormant["minutes_28d"] == 0 and dormant["days_since_active"] == 50


def test_project_patterns_only_see_that_projects_prompts(fortnight):
    from retrace.insights import load_events

    ev = load_events(fortnight, days=28, end=_end())
    out = project_patterns("acme", events=ev)
    assert out["project"] == "acme" and out["prompts"] == 5, "acme/worker's prompt counts towards acme"
    other = project_patterns("other", events=ev)
    assert other["prompts"] == 2


def test_http_routes_carry_no_text(fortnight):
    from retrace.api.app import app

    with TestClient(app) as c:
        listing = c.get("/insights/projects", params={"days": 28})
        one = c.get("/insights/projects/acme")
        pats = c.get("/insights/projects/acme/patterns")
        everything = c.get("/insights/patterns", params={"days": 28, "limit": 5})
        steps = c.get("/insights/steps", params={"last_minutes": 60 * 24 * 3})
        bad = c.get("/insights/steps", params={"start": "not a date"})
    for r in (listing, one, pats, everything, steps):
        assert r.status_code == 200, r.text
        assert SECRET not in json.dumps(r.json())
    assert {p["project"] for p in listing.json()["projects"]} >= {"acme", "worker"}
    assert one.json()["prompts_28d"] >= 4
    assert len(everything.json()["candidates"]) <= 5
    assert steps.json()["counts"]["screen"] >= 1
    assert bad.status_code == 400
