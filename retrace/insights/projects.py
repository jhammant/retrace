"""Where your time went, project by project.

A project is a folder you work in with Claude Code (``~/dev/acme`` is ``acme``). Activity
is tied to it three ways:

- **prompts** you typed to Claude Code in that folder;
- **screens** that showed one of its sessions: terminals title a tab with the session's
  name, so a capture of that tab is time on that project (titles shared by two projects,
  and generic ones like "New session", are left out);
- **pages and files**: the project's GitHub repository, or a file under its folder.

Time is counted in 5-minute blocks with any of that activity, so it measures your own
hands-on time (prompting, reading, reviewing), not how long an agent ran by itself. Only
names, counts and times come out of here: never prompt, screen or clipboard text.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import urlparse

from ..config import Settings, get_settings
from .events import CLAUDE_CODE_BUNDLE, SCREEN_SOURCES, TERMINALS, Events, _local, clean_title, load_events, typed_prompt
from .patterns import mine

BLOCK_MIN = 5
LAST_ACTIVE_LOOKBACK_DAYS = 120
ESTIMATE = (f"{BLOCK_MIN}-minute blocks with a prompt to Claude Code in the project's folder, a terminal tab "
            "showing one of its sessions, its GitHub repository, or a file under its folder")
_GENERIC_TITLES = re.compile(
    r"^(claude( code)?( session)?|ready\b.*|new( coding)? session|resume( previous)? session|untitled|"
    r"(fork|handoff)\b.*summary)$", re.IGNORECASE)
_EVENTS_TTL_S = 300
_events_cache: dict[tuple[str, int], tuple[float, Events]] = {}


def project_key(name: str | None) -> str:
    """'TaxiRL', 'taxi-rl' and 'taxi_rl' are the same project."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


@dataclass
class _Project:
    names: Counter = field(default_factory=Counter)
    cwds: set = field(default_factory=set)
    titles: set = field(default_factory=set)
    hits: list = field(default_factory=list)  # (local datetime, "prompt" | "screen" | "page")


def _utc_naive(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def _path_parts(path: str) -> tuple[str, ...] | None:
    if path.startswith("/"):
        return PurePosixPath(path).parts
    if re.match(r"^[A-Za-z]:\\", path):
        return tuple(p.lower() for p in PureWindowsPath(path).parts)
    return None


def _github_repo(url: str | None) -> str | None:
    if not url:
        return None
    try:
        p = urlparse(url)
    except ValueError:
        return None
    if (p.netloc or "").lower().removeprefix("www.") != "github.com":
        return None
    seg = [s for s in p.path.split("/") if s]
    return project_key(seg[1]) if len(seg) >= 2 else None


def _collect(settings: Settings, start: datetime, end: datetime) -> dict[str, _Project]:
    """Every project with activity in [start, end), keyed by ``project_key``."""
    conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True, timeout=10)
    try:
        prompts = conn.execute(
            "SELECT captured_at, window_title, doc_path, caption, text FROM captures "
            "WHERE bundle_id = ? AND captured_at >= ? AND captured_at < ?",
            (CLAUDE_CODE_BUNDLE, _utc_naive(start), _utc_naive(end)),
        ).fetchall()
        marks = ",".join("?" * len(SCREEN_SOURCES))
        screens = conn.execute(
            f"SELECT captured_at, bundle_id, window_title, url, doc_path FROM captures "
            f"WHERE text_source IN ({marks}) AND captured_at >= ? AND captured_at < ?",
            (*SCREEN_SOURCES, _utc_naive(start), _utc_naive(end)),
        ).fetchall()
    finally:
        conn.close()

    projects: dict[str, _Project] = defaultdict(_Project)
    title_owners: dict[str, set[str]] = defaultdict(set)
    for at, project, cwd, session, text in prompts:
        if not typed_prompt(text):
            continue  # Claude's own turns and machine-written prompts are not your activity
        name = project or (PurePosixPath(cwd).name if cwd else None)
        key = project_key(name)
        if not key:
            continue
        p = projects[key]
        p.names[name] += 1
        root = _path_parts(cwd) if cwd else None
        if root and len(root) > 3:  # never the home folder itself
            p.cwds.add(root)
        title = clean_title(session)
        if title and not _GENERIC_TITLES.match(title):
            title_owners[title].add(key)
        p.hits.append((_local(at), "prompt"))

    by_title = {t: next(iter(keys)) for t, keys in title_owners.items() if len(keys) == 1}
    roots = sorted(((cwd, key) for key, p in projects.items() for cwd in p.cwds), key=lambda c: -len(c[0]))
    for at, bundle, title, url, doc in screens:
        key, kind = None, None
        if bundle in TERMINALS:
            key, kind = by_title.get(clean_title(title)), "screen"
        if not key:
            repo = _github_repo(url)
            if repo in projects:
                key, kind = repo, "page"
        if not key and doc:
            parts = _path_parts(doc)
            if parts:
                key = next((k for root, k in roots if parts[: len(root)] == root), None)
                kind = "page"
        if key:
            projects[key].hits.append((_local(at), kind))
    return projects


def _stamp(dt: datetime) -> str:
    """UTC to the second ("2026-10-10T10:53:21Z"), which every client parses as an instant."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _summarise(p: _Project, since: datetime | None = None) -> dict:
    hits = [(t, k) for t, k in p.hits if since is None or t >= since]
    blocks = {int(t.timestamp() // (BLOCK_MIN * 60)) for t, _ in hits}
    kinds = Counter(k for _, k in hits)
    return {
        "minutes": len(blocks) * BLOCK_MIN,
        "prompts": kinds["prompt"],
        "active_days": len({t.date() for t, _ in hits}),
        "last_active": _stamp(max(t for t, _ in hits)) if hits else None,
        "evidence": {"prompts": kinds["prompt"], "terminal_captures": kinds["screen"],
                     "page_and_file_captures": kinds["page"]},
    }


def project_time(settings: Settings | None = None, *, days: int = 7, end: datetime | None = None) -> dict:
    """Every project you worked on in the last ``days`` days, most hands-on time first."""
    s = settings or get_settings()
    end = (end or datetime.now().astimezone()).astimezone()
    start = end - timedelta(days=days)
    projects = _collect(s, start, end)
    rows = [{"project": p.names.most_common(1)[0][0], **_summarise(p)} for p in projects.values() if p.names]
    rows.sort(key=lambda r: (r["minutes"], r["prompts"]), reverse=True)
    return {"window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
            "estimate": ESTIMATE, "projects": rows}


def _within(parts: tuple[str, ...], key: str) -> bool:
    """Is this folder the project, or somewhere inside it (looking below the home folder)?"""
    return any(project_key(part) == key for part in parts[3:])


def _merged(projects: dict[str, _Project], key: str) -> _Project:
    """The project and every folder inside it: ``acme`` takes in ``acme/worker`` too."""
    out = _Project()
    for k, p in projects.items():
        if k == key or any(_within(cwd, key) for cwd in p.cwds):
            out.names.update(p.names if k == key else {})
            out.cwds |= p.cwds
            out.hits += p.hits
    return out


def project_activity(name: str, settings: Settings | None = None, *, end: datetime | None = None) -> dict:
    """One project (with its subfolders): hands-on time over the last 7 and 28 days, and when you last touched it."""
    s = settings or get_settings()
    end = (end or datetime.now().astimezone()).astimezone()
    key = project_key(name)
    week = _summarise(_merged(_collect(s, end - timedelta(days=7), end), key))
    found = _merged(_collect(s, end - timedelta(days=28), end), key)
    month = _summarise(found)
    last = month["last_active"]
    if last is None:
        older = _merged(_collect(s, end - timedelta(days=LAST_ACTIVE_LOOKBACK_DAYS), end - timedelta(days=28)), key)
        last = _summarise(older)["last_active"]
    days_since = (end.date() - datetime.fromisoformat(last).astimezone(end.tzinfo).date()).days if last else None
    return {
        "project": found.names.most_common(1)[0][0] if found.names else name,
        "found": last is not None,
        "last_active": last,
        "days_since_active": days_since,
        "minutes_7d": week["minutes"], "prompts_7d": week["prompts"], "active_days_7d": week["active_days"],
        "minutes_28d": month["minutes"], "prompts_28d": month["prompts"], "active_days_28d": month["active_days"],
        "evidence_28d": month["evidence"],
        "estimate": ESTIMATE + "; subfolders count towards the folder they are in",
    }


def cached_events(settings: Settings | None = None, *, days: int = 28) -> Events:
    """``load_events`` takes seconds over a month; repeat callers in the next few minutes share one load."""
    s = settings or get_settings()
    key = (str(s.db_path), days)
    hit = _events_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    ev = load_events(s, days=days)
    _events_cache.clear()
    _events_cache[key] = (time.monotonic() + _EVENTS_TTL_S, ev)
    return ev


def project_patterns(name: str, settings: Settings | None = None, *, days: int = 28,
                     events: Events | None = None) -> dict:
    """What you keep asking for in one project (with its subfolders): the ask candidates of its prompts."""
    ev = events or cached_events(settings, days=days)
    key = project_key(name)
    asks = [a for a in ev.asks if project_key(a.project) == key or _within(_path_parts(a.cwd or "") or (), key)]
    report = mine(Events(ev.start, ev.end, [], [], [], asks, ev.focus_source))
    names = Counter(a.project for a in asks if project_key(a.project) == key)
    return {
        "project": names.most_common(1)[0][0] if names else name,
        "window": {"start": ev.start.isoformat(), "end": ev.end.isoformat()},
        "prompts": len(asks),
        "note": "From your prompts to Claude Code in this project; loops, habits and copies are not per project.",
        "candidates": report["candidates"],
    }
