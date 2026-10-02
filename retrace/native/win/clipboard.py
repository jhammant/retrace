"""Read clipboard text on Windows, honouring the "don't record this" markers.

Password managers (KeePass, 1Password, Bitwarden, ...) tag copied secrets with
standard clipboard formats asking monitors and clipboard history to look away.
Those copies read as None here, so they are never stored.
"""

from __future__ import annotations

import ctypes
import time

from . import _win32 as w

# Formats an app adds to mark clipboard content as private.
_PRIVATE_MARKERS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")


def _marked_private() -> bool:
    for name in _PRIVATE_MARKERS:
        fmt = w.RegisterClipboardFormatW(name)
        if fmt and w.IsClipboardFormatAvailable(fmt):
            return True
    # "CanIncludeInClipboardHistory" = DWORD 0 also means "keep this out".
    fmt = w.RegisterClipboardFormatW("CanIncludeInClipboardHistory")
    if fmt and w.IsClipboardFormatAvailable(fmt):
        h = w.GetClipboardData(fmt)
        p = w.GlobalLock(h) if h else None
        if p:
            try:
                return ctypes.c_uint32.from_address(p).value == 0
            finally:
                w.GlobalUnlock(h)
    return False


def read_text() -> str | None:
    """Clipboard text, "" when there is none, None when private or unreadable."""
    for _ in range(5):  # another app may hold the clipboard for a moment
        if w.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return None
    try:
        if _marked_private():
            return None
        if not w.IsClipboardFormatAvailable(w.CF_UNICODETEXT):
            return ""
        h = w.GetClipboardData(w.CF_UNICODETEXT)
        if not h:
            return ""
        p = w.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            w.GlobalUnlock(h)
    finally:
        w.CloseClipboard()
