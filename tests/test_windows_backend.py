"""The Windows backend's pure logic, tested on every OS.

Anything that needs a real Windows desktop lives in ``test_windows_live.py``.
"""

from __future__ import annotations

import os
import sqlite3
import struct
import sys
import types
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from PIL import Image

# --- browser rules -------------------------------------------------------------

from retrace.native.win.browser import is_browser, normalize_address, title_says_private


@pytest.mark.parametrize("value,expected", [
    ("github.com/jhammant/retrace", "https://github.com/jhammant/retrace"),
    ("https://example.com/a?b=1", "https://example.com/a?b=1"),
    ("http://localhost:8766/#/settings", "http://localhost:8766/#/settings"),
    ("localhost:8766", "https://localhost:8766"),
    ("edge://settings/privacy", "edge://settings/privacy"),
    ("about:blank", "about:blank"),
    ("how to port swift to windows", None),  # a half-typed search, not a URL
    ("", None),
    (None, None),
    ("retrace", None),
])
def test_normalize_address(value, expected):
    assert normalize_address(value) == expected


@pytest.mark.parametrize("app,title,expected", [
    ("msedge.exe", "Inbox - [InPrivate] - Microsoft\u200b Edge", True),
    ("chrome.exe", "New Tab - Google Chrome (Incognito)", True),
    ("firefox.exe", "Mozilla Firefox Private Browsing", True),
    ("brave.exe", "Search - Brave (Private)", True),
    ("chrome.exe", "Retrace - GitHub - Google Chrome", False),
    ("notepad.exe", "incognito.txt - Notepad", False),  # not a browser
    ("chrome.exe", None, False),
])
def test_private_window_titles(app, title, expected):
    assert title_says_private(app, title) is expected


def test_private_profile_button():
    from retrace.native.win.browser import button_says_private

    assert button_says_private(["Back", "Reload", "Incognito", "Chrome"])
    assert button_says_private(["Incognito (2)"])
    assert button_says_private(["InPrivate"]) and button_says_private(["Private"])
    assert not button_says_private(["Back", "Profile 1", "Extensions", "You", None])
    assert not button_says_private(["Privacy Badger"])


def test_is_browser_is_case_insensitive():
    assert is_browser("MSEdge.exe") and is_browser("firefox.exe")
    assert not is_browser("code.exe") and not is_browser(None)


# --- capture: denylist redaction + outputs ---------------------------------------

from retrace.native.win import capture as wcap


def test_denylist_matching_mirrors_privacy_rules():
    deny = ["1password.exe", "keepass", "com.bitwarden.desktop"]
    assert wcap._matches("1password.exe", deny)
    assert wcap._matches("keepassxc.exe", deny)  # substring, like bundle-id variants
    assert not wcap._matches("chrome.exe", deny)
    assert not wcap._matches(None, deny)


def test_redact_blacks_out_windows_on_this_display_only():
    img = Image.new("RGB", (100, 80), (255, 255, 255))
    hit = wcap.redact(img, [(10, 10, 30, 30), (500, 500, 600, 600), (-20, -20, 5, 5)])
    assert hit == 2  # the off-display window is ignored
    assert img.getpixel((15, 15)) == (0, 0, 0)
    assert img.getpixel((2, 2)) == (0, 0, 0)  # clipped at the edge
    assert img.getpixel((50, 50)) == (255, 255, 255)


def test_write_outputs_writes_frame_and_bounded_thumbnail(tmp_path):
    img = Image.new("RGBA", (2000, 1000), (10, 20, 30, 255))
    frame, thumb = tmp_path / "f.png", tmp_path / "t.jpg"
    wrote = wcap.write_outputs(img, frame_path=str(frame), thumb_path=str(thumb), max_edge=640, jpeg_quality=70)
    assert wrote == (True, True)
    with Image.open(thumb) as t:
        assert max(t.size) == 640 and t.format == "JPEG"
    with Image.open(frame) as f:
        assert f.size == (2000, 1000)


# --- shortcuts (.lnk) -------------------------------------------------------------

from retrace.native.win.shortcuts import lnk_target


def _lnk(path: str, *, unicode: bool = True, id_list: bool = False, local: bool = True) -> bytes:
    """A minimal Shell Link ([MS-SHLLINK]) pointing at ``path``."""
    flags = 0x02 | (0x01 if id_list else 0) | 0x80
    header = bytearray(0x4C)
    struct.pack_into("<I", header, 0, 0x4C)
    struct.pack_into("<I", header, 20, flags)
    out = bytes(header)
    if id_list:
        out += struct.pack("<H", 4) + b"\x02\x00\x00\x00"
    ansi = path.encode("cp1252", "replace") + b"\x00"
    suffix = b"\x00"
    if unicode:
        hdr = 0x24
        base_off = hdr
        suffix_off = base_off + len(ansi)
        base_u = suffix_off + len(suffix)
        wide = path.encode("utf-16-le") + b"\x00\x00"
        suffix_u = base_u + len(wide)
        body = ansi + suffix + wide + b"\x00\x00"
        info = struct.pack("<9I", hdr + len(body), hdr, 1 if local else 2, 0, base_off, 0, suffix_off, base_u, suffix_u)
    else:
        hdr = 0x1C
        base_off = hdr
        suffix_off = base_off + len(ansi)
        body = ansi + suffix
        info = struct.pack("<7I", hdr + len(body), hdr, 1 if local else 2, 0, base_off, 0, suffix_off)
    return out + info + body


@pytest.mark.parametrize("kwargs", [{}, {"unicode": False}, {"id_list": True}])
def test_lnk_target_reads_local_path(kwargs):
    assert lnk_target(_lnk(r"C:\Users\jon\Documents\Q3 plan.docx", **kwargs)) == r"C:\Users\jon\Documents\Q3 plan.docx"


def test_lnk_target_unicode_path():
    assert lnk_target(_lnk(r"C:\Users\jon\Café notes ✓.md")) == r"C:\Users\jon\Café notes ✓.md"


def test_lnk_target_rejects_network_and_garbage():
    assert lnk_target(_lnk(r"\\server\share\x.txt", local=False)) is None
    assert lnk_target(b"not a shortcut") is None
    assert lnk_target(b"") is None


def test_windows_recent_rows(tmp_path):
    from retrace.plugins.builtin.recent_files import windows_recent

    now = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc).timestamp()
    files = {
        "plan.docx.lnk": (r"C:\Users\jon\Documents\plan.docx", now - 3600),
        "old.xlsx.lnk": (r"C:\Users\jon\Documents\old.xlsx", now - 30 * 86400),
        "Documents.lnk": (r"C:\Users\jon\Documents", now - 60),  # a folder
        "cache.lnk": (r"C:\Users\jon\AppData\Local\x.tmp", now - 60),
    }
    for name, (target, mtime) in files.items():
        p = tmp_path / name
        p.write_bytes(_lnk(target))
        os.utime(p, (mtime, mtime))
    rows = windows_recent(tmp_path, days=7, now=now)
    assert [r["doc_path"] for r in rows] == [r"C:\Users\jon\Documents\plan.docx"]
    assert rows[0]["caption"] == "📄 plan.docx"
    assert rows[0]["window_title"] == r"C:\Users\jon\Documents"


# --- notifications (wpndatabase.db) ----------------------------------------------

def _filetime(dt: datetime) -> int:
    return int((dt.timestamp() + 11644473600) * 10_000_000)


def test_windows_notifications_store_app_and_time_only(tmp_path):
    from retrace.plugins.builtin.notifications import windows_notifications

    db = tmp_path / "wpndatabase.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE NotificationHandler (RecordId INTEGER PRIMARY KEY, PrimaryId TEXT);"
        "CREATE TABLE Notification (Id INTEGER PRIMARY KEY, HandlerId INTEGER, Type TEXT,"
        " Payload BLOB, ArrivalTime INTEGER);"
    )
    t1 = datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc)
    conn.execute("INSERT INTO NotificationHandler VALUES (1, 'Microsoft.Teams_8wekyb3d8bbwe!MSTeams')")
    conn.execute("INSERT INTO Notification VALUES (10, 1, 'toast', ?, ?)",
                 (b"<toast><text>Your code is 481516</text></toast>", _filetime(t1)))
    conn.execute("INSERT INTO Notification VALUES (11, 1, 'tile', x'00', ?)", (_filetime(t1),))
    conn.commit()
    conn.close()

    rows, latest = windows_notifications(db, 0)
    assert len(rows) == 1  # tiles/badges are not notifications
    row = rows[0]
    assert row["app_name"] == "Teams"
    assert row["captured_at"] == t1.replace(tzinfo=None)
    assert "481516" not in row["text"] and "481516" not in row["caption"]
    assert latest == _filetime(t1)
    assert windows_notifications(db, latest)[0] == []  # incremental


# --- spotify window titles --------------------------------------------------------

from retrace.plugins.builtin.spotify import _parse_window_title


def test_spotify_title_parsing():
    assert _parse_window_title("Radiohead - Weird Fishes/Arpeggi") == {
        "id": "Radiohead - Weird Fishes/Arpeggi", "name": "Weird Fishes/Arpeggi",
        "artist": "Radiohead", "album": "",
    }
    for idle in ("Spotify", "Spotify Premium", "Spotify Free", "", None, "Advertisement"):
        assert _parse_window_title(idle) is None


# --- embeddings --------------------------------------------------------------------

from retrace.search import hashembed


def _vec(text):
    out = hashembed.embed(text)
    assert out["ok"] and out["dim"] == hashembed.DIM and out["model"] == hashembed.MODEL
    return np.asarray(out["vec"], dtype=np.float32)


def test_hash_embedding_is_deterministic_and_unit_length():
    a, b = _vec("Quarterly planning deck"), _vec("Quarterly planning deck")
    assert np.array_equal(a, b)
    assert abs(float(np.linalg.norm(a)) - 1.0) < 1e-5


def test_hash_embedding_tolerates_typos_and_inflections():
    q = _vec("deployment pipeline")
    near = float(np.dot(q, _vec("deploymnt pipelines failing")))
    far = float(np.dot(q, _vec("banana smoothie recipe")))
    assert near > far + 0.2


def test_hash_embedding_rejects_empty():
    assert hashembed.embed("   ")["ok"] is False


# --- tray --------------------------------------------------------------------------

from retrace.native.win.tray import icon_image, tray_view


def test_tray_view_states():
    now = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    base = {"enabled": True, "presence": {"present": True}, "last_app": "Code",
            "last_capture_at": (now - timedelta(minutes=5)).isoformat(),
            "counters": {"stored": 12, "skipped_dupe": 3, "skipped_gated": 1}}
    v = tray_view(base, now)
    assert v.state == "recording" and v.header == "Retrace — Recording"
    assert v.counts == "Today: 12 captured · 4 skipped"
    assert v.focus == "Focus: Code" and v.last == "Last: 5m ago"
    assert tray_view({**base, "enabled": False}, now).state == "paused"
    assert tray_view({**base, "snooze_until": "indefinite"}, now).state == "hidden"
    assert tray_view({**base, "presence": {"screen_locked": True}}, now).header == "Retrace — Screen locked"
    assert tray_view({**base, "presence": {"present": False}}, now).state == "away"
    assert tray_view(None).state == "offline"


@pytest.mark.parametrize("state", ["recording", "paused", "hidden", "away", "offline", "loading"])
def test_tray_icons_render(state):
    img = icon_image(state)
    assert img.size == (64, 64) and img.mode == "RGBA"


# --- dispatch: the helper wrappers route to the Windows backend --------------------

def test_helper_wrappers_dispatch_to_windows_backends(monkeypatch, settings):
    from retrace.native import helpers as H

    calls = {}
    monkeypatch.setattr(H, "IS_WINDOWS", True)
    monkeypatch.setattr("retrace.native.win.capture.capture_frame",
                        lambda **kw: calls.setdefault("capture", kw) and {"ok": True})
    monkeypatch.setattr("retrace.native.win.ocr.ocr_image",
                        lambda path: {"ok": True, "text": f"ocr:{path}"})
    # These two import the Win32 bindings, so stand in for the whole module.
    monkeypatch.setitem(sys.modules, "retrace.native.win.context", types.SimpleNamespace(
        read_context=lambda **kw: {"ok": True, "app_name": "Notepad", "cfg": kw}))
    monkeypatch.setitem(sys.modules, "retrace.native.win.presence", types.SimpleNamespace(
        get_presence=lambda t: {"ok": True, "threshold": t}))

    H.capture_frame(frame_path="f", thumb_path="t", max_edge=10, jpeg_quality=50,
                    exclude_bundle_ids=["x.exe"], settings=settings)
    assert calls["capture"]["exclude_bundle_ids"] == ["x.exe"]
    assert H.read_context(settings=settings)["app_name"] == "Notepad"
    assert H.ocr_image("frame.png", settings=settings)["text"] == "ocr:frame.png"
    assert H.get_presence(42.0, settings=settings) == {"ok": True, "threshold": 42.0}
    assert H.analyze_sensitivity("frame.png", settings=settings)["available"] is False
    assert H.embed_text("hello world", settings=settings)["model"] == hashembed.MODEL


def test_daemon_runs_the_python_watcher_on_windows(monkeypatch, settings):
    from retrace.capture import daemon as D

    monkeypatch.setattr(D, "IS_WINDOWS", True)
    d = D.CaptureDaemon(settings, enable_watcher=False, enable_fallback=False)
    assert d._watch_command() == [sys.executable, "-m", "retrace.native.win.watch"]


def test_no_foundation_models_caption_off_macos(monkeypatch, settings):
    from retrace.capture import caption_native

    monkeypatch.setattr(caption_native, "IS_MACOS", False)
    assert caption_native.native_caption(app="A", window="w", url=None, text="t", settings=settings) is None


# --- plugins, stats, status ---------------------------------------------------------

def test_plugins_declare_their_platforms():
    from retrace.plugins.base import RetracePlugin
    from retrace.plugins.registry import _builtin

    mac_only = {p.name for p in _builtin() if p.platforms == ("darwin",)}
    assert mac_only == {"apple-music", "calendar", "mail", "safari-history", "reading-list"}
    p = RetracePlugin()
    assert p.supported()
    p.platforms = ("no-such-os",)
    assert not p.supported()


def test_windows_app_ids_read_well_in_stats():
    from retrace.stats.service import BROWSER_BUNDLES, _pretty_bundle

    assert _pretty_bundle("spotify.exe") == "Spotify"
    assert "msedge.exe" in BROWSER_BUNDLES


def test_windows_app_id_from_exe_path():
    from retrace.platform import windows_app_id

    assert windows_app_id(r"C:\Program Files\Google\Chrome\Application\chrome.exe") == "chrome.exe"
    assert windows_app_id(r"C:\Program Files\1Password\app\8\1Password.exe") == "1password.exe"
    assert windows_app_id(None) is None


def test_status_write_rides_out_a_locked_file(monkeypatch, ledger):
    """Windows refuses to replace a file a reader holds open; the ledger retries."""
    from retrace import status as S

    real = os.replace
    failures = {"n": 0}

    def flaky(src, dst):
        if failures["n"] < 3:
            failures["n"] += 1
            raise PermissionError(13, "in use")
        real(src, dst)

    monkeypatch.setattr(S.os, "replace", flaky)
    ledger.set_enabled(True)
    assert failures["n"] == 3 and ledger.is_enabled()


def test_autostart_command_points_macos_users_at_launchd(monkeypatch, capsys):
    from retrace import cli
    from retrace import platform as P

    monkeypatch.setattr(P, "IS_WINDOWS", False)
    assert cli.main(["autostart", "status"]) == 1
    assert "launchd" in capsys.readouterr().out


def test_powershell_quoting():
    from retrace.native.win.autostart import _ps_quote

    assert _ps_quote(r"C:\Users\O'Brien\py\pythonw.exe") == r"'C:\Users\O''Brien\py\pythonw.exe'"
