"""Clipboard plugin — log clipboard text changes (poll).

Privacy: skips anything matching your sensitive keywords, and the whole plugin
respects capture being enabled / not in Hidden mode (the daemon gates poll()).
Turn it off with ``log_clipboard = false`` or by disabling the plugin.
"""

from __future__ import annotations

import hashlib
import subprocess

from ...config import Settings
from ...db import session_scope
from ...models import Capture, utcnow
from ..base import RetracePlugin

BUNDLE = "com.apple.clipboard"
_MAX = 2000


class ClipboardPlugin(RetracePlugin):
    name = "clipboard"
    description = "Log clipboard text changes (sensitive content skipped)."

    def __init__(self) -> None:
        self._last: str | None = None

    def poll(self, settings: Settings) -> None:
        if not getattr(settings, "log_clipboard", True):
            return
        try:
            out = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return
        txt = out.stdout or ""
        if not txt.strip() or txt == self._last:
            return
        self._last = txt

        low = txt.lower()
        for kw in settings.sensitive_keywords:
            if kw and kw.lower() in low:
                return  # looks sensitive; don't store

        snippet = txt[:_MAX]
        chash = hashlib.sha256(("clip:" + snippet).encode("utf-8", "replace")).hexdigest()
        with session_scope(settings) as s:
            if s.query(Capture).filter(Capture.content_hash == chash).first():
                return
            s.add(Capture(
                captured_at=utcnow(), app_name="Clipboard", bundle_id=BUNDLE,
                window_title="Copied", text=snippet, text_len=len(snippet),
                text_source="plugin", caption="📋 " + " ".join(snippet.split())[:70],
                caption_model="clipboard", content_hash=chash,
            ))
