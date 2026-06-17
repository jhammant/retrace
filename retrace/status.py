"""The capture status ledger persisted at ``~/.retrace/status.json``.

Tracks the enabled flag, last-capture bookkeeping for dedup, per-day counters
(reset on date rollover), the last error, and the last-known permission state.
Writes are atomic (temp file + ``os.replace``).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings, get_settings


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _local_day() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _empty_counters(day: str) -> dict[str, Any]:
    return {
        "day": day,
        "captured": 0,        # cycles that grabbed a frame
        "stored": 0,          # rows actually persisted
        "skipped_dupe": 0,    # content unchanged within dedup window
        "skipped_denylist": 0,
        "skipped_sensitive": 0,  # adult/sensitive content blocked
        "skipped_gated": 0,   # disabled / idle / power / hidden gating
        "errors": 0,
    }


def _default_state() -> dict[str, Any]:
    return {
        "enabled": False,
        "enabled_changed_at": None,
        "snooze_until": None,
        "last_capture_at": None,
        "last_hash": None,
        "last_hash_at": None,
        "last_app": None,
        "last_window": None,
        "last_error": None,
        "last_error_at": None,
        "permissions": {},
        "presence": {"present": None, "idle_seconds": None, "screen_locked": None,
                     "display_asleep": None, "at": None},
        "last_gate": None,
        "counters": _empty_counters(_local_day()),
        "updated_at": _utc_iso(),
    }


class StatusLedger:
    """Thread-safe accessor for the JSON status file."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._path: Path = self._settings.status_path
        self._lock = threading.Lock()

    # --- low-level io ------------------------------------------------------
    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return _default_state()
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return _default_state()
        # Merge onto defaults so new keys appear after upgrades.
        base = _default_state()
        base.update(data)
        # Day rollover: reset counters if they belong to a previous day.
        today = _local_day()
        if base.get("counters", {}).get("day") != today:
            base["counters"] = _empty_counters(today)
        return base

    def _write(self, data: dict[str, Any]) -> None:
        data["updated_at"] = _utc_iso()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".status-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2, sort_keys=False)
            os.replace(tmp, self._path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _mutate(self, fn) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            fn(data)
            self._write(data)
            return data

    # --- public api --------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._read()

    def is_enabled(self) -> bool:
        return bool(self._read().get("enabled"))

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        def _fn(d: dict[str, Any]) -> None:
            d["enabled"] = bool(enabled)
            d["enabled_changed_at"] = _utc_iso()
        return self._mutate(_fn)

    def set_snooze(self, until: datetime | None) -> dict[str, Any]:
        """Hidden mode: pause recording until ``until`` (UTC) or indefinitely.

        ``until=None`` clears the snooze (resume).
        """
        def _fn(d: dict[str, Any]) -> None:
            d["snooze_until"] = until.replace(microsecond=0).isoformat() if until else None
        return self._mutate(_fn)

    def is_snoozed(self) -> bool:
        raw = self._read().get("snooze_until")
        if not raw:
            return False
        if raw == "indefinite":
            return True
        try:
            until = datetime.fromisoformat(raw)
        except ValueError:
            return False
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < until

    def set_snooze_indefinite(self) -> dict[str, Any]:
        def _fn(d: dict[str, Any]) -> None:
            d["snooze_until"] = "indefinite"
        return self._mutate(_fn)

    def bump(self, counter: str, n: int = 1) -> None:
        def _fn(d: dict[str, Any]) -> None:
            today = _local_day()
            if d["counters"].get("day") != today:
                d["counters"] = _empty_counters(today)
            d["counters"][counter] = d["counters"].get(counter, 0) + n
        self._mutate(_fn)

    def record_capture(self, *, content_hash: str, app: str | None, window: str | None,
                       stored: bool) -> None:
        now = _utc_iso()

        def _fn(d: dict[str, Any]) -> None:
            today = _local_day()
            if d["counters"].get("day") != today:
                d["counters"] = _empty_counters(today)
            d["last_capture_at"] = now
            d["last_hash"] = content_hash
            d["last_hash_at"] = now
            d["last_app"] = app
            d["last_window"] = window
            d["counters"]["captured"] += 1
            if stored:
                d["counters"]["stored"] += 1
        self._mutate(_fn)

    def record_skip(self, reason: str) -> None:
        key = {
            "dupe": "skipped_dupe",
            "denylist": "skipped_denylist",
            "sensitive": "skipped_sensitive",
            "gated": "skipped_gated",
        }.get(reason, "skipped_gated")
        self.bump(key)

    def record_error(self, message: str) -> None:
        now = _utc_iso()

        def _fn(d: dict[str, Any]) -> None:
            today = _local_day()
            if d["counters"].get("day") != today:
                d["counters"] = _empty_counters(today)
            d["last_error"] = message
            d["last_error_at"] = now
            d["counters"]["errors"] += 1
        self._mutate(_fn)

    def set_permissions(self, permissions: dict[str, Any]) -> None:
        def _fn(d: dict[str, Any]) -> None:
            d["permissions"] = permissions
        self._mutate(_fn)

    def set_presence(self, presence: dict[str, Any]) -> None:
        snap = {
            "present": presence.get("present"),
            "idle_seconds": presence.get("idle_seconds"),
            "screen_locked": presence.get("screen_locked"),
            "display_asleep": presence.get("display_asleep"),
            "at": _utc_iso(),
        }

        def _fn(d: dict[str, Any]) -> None:
            d["presence"] = snap
        self._mutate(_fn)

    def set_gate(self, reason: str | None) -> None:
        def _fn(d: dict[str, Any]) -> None:
            d["last_gate"] = reason
        self._mutate(_fn)

    def last_hash_within(self, content_hash: str, window_s: float) -> bool:
        """True if ``content_hash`` matches the last hash recorded within ``window_s``."""
        d = self._read()
        if d.get("last_hash") != content_hash:
            return False
        ts = d.get("last_hash_at")
        if not ts:
            return False
        try:
            then = datetime.fromisoformat(ts)
        except ValueError:
            return False
        age = (datetime.now(timezone.utc) - then).total_seconds()
        return age <= window_s
