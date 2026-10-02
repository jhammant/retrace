"""Which operating system Retrace is running on, plus the per-OS conventions shared
across modules.

macOS uses the compiled Swift helpers under ``retrace/native/swift``. Windows uses
the pure-Python backends under ``retrace/native/win`` (Win32 via ctypes, Windows
OCR via WinRT, UI Automation via comtypes). Everything above the native layer is
the same on both.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
PLATFORM = "macos" if IS_MACOS else "windows" if IS_WINDOWS else sys.platform


def no_window() -> dict:
    """``subprocess`` kwargs that stop a console window flashing up on Windows.

    Without this, every ``git``/``python`` child spawned from a windowless
    (``pythonw``) Retrace pops a console. A no-op elsewhere.
    """
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def detached() -> dict:
    """``subprocess.Popen`` kwargs for a background process that outlives its parent."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def windows_app_id(exe_path: str | None) -> str | None:
    """The app identifier Retrace uses on Windows: the executable's file name, lowercased.

    It plays the role of a macOS bundle id (denylist matching, plugin enrichment,
    per-app stats), e.g. ``chrome.exe`` or ``1password.exe``.
    """
    if not exe_path:
        return None
    return Path(exe_path.replace("\\", "/")).name.lower() or None


def local_appdata() -> Path:
    """``%LOCALAPPDATA%`` (falls back to the conventional location)."""
    import os

    env = os.environ.get("LOCALAPPDATA")
    return Path(env) if env else Path.home() / "AppData" / "Local"


def roaming_appdata() -> Path:
    """``%APPDATA%`` (falls back to the conventional location)."""
    import os

    env = os.environ.get("APPDATA")
    return Path(env) if env else Path.home() / "AppData" / "Roaming"
