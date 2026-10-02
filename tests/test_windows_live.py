"""End-to-end on a real Windows desktop: screen grab, Windows OCR, foreground
window, app-switch watcher, clipboard markers, tray icon, autostart, and the full
server + daemon over HTTP.

Opt-in: runs only on Windows with ``RETRACE_LIVE_TESTS=1`` (CI sets it). A
topmost window showing known words gives every check something to find.
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.windows_live,
    pytest.mark.skipif(sys.platform != "win32", reason="needs Windows"),
    pytest.mark.skipif(os.environ.get("RETRACE_LIVE_TESTS") != "1", reason="set RETRACE_LIVE_TESTS=1"),
]

WORDS = "Retrace pineapple harbour lighthouse"
TITLE = "Retrace live test window"

_SHOW_WINDOW = textwrap.dedent(f"""
    import tkinter as tk
    root = tk.Tk()
    root.title({TITLE!r})
    root.geometry("980x360+40+40")
    root.configure(bg="white")
    root.attributes("-topmost", True)
    tk.Label(root, text={WORDS!r}, font=("Segoe UI", 44), bg="white", fg="black",
             wraplength=940).pack(expand=True)
    root.after(200, lambda: (root.lift(), root.focus_force()))
    root.after(240000, root.destroy)
    root.mainloop()
""")


def _find_window(title: str, timeout: float = 15.0):
    from retrace.native.win import _win32 as w

    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd in w.enum_windows():
            if w.window_text(hwnd) == title and w.IsWindowVisible(hwnd):
                return hwnd
        time.sleep(0.2)
    return None


@pytest.fixture(scope="module")
def word_window():
    proc = subprocess.Popen([sys.executable, "-c", _SHOW_WINDOW])
    hwnd = _find_window(TITLE)
    assert hwnd, "test window never appeared"
    time.sleep(1.0)  # let it paint
    yield proc
    proc.terminate()
    proc.wait(timeout=10)


# --- the individual backends --------------------------------------------------------

def test_presence_reports_an_unlocked_session():
    from retrace.native.win.presence import _wts_locked, get_presence, power_state

    assert _wts_locked() is False  # the WTS struct layout resolved, not the fallback
    p = get_presence(120.0)
    assert p["ok"] is True
    assert p["idle_seconds"] >= 0
    assert p["screen_locked"] is False
    assert set(power_state()) == {"on_battery", "low_power"}


def test_foreground_context(word_window):
    from retrace.native.win.context import read_context

    ctx = read_context()
    assert ctx["ok"] is True
    print("foreground:", json.dumps({k: ctx.get(k) for k in ("app_name", "bundle_id", "window_title")}))
    if ctx.get("window_title") == TITLE:  # foreground could be granted to the test window
        assert ctx["bundle_id"] == "python.exe"


def test_grab_then_ocr_reads_the_window(word_window, tmp_path):
    from retrace.native.win.capture import capture_frame
    from retrace.native.win.ocr import ocr_image

    frame, thumb = tmp_path / "f.png", tmp_path / "t.jpg"
    res = capture_frame(frame_path=str(frame), thumb_path=str(thumb), max_edge=1280,
                        jpeg_quality=70, exclude_bundle_ids=[])
    assert res["ok"], res
    assert res["width"] > 0 and frame.exists() and thumb.exists()

    ocr = ocr_image(str(frame))
    assert ocr["ok"], ocr
    text = ocr["text"].lower()
    print("OCR:", text[:400])
    assert "pineapple" in text and "lighthouse" in text
    os.remove(frame)  # OCR must not hold the frame open (the privacy invariant)


def test_denylisted_app_is_blacked_out_of_the_frame(word_window, tmp_path):
    from retrace.native.win.capture import capture_frame
    from retrace.native.win.ocr import ocr_image

    frame = tmp_path / "f.png"
    res = capture_frame(frame_path=str(frame), thumb_path="", max_edge=1280,
                        jpeg_quality=70, exclude_bundle_ids=["python.exe"])
    assert res["ok"], res
    assert res["excluded_windows"] >= 1
    assert "pineapple" not in ocr_image(str(frame))["text"].lower()


def test_watcher_streams_foreground_changes():
    proc = subprocess.Popen([sys.executable, "-m", "retrace.native.win.watch"],
                            stdout=subprocess.PIPE, text=True)
    try:
        first = json.loads(proc.stdout.readline())  # the initial foreground app
        assert first["event"] == "app" and "bundle_id" in first
        # Opening a new topmost window moves the foreground: that must stream an event.
        # (Match on the app, not the pid: uv's python.exe may be a launcher process.)
        other = subprocess.Popen([sys.executable, "-c", _SHOW_WINDOW.replace(TITLE, "Retrace watcher probe")])
        try:
            deadline = time.time() + 45  # heartbeats every 30 s keep readline from blocking forever
            evt = json.loads(proc.stdout.readline())
            while evt["event"] == "heartbeat" and time.time() < deadline:
                evt = json.loads(proc.stdout.readline())
            print("initial:", first, "after switch:", evt)
            assert evt["event"] == "app" and evt["bundle_id"] == "python.exe"
        finally:
            other.terminate()
            other.wait(timeout=10)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _set_clipboard(text: str, private: bool = False) -> None:
    k32, u32 = ctypes.WinDLL("kernel32"), ctypes.WinDLL("user32")
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]

    def put(fmt: int, data: bytes) -> None:
        h = k32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        p = k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        assert u32.SetClipboardData(fmt, h)

    assert u32.OpenClipboard(None)
    try:
        u32.EmptyClipboard()
        put(13, (text + "\0").encode("utf-16-le"))  # CF_UNICODETEXT
        if private:
            put(u32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing"), b"\0")
    finally:
        u32.CloseClipboard()


def test_clipboard_reads_text_but_not_password_manager_copies():
    from retrace.native.win.clipboard import read_text

    _set_clipboard("ordinary copied text ✓")
    assert read_text() == "ordinary copied text ✓"
    _set_clipboard("hunter2-secret", private=True)
    assert read_text() is None


def test_tray_icon_starts_and_stops():
    import threading

    from retrace.native.win.tray import build

    icon = build("http://127.0.0.1:9", refresh_s=0.5)  # nothing listening: "offline"
    t = threading.Thread(target=lambda: icon.run(setup=icon.retrace_setup), daemon=True)
    t.start()
    time.sleep(2.0)
    assert icon.visible
    icon.retrace_stop()
    t.join(10)
    assert not t.is_alive(), "tray loop did not exit"


def test_autostart_round_trip(monkeypatch, tmp_path):
    from retrace.native.win import autostart
    from retrace.native.win.shortcuts import lnk_target

    monkeypatch.setenv("APPDATA", str(tmp_path))  # never touch the real Startup folder
    link = autostart.install()
    assert link.exists() and autostart.status()["installed"]
    # Our own .lnk parser agrees with what Windows wrote.
    assert lnk_target(link.read_bytes()).lower() == autostart.windowless_python().lower()
    assert autostart.remove() and not link.exists()


# --- everything together ------------------------------------------------------------

def test_capture_once_end_to_end(word_window, settings):
    from sqlalchemy import select

    from retrace.capture.pipeline import capture_once
    from retrace.db import session_scope
    from retrace.models import Capture

    res = capture_once(force=True, settings=settings)
    print("capture_once:", res.as_dict())
    assert res.status == "stored", res.as_dict()
    assert res.frame_deleted is True
    assert not any(settings.tmp_dir.iterdir()), "a raw frame survived"
    assert (settings.thumbs_dir / res.thumb_path).exists()
    with session_scope(settings) as s:
        row = s.execute(select(Capture).where(Capture.id == res.capture_id)).scalar_one()
        assert "pineapple" in row.text.lower()
        assert row.text_source in ("ocr", "mixed")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _http(method: str, url: str, timeout: float = 30.0):
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        ctype = r.headers.get("content-type", "")
        return (json.loads(body) if "json" in ctype else body), ctype


def test_server_and_daemon_over_http(word_window, tmp_path):
    """`retrace serve` with the real daemon: health, a daemon capture, search, image."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items() if k != "RETRACE_DISABLE_DAEMON"}
    # A CI desktop has had no keyboard/mouse input, so "away" gating would skip everything.
    env.update(RETRACE_HOME=str(tmp_path / "home"), RETRACE_CAPTURE_INTERVAL_S="5",
               RETRACE_ENABLE_PLUGINS="false", RETRACE_PAUSE_WHEN_AWAY="false")
    log = open(tmp_path / "server.log", "w", encoding="utf-8")
    server = subprocess.Popen([sys.executable, "-m", "retrace.cli", "serve", "--port", str(port)],
                              env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 60
        health = None
        while time.time() < deadline:
            try:
                health, _ = _http("GET", base + "/api/health", timeout=3)
                break
            except OSError:
                time.sleep(0.5)
        assert health, "server never came up"
        assert health["daemon"] == "alive", health

        _http("POST", base + "/capture/start")
        # The daemon's own loop (fallback tick every 5 s) captures without help.
        deadline = time.time() + 60
        captures = []
        while time.time() < deadline and not captures:
            time.sleep(2)
            captures = _http("GET", base + "/capture/recent")[0]["captures"]
        assert captures, "the daemon stored nothing"

        hits = _http("GET", base + "/search?q=pineapple&mode=text")[0]["results"]
        assert hits, "OCR text not searchable"
        img, ctype = _http("GET", base + f"/capture/{hits[0]['id']}/image")
        assert ctype.startswith("image/jpeg") and img[:2] == b"\xff\xd8"

        perms = _http("GET", base + "/permissions")[0]
        assert perms["permissions"]["ocr_language"]["state"] == "granted", perms
        health, _ = _http("GET", base + "/api/health")
        assert health["ok"] is True, health
    finally:
        server.terminate()
        server.wait(timeout=20)
        log.close()
        print((tmp_path / "server.log").read_text(encoding="utf-8")[-3000:])


# --- real browsers: address bar + private-window detection --------------------------

_BROWSERS = {
    "msedge.exe": ([r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"], "--inprivate"),
    "chrome.exe": ([r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"], "--incognito"),
}


def _browser_windows(app_id: str) -> list:
    from retrace.native.win import _win32 as w
    from retrace.native.win.apps import window_owner

    # Main windows only: popups such as the translate bubble are separate windows too.
    return [h for h in w.enum_windows()
            if w.IsWindowVisible(h) and w.window_text(h).endswith(("Edge", "Chrome"))
            and window_owner(h).app_id == app_id]


def _wait_new_window(app_id: str, known: set, timeout: float = 45.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        fresh = [h for h in _browser_windows(app_id) if h not in known]
        if fresh:
            return fresh[0]
        time.sleep(0.5)
    return None


def _toolbar_buttons(hwnd) -> list[str]:
    """Debug aid: the toolbar button names the private check looks at."""
    from retrace.native.win import uia

    client = uia._client()
    root = client[0].ElementFromHandle(hwnd)
    cond = client[0].CreatePropertyCondition(uia._UIA_ControlTypePropertyId, uia._UIA_EditControlTypeId)
    edit = root.FindFirst(uia._TreeScope_Descendants, cond)
    toolbar = uia._toolbar_of(client[0], edit) if edit else None
    return uia._button_names(client[0], toolbar) if toolbar else []


def _wait_url(hwnd, timeout: float = 30.0):
    from retrace.native.win.uia import browser_snapshot

    deadline = time.time() + timeout
    snap = {}
    while time.time() < deadline:
        snap = browser_snapshot(hwnd)
        if snap.get("url") and "example." in snap["url"]:
            break
        time.sleep(1.0)
    return snap


@pytest.mark.parametrize("app_id", list(_BROWSERS))
def test_browser_url_and_private_window(app_id, tmp_path):
    import psutil

    from retrace.native.win import _win32 as w
    from retrace.native.win.browser import title_says_private

    paths, private_flag = _BROWSERS[app_id]
    exe = next((p for p in paths if os.path.exists(p)), None)
    if exe is None:
        pytest.skip(f"{app_id} not installed")
    common = [f"--user-data-dir={tmp_path / 'profile'}", "--no-first-run",
              "--no-default-browser-check", "--disable-sync"]
    known = set(_browser_windows(app_id))
    launched = [subprocess.Popen([exe, *common, "--new-window", "https://example.com/"])]
    try:
        normal = _wait_new_window(app_id, known)
        assert normal, "browser window never appeared"
        snap = _wait_url(normal)
        title = w.window_text(normal)
        print(app_id, "normal:", {"title": title, **snap}, "buttons:", _toolbar_buttons(normal))
        assert snap["url"] and snap["url"].startswith("https://example.com")
        assert not title_says_private(app_id, title)
        assert not title_says_private(app_id, snap["accessible_title"])

        known.add(normal)
        launched.append(subprocess.Popen([exe, *common, private_flag, "https://example.org/"]))
        private = _wait_new_window(app_id, known)
        assert private, "private window never appeared"
        psnap = _wait_url(private)
        ptitle = w.window_text(private)
        print(app_id, "private:", {"title": ptitle, **psnap}, "buttons:", _toolbar_buttons(private))
        assert snap["private"] is False
        assert psnap["private"] or title_says_private(app_id, ptitle)
    finally:
        for proc in launched:
            try:
                parent = psutil.Process(proc.pid)
                for child in parent.children(recursive=True):
                    child.kill()
                parent.kill()
            except psutil.NoSuchProcess:
                pass
