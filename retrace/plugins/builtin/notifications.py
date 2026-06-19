"""Notifications plugin — ingest macOS notification history from knowledgeC."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ...config import Settings
from .._ingest import ingest_captures
from ..base import RetracePlugin

MAC_OFFSET = 978307200
KC = Path.home() / "Library" / "Application Support" / "Knowledge" / "knowledgeC.db"
BUNDLE = "com.apple.notificationcenterui"


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts + MAC_OFFSET, timezone.utc).replace(tzinfo=None)


class NotificationsPlugin(RetracePlugin):
    name = "notifications"
    description = "Ingest macOS notifications (per app) from knowledgeC."

    def _state_path(self, s: Settings) -> Path:
        return s.home / "plugin_notifications.json"

    def collect(self, settings: Settings) -> dict:
        if not KC.exists():
            return {"name": self.name, "ingested": 0, "note": "no knowledgeC"}
        p = self._state_path(settings)
        cutoff = 0.0
        if p.exists():
            try:
                cutoff = json.loads(p.read_text()).get("cutoff_mac", 0.0)
            except (OSError, json.JSONDecodeError):
                cutoff = 0.0
        try:
            conn = sqlite3.connect(f"file:{KC}?mode=ro&immutable=1", uri=True, timeout=2)
        except sqlite3.Error:
            return {"name": self.name, "ingested": 0, "note": "Full Disk Access needed"}
        rows, latest = [], cutoff
        try:
            cur = conn.execute(
                "SELECT ZVALUESTRING, ZSTARTDATE FROM ZOBJECT "
                "WHERE ZSTREAMNAME='/notification/usage' AND ZSTARTDATE > ? ORDER BY ZSTARTDATE",
                (cutoff,),
            )
            for app, zstart in cur:
                if zstart is None or not app:
                    continue
                latest = max(latest, zstart)
                short = app.split(".")[-1].replace("-", " ").title()
                chash = hashlib.sha256(f"notif:{app}:{zstart}".encode()).hexdigest()
                rows.append({
                    "captured_at": _utc(zstart), "app_name": short, "window_title": app,
                    "text": f"Notification from {app}", "caption": f"📣 {short} notification",
                    "caption_model": "knowledgec", "content_hash": chash,
                })
        except sqlite3.Error:
            pass
        finally:
            conn.close()
        n = ingest_captures(settings, BUNDLE, rows)
        p.write_text(json.dumps({"cutoff_mac": latest}))
        return {"name": self.name, "ingested": n}
