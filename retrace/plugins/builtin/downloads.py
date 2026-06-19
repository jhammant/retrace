"""Downloads plugin — ingest browser downloads (Chrome + Safari)."""

from __future__ import annotations

import hashlib
import plistlib
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ...config import Settings
from .._ingest import ingest_captures
from ..base import RetracePlugin

CHROME_OFFSET = 11644473600  # microseconds-since-1601 -> unix
MAC_OFFSET = 978307200
BUNDLE = "com.retrace.downloads"

_CHROME = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "History"
_SAFARI_DL = Path.home() / "Library" / "Safari" / "Downloads.plist"


def _row(when: datetime, name: str, where: str, key: str) -> dict:
    chash = hashlib.sha256(f"dl:{key}".encode()).hexdigest()
    return {
        "captured_at": when, "app_name": "Downloads", "window_title": where,
        "text": f"{name}\n{where}", "caption": f"⬇️ {name}", "caption_model": "downloads",
        "content_hash": chash,
    }


def _chrome_downloads() -> list[dict]:
    if not _CHROME.exists():
        return []
    rows = []
    try:
        fd, tmp = tempfile.mkstemp(suffix=".chromedl.db")
        Path(tmp).write_bytes(_CHROME.read_bytes())
    except OSError:
        return []
    try:
        conn = sqlite3.connect(tmp, timeout=2)
        cur = conn.execute(
            "SELECT id, target_path, start_time, tab_url FROM downloads ORDER BY start_time DESC LIMIT 500"
        )
        for did, path, start, url in cur:
            if not path:
                continue
            when = datetime.fromtimestamp((start or 0) / 1_000_000 - CHROME_OFFSET, timezone.utc).replace(tzinfo=None) if start else datetime.now(timezone.utc).replace(tzinfo=None)
            rows.append(_row(when, Path(path).name, url or path, f"chrome:{did}:{path}"))
        conn.close()
    except sqlite3.Error:
        pass
    finally:
        Path(tmp).unlink(missing_ok=True)
    return rows


def _safari_downloads() -> list[dict]:
    if not _SAFARI_DL.exists():
        return []
    try:
        data = plistlib.loads(_SAFARI_DL.read_bytes())
    except Exception:
        return []
    rows = []
    for entry in data.get("DownloadHistory", []) if isinstance(data, dict) else []:
        path = entry.get("DownloadEntryPath") or entry.get("DownloadEntryURL") or ""
        if not path:
            continue
        when = entry.get("DownloadEntryDateAddedKey")
        dt = when.replace(tzinfo=timezone.utc).replace(tzinfo=None) if hasattr(when, "year") else datetime.now(timezone.utc).replace(tzinfo=None)
        ident = entry.get("DownloadEntryIdentifier") or path
        rows.append(_row(dt, Path(path).name, entry.get("DownloadEntryURL", path), f"safari:{ident}"))
    return rows


class DownloadsPlugin(RetracePlugin):
    name = "downloads"
    description = "Ingest browser downloads (Chrome + Safari) into the timeline."

    def collect(self, settings: Settings) -> dict:
        rows = _chrome_downloads() + _safari_downloads()
        return {"name": self.name, "ingested": ingest_captures(settings, BUNDLE, rows)}
