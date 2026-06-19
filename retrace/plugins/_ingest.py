"""Shared helper for collector plugins: bulk-insert captures with dedup."""

from __future__ import annotations

from sqlalchemy import select

from ..config import Settings
from ..db import session_scope
from ..models import Capture


def ingest_captures(settings: Settings, bundle_id: str, rows: list[dict]) -> int:
    """Insert capture rows (dicts) for ``bundle_id``, skipping existing content_hashes.

    Each row needs at least ``content_hash`` and ``captured_at``; other Capture
    fields (app_name, window_title, text, caption, caption_model, url, doc_path)
    are optional. ``text_source`` defaults to ``"plugin"``.
    """
    if not rows:
        return 0
    with session_scope(settings) as s:
        seen = {
            h for (h,) in s.execute(
                select(Capture.content_hash).where(Capture.bundle_id == bundle_id)
            ).all() if h
        }
        n = 0
        for r in rows:
            ch = r.get("content_hash")
            if not ch or ch in seen:
                continue
            seen.add(ch)
            text = r.get("text", "") or ""
            s.add(Capture(
                captured_at=r["captured_at"], app_name=r.get("app_name"),
                bundle_id=bundle_id, window_title=r.get("window_title"),
                url=r.get("url"), doc_path=r.get("doc_path"),
                text=text, text_len=len(text), text_source=r.get("text_source", "plugin"),
                caption=r.get("caption"), caption_model=r.get("caption_model"),
                content_hash=ch,
            ))
            n += 1
    return n
