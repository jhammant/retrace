"""Best-effort permission/capability detection with human-readable guidance.

Uses non-prompting preflight checks where possible (Screen Recording,
Accessibility via the ``retrace-present`` helper; Full Disk Access by attempting
to read knowledgeC.db). Calendar/Automation are reported as ``unknown`` until
exercised, since macOS grants them lazily on first use.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..platform import IS_WINDOWS
from .helpers import get_presence

GRANTED = "granted"
DENIED = "denied"
UNKNOWN = "unknown"

_KNOWLEDGEC = Path.home() / "Library" / "Application Support" / "Knowledge" / "knowledgeC.db"

_GUIDANCE = {
    "screen_recording": "System Settings → Privacy & Security → Screen & System Audio Recording → enable your terminal/Retrace.",
    "accessibility": "System Settings → Privacy & Security → Accessibility → enable your terminal/Retrace.",
    "automation": "System Settings → Privacy & Security → Automation → allow control of Safari/Chrome (prompted on first use).",
    "calendar": "System Settings → Privacy & Security → Calendars → enable your terminal/Retrace.",
    "full_disk_access": "System Settings → Privacy & Security → Full Disk Access → enable your terminal/Retrace (needed to read knowledgeC.db focus history).",
    "swift_toolchain": "Install the Xcode command line tools: xcode-select --install.",
    # Windows
    "screen_capture": "Retrace captures the primary display of the signed-in desktop; run it in your own session, not as a service.",
    "ocr_language": "Settings > Time & language > Language & region > add a language (its OCR pack installs with it).",
    "ui_automation": "Reinstall retrace-cli on Windows (it pulls in comtypes) to read browser URLs.",
    "tray": "Reinstall retrace-cli on Windows (it pulls in pystray) for the tray icon.",
}


def _entry(state: str, required: bool, key: str, detail: str = "") -> dict[str, Any]:
    return {
        "state": state,
        "required": required,
        "guidance": _GUIDANCE.get(key, ""),
        "detail": detail,
    }


def _full_disk_access() -> str:
    if not _KNOWLEDGEC.exists():
        return UNKNOWN
    try:
        with open(_KNOWLEDGEC, "rb") as fh:
            fh.read(16)
        return GRANTED
    except PermissionError:
        return DENIED
    except OSError:
        return UNKNOWN


def _check_windows(s: Settings) -> dict[str, Any]:
    """Windows grants desktop apps screen capture and UI Automation without prompts;
    what can be missing is an OCR language pack or an optional package."""
    pres = get_presence(s.idle_threshold_s, settings=s) or {}
    try:
        from .win.ocr import available as ocr_available

        ocr_ok, ocr_detail = ocr_available()
    except Exception as exc:
        ocr_ok, ocr_detail = False, str(exc)
    try:
        from .win.uia import available as uia_available

        uia_ok = uia_available()
    except Exception:
        uia_ok = False
    try:
        import pystray  # noqa: F401

        tray_ok = True
    except Exception:
        tray_ok = False
    return {
        "screen_capture": _entry(
            GRANTED if pres.get("ok") else UNKNOWN, required=True, key="screen_capture",
            detail="No permission needed on Windows.",
        ),
        "ocr_language": _entry(
            GRANTED if ocr_ok else DENIED, required=True, key="ocr_language",
            detail=f"Windows OCR reads on-screen text ({ocr_detail}).",
        ),
        "ui_automation": _entry(
            GRANTED if uia_ok else DENIED, required=False, key="ui_automation",
            detail="Reads browser URLs and private-window state.",
        ),
        "tray": _entry(
            GRANTED if tray_ok else DENIED, required=False, key="tray",
            detail="Notification-area icon (retrace menubar).",
        ),
    }


def check_all(settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    if IS_WINDOWS:
        return _check_windows(s)

    pres = get_presence(s.idle_threshold_s, settings=s) or {}
    sr = pres.get("screen_recording")
    ax = pres.get("accessibility")

    swift = GRANTED if shutil.which("swiftc") else DENIED

    return {
        "screen_recording": _entry(
            GRANTED if sr is True else DENIED if sr is False else UNKNOWN,
            required=True, key="screen_recording",
        ),
        "accessibility": _entry(
            GRANTED if ax is True else DENIED if ax is False else UNKNOWN,
            required=True, key="accessibility",
            detail="Enables reading real on-screen text, window titles, and URLs.",
        ),
        "automation": _entry(
            UNKNOWN, required=False, key="automation",
            detail="Grants browser URL/incognito detection (granted lazily).",
        ),
        "calendar": _entry(
            UNKNOWN, required=False, key="calendar",
            detail="Correlates captures with calendar events.",
        ),
        "full_disk_access": _entry(
            _full_disk_access(), required=False, key="full_disk_access",
            detail="Reads knowledgeC.db focus history for time analytics.",
        ),
        "swift_toolchain": _entry(
            swift, required=True, key="swift_toolchain",
            detail="Needed to compile the native helper binaries.",
        ),
    }
