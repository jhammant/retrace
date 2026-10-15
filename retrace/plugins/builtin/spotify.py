"""Spotify now-playing logger — logs tracks you play into the timeline.

Polls the local Spotify app each daemon tick (on-device, no network) and records
a capture whenever the track changes — so it catches tracks even when Spotify is
in the background or minimized. On macOS it asks Spotify over AppleScript (needs
Automation permission, prompted on first use). On Windows it reads the Spotify
window title, which is "Artist - Track" while playing.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from datetime import timezone

from ...config import Settings
from ...db import session_scope
from ...models import Capture, utcnow
from ...platform import IS_WINDOWS
from ..base import RetracePlugin

log = logging.getLogger("retrace.plugins.spotify")

BUNDLE = "com.spotify.client"

# One osascript call: bail (return "") if Spotify isn't running so we never launch it.
_SCRIPT = (
    'tell application "System Events"\n'
    '  if not (exists process "Spotify") then return ""\n'
    'end tell\n'
    'tell application "Spotify"\n'
    '  if player state is playing then\n'
    '    set t to current track\n'
    '    return (id of t) & tab & (name of t) & tab & (artist of t) & tab & (album of t)\n'
    '  end if\n'
    'end tell\n'
    'return ""'
)


def _parse_window_title(title: str | None) -> dict | None:
    """Spotify for Windows titles its window "Artist - Track" while playing, and
    "Spotify" / "Spotify Premium" / "Spotify Free" when paused or idle."""
    t = (title or "").strip()
    if not t or t.lower().startswith("spotify") or " - " not in t:
        return None
    artist, _, track = t.partition(" - ")
    if not artist.strip() or not track.strip():
        return None
    return {"id": t, "name": track.strip(), "artist": artist.strip(), "album": ""}


def _now_playing_windows() -> dict | None:
    import psutil

    from ...native.win import _win32 as w

    pids = set()
    for proc in psutil.process_iter(["name"]):
        if (proc.info.get("name") or "").lower() == "spotify.exe":
            pids.add(proc.pid)
    if not pids:
        return None  # not running; never launch it
    for hwnd in w.enum_windows():
        if w.window_pid(hwnd) in pids:
            info = _parse_window_title(w.window_text(hwnd))
            if info:
                return info
    return None


def _now_playing() -> dict | None:
    if IS_WINDOWS:
        try:
            return _now_playing_windows()
        except Exception:
            return None
    try:
        r = subprocess.run(["osascript", "-e", _SCRIPT], capture_output=True, text=True, timeout=6)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        return None
    parts = out.split("\t")
    if len(parts) < 4 or not parts[1]:
        return None
    return {"id": parts[0], "name": parts[1], "artist": parts[2], "album": parts[3]}


class SpotifyPlugin(RetracePlugin):
    name = "spotify"
    description = "Log Spotify tracks you play (including in the background)."

    def __init__(self) -> None:
        self._last_track_id: str | None = None

    def poll(self, settings: Settings) -> None:
        info = _now_playing()
        if not info:
            return
        if info["id"] == self._last_track_id:
            return  # same track still playing
        self._last_track_id = info["id"]

        now = utcnow()
        # Dedup within a 5-minute bucket so a daemon restart doesn't re-log the
        # same track, while genuine re-listens later still create a new entry.
        bucket = int(now.replace(tzinfo=timezone.utc).timestamp() // 300)
        chash = hashlib.sha256(f"spotify:{info['id']}:{bucket}".encode()).hexdigest()
        text = f"{info['name']} — {info['artist']}" + (f" · {info['album']}" if info["album"] else "")
        with session_scope(settings) as s:
            if s.query(Capture).filter(Capture.content_hash == chash).first():
                return
            s.add(Capture(
                captured_at=now, app_name="Spotify", bundle_id=BUNDLE,
                window_title=info["album"] or "Spotify", text=text, text_len=len(text),
                text_source="plugin", caption=f"🎵 {info['name']} — {info['artist']}",
                caption_model="spotify", content_hash=chash,
            ))
        log.info("spotify: %s — %s", info["name"], info["artist"])
