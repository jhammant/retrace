"""Seed a Retrace home with believable FAKE data for screenshots / the demo video.

Usage:  RETRACE_HOME=~/.retrace-demo uv run python scripts/seed_demo.py

Generates fake captures (with gradient placeholder thumbnails), activity/time
analytics, a system-load curve, now-playing tracks, notifications, git commits,
and calendar events. No real data anywhere.
"""

from __future__ import annotations

import hashlib
import math
import os
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

from retrace.config import get_settings
from retrace.db import init_db, session_scope
from retrace.models import ActivityEvent, Capture

os.environ.setdefault("RETRACE_HOME", os.path.expanduser("~/.retrace-demo"))
S = get_settings()
S.ensure_dirs()
init_db(S)

NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _font(size):
    for path in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _hue_rgb(h, s=0.55, v=0.7):
    import colorsys
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def make_thumb(path, app, hue):
    """A tasteful gradient 'screenshot' mock — no real content."""
    W, H = 1280, 800
    top, bot = _hue_rgb(hue, 0.5, 0.32), _hue_rgb(hue + 25, 0.45, 0.12)
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / H
        px_row = tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3))
        for x in range(W):
            px[x, y] = px_row
    d = ImageDraw.Draw(img)
    # mock window
    d.rounded_rectangle([90, 110, W - 90, H - 80], radius=18, fill=_hue_rgb(hue, 0.18, 0.95))
    d.rounded_rectangle([90, 110, W - 90, 170], radius=18, fill=_hue_rgb(hue, 0.3, 0.85))
    for i, cx in enumerate((120, 150, 180)):
        d.ellipse([cx - 7, 132, cx + 7, 146], fill=(230, 90, 80) if i == 0 else (240, 200, 90) if i == 1 else (120, 210, 120))
    # mock content blocks
    for r in range(7):
        y = 210 + r * 72
        d.rounded_rectangle([130, y, 130 + (520 - r * 40), y + 34], radius=8, fill=_hue_rgb(hue + r * 8, 0.25, 0.8))
    d.rounded_rectangle([720, 210, W - 130, H - 120], radius=12, fill=_hue_rgb(hue + 40, 0.2, 0.9))
    d.text((110, H - 64), app, font=_font(40), fill=(255, 255, 255))
    img.save(path, "JPEG", quality=82)


# --- fake captures ---------------------------------------------------------
APPS = [
    ("Arc", "company.thebrowser.Browser", 12),
    ("Figma", "com.figma.Desktop", 200),
    ("VS Code", "com.microsoft.VSCode", 210),
    ("Notion", "notion.id", 270),
    ("Slack", "com.tinyspeck.slackmacgap", 320),
    ("Linear", "com.linear", 250),
    ("Terminal", "com.apple.Terminal", 150),
    ("Spotify", "com.spotify.client", 140),
]

CAPTURES = [
    ("Arc", "Acme — Pricing page", "https://acme.example/pricing",
     "Reviewing the Acme pricing page, comparing the Pro and Team tiers.", 12),
    ("Figma", "Onboarding flow — v3", None,
     "Designing the onboarding flow in Figma, refining the empty-state screen.", 200),
    ("VS Code", "auth.py — widget-api", None,
     "Editing auth.py in the widget-api project, adding token refresh handling.", 210),
    ("Notion", "Q3 roadmap", "https://notion.so/q3-roadmap",
     "Writing the Q3 roadmap doc in Notion, outlining the launch milestones.", 270),
    ("Slack", "#engineering", None,
     "Reading the #engineering channel in Slack about the deploy window.", 320),
    ("Linear", "WIDG-241 — Fix timeline paging", "https://linear.app/widget/WIDG-241",
     "Triaging issue WIDG-241 in Linear about timeline pagination.", 250),
    ("Arc", "Hacker News — front page", "https://news.ycombinator.com",
     "Skimming the Hacker News front page over coffee.", 12),
    ("Terminal", "zsh — make build", None,
     "Running the build in Terminal and watching the test suite pass.", 150),
    ("VS Code", "charts.tsx — dashboard", None,
     "Building the dashboard charts component in VS Code.", 215),
    ("Figma", "Brand palette", None,
     "Tweaking the brand palette in Figma — warm ember + cool teal.", 195),
]


def seed_captures():
    rows = []
    t = NOW - timedelta(minutes=4)
    for i in range(28):
        app, window, url, caption, hue = CAPTURES[i % len(CAPTURES)]
        bundle = dict((a, b) for a, b, _ in APPS).get(app, "com.example.app")
        uid = hashlib.md5(f"{i}{app}".encode()).hexdigest()
        day = t.strftime("%Y-%m-%d")
        thumb_dir = S.thumb_dir_for_day(day)
        thumb = thumb_dir / f"{uid}.jpg"
        make_thumb(thumb, app, hue)
        text = f"{caption}\n\n(demo capture — extracted on-screen text would appear here, fully searchable.)"
        rows.append(Capture(
            captured_at=t, app_name=app, bundle_id=bundle, window_title=window, url=url,
            text=text, text_len=len(text), text_source="accessibility" if i % 3 else "mixed",
            caption=caption, caption_model="apple-foundation-models",
            content_hash=f"demo-{uid}", thumb_path=f"{day}/{uid}.jpg",
        ))
        t -= timedelta(minutes=7 + (i % 5) * 3)
    # a few plugin entries
    extras = [
        ("Spotify", "com.spotify.client", "Midnight City — M83", "🎵 Midnight City — M83", "plugin"),
        ("Git", "com.apple.git", "fix: timeline paging off-by-one\n\nwidget-api · a1b9f3c2 · you",
         "⎇ widget-api: fix timeline paging off-by-one", "plugin"),
        ("Calendar", "com.apple.iCal", "Design review\n@ Zoom", "📅 Design review", "plugin"),
        ("Slack", "com.apple.notificationcenterui", "Notification from Slack", "📣 Slack notification", "plugin"),
    ]
    tt = NOW - timedelta(hours=2, minutes=20)
    for app, bundle, text, cap, src in extras:
        rows.append(Capture(captured_at=tt, app_name=app, bundle_id=bundle, window_title=app,
                            text=text, text_len=len(text), text_source=src, caption=cap,
                            caption_model="plugin", content_hash=f"demo-extra-{cap[:6]}"))
        tt -= timedelta(minutes=11)
    with session_scope(S) as s:
        for r in rows:
            s.add(r)
    return len(rows)


def seed_activity():
    rows = []
    # per-app focus time over the last 7 days (knowledgec)
    app_hours = {"com.microsoft.VSCode": 18, "company.thebrowser.Browser": 11, "com.figma.Desktop": 7,
                 "com.tinyspeck.slackmacgap": 5, "notion.id": 4, "com.spotify.client": 6,
                 "com.apple.Terminal": 9, "com.linear": 3}
    for bundle, hrs in app_hours.items():
        secs = hrs * 3600
        chunks = 40
        for c in range(chunks):
            start = NOW - timedelta(days=7) + timedelta(seconds=c * (7 * 86400 / chunks))
            rows.append(ActivityEvent(source="knowledgec", app=bundle, url="", start_at=start,
                        end_at=start + timedelta(seconds=secs / chunks), seconds=secs / chunks,
                        day=start.strftime("%Y-%m-%d")))
    # domains (chrome/safari visits)
    domains = {"github.com": 142, "figma.com": 98, "news.ycombinator.com": 76, "linear.app": 64,
               "notion.so": 58, "stripe.com": 41, "acme.example": 33, "vercel.com": 27}
    for dom, visits in domains.items():
        for v in range(visits):
            start = NOW - timedelta(days=7) + timedelta(seconds=v * 400)
            rows.append(ActivityEvent(source="chrome", app="com.google.Chrome",
                        url=f"https://{dom}/page/{v}", start_at=start, end_at=None, seconds=0.0,
                        day=start.strftime("%Y-%m-%d")))
    # active samples today so per-app time is fresh
    for c in range(120):
        start = NOW - timedelta(hours=8) + timedelta(minutes=c * 4)
        app = list(app_hours.keys())[c % len(app_hours)]
        rows.append(ActivityEvent(source="active", app=app, url="", start_at=start,
                    end_at=start + timedelta(minutes=4), seconds=240.0, day=start.strftime("%Y-%m-%d")))
    # system load curve today
    from retrace.plugins.builtin.system_stats import _local_day
    for i in range(90):
        t = NOW - timedelta(hours=7) + timedelta(minutes=i * 4.6)
        cpu = 14 + 16 * abs(math.sin(i / 6.0)) + (34 if i % 19 == 0 else 0)
        mem = 44 + 8 * math.sin(i / 9.0)
        rows.append(ActivityEvent(source="system", app="system", url="", start_at=t, end_at=t,
                    seconds=0.0, day=_local_day(t),
                    detail={"cpu_percent": round(min(cpu, 98), 1), "mem_percent": round(mem, 1),
                            "load_1m": round(1.4 + abs(math.sin(i / 7.0)) * 2, 2)}))
    with session_scope(S) as s:
        for r in rows:
            s.add(r)
    return len(rows)


if __name__ == "__main__":
    nc = seed_captures()
    na = seed_activity()
    # build search embeddings would need the helper; skip (text search still works)
    print(f"seeded {nc} captures + {na} activity rows into {S.home}")
