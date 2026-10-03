"""Replay a stretch of time as ordered steps: the raw material for capturing a workflow.

Consecutive screen captures of the same thing (one site, one terminal session,
one app) fold into a single step with how long it lasted, the window titles,
URLs and documents seen. Clipboard copies, prompts typed to AI tools and plugin
events (commits, downloads, mail, calendar) sit between them in time order.

Text (what was copied or typed, captions, on-screen text) is only included with
``include_text``; without it a step says what kind of thing happened and how big.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

from ..config import Settings, get_settings
from .events import (
    CLAUDE_CODE_BUNDLE, CLIPBOARD_BUNDLE, SCREEN_SOURCES, _local, clean_title, context_label, typed_prompt,
)
from .patterns import clip_kind

_FOLD_GAP_S = 120  # captures of the same thing within 2 minutes belong to one step


def _utc_naive(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def steps(
    settings: Settings | None = None,
    *,
    start: datetime,
    end: datetime,
    keep: Callable[[int], bool] | None = None,
    include_text: bool = False,
    max_steps: int = 300,
) -> dict:
    s = settings or get_settings()
    passes = keep or (lambda _cid: True)
    conn = sqlite3.connect(f"file:{s.db_path}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute(
            "SELECT id, captured_at, app_name, bundle_id, window_title, url, doc_path, text, text_len, "
            "text_source, caption FROM captures WHERE captured_at >= ? AND captured_at < ? ORDER BY captured_at",
            (_utc_naive(start), _utc_naive(end)),
        ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for cid, at, app, bundle, title, url, doc, text, text_len, source, caption in rows:
        if not passes(cid):
            continue
        t = _local(at)
        if source in SCREEN_SOURCES:
            ctx = context_label(app, bundle, title, url)
            last = out[-1] if out else None
            if (last and last["type"] == "screen" and last["context"] == ctx
                    and (t - datetime.fromisoformat(last["end"])).total_seconds() <= _FOLD_GAP_S):
                last["end"] = t.isoformat()
                last["captures"] += 1
                _add(last, "titles", clean_title(title))
                _add(last, "urls", url)
                _add(last, "documents", doc)
                if include_text and text and len(last.get("text_sample", "")) < 600:
                    last["text_sample"] = (last.get("text_sample", "") + " " + " ".join(text.split())[:300]).strip()
                continue
            step = {"type": "screen", "start": t.isoformat(), "end": t.isoformat(), "app": app,
                    "context": ctx, "captures": 1, "titles": [], "urls": [], "documents": []}
            _add(step, "titles", clean_title(title))
            _add(step, "urls", url)
            _add(step, "documents", doc)
            if include_text and text:
                step["text_sample"] = " ".join(text.split())[:300]
            out.append(step)
        elif bundle == CLIPBOARD_BUNDLE:
            item = {"type": "copy", "at": t.isoformat(), "kind": clip_kind(text or ""), "chars": len(text or "")}
            if include_text:
                item["text"] = (text or "")[:500]
            out.append(item)
        elif bundle == CLAUDE_CODE_BUNDLE:
            prompt = typed_prompt(text)
            if not prompt:
                continue
            item = {"type": "ask", "at": t.isoformat(), "assistant": "Claude Code", "project": title,
                    "session": caption, "words": len(prompt.split())}
            if include_text:
                item["prompt"] = prompt[:800]
            out.append(item)
        else:
            item = {"type": "event", "at": t.isoformat(), "app": app, "source": source}
            if include_text and caption:
                item["caption"] = caption[:200]
            out.append(item)

    for st in out:
        if st["type"] == "screen":
            secs = (datetime.fromisoformat(st["end"]) - datetime.fromisoformat(st["start"])).total_seconds()
            st["minutes"] = round(secs / 60, 1)
    truncated = len(out) > max_steps
    out = out[:max_steps]
    screens = [st for st in out if st["type"] == "screen"]
    return {
        "window": {"start": _local(start).isoformat(), "end": _local(end).isoformat()},
        "steps": out,
        "truncated": truncated,
        "apps": sorted({st["app"] for st in screens if st.get("app")}),
        "counts": {k: sum(1 for st in out if st["type"] == k) for k in ("screen", "copy", "ask", "event")},
    }


def _add(step: dict, key: str, value) -> None:
    if value and value not in step[key] and len(step[key]) < 6:
        step[key].append(value)
