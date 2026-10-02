"""Idle time, screen lock and power state on Windows (the ``retrace-present`` equivalent)."""

from __future__ import annotations

import ctypes

from . import _win32 as w


def idle_seconds() -> float:
    info = w.LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not w.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    # Both are 32-bit millisecond tick counts; mask so a wrap (every ~49.7 days) can't go negative.
    return ((w.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def _current_session() -> int | None:
    sid = w.DWORD(0)
    if not w.ProcessIdToSessionId(w.GetCurrentProcessId(), ctypes.byref(sid)):
        return None
    return int(sid.value)


def _wts_locked() -> bool | None:
    """Session lock state from the Terminal Services API (Windows 8+). None if unknown."""
    session = _current_session()
    buf = ctypes.c_void_p()
    size = w.DWORD(0)
    if session is None or not w.WTSQuerySessionInformationW(
        w.WTS_CURRENT_SERVER_HANDLE, w.WTS_CURRENT_SESSION, w.WTSSessionInfoEx,
        ctypes.byref(buf), ctypes.byref(size),
    ):
        return None
    try:
        if size.value < 20 or not buf.value:
            return None
        # WTSINFOEXW is {DWORD Level; union Data}, and the union holds LARGE_INTEGERs,
        # so Data is 8-byte aligned: SessionId, SessionState, SessionFlags start at
        # offset 8. Check SessionId against ours rather than trusting the layout.
        words = (ctypes.c_long * 5).from_address(buf.value)
        if words[0] != 1:
            return None
        if words[2] == session:
            flags = words[4]
        elif words[1] == session:  # tightly packed layout
            flags = words[3]
        else:
            return None
        if flags == w.WTS_SESSIONSTATE_LOCK:
            return True
        if flags == w.WTS_SESSIONSTATE_UNLOCK:
            return False
        return None
    finally:
        w.WTSFreeMemory(buf)


def _input_desktop_locked() -> bool:
    """Fallback: while locked, the input desktop is Winlogon's, which we can't open."""
    hdesk = w.OpenInputDesktop(0, False, w.DESKTOP_READOBJECTS)
    if not hdesk:
        return True
    try:
        buf = ctypes.create_unicode_buffer(256)
        needed = w.DWORD(0)
        if not w.GetUserObjectInformationW(hdesk, w.UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
            return False
        return buf.value.lower() != "default"
    finally:
        w.CloseDesktop(hdesk)


def screen_locked() -> bool:
    state = _wts_locked()
    return state if state is not None else _input_desktop_locked()


def get_presence(threshold_s: float = 120.0) -> dict:
    """Same shape as the macOS ``retrace-present`` helper's JSON."""
    try:
        idle = idle_seconds()
        locked = screen_locked()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "idle_seconds": idle,
        "present": idle < threshold_s and not locked,
        "screen_locked": locked,
        # Windows has no cheap display-power query; a sleeping display means no
        # input for longer than the idle threshold, so the idle gate covers it.
        "display_asleep": False,
        # Desktop apps need no per-app grant for screen capture or UI Automation.
        "screen_recording": True,
        "accessibility": True,
    }


def power_state() -> dict:
    """AC/battery + battery saver, matching the daemon's macOS ``pmset`` reading."""
    status = w.SYSTEM_POWER_STATUS()
    if not w.GetSystemPowerStatus(ctypes.byref(status)):
        return {"on_battery": False, "low_power": False}
    return {
        "on_battery": status.ACLineStatus == 0,
        "low_power": bool(status.SystemStatusFlag & 1),
    }
