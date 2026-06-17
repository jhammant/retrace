"""Claude Code plugin — ingest CLI session transcripts into the timeline.

Reads ``~/.claude/projects/<project>/<session>.jsonl`` transcripts and creates one
capture per user turn (the prompt + Claude's reply), so your Claude Code history
is searchable in Retrace alongside everything else. Incremental (per-file mtime)
and idempotent (dedup by session+message uuid).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from ...config import Settings
from ...db import session_scope
from ...models import Capture, utcnow
from ..base import RetracePlugin

log = logging.getLogger("retrace.plugins.claude_code")

BUNDLE_ID = "com.anthropic.claude-code"
_MAX_TEXT = 8000
_MAX_TURNS_PER_FILE = 400
_DEFAULT_TOTAL_CAP = 4000


def _parse_ts(value) -> datetime:
    if not value:
        return utcnow()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
            timezone.utc
        ).replace(tzinfo=None)
    except ValueError:
        return utcnow()


def _assistant_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(p for p in parts if p)
    return ""


class ClaudeCodePlugin(RetracePlugin):
    name = "claude-code"
    description = "Ingest Claude Code session transcripts into the timeline."

    def __init__(self, projects_dir: Path | None = None) -> None:
        self._projects = projects_dir or (Path.home() / ".claude" / "projects")

    # --- state ------------------------------------------------------------
    def _state_path(self, s: Settings) -> Path:
        return s.home / "plugin_claude_code.json"

    def _load_state(self, s: Settings) -> dict:
        p = self._state_path(s)
        if p.exists():
            try:
                return json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        return {"mtimes": {}}

    def _save_state(self, s: Settings, state: dict) -> None:
        self._state_path(s).write_text(json.dumps(state, indent=2, default=str))

    # --- collect ----------------------------------------------------------
    def collect(self, settings: Settings) -> dict:
        if not self._projects.is_dir():
            return {"name": self.name, "ingested": 0, "note": "no ~/.claude/projects"}

        state = self._load_state(settings)
        mtimes: dict[str, float] = dict(state.get("mtimes", {}))
        total_cap = int(getattr(settings, "claude_code_total_cap", _DEFAULT_TOTAL_CAP) or _DEFAULT_TOTAL_CAP)

        # Existing keys so re-ingest is idempotent without per-row queries.
        with session_scope(settings) as session:
            existing = {
                h for (h,) in session.execute(
                    select(Capture.content_hash).where(Capture.bundle_id == BUNDLE_ID)
                ).all() if h
            }

        files = sorted(self._projects.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        ingested = 0
        files_scanned = 0
        capped = False

        for path in files:
            if ingested >= total_cap:
                capped = True
                break
            key = str(path)
            mt = path.stat().st_mtime
            if mtimes.get(key) == mt:
                continue  # unchanged since last run
            files_scanned += 1
            turns = self._parse_file(path)
            rows = []
            for turn in turns:
                chash = hashlib.sha256(f"{turn['session']}:{turn['uuid']}".encode()).hexdigest()
                if chash in existing:
                    continue
                existing.add(chash)
                rows.append((turn, chash))
            if rows:
                with session_scope(settings) as session:
                    for turn, chash in rows:
                        session.add(Capture(
                            captured_at=turn["ts"],
                            app_name="Claude Code", bundle_id=BUNDLE_ID,
                            window_title=turn["project"], doc_path=turn["cwd"],
                            text=turn["text"][:_MAX_TEXT], text_len=len(turn["text"][:_MAX_TEXT]),
                            text_source="plugin", caption=turn["caption"],
                            caption_model="claude-code-plugin", content_hash=chash,
                        ))
                        ingested += 1
                        if ingested >= total_cap:
                            capped = True
                            break
            mtimes[key] = mt

        state["mtimes"] = mtimes
        state["last_collect"] = utcnow().isoformat()
        self._save_state(settings, state)

        result = {"name": self.name, "ingested": ingested, "files_scanned": files_scanned}
        if capped:
            result["capped_at"] = total_cap
            log.info("claude-code collect hit cap of %d turns; re-run to continue", total_cap)
        return result

    def _parse_file(self, path: Path) -> list[dict]:
        title = None
        cwd = None
        session_id = path.stem
        turns: list[dict] = []
        pending: dict | None = None

        def flush():
            nonlocal pending
            if pending and pending.get("user"):
                user = pending["user"].strip()
                assistant = pending.get("assistant", "").strip()
                text = f"You: {user}"
                if assistant:
                    text += f"\n\nClaude: {assistant}"
                project = Path(pending["cwd"]).name if pending.get("cwd") else (title or session_id)
                cap = title or (user.splitlines()[0][:80] if user else "Claude Code session")
                turns.append({
                    "session": session_id, "uuid": pending["uuid"], "ts": pending["ts"],
                    "text": text, "caption": cap, "project": project, "cwd": pending.get("cwd"),
                })
            pending = None

        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if len(turns) >= _MAX_TURNS_PER_FILE:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    t = d.get("type")
                    if t == "ai-title":
                        title = d.get("aiTitle") or title
                        continue
                    if d.get("cwd"):
                        cwd = d.get("cwd")
                    msg = d.get("message")
                    if t == "user" and isinstance(msg, dict) and isinstance(msg.get("content"), str):
                        flush()
                        pending = {
                            "user": msg["content"], "assistant": "",
                            "ts": _parse_ts(d.get("timestamp")), "uuid": d.get("uuid") or "",
                            "cwd": cwd,
                        }
                    elif t == "assistant" and isinstance(msg, dict) and pending is not None:
                        txt = _assistant_text(msg.get("content"))
                        if txt:
                            pending["assistant"] = (pending["assistant"] + "\n" + txt).strip()
            flush()
        except OSError as exc:
            log.warning("could not read %s: %s", path, exc)
        return turns
