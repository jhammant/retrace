"""Apple Mail plugin — ingest recent message subjects/senders from the index.

Reads Mail's ``Envelope Index`` SQLite (subjects + senders only, never bodies),
read-only. Needs Full Disk Access. Fails soft if the schema/path differs.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ...config import Settings
from .._ingest import ingest_captures
from ..base import RetracePlugin

MAC_OFFSET = 978307200
BUNDLE = "com.apple.mail"


def _find_index() -> Path | None:
    base = Path.home() / "Library" / "Mail"
    if not base.is_dir():
        return None
    candidates = sorted(base.glob("V*/MailData/Envelope Index"), reverse=True)
    return candidates[0] if candidates else None


def _utc(ts: float) -> datetime:
    # Envelope Index date_received is usually Unix; older builds used Mac epoch.
    if ts < 1_000_000_000:
        ts += MAC_OFFSET
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


class MailPlugin(RetracePlugin):
    name = "mail"
    description = "Ingest recent Apple Mail subjects/senders (not bodies)."

    def collect(self, settings: Settings) -> dict:
        idx = _find_index()
        if not idx:
            return {"name": self.name, "ingested": 0, "note": "no Apple Mail index"}
        try:
            conn = sqlite3.connect(f"file:{idx}?mode=ro&immutable=1", uri=True, timeout=2)
        except sqlite3.Error:
            return {"name": self.name, "ingested": 0, "note": "Full Disk Access needed"}
        rows = []
        try:
            cur = conn.execute(
                """
                SELECT m.ROWID, m.date_received, s.subject, a.comment, a.address
                FROM messages m
                LEFT JOIN subjects s ON s.ROWID = m.subject
                LEFT JOIN addresses a ON a.ROWID = m.sender
                ORDER BY m.date_received DESC LIMIT 1000
                """
            )
            for rowid, dr, subject, comment, address in cur:
                if not dr:
                    continue
                subject = subject or "(no subject)"
                sender = comment or address or ""
                when = _utc(float(dr))
                chash = hashlib.sha256(f"mail:{rowid}:{subject}".encode()).hexdigest()
                rows.append({
                    "captured_at": when, "app_name": "Mail", "window_title": sender,
                    "text": f"{subject}\nfrom: {sender}",
                    "caption": f"✉️ {subject[:70]}" + (f" — {sender}" if sender else ""),
                    "caption_model": "mail", "content_hash": chash,
                })
        except sqlite3.Error:
            pass
        finally:
            conn.close()
        return {"name": self.name, "ingested": ingest_captures(settings, BUNDLE, rows)}
