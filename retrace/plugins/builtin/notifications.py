"""Notifications plugin — ingest which apps notified you, and when.

macOS: knowledgeC's ``/notification/usage`` stream. Windows: the notification
platform's own database (``wpndatabase.db``). On both, only the app and the time
are stored, never the notification's text (it can carry message content and
one-time codes).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ...config import Settings
from ...platform import IS_WINDOWS, local_appdata
from ...tmpcopy import temp_copy
from .._ingest import ingest_captures
from ..base import RetracePlugin

MAC_OFFSET = 978307200
KC = Path.home() / "Library" / "Application Support" / "Knowledge" / "knowledgeC.db"
BUNDLE = "com.apple.notificationcenterui"
WPN_DB = local_appdata() / "Microsoft" / "Windows" / "Notifications" / "wpndatabase.db"
FILETIME_OFFSET = 11644473600  # seconds from 1601-01-01 to the Unix epoch


def pretty_app_id(app_id: str) -> str:
    """A short app name from a Windows notifier id.

    Ids are AppUserModelIDs ("Microsoft.Teams_8wekyb3d8bbwe!MSTeams"), plain names
    ("MSEdge") or executable paths ("{GUID}\\Slack\\slack.exe").
    """
    s = app_id.replace("/", "\\").split("\\")[-1]
    s = s.split("!")[0].split("_")[0]
    if s.lower().endswith(".exe"):
        s = s[:-4]
    elif "." in s:
        s = s.rsplit(".", 1)[-1]
    s = s.replace("-", " ").strip()
    return (s[:1].upper() + s[1:]) if s else app_id


def windows_notifications(db_path, cutoff_ft: int) -> tuple[list[dict], int]:
    """Toast notifications newer than ``cutoff_ft`` (FILETIME) -> (rows, latest FILETIME)."""
    if not db_path.exists():
        return [], cutoff_ft
    query = (
        "SELECT n.Id, n.ArrivalTime, h.PrimaryId FROM Notification n "
        "JOIN NotificationHandler h ON h.RecordId = n.HandlerId "
        "WHERE n.Type = 'toast' AND n.ArrivalTime > ? ORDER BY n.ArrivalTime"
    )

    def _read(path) -> list[tuple]:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            return conn.execute(query, (cutoff_ft,)).fetchall()
        finally:
            conn.close()

    try:
        found = _read(db_path)  # read in place so the WAL's newest rows are included
    except sqlite3.Error:
        try:
            with temp_copy(db_path, suffix=".wpn.db") as tmp:
                found = _read(tmp)
        except (OSError, sqlite3.Error):
            return [], cutoff_ft
    rows, latest = [], cutoff_ft
    for nid, arrival, app_id in found:
        if not arrival or not app_id:
            continue
        latest = max(latest, int(arrival))
        short = pretty_app_id(app_id)
        when = datetime.fromtimestamp(int(arrival) / 10_000_000 - FILETIME_OFFSET, timezone.utc).replace(tzinfo=None)
        rows.append({
            "captured_at": when, "app_name": short, "window_title": app_id,
            "text": f"Notification from {short}", "caption": f"📣 {short} notification",
            "caption_model": "wpndatabase",
            "content_hash": hashlib.sha256(f"notif:{app_id}:{nid}:{arrival}".encode()).hexdigest(),
        })
    return rows, latest


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts + MAC_OFFSET, timezone.utc).replace(tzinfo=None)


class NotificationsPlugin(RetracePlugin):
    name = "notifications"
    description = "Ingest which apps sent you notifications, and when."

    def _state_path(self, s: Settings) -> Path:
        return s.home / "plugin_notifications.json"

    def _load_state(self, s: Settings) -> dict:
        try:
            return json.loads(self._state_path(s).read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def collect(self, settings: Settings) -> dict:
        if IS_WINDOWS:
            state = self._load_state(settings)
            rows, latest = windows_notifications(WPN_DB, int(state.get("cutoff_filetime", 0)))
            n = ingest_captures(settings, BUNDLE, rows)
            state["cutoff_filetime"] = latest
            self._state_path(settings).write_text(json.dumps(state))
            return {"name": self.name, "ingested": n}
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
