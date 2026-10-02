"""Safari Reading List plugin — ingest saved articles, including their content.

Reads ``Bookmarks.plist`` for Reading List entries (title, URL, date, and Safari's
own ``PreviewText`` snippet). When "save articles for offline reading" is on, the
full page is archived locally under ``ReadingListArchives/<id>/*.webarchive`` — we
extract its readable text too. All on-device, zero network. Reading List syncs
from your iPhone via iCloud, so phone-saved articles land here with real content.
Needs Full Disk Access. Fails soft.
"""

from __future__ import annotations

import hashlib
import plistlib
from datetime import datetime, timezone
from pathlib import Path

from ...config import Settings
from ..base import RetracePlugin
from .._ingest import ingest_captures
from .page_backfill import html_to_text  # shared extractor (trafilatura → stdlib)

BUNDLE = "com.apple.Safari"
TEXT_SOURCE = "reading-list"
_MAX_TEXT = 12000


def _default_bookmarks_path() -> Path:
    return Path.home() / "Library" / "Safari" / "Bookmarks.plist"


def _default_archives_dir() -> Path:
    return Path.home() / "Library" / "Safari" / "ReadingListArchives"


def _iter_reading_list(node) -> list[dict]:
    """Walk the Bookmarks.plist tree and yield items that carry a ReadingList dict."""
    found: list[dict] = []
    if isinstance(node, dict):
        if isinstance(node.get("ReadingList"), dict):
            found.append(node)
        for child in node.get("Children", []) or []:
            found.extend(_iter_reading_list(child))
    return found


def _archive_text(archives_dir: Path, item_id: str | None) -> str:
    """Pull readable text from a saved ``.webarchive`` for this item, if present."""
    if not item_id:
        return ""
    folder = archives_dir / item_id.strip("{}")
    if not folder.is_dir():
        # ids are sometimes stored with/without braces; scan as a fallback
        return ""
    archives = list(folder.glob("*.webarchive"))
    if not archives:
        return ""
    try:
        with archives[0].open("rb") as fh:
            arch = plistlib.load(fh)
        data = arch.get("WebMainResource", {}).get("WebResourceData")
        if not isinstance(data, (bytes, bytearray)):
            return ""
        return html_to_text(bytes(data).decode("utf-8", "replace"))
    except (OSError, plistlib.InvalidFileException, ValueError):
        return ""


class ReadingListPlugin(RetracePlugin):
    name = "reading-list"
    description = "Ingest Safari Reading List articles + offline content (on-device)."

    def __init__(self, bookmarks_path: Path | None = None, archives_dir: Path | None = None) -> None:
        self._bookmarks = bookmarks_path
        self._archives = archives_dir

    def collect(self, settings: Settings) -> dict:
        from ...capture.privacy import is_sensitive

        bm = self._bookmarks or _default_bookmarks_path()
        archives_dir = self._archives or _default_archives_dir()
        if not bm.is_file():
            return {"name": self.name, "ingested": 0, "note": "no Safari Bookmarks.plist"}
        try:
            with bm.open("rb") as fh:
                tree = plistlib.load(fh)
        except (OSError, plistlib.InvalidFileException, ValueError):
            return {"name": self.name, "ingested": 0, "note": "Full Disk Access needed"}

        cap = int(getattr(settings, "reading_list_max", 300) or 300)
        rows: list[dict] = []
        skipped_sensitive = 0
        for item in _iter_reading_list(tree)[:cap]:
            url = item.get("URLString") or item.get("URIDictionary", {}).get("", "")
            if not url:
                continue
            rl = item.get("ReadingList", {})
            title = (item.get("URIDictionary", {}).get("title") or rl.get("Title") or "").strip()
            if is_sensitive({"url": url, "window_title": title}, settings):
                skipped_sensitive += 1
                continue
            added = rl.get("DateAdded")
            when = (added if isinstance(added, datetime) else datetime.now(timezone.utc))
            if when.tzinfo is not None:
                when = when.astimezone(timezone.utc).replace(tzinfo=None)
            preview = (rl.get("PreviewText") or "").strip()
            content = _archive_text(archives_dir, item.get("WebBookmarkUUID")) or preview
            label = title or url
            body = "\n\n".join(p for p in (label, content) if p)[:_MAX_TEXT]
            chash = hashlib.sha256(f"reading-list:{url}".encode()).hexdigest()
            rows.append({
                "captured_at": when, "app_name": "Safari", "window_title": title or None,
                "url": url, "text": body, "text_source": TEXT_SOURCE,
                "caption": f"📖 {label[:80]}", "caption_model": "reading-list",
                "content_hash": chash,
            })

        ingested = ingest_captures(settings, BUNDLE, rows)
        out = {"name": self.name, "ingested": ingested}
        if skipped_sensitive:
            out["skipped_sensitive"] = skipped_sensitive
        return out
