"""Native (Foundation Models) caption path.

Until the ``retrace-caption`` Swift helper exists (Milestone 8) this returns
``None`` so callers fall back to the template caption. The logic is written so
that simply adding the helper source turns this on with no further changes.
"""

from __future__ import annotations

import json
import logging

from ..config import Settings, get_settings
from ..native.helpers import get_helper

log = logging.getLogger("retrace.caption")

_MAX_TEXT = 4000


def native_caption(
    *,
    app: str | None,
    window: str | None,
    url: str | None,
    text: str,
    settings: Settings | None = None,
) -> str | None:
    """Ask the on-device LLM for a 1-2 sentence caption. ``None`` if unavailable."""
    s = settings or get_settings()
    helper = get_helper("retrace-caption", s)
    if not helper.source_exists():
        return None

    payload = {
        "app": app or "",
        "window": window or "",
        "url": url or "",
        "text": (text or "")[:_MAX_TEXT],
    }
    result = helper.run([json.dumps(payload)], timeout=30.0)
    if not result or not result.get("ok"):
        return None
    caption = (result.get("caption") or "").strip()
    return caption or None
