"""Calendar plugin — ingest EventKit calendar events into the timeline."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from ...config import Settings
from ...native.helpers import get_helper
from .._ingest import ingest_captures
from ..base import RetracePlugin

BUNDLE = "com.apple.iCal"


class CalendarPlugin(RetracePlugin):
    name = "calendar"
    description = "Ingest calendar events (EventKit) into the timeline."

    def collect(self, settings: Settings) -> dict:
        res = get_helper("retrace-calendar", settings).run(["30"], timeout=30.0)
        if not res or not res.get("ok") or not res.get("available"):
            return {"name": self.name, "ingested": 0, "note": "calendar access not granted"}
        rows = []
        for e in res.get("events", []):
            start = e.get("start") or 0
            if not start:
                continue
            when = datetime.fromtimestamp(start, timezone.utc).replace(tzinfo=None)
            title = e.get("title") or "(untitled)"
            loc, cal = e.get("location") or "", e.get("calendar") or ""
            text = title + (f"\n@ {loc}" if loc else "") + (f"\ncalendar: {cal}" if cal else "")
            chash = hashlib.sha256(f"cal:{e.get('id') or title}:{int(start)}".encode()).hexdigest()
            rows.append({
                "captured_at": when, "app_name": "Calendar", "window_title": cal,
                "text": text, "caption": f"📅 {title}", "caption_model": "calendar",
                "content_hash": chash,
            })
        return {"name": self.name, "ingested": ingest_captures(settings, BUNDLE, rows)}
