"""Deduplication helpers and bounded-retention purge.

- ``content_hash`` / ``is_duplicate`` back the "skip unchanged screens" logic.
- ``purge_older_than`` enforces the retention window: it deletes capture rows
  (cascading embeddings + FTS) and removes their thumbnails from disk.
- ``clean_tmp`` removes any stray transient frames on startup.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import ActivityEvent, Capture
from ..models import utcnow

log = logging.getLogger("retrace.retention")

_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Collapse whitespace so trivial layout churn doesn't defeat dedup."""
    return _WS_RE.sub(" ", (text or "").strip())


def content_hash(text: str, app: str | None, window: str | None) -> str:
    """Stable hash of (app, window, normalized text) for dedup. Not a secret."""
    payload = "\x00".join([(app or ""), (window or ""), normalize_text(text)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_duplicate(ledger, content_hash_value: str, window_s: float) -> bool:
    """True if this content matches the last stored hash within ``window_s``."""
    return ledger.last_hash_within(content_hash_value, window_s)


def clean_tmp(settings: Settings | None = None) -> int:
    """Delete any leftover transient frames in ``~/.retrace/tmp``. Returns count."""
    s = settings or get_settings()
    if not s.tmp_dir.exists():
        return 0
    n = 0
    for p in s.tmp_dir.iterdir():
        if p.is_file():
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n


def purge_older_than(days: int, settings: Settings | None = None) -> dict:
    """Delete captures + thumbnails (and stale activity rows) older than ``days``."""
    s = settings or get_settings()
    cutoff = utcnow() - timedelta(days=days)

    thumb_rels: list[str] = []
    captures_deleted = 0
    activity_deleted = 0

    with session_scope(s) as session:
        rows = session.execute(
            select(Capture).where(Capture.captured_at < cutoff)
        ).scalars().all()
        for row in rows:
            if row.thumb_path:
                thumb_rels.append(row.thumb_path)
            session.delete(row)  # cascades embeddings + fires FTS delete trigger
        captures_deleted = len(rows)

        activity_deleted = session.execute(
            delete(ActivityEvent).where(ActivityEvent.start_at < cutoff)
        ).rowcount or 0

    thumbs_deleted = 0
    for rel in thumb_rels:
        path = _resolve_thumb(rel, s)
        try:
            if path.exists():
                path.unlink()
                thumbs_deleted += 1
        except OSError:
            pass

    _prune_empty_day_dirs(s)

    result = {
        "cutoff": cutoff.isoformat(),
        "days": days,
        "captures_deleted": captures_deleted,
        "thumbs_deleted": thumbs_deleted,
        "activity_deleted": activity_deleted,
    }
    log.info("retention purge: %s", result)
    return result


def _resolve_thumb(rel_or_abs: str, settings: Settings) -> Path:
    p = Path(rel_or_abs)
    return p if p.is_absolute() else settings.thumbs_dir / rel_or_abs


def _prune_empty_day_dirs(settings: Settings) -> None:
    if not settings.thumbs_dir.exists():
        return
    for day_dir in settings.thumbs_dir.iterdir():
        if day_dir.is_dir():
            try:
                next(day_dir.iterdir())
            except StopIteration:
                try:
                    day_dir.rmdir()
                except OSError:
                    pass
