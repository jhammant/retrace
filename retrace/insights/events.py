"""Load one timeline of what happened, from the Retrace database (read-only).

- **Spans**: periods of focus on one app, labelled with what it was showing (the
  site in a browser, the session in a terminal). From the macOS focus log
  (knowledgeC) when present, else stitched together from screen captures.
- **Visits**: browser history.
- **Clips**: clipboard copies, with the context copied from and the one switched to next.
- **Asks**: prompts you typed to AI coding tools (Claude Code transcripts), with
  machine-generated prompts (sub-agents, resumed-session summaries) left out.

``keep`` limits everything to captures that pass it (for example one workspace's
captures); events with no capture nearby to judge by are then left out too.
"""

from __future__ import annotations

import bisect
import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from ..config import Settings, get_settings

SCREEN_SOURCES = ("accessibility", "ocr", "mixed", "page", "none")
BROWSER_SOURCES = ("safari", "chrome", "edge", "brave")
CLIPBOARD_BUNDLE = "com.apple.clipboard"
CLAUDE_CODE_BUNDLE = "com.anthropic.claude-code"

TERMINALS = {
    "com.googlecode.iterm2", "com.apple.Terminal", "dev.warp.Warp-Stable", "com.mitchellh.ghostty",
    "net.kovidgoyal.kitty", "io.alacritty", "windowsterminal.exe", "wezterm-gui.exe",
}
BROWSERS = {
    "com.apple.Safari", "com.google.Chrome", "com.google.Chrome.canary", "com.brave.Browser",
    "com.microsoft.edgemac", "com.vivaldi.Vivaldi", "company.thebrowser.Browser", "org.mozilla.firefox",
    "chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "vivaldi.exe", "arc.exe",
}
_SHELL_TITLES = {"", "zsh", "-zsh", "bash", "-bash", "fish", "sh", "powershell", "pwsh", "cmd"}
# Status glyphs terminals and agents prefix to titles ("◐ Orbital meeting prep").
_TITLE_NOISE = re.compile(r"^[\s◐◑◒◓◔◕✳✶✻✽✢·•*⠀-⣿⏺●○]+")
_DOCS_KINDS = ("presentation", "document", "spreadsheets", "forms")

# Prompts that a tool, not a person, wrote into a Claude Code session.
_MACHINE_PROMPT = re.compile(
    r"^(you are |this session is being continued|another claude session|# |\[cross-session|"
    r"important, headless|read task\.md|\(re-invocation|base directory for this skill|caveat:|<|"
    r"\[request interrupted|\[image: original|\[image #\d+\]\s*$)",
    re.IGNORECASE,
)

MERGE_GAP_S = 60       # same app again within a minute: one span
MIN_SPAN_S = 3         # shorter focus blips are noise (alt-tab passing through)
NEXT_SWITCH_S = 300    # a copy followed by a switch within 5 min is treated as a carry
NEAREST_LABEL_S = 90   # a span with no capture of its own borrows one this close


@dataclass
class Span:
    start: datetime
    end: datetime
    app_id: str
    app: str
    context: str
    capture_ids: list[int] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())


@dataclass
class Visit:
    at: datetime
    url: str
    title: str | None


@dataclass
class Clip:
    at: datetime
    text: str
    source: str | None
    dest: str | None


@dataclass
class Ask:
    at: datetime
    project: str | None
    session: str | None
    prompt: str


@dataclass
class Events:
    start: datetime
    end: datetime
    spans: list[Span]
    visits: list[Visit]
    clips: list[Clip]
    asks: list[Ask]
    focus_source: str            # "knowledgec" or "captures"

    @property
    def days(self) -> int:
        return max(1, len({s.start.date() for s in self.spans} | {a.at.date() for a in self.asks}))


# --- helpers ---------------------------------------------------------------------

def _local(value) -> datetime:
    """Stored timestamps are naive UTC; work in local time (days, hours of the day)."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone()


def _connect(settings: Settings) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True, timeout=10)


def site_label(url: str | None) -> str | None:
    """"docs.google.com/presentation", "github.com", "localhost:8766"; None for non-web URLs."""
    if not url:
        return None
    try:
        p = urlparse(url)
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None
    host = (p.netloc or "").lower().removeprefix("www.")
    if not host:
        return None
    if host == "docs.google.com":
        kind = next((k for k in _DOCS_KINDS if p.path.startswith(f"/{k}")), None)
        return f"{host}/{kind}" if kind else host
    return host


def clean_title(title: str | None) -> str:
    return _TITLE_NOISE.sub("", title or "").strip()


def context_label(app: str | None, bundle: str | None, title: str | None, url: str | None) -> str:
    """What an app was being used for: the site in a browser, the session in a terminal."""
    name = app or bundle or "?"
    if bundle in BROWSERS or url:
        site = site_label(url)
        if site:
            return f"{name} · {site}"
    if bundle in TERMINALS:
        t = clean_title(title)
        return f"{name} · {'shell' if t.lower() in _SHELL_TITLES else t[:60]}"
    return name


def is_machine_prompt(prompt: str) -> bool:
    return bool(_MACHINE_PROMPT.match(prompt.strip()))


def typed_prompt(text: str | None) -> str | None:
    """The person's own words from a Claude Code transcript row ("You: ...\\n\\nClaude: ...")."""
    m = re.match(r"You: (.*?)(?:\n\nClaude:|$)", text or "", re.S)
    if not m:
        return None
    p = m.group(1).strip()
    # A pasted screenshot leads with its own metadata; the ask follows it.
    p = re.sub(r"^(\[Image: original[^\]]*\]\s*|\[Image #\d+\]\s*)+", "", p).strip()
    if len(p) < 2 or is_machine_prompt(p):
        return None
    return p


# --- loading ---------------------------------------------------------------------

def load_events(
    settings: Settings | None = None,
    *,
    days: int = 28,
    end: datetime | None = None,
    keep: Callable[[int], bool] | None = None,
) -> Events:
    """Read the last ``days`` days (to ``end``, default now) into one timeline."""
    s = settings or get_settings()
    end_local = _local(end) if end else datetime.now().astimezone()
    start_local = end_local - timedelta(days=days)
    lo = start_local.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    hi = end_local.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    passes = keep or (lambda _cid: True)

    conn = _connect(s)
    try:
        marks = ",".join("?" * len(SCREEN_SOURCES))
        screen = [
            (_local(at), cid, app, bundle, title, url)
            for cid, at, app, bundle, title, url in conn.execute(
                f"SELECT id, captured_at, app_name, bundle_id, window_title, url FROM captures "
                f"WHERE text_source IN ({marks}) AND captured_at >= ? AND captured_at < ? ORDER BY captured_at",
                (*SCREEN_SOURCES, lo, hi),
            )
        ]
        names = dict(conn.execute(
            "SELECT bundle_id, app_name FROM captures WHERE bundle_id IS NOT NULL AND app_name IS NOT NULL "
            "GROUP BY bundle_id"
        ).fetchall())
        focus = conn.execute(
            "SELECT app, start_at, end_at FROM activity_events WHERE source = 'knowledgec' "
            "AND start_at >= ? AND start_at < ? ORDER BY start_at", (lo, hi),
        ).fetchall()
        vmarks = ",".join("?" * len(BROWSER_SOURCES))
        visit_rows = conn.execute(
            f"SELECT start_at, url, title FROM activity_events WHERE source IN ({vmarks}) "
            f"AND start_at >= ? AND start_at < ? ORDER BY start_at", (*BROWSER_SOURCES, lo, hi),
        ).fetchall()
        clip_rows = conn.execute(
            "SELECT id, captured_at, text FROM captures WHERE bundle_id = ? AND captured_at >= ? "
            "AND captured_at < ? ORDER BY captured_at", (CLIPBOARD_BUNDLE, lo, hi),
        ).fetchall()
        ask_rows = conn.execute(
            "SELECT id, captured_at, window_title, caption, text FROM captures WHERE bundle_id = ? "
            "AND captured_at >= ? AND captured_at < ? ORDER BY captured_at", (CLAUDE_CODE_BUNDLE, lo, hi),
        ).fetchall()
    finally:
        conn.close()

    # Screen captures, in time order, for labelling spans and judging other events.
    cap_times = [c[0] for c in screen]

    def nearest_capture_passes(at: datetime, within_s: float = 60.0) -> bool:
        i = bisect.bisect_left(cap_times, at)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(screen):
                gap = abs((screen[j][0] - at).total_seconds())
                if gap <= within_s and (best is None or gap < best[0]):
                    best = (gap, screen[j][1])
        return best is not None and passes(best[1])

    spans = _spans_from_focus(focus, names) if focus else _spans_from_captures(screen)
    focus_source = "knowledgec" if focus else "captures"
    _label_spans(spans, screen)
    if keep is not None:
        spans = [sp for sp in spans if sp.capture_ids and _majority_passes(sp.capture_ids, passes)]

    visits = [Visit(_local(at), url, title) for at, url, title in visit_rows if url]
    if keep is not None:
        visits = [v for v in visits if nearest_capture_passes(v.at)]

    span_starts = [sp.start for sp in spans]

    def span_at(at: datetime) -> int | None:
        i = bisect.bisect_right(span_starts, at) - 1
        return i if i >= 0 else None

    clips = []
    for cid, at, text in clip_rows:
        if keep is not None and not passes(cid):
            continue
        t = _local(at)
        i = span_at(t)
        src = spans[i].context if i is not None else None
        dest = None
        if i is not None and i + 1 < len(spans):
            nxt = spans[i + 1]
            if (nxt.start - t).total_seconds() <= NEXT_SWITCH_S and nxt.context != src:
                dest = nxt.context
        clips.append(Clip(t, text or "", src, dest))

    asks = []
    for cid, at, project, session, text in ask_rows:
        if keep is not None and not passes(cid):
            continue
        p = typed_prompt(text)
        if p:
            asks.append(Ask(_local(at), project, session, p))

    return Events(start_local, end_local, spans, visits, clips, asks, focus_source)


def _app_name(bundle: str, names: dict[str, str]) -> str:
    if bundle in names:
        return names[bundle].lstrip("‎")  # WhatsApp's name carries a direction mark
    tail = bundle.split(".")[-1]
    return tail[:1].upper() + tail[1:]


def _spans_from_focus(rows, names: dict[str, str]) -> list[Span]:
    spans: list[Span] = []
    for bundle, start, end in rows:
        if not bundle:
            continue
        a = _local(start)
        b = _local(end) if end else a
        if (b - a).total_seconds() < MIN_SPAN_S:
            continue
        if spans and spans[-1].app_id == bundle and (a - spans[-1].end).total_seconds() < MERGE_GAP_S:
            spans[-1].end = max(spans[-1].end, b)
            continue
        name = _app_name(bundle, names)
        spans.append(Span(a, b, bundle, name, name))
    return spans


def _spans_from_captures(screen) -> list[Span]:
    """No focus log (Windows, or no Full Disk Access): consecutive captures of one app."""
    spans: list[Span] = []
    for at, _cid, app, bundle, _title, _url in screen:
        key = bundle or app or "?"
        if spans and spans[-1].app_id == key and (at - spans[-1].end).total_seconds() < 2 * MERGE_GAP_S:
            spans[-1].end = at
            continue
        if spans and (at - spans[-1].end).total_seconds() < 2 * MERGE_GAP_S:
            spans[-1].end = at  # it lasted until this switch
        name = app or key
        spans.append(Span(at, at, key, name, name))
    return spans


def _label_spans(spans: list[Span], screen) -> None:
    """Give each span the context most of its captures show, and their ids."""
    times = [c[0] for c in screen]
    for sp in spans:
        lo = bisect.bisect_left(times, sp.start - timedelta(seconds=5))
        hi = bisect.bisect_right(times, sp.end + timedelta(seconds=5))
        labels: Counter[str] = Counter()
        for at, cid, app, bundle, title, url in screen[lo:hi]:
            if bundle and bundle != sp.app_id:
                continue
            sp.capture_ids.append(cid)
            labels[context_label(app or sp.app, bundle or sp.app_id, title, url)] += 1
        if not labels:
            # Shorter than the capture interval: borrow the nearest capture of this app.
            near = _nearest_same_app(screen, times, sp, NEAREST_LABEL_S)
            if near is not None:
                at, cid, app, bundle, title, url = near
                sp.capture_ids.append(cid)
                labels[context_label(app or sp.app, bundle or sp.app_id, title, url)] += 1
        if labels:
            sp.context = labels.most_common(1)[0][0]


def _nearest_same_app(screen, times, sp: Span, within_s: float):
    lo = bisect.bisect_left(times, sp.start - timedelta(seconds=within_s))
    hi = bisect.bisect_right(times, sp.end + timedelta(seconds=within_s))
    best = None
    for c in screen[lo:hi]:
        if c[3] != sp.app_id:
            continue
        gap = min(abs((c[0] - sp.start).total_seconds()), abs((c[0] - sp.end).total_seconds()))
        if best is None or gap < best[0]:
            best = (gap, c)
    return best[1] if best else None


def _majority_passes(ids: list[int], passes: Callable[[int], bool]) -> bool:
    yes = sum(1 for i in ids if passes(i))
    return yes * 2 > len(ids)
