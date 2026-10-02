"""Downloads plugin — ingest browser downloads (Chrome + Safari; Chrome, Edge + Brave on Windows)."""

from __future__ import annotations

import hashlib
import plistlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ...browsers import chrome_history, extra_chromium_histories
from ...tmpcopy import temp_copy

from ...config import Settings
from .._ingest import ingest_captures
from ..base import RetracePlugin

CHROME_OFFSET = 11644473600  # microseconds-since-1601 -> unix
MAC_OFFSET = 978307200
BUNDLE = "com.retrace.downloads"

_CHROME = chrome_history()[1]
_EXTRA_CHROMIUM = [(src, path) for src, _app, path in extra_chromium_histories()]
_SAFARI_DL = Path.home() / "Library" / "Safari" / "Downloads.plist"


def _row(when: datetime, name: str, where: str, key: str) -> dict:
    chash = hashlib.sha256(f"dl:{key}".encode()).hexdigest()
    return {
        "captured_at": when, "app_name": "Downloads", "window_title": where,
        "text": f"{name}\n{where}", "caption": f"⬇️ {name}", "caption_model": "downloads",
        "content_hash": chash,
    }


def _chrome_downloads(path: Path | None = None, source: str = "chrome") -> list[dict]:
    path = path or _CHROME
    if not path.exists():
        return []
    rows = []
    try:
        with temp_copy(path, suffix=f".{source}dl.db") as tmp:
            conn = sqlite3.connect(str(tmp), timeout=2)
            try:
                cur = conn.execute(
                    "SELECT id, target_path, start_time, tab_url FROM downloads ORDER BY start_time DESC LIMIT 500"
                )
                for did, target, start, url in cur:
                    if not target:
                        continue
                    when = datetime.fromtimestamp((start or 0) / 1_000_000 - CHROME_OFFSET, timezone.utc).replace(tzinfo=None) if start else datetime.now(timezone.utc).replace(tzinfo=None)
                    rows.append(_row(when, Path(target).name, url or target, f"{source}:{did}:{target}"))
            except sqlite3.Error:
                pass
            finally:
                conn.close()
    except OSError:
        return []
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
    description = "Ingest browser downloads (Chrome, Edge, Brave, Safari) into the timeline."

    def collect(self, settings: Settings) -> dict:
        rows = _chrome_downloads() + _safari_downloads()
        for source, path in _EXTRA_CHROMIUM:
            rows += _chrome_downloads(path, source)
        return {"name": self.name, "ingested": ingest_captures(settings, BUNDLE, rows)}
