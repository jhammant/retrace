"""Spotify now-playing logger — logs tracks you play into the timeline.

Polls the local Spotify app via AppleScript (on-device, no network) each daemon
tick, and records a capture whenever the track changes — so it catches tracks
even when Spotify is in the background or minimized. Needs Automation permission
for Spotify (prompted on first use).
"""

from __future__ import annotations

import hashlib
import logging
import subprocess

from ...config import Settings
from ...db import session_scope
from ...models import Capture, utcnow
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


def _now_playing() -> dict | None:
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
        bucket = int(now.timestamp() // 300)
        chash = hashlib.sha256(f"spotify:{info['id']}:{bucket}".encode()).hexdigest()
        text = f"{info['name']} — {info['artist']} · {info['album']}"
        with session_scope(settings) as s:
            if s.query(Capture).filter(Capture.content_hash == chash).first():
                return
            s.add(Capture(
                captured_at=now, app_name="Spotify", bundle_id=BUNDLE,
                window_title=info["album"], text=text, text_len=len(text),
                text_source="plugin", caption=f"🎵 {info['name']} — {info['artist']}",
                caption_model="spotify", content_hash=chash,
            ))
        log.info("spotify: %s — %s", info["name"], info["artist"])
