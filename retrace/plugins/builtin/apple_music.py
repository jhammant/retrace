"""Apple Music now-playing logger — mirrors the Spotify plugin for the Music app.

Polls the local Music app via AppleScript (on-device, no network) and logs each
track change, even in the background. Needs Automation permission for Music.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess

from ...config import Settings
from ...db import session_scope
from ...models import Capture, utcnow
from ..base import RetracePlugin

log = logging.getLogger("retrace.plugins.music")

BUNDLE = "com.apple.Music"

_SCRIPT = (
    'tell application "System Events"\n'
    '  if not (exists process "Music") then return ""\n'
    'end tell\n'
    'tell application "Music"\n'
    '  if player state is playing then\n'
    '    set t to current track\n'
    '    return (persistent ID of t) & tab & (name of t) & tab & (artist of t) & tab & (album of t)\n'
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


class AppleMusicPlugin(RetracePlugin):
    name = "apple-music"
    description = "Log Apple Music tracks you play (including in the background)."

    def __init__(self) -> None:
        self._last_track_id: str | None = None

    def poll(self, settings: Settings) -> None:
        info = _now_playing()
        if not info:
            return
        if info["id"] == self._last_track_id:
            return
        self._last_track_id = info["id"]

        now = utcnow()
        bucket = int(now.timestamp() // 300)
        chash = hashlib.sha256(f"music:{info['id']}:{bucket}".encode()).hexdigest()
        text = f"{info['name']} — {info['artist']} · {info['album']}"
        with session_scope(settings) as s:
            if s.query(Capture).filter(Capture.content_hash == chash).first():
                return
            s.add(Capture(
                captured_at=now, app_name="Music", bundle_id=BUNDLE,
                window_title=info["album"], text=text, text_len=len(text),
                text_source="plugin", caption=f"🎵 {info['name']} — {info['artist']}",
                caption_model="apple-music", content_hash=chash,
            ))
        log.info("music: %s — %s", info["name"], info["artist"])
