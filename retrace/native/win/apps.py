"""Who owns a window: process id, executable, app id and a human app name."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ...platform import windows_app_id
from . import _win32 as w

# UWP/Store apps are hosted inside this frame process; the real app is a child window.
_FRAME_HOST = "applicationframehost.exe"


@dataclass(frozen=True)
class WindowOwner:
    pid: int
    exe_path: str | None
    app_id: str | None     # e.g. "chrome.exe" (plays the role of a macOS bundle id)
    app_name: str | None   # e.g. "Google Chrome"


@lru_cache(maxsize=256)
def _app_name_for(exe_path: str) -> str:
    try:
        desc = w.file_description(exe_path)
    except OSError:
        desc = None
    return desc or Path(exe_path.replace("\\", "/")).stem


def _owner_of_pid(pid: int) -> WindowOwner:
    exe = w.process_image_path(pid) if pid else None
    if not exe and pid:
        try:
            import psutil

            exe = psutil.Process(pid).exe() or None
        except Exception:
            exe = None
    return WindowOwner(
        pid=pid,
        exe_path=exe,
        app_id=windows_app_id(exe),
        app_name=_app_name_for(exe) if exe else None,
    )


def window_owner(hwnd) -> WindowOwner:
    """Resolve the app behind ``hwnd``, looking through the UWP frame host."""
    pid = w.window_pid(hwnd)
    owner = _owner_of_pid(pid)
    if owner.app_id == _FRAME_HOST:
        for child in w.enum_child_windows(hwnd):
            cpid = w.window_pid(child)
            if cpid and cpid != pid:
                return _owner_of_pid(cpid)
    return owner
