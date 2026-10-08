"""Repair legacy active-time rows in an explicitly selected SQLite database."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .service import _local_day, credited_seconds


def repair(db_path: Path, *, interval_s: float, recredit: bool = False,
           dry_run: bool = False) -> tuple[dict[str, float], dict[str, float], int]:
    """Return before/after local-day totals and changed row count."""
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    before: dict[str, float] = defaultdict(float)
    after: dict[str, float] = defaultdict(float)
    changed = 0
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, end_at, seconds, day, start_at FROM activity_events "
            "WHERE source='active' AND end_at IS NOT NULL ORDER BY end_at, id"
        ).fetchall()
        previous_end: datetime | None = None
        updates = []
        for row_id, end_raw, old_seconds, old_day, old_start in rows:
            end = datetime.fromisoformat(end_raw)
            gap = (end - previous_end).total_seconds() if previous_end else None
            seconds = credited_seconds(gap, interval_s) if recredit else float(old_seconds)
            start = end - timedelta(seconds=seconds)
            day = _local_day(start)
            before[old_day] += float(old_seconds)
            after[day] += seconds
            new_start = start.isoformat(sep=" ")
            if datetime.fromisoformat(old_start) != start or old_day != day or float(old_seconds) != seconds:
                updates.append((new_start, day, seconds, row_id))
            previous_end = end
        changed = len(updates)
        if not dry_run:
            conn.executemany(
                "UPDATE activity_events SET start_at=?, day=?, seconds=? WHERE id=?", updates
            )
    return dict(before), dict(after), changed
