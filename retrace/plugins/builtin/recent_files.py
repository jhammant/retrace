"""Recent files plugin — ingest recently-used documents.

macOS asks Spotlight (``kMDItemLastUsedDate``); Windows reads the shortcuts it keeps
in ``%APPDATA%\\Microsoft\\Windows\\Recent``.
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from ...config import Settings
from ...platform import IS_WINDOWS, roaming_appdata
from .._ingest import ingest_captures
from ..base import RetracePlugin

BUNDLE = "com.retrace.recentfiles"
_SKIP_DIRS = ("/Library/", "/.Trash/", "/node_modules/", "/.git/", "/Caches/")
_SKIP_DIRS_WIN = ("\\AppData\\", "\\node_modules\\", "\\.git\\", "\\$Recycle.Bin\\")


def _path(path: str):
    """A path object for ``path`` in its own OS's syntax (Windows paths use backslashes)."""
    return PureWindowsPath(path) if "\\" in path else Path(path)


def _row(path: str, used_ts: float) -> dict:
    p = _path(path)
    when = datetime.fromtimestamp(used_ts, timezone.utc).replace(tzinfo=None)
    day = when.strftime("%Y%m%d")
    chash = hashlib.sha256(f"file:{path}:{day}".encode()).hexdigest()
    return {
        "captured_at": when, "app_name": "Files", "window_title": str(p.parent),
        "doc_path": path, "text": path, "caption": f"📄 {p.name}",
        "caption_model": "recent-files", "content_hash": chash,
    }


def windows_recent(recent_dir: Path, days: int, now: float | None = None) -> list[dict]:
    """Rows for documents Windows recorded as opened in the last ``days`` days."""
    import time

    from ...native.win.shortcuts import lnk_target

    cutoff = (now or time.time()) - days * 86400
    rows = []
    try:
        links = sorted(recent_dir.glob("*.lnk"), key=lambda q: q.stat().st_mtime, reverse=True)
    except OSError:
        return []
    for link in links[:400]:
        try:
            used = link.stat().st_mtime  # Windows rewrites the shortcut on each open
            if used < cutoff:
                break
            target = lnk_target(link.read_bytes())
        except OSError:
            continue
        if not target or any(skip in target for skip in _SKIP_DIRS_WIN):
            continue
        if _path(target).suffix == "":  # a folder, not a document
            continue
        rows.append(_row(target, used))
    return rows


class RecentFilesPlugin(RetracePlugin):
    name = "recent-files"
    description = "Ingest recently-opened documents (Spotlight on macOS, Recent items on Windows)."

    def collect(self, settings: Settings) -> dict:
        days = int(getattr(settings, "recent_files_days", 7) or 7)
        if IS_WINDOWS:
            rows = windows_recent(roaming_appdata() / "Microsoft" / "Windows" / "Recent", days)
            return {"name": self.name, "ingested": ingest_captures(settings, BUNDLE, rows)}
        secs = days * 86400
        try:
            out = subprocess.run(
                ["mdfind", "-onlyin", str(Path.home()),
                 f"kMDItemLastUsedDate >= $time.now(-{secs}) && kMDItemContentTypeTree == 'public.content'"],
                capture_output=True, text=True, timeout=12,
            )
        except (OSError, subprocess.SubprocessError):
            return {"name": self.name, "ingested": 0}
        rows = []
        for path in out.stdout.splitlines()[:400]:
            if not path or any(skip in path for skip in _SKIP_DIRS):
                continue
            try:
                mtime = Path(path).stat().st_mtime
            except OSError:
                continue
            rows.append(_row(path, mtime))
        return {"name": self.name, "ingested": ingest_captures(settings, BUNDLE, rows)}
