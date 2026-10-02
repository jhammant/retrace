"""Safari history plugin — ingest browsing history, including iPhone visits.

Reads Safari's ``History.db`` SQLite read-only. With iCloud Safari sync on, this
database also contains visits made on your iPhone/iPad, so this is the on-device
way to pull mobile browsing into Retrace — no iOS app required. Needs Full Disk
Access. URLs/titles/timestamps only (page *content* is never synced — see the
``page-backfill`` plugin for opt-in content fetching). Fails soft.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ...config import Settings
from ..base import RetracePlugin
from .._ingest import ingest_captures

# Safari stores visit_time as CFAbsoluteTime (seconds since 2001-01-01 UTC).
MAC_EPOCH_OFFSET = 978307200
BUNDLE = "com.apple.Safari"
TEXT_SOURCE = "safari-history"  # marker the page-backfill plugin looks for


def _default_db_path() -> Path:
    return Path.home() / "Library" / "Safari" / "History.db"


def _utc(cf_time: float) -> datetime:
    return datetime.fromtimestamp(cf_time + MAC_EPOCH_OFFSET, timezone.utc).replace(tzinfo=None)


class SafariHistoryPlugin(RetracePlugin):
    name = "safari-history"
    description = "Ingest Safari history (incl. iPhone visits synced via iCloud)."
    platforms = ("darwin",)

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path

    def _path(self) -> Path:
        return self._db_path or _default_db_path()

    def collect(self, settings: Settings) -> dict:
        from ...capture.privacy import is_sensitive  # lazy to avoid import cycles

        db = self._path()
        if not db.is_file():
            return {"name": self.name, "ingested": 0, "note": "no Safari History.db"}
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True, timeout=2)
        except sqlite3.Error:
            return {"name": self.name, "ingested": 0, "note": "Full Disk Access needed"}

        days = int(getattr(settings, "safari_history_days", 7) or 7)
        cutoff = (datetime.now(timezone.utc).timestamp() - days * 86400) - MAC_EPOCH_OFFSET
        rows: list[dict] = []
        skipped_sensitive = 0
        try:
            cur = conn.execute(
                """
                SELECT v.id, i.url, v.title, v.visit_time
                FROM history_visits v
                JOIN history_items i ON i.id = v.history_item
                WHERE v.visit_time >= ?
                ORDER BY v.visit_time DESC
                LIMIT 5000
                """,
                (cutoff,),
            )
            for vid, url, title, vtime in cur:
                if not url or not vtime:
                    continue
                title = (title or "").strip()
                # Reuse the same sensitive-content gate as live capture.
                if is_sensitive({"url": url, "window_title": title}, settings):
                    skipped_sensitive += 1
                    continue
                when = _utc(float(vtime))
                chash = hashlib.sha256(f"safari-history:{vid}".encode()).hexdigest()
                label = title or url
                rows.append({
                    "captured_at": when, "app_name": "Safari", "window_title": title or None,
                    "url": url, "text": f"{label}\n{url}".strip(),
                    "text_source": TEXT_SOURCE,
                    "caption": f"🧭 {label[:80]}", "caption_model": "safari-history",
                    "content_hash": chash,
                })
        except sqlite3.Error:
            pass
        finally:
            conn.close()

        ingested = ingest_captures(settings, BUNDLE, rows)
        out = {"name": self.name, "ingested": ingested}
        if skipped_sensitive:
            out["skipped_sensitive"] = skipped_sensitive
        return out
