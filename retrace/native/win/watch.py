"""Stream foreground-window changes as JSON lines (the ``retrace-watch`` equivalent).

Run as ``python -m retrace.native.win.watch``. The daemon reads stdout exactly as
it reads the macOS helper:

    {"event":"app","app_name":"Google Chrome","bundle_id":"chrome.exe","pid":1234,"ts":...}
    {"event":"heartbeat","ts":...}

A WinEvent hook on EVENT_SYSTEM_FOREGROUND needs a message loop on the thread
that installed it, which is why this runs as its own process.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time

_print_lock = threading.Lock()


def _emit(obj: dict) -> None:
    line = json.dumps(obj, sort_keys=True)
    with _print_lock:
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            os._exit(0)  # the daemon closed the pipe: nobody is listening


def _emit_app(hwnd, event: str) -> None:
    from .apps import window_owner

    if not hwnd:
        return
    try:
        owner = window_owner(hwnd)
    except OSError:
        return
    _emit({
        "event": event,
        "app_name": owner.app_name or "",
        "bundle_id": owner.app_id or "",
        "pid": owner.pid,
        "ts": time.time(),
    })


def _heartbeat(interval: float = 30.0) -> None:
    while True:
        time.sleep(interval)
        _emit({"event": "heartbeat", "ts": time.time()})


def main() -> int:
    from . import _win32 as w

    @w.WINEVENTPROC
    def on_foreground(_hook, _event, hwnd, _id_object, _id_child, _thread, _time):
        _emit_app(hwnd, "app")

    hook = w.SetWinEventHook(
        w.EVENT_SYSTEM_FOREGROUND, w.EVENT_SYSTEM_FOREGROUND, None, on_foreground,
        0, 0, w.WINEVENT_OUTOFCONTEXT | w.WINEVENT_SKIPOWNPROCESS,
    )
    if not hook:
        _emit({"event": "error", "error": f"SetWinEventHook failed ({ctypes.get_last_error()})"})
        return 1

    _emit_app(w.GetForegroundWindow(), "app")  # initial state: capture straight away
    threading.Thread(target=_heartbeat, daemon=True).start()

    msg = w.wintypes.MSG()
    try:
        while w.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            w.TranslateMessage(ctypes.byref(msg))
            w.DispatchMessageW(ctypes.byref(msg))
    finally:
        w.UnhookWinEvent(hook)
    return 0


if __name__ == "__main__":
    sys.exit(main())
