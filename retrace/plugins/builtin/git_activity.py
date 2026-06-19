"""Git commits plugin — ingest your recent commits across local repos."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ...config import Settings
from .._ingest import ingest_captures
from ..base import RetracePlugin

BUNDLE = "com.apple.git"
_SEP = "\x00"  # git emits NUL via %x00; we split the OUTPUT on it


class GitActivityPlugin(RetracePlugin):
    name = "git-commits"
    description = "Ingest your recent git commits across local repos into the timeline."

    def collect(self, settings: Settings) -> dict:
        roots = [Path(r).expanduser() for r in getattr(settings, "git_repo_roots", [])]
        repos: list[Path] = []
        for root in roots:
            if not root.is_dir():
                continue
            for git in list(root.glob("*/.git")) + list(root.glob("*/*/.git")):
                repos.append(git.parent)

        rows = []
        for repo in repos[:300]:
            try:
                out = subprocess.run(
                    ["git", "-C", str(repo), "log", "--all", "--since=60 days ago",
                     "--pretty=%H%x00%ct%x00%an%x00%s", "-n", "300"],
                    capture_output=True, text=True, timeout=8,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if out.returncode != 0:
                continue
            for line in out.stdout.splitlines():
                parts = line.split(_SEP)
                if len(parts) < 4:
                    continue
                sha, ct, author, subj = parts
                try:
                    when = datetime.fromtimestamp(int(ct), timezone.utc).replace(tzinfo=None)
                except ValueError:
                    continue
                chash = hashlib.sha256(f"git:{sha}".encode()).hexdigest()
                rows.append({
                    "captured_at": when, "app_name": "Git", "window_title": repo.name,
                    "text": f"{subj}\n\n{repo.name} · {sha[:9]} · {author}",
                    "caption": f"⎇ {repo.name}: {subj[:70]}", "caption_model": "git",
                    "content_hash": chash,
                })
        return {"name": self.name, "ingested": ingest_captures(settings, BUNDLE, rows),
                "repos": len(repos)}
