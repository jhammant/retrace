"""Recent files plugin — ingest recently-used documents via Spotlight (mdfind)."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ...config import Settings
from .._ingest import ingest_captures
from ..base import RetracePlugin

BUNDLE = "com.retrace.recentfiles"
_SKIP_DIRS = ("/Library/", "/.Trash/", "/node_modules/", "/.git/", "/Caches/")


class RecentFilesPlugin(RetracePlugin):
    name = "recent-files"
    description = "Ingest recently-opened documents (Spotlight kMDItemLastUsedDate)."

    def collect(self, settings: Settings) -> dict:
        days = int(getattr(settings, "recent_files_days", 7) or 7)
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
            p = Path(path)
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            when = datetime.fromtimestamp(mtime, timezone.utc).replace(tzinfo=None)
            day = when.strftime("%Y%m%d")
            chash = hashlib.sha256(f"file:{path}:{day}".encode()).hexdigest()
            rows.append({
                "captured_at": when, "app_name": "Files", "window_title": str(p.parent),
                "doc_path": path, "text": path, "caption": f"📄 {p.name}",
                "caption_model": "recent-files", "content_hash": chash,
            })
        return {"name": self.name, "ingested": ingest_captures(settings, BUNDLE, rows)}
