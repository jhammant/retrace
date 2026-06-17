"""Serialize ORM rows into JSON-friendly dicts for the API + MCP."""

from __future__ import annotations

from datetime import timezone
from typing import Any

from ..models import ActivityEvent, Capture

_SNIPPET = 280


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()


def capture_brief(row: Capture) -> dict[str, Any]:
    """Compact capture for list/timeline views (snippet, not full text)."""
    return {
        "id": row.id,
        "captured_at": _iso(row.captured_at),
        "app_name": row.app_name,
        "bundle_id": row.bundle_id,
        "window_title": row.window_title,
        "url": row.url,
        "caption": row.caption,
        "text_source": row.text_source,
        "text_len": row.text_len,
        "snippet": (row.text or "")[:_SNIPPET],
        "calendar_event": row.calendar_event,
        "has_thumb": bool(row.thumb_path),
        "image_url": f"/capture/{row.id}/image" if row.thumb_path else None,
    }


def capture_full(row: Capture) -> dict[str, Any]:
    """Full capture including the complete extracted text + metadata."""
    data = capture_brief(row)
    data.update(
        {
            "text": row.text,
            "doc_path": row.doc_path,
            "caption_model": row.caption_model,
            "content_hash": row.content_hash,
            "thumb_path": row.thumb_path,
            "created_at": _iso(row.created_at),
        }
    )
    return data


def activity_brief(row: ActivityEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "source": row.source,
        "app": row.app,
        "title": row.title,
        "url": row.url,
        "start_at": _iso(row.start_at),
        "end_at": _iso(row.end_at),
        "seconds": row.seconds,
        "day": row.day,
        "detail": row.detail,
    }
