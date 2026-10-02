"""The foreground window's context on Windows (the ``retrace-context`` equivalent).

Returns the same JSON shape as the macOS helper. On-screen text is left to the
pipeline's OCR step (Windows OCR is fast and works for every app), so ``text``
is empty here; browsers additionally get their URL via UI Automation.
"""

from __future__ import annotations

import logging

from . import _win32 as w
from .apps import window_owner
from .browser import is_browser, title_says_private

log = logging.getLogger("retrace.native.win.context")


def read_context(*, fetch_url: bool = True, **_ignored) -> dict:
    """Foreground app + window context. Page text/HTML capture is macOS-only."""
    try:
        hwnd = w.GetForegroundWindow()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    if not hwnd:
        # Nothing focused (e.g. the desktop mid-switch): same answer as macOS.
        return {"ok": True, "app_name": None, "bundle_id": None, "text": "",
                "text_source": "none", "ax_trusted": True, "private_browsing": False}

    owner = window_owner(hwnd)
    if is_browser(owner.app_id):
        # A focused popup (translate bubble, permission prompt) belongs to a browser
        # window; judge that window, not the popup, or a private window behind it
        # would be captured.
        hwnd = w.GetAncestor(hwnd, w.GA_ROOTOWNER) or hwnd
    title = w.window_text(hwnd) or None
    url = None
    private = title_says_private(owner.app_id, title)

    if is_browser(owner.app_id) and not private:  # already private: it's skipped anyway
        from .uia import browser_snapshot

        snap = browser_snapshot(hwnd)
        url = snap.get("url") if fetch_url else None
        # Chrome marks incognito only on the toolbar's profile button.
        private = bool(snap.get("private")) or title_says_private(owner.app_id, snap.get("accessible_title"))

    return {
        "ok": True,
        "app_name": owner.app_name,
        "bundle_id": owner.app_id,
        "pid": owner.pid,
        "window_title": title,
        "url": url,
        "doc_path": None,
        "text": "",
        "text_source": "none",
        "ax_trusted": True,
        "private_browsing": private,
        "page_text": None,
        "page_html": None,
    }
