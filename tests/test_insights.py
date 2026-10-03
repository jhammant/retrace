"""Pattern mining and step replay over a synthetic week."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from retrace.insights import load_events, mine, steps
from retrace.insights.events import context_label, typed_prompt
from retrace.insights.patterns import clip_kind, similarity
from retrace.models import ActivityEvent, Capture

END = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
DAYS = 6
ITERM, CHROME = "com.googlecode.iterm2", "com.google.Chrome"


def _cap(s, at, **kw):
    kw.setdefault("text_source", "accessibility")
    text = kw.pop("text", "")
    s.add(Capture(captured_at=at, text=text, text_len=len(text), **kw))


def _focus(s, app, start, secs):
    s.add(ActivityEvent(source="knowledgec", app=app, url="", start_at=start,
                        end_at=start + timedelta(seconds=secs), seconds=secs, day=start.strftime("%Y-%m-%d")))


def _visit(s, url, at, source="chrome"):
    s.add(ActivityEvent(source=source, app=CHROME, url=url, title=None, start_at=at,
                        end_at=None, seconds=0.0, day=at.strftime("%Y-%m-%d")))


@pytest.fixture()
def week(settings):
    from retrace.db import session_scope

    with session_scope(settings) as s:
        for d in range(1, DAYS + 1):
            t = (END - timedelta(days=d)).replace(tzinfo=None) + timedelta(hours=1)
            # A terminal session and GitHub, back and forth: iTerm, Chrome, iTerm, Chrome, iTerm.
            for k in range(5):
                term = k % 2 == 0
                secs = 60 if term else 30
                _focus(s, ITERM if term else CHROME, t, secs)
                if term:
                    _cap(s, t + timedelta(seconds=5), app_name="iTerm2", bundle_id=ITERM,
                         window_title="◐ Deploy fixes")
                else:
                    _cap(s, t + timedelta(seconds=5), app_name="Google Chrome", bundle_id=CHROME,
                         window_title="PR · GitHub", url="https://github.com/acme/app/pull/1")
                if k in (0, 2):  # copy a draft in the terminal, then switch to the browser
                    _cap(s, t + timedelta(seconds=40), app_name="Clipboard", bundle_id="com.apple.clipboard",
                         text_source="plugin", text="Thanks for the review, I've pushed the fix and re-run CI.")
                t += timedelta(seconds=secs + 2)
            # Habits: a dashboard every day (short), a call every day (not a check-in).
            _visit(s, "https://dash.example.com/home", t + timedelta(minutes=5))
            _visit(s, "https://meet.google.com/abc-defg-hij", t + timedelta(minutes=6))
            if d <= 3:
                _visit(s, "https://www.google.com/search?q=north+face+duffel+small", t + timedelta(minutes=7))
            # Prompts to Claude Code: two nudges, one repeated request, and machine noise.
            for i, prompt in enumerate([
                "keep going", "Keep going please",
                "check the deploy dashboard for errors please",
                "You are a learning-signal extractor. Below is a digest",
                "[Image: original 10x10, displayed at 10x10.]",
            ]):
                _cap(s, t + timedelta(minutes=10, seconds=i), app_name="Claude Code",
                     bundle_id="com.anthropic.claude-code", text_source="plugin", window_title="app",
                     caption="Deploy fixes", text=f"You: {prompt}\n\nClaude: Done.")
    return settings


def _end():
    return END.astimezone()


def test_timeline_labels_what_each_app_was_showing(week):
    ev = load_events(week, days=10, end=_end())
    contexts = {sp.context for sp in ev.spans}
    assert "iTerm2 · Deploy fixes" in contexts  # status glyph stripped
    assert "Google Chrome · github.com" in contexts
    assert ev.focus_source == "knowledgec"
    # Machine-written prompts are not "asks"; the pasted-image-only one neither.
    assert sorted({a.prompt for a in ev.asks}) == sorted(
        {"keep going", "Keep going please", "check the deploy dashboard for errors please"})


def test_mining_finds_each_kind_of_pattern(week):
    report = mine(load_events(week, days=10, end=_end()))
    kinds = {c["kind"] for c in report["candidates"]}
    assert {"loop", "habit", "carry", "search", "ask-intent", "ask-repeat"} <= kinds

    loop = next(c for c in report["candidates"] if c["kind"] == "loop")
    assert "iTerm2 · Deploy fixes" in loop["title"] and "github.com" in loop["title"]
    assert loop["evidence"]["days"] == DAYS

    habits = {h["site"] for h in report["habits"]}
    assert "dash.example.com" in habits and "meet.google.com" not in habits

    carry = next(c for c in report["candidates"] if c["kind"] == "carry")
    assert carry["title"] == "Copying text from iTerm2 into Google Chrome by hand"
    assert carry["evidence"]["carries"] == 2 * DAYS

    nudge = next(i for i in report["asks"]["by_intent"] if i["intent"] == "nudge")
    assert nudge["prompts"] == 2 * DAYS and nudge["days"] == DAYS
    repeat = report["asks"]["repeated"][0]
    assert repeat["count"] == DAYS and repeat["days"] == DAYS


def test_reports_hold_no_typed_or_copied_text_unless_asked(week):
    ev = load_events(week, days=10, end=_end())
    quiet = str(mine(ev))
    for secret in ("deploy dashboard", "north face", "pushed the fix", "keep going please"):
        assert secret not in quiet.lower()
    loud = str(mine(ev, include_examples=True)).lower()
    assert "deploy dashboard" in loud and "north face" in loud and "pushed the fix" in loud


def test_candidate_ids_are_stable(week):
    a = [c["id"] for c in mine(load_events(week, days=10, end=_end()))["candidates"]]
    b = [c["id"] for c in mine(load_events(week, days=10, end=_end()), include_examples=True)["candidates"]]
    assert a and sorted(a) == sorted(b)


def test_keep_limits_everything_to_chosen_captures(week):
    from retrace.db import session_scope

    with session_scope(week) as s:
        github_ids = {c.id for c in s.query(Capture).filter(Capture.bundle_id == CHROME)}
    ev = load_events(week, days=10, end=_end(), keep=lambda cid: cid in github_ids)
    assert ev.spans and all(sp.app_id == CHROME for sp in ev.spans)
    assert ev.asks == [] and ev.clips == []


def test_steps_replay_a_stretch(week):
    start = (END - timedelta(days=1)).astimezone() + timedelta(minutes=59)
    out = steps(week, start=start, end=start + timedelta(minutes=20))
    kinds = [st["type"] for st in out["steps"]]
    assert kinds[0] == "screen" and "copy" in kinds and "ask" in kinds
    first = out["steps"][0]
    assert first["context"] == "iTerm2 · Deploy fixes" and first["titles"] == ["Deploy fixes"]
    assert "pushed the fix" not in str(out["steps"])
    assert all("prompt" not in st for st in out["steps"])
    with_text = steps(week, start=start, end=start + timedelta(minutes=20), include_text=True)
    assert any(st.get("prompt") == "keep going" for st in with_text["steps"])


def test_helpers():
    assert context_label("Google Chrome", CHROME, "x", "https://docs.google.com/presentation/d/1/edit") \
        == "Google Chrome · docs.google.com/presentation"
    assert context_label("iTerm2", ITERM, "-zsh", None) == "iTerm2 · shell"
    assert context_label("Slack", "com.tinyspeck.slackmacgap", "general", None) == "Slack"
    assert typed_prompt("You: [Image #1] fix this\n\nClaude: ok") == "fix this"
    assert typed_prompt("You: <task-notification>x</task-notification>") is None
    assert clip_kind("https://example.com/x") == "link"
    assert clip_kind("git status") == "command"
    assert similarity("north face duffel small", "north face small duffel") > 0.6


def test_cli_patterns_and_steps(week, capsys):
    from retrace.cli import main

    assert main(["patterns", "--days", "10"]) == 0
    assert "Automation candidates" in capsys.readouterr().out
    assert main(["steps", "--last", "30m"]) == 0
