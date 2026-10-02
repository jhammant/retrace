"""Automatic storage optimiser: age-tiered thumbnail compaction plus an optional disk budget.

Thumbnails are written at capture time by ImageIO, whose quality scale keeps files large
(~250 KB for a 1280 px frame). Recent frames are the ones people scrub through, so they are
left alone. As a day ages it is re-encoded in place, once per tier:

- ``compact`` (default after 3 days): same size, JPEG quality 60. Roughly halves a frame.
- ``deep`` (default after 14 days): longest edge 960 px, quality 55. About a quarter of
  the original.

Text, OCR, captions and embeddings live in the database and are never touched, so search
works exactly as before. A day is recorded in ``optimize_state.json`` only once every frame in
it has been processed, so an interrupted run simply resumes next time.

``max_storage_mb`` (off by default) is a hard ceiling: when thumbnails plus database exceed it,
the oldest days lose their thumbnails first (their text rows stay searchable).
"""

from __future__ import annotations

import io
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import or_, text, update

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import Capture

log = logging.getLogger("retrace.optimize")

STATE_FILENAME = "optimize_state.json"
MIN_SAVING = 0.10          # only replace a frame when the new file is at least 10% smaller
KEEP_RECENT_DAYS = 2       # the budget never evicts today or yesterday
DRY_RUN_SAMPLE = 40        # frames sampled per tier to project savings without writing


@dataclass(frozen=True)
class Tier:
    name: str
    after_days: int
    max_edge: int
    quality: int


def tiers_for(s: Settings) -> list[Tier]:
    """Enabled tiers, gentlest first. A tier with ``after_days <= 0`` is switched off."""
    tiers = [
        Tier("compact", s.compact_after_days, s.thumb_max_edge, s.compact_jpeg_quality),
        Tier("deep", s.deep_compact_after_days, s.deep_compact_max_edge, s.deep_compact_jpeg_quality),
    ]
    return sorted((t for t in tiers if t.after_days > 0), key=lambda t: t.after_days)


def _day_dirs(s: Settings) -> list[tuple[date, Path]]:
    out: list[tuple[date, Path]] = []
    if not s.thumbs_dir.exists():
        return out
    for p in s.thumbs_dir.iterdir():
        if not p.is_dir():
            continue
        try:
            out.append((date.fromisoformat(p.name), p))
        except ValueError:
            continue
    return sorted(out)


def _frames(day_dir: Path) -> list[Path]:
    return sorted(p for p in day_dir.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg"))


def _dir_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def storage_usage(settings: Settings | None = None) -> dict:
    """Bytes used by thumbnails (total and per day) and by the database files."""
    s = settings or get_settings()
    by_day = {d.isoformat(): _dir_bytes(p) for d, p in _day_dirs(s)}
    thumbs = _dir_bytes(s.thumbs_dir) if s.thumbs_dir.exists() else 0
    db = 0
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(s.db_path) + suffix)
        if p.exists():
            db += p.stat().st_size
    return {"thumbs_bytes": thumbs, "db_bytes": db, "total_bytes": thumbs + db, "by_day": by_day}


def _load_state(s: Settings) -> dict:
    p = s.home / STATE_FILENAME
    try:
        data = json.loads(p.read_text())
        if isinstance(data, dict) and isinstance(data.get("days"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"days": {}}


def _save_state(s: Settings, state: dict) -> None:
    p = s.home / STATE_FILENAME
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    os.replace(tmp, p)


def _encode(path: Path, max_edge: int, quality: int) -> bytes | None:
    """Re-encode one frame; ``None`` if it can't be read (left untouched)."""
    from PIL import Image  # lazy: only the optimiser needs Pillow

    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            if max(im.size) > max_edge:
                im.thumbnail((max_edge, max_edge), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
            return buf.getvalue()
    except Exception:
        log.debug("could not re-encode %s", path, exc_info=True)
        return None


def _recompress(path: Path, tier: Tier) -> tuple[int, int]:
    """Re-encode ``path`` in place if that saves enough. Returns (bytes before, bytes after)."""
    try:
        before = path.stat().st_size
    except FileNotFoundError:  # purged meanwhile
        return 0, 0
    data = _encode(path, tier.max_edge, tier.quality)
    if data is None or len(data) > before * (1 - MIN_SAVING):
        return before, before
    tmp = path.with_name(path.name + ".opt")
    try:
        tmp.write_bytes(data)
        from PIL import Image

        with Image.open(tmp) as check:  # never replace a frame with something unreadable
            check.verify()
        os.replace(tmp, path)
    except Exception:
        log.debug("kept original %s", path, exc_info=True)
        tmp.unlink(missing_ok=True)
        return before, before
    return before, len(data)


def _projected_ratio(frames: list[Path], tier: Tier) -> float:
    sample = frames if len(frames) <= DRY_RUN_SAMPLE else random.Random(0).sample(frames, DRY_RUN_SAMPLE)
    before = after = 0
    for f in sample:
        try:
            b = f.stat().st_size
        except FileNotFoundError:
            continue
        data = _encode(f, tier.max_edge, tier.quality)
        a = len(data) if data is not None and len(data) <= b * (1 - MIN_SAVING) else b
        before += b
        after += a
    return after / before if before else 1.0


def _evict_day(s: Settings, day: str, day_dir: Path) -> int:
    """Delete a day's thumbnails and clear their rows' ``thumb_path``. Returns bytes freed."""
    freed = 0
    for f in _frames(day_dir):
        try:
            freed += f.stat().st_size
            f.unlink()
        except OSError:
            pass
    with session_scope(s) as session:
        session.execute(
            update(Capture)
            .where(or_(Capture.thumb_path.like(f"{day}/%"),
                       Capture.thumb_path.like(f"%/thumbs/{day}/%")))
            .values(thumb_path=None)
        )
    try:
        day_dir.rmdir()
    except OSError:
        pass
    return freed


def _maintain_db(s: Settings) -> dict:
    """Merge FTS segments and refresh planner stats; VACUUM only when much space is free."""
    out = {"fts_optimized": False, "vacuumed": False}
    try:
        with session_scope(s) as session:
            session.execute(text("INSERT INTO captures_fts(captures_fts) VALUES('optimize')"))
            session.execute(text("PRAGMA optimize"))
            pages = session.execute(text("PRAGMA page_count")).scalar() or 0
            free = session.execute(text("PRAGMA freelist_count")).scalar() or 0
        out["fts_optimized"] = True
        out["free_pages"] = free
        if pages and free / pages > 0.2:
            with session_scope(s) as session:
                session.execute(text("VACUUM"))
            out["vacuumed"] = True
    except Exception as exc:  # busy, or an older schema without FTS: not worth failing over
        out["error"] = str(exc)
    return out


def optimize(settings: Settings | None = None, *, dry_run: bool = False,
             max_seconds: float | None = None, today: date | None = None) -> dict:
    """Run one optimisation pass and return a report. ``dry_run`` writes nothing."""
    s = settings or get_settings()
    today = today or date.today()
    deadline = time.monotonic() + max_seconds if max_seconds else None
    tiers = tiers_for(s)
    rank = {t.name: i for i, t in enumerate(tiers)}
    state = _load_state(s)
    before = storage_usage(s)

    report: dict = {
        "dry_run": dry_run,
        "before_bytes": before["total_bytes"],
        "tiers": {t.name: {"after_days": t.after_days, "max_edge": t.max_edge, "quality": t.quality,
                           "days": 0, "frames": 0, "bytes_before": 0, "bytes_after": 0} for t in tiers},
        "incomplete": False,
    }

    pending: dict[str, list[Path]] = {t.name: [] for t in tiers}
    for day, day_dir in _day_dirs(s):
        age = (today - day).days
        due = [t for t in tiers if age >= t.after_days]
        if not due:
            continue
        target = due[-1]
        done = state["days"].get(day.isoformat())
        if done in rank and rank[done] >= rank[target.name]:
            continue
        frames = _frames(day_dir)
        stats = report["tiers"][target.name]
        stats["days"] += 1
        if dry_run:
            pending[target.name].extend(frames)
            continue
        finished = True
        for f in frames:
            if deadline and time.monotonic() > deadline:
                finished = False
                break
            b, a = _recompress(f, target)
            stats["frames"] += 1
            stats["bytes_before"] += b
            stats["bytes_after"] += a
        if not finished:
            report["incomplete"] = True
            break
        state["days"][day.isoformat()] = target.name

    if dry_run:
        for t in tiers:
            frames = pending[t.name]
            stats = report["tiers"][t.name]
            size = 0
            for f in frames:
                try:
                    size += f.stat().st_size
                except FileNotFoundError:
                    pass
            stats["frames"] = len(frames)
            stats["bytes_before"] = size
            stats["bytes_after"] = int(size * _projected_ratio(frames, t)) if frames else 0

    budget = {"max_storage_mb": s.max_storage_mb, "days_evicted": [], "bytes_freed": 0}
    if s.max_storage_mb > 0:
        limit = s.max_storage_mb * 1024 * 1024
        projected_saving = sum(v["bytes_before"] - v["bytes_after"] for v in report["tiers"].values())
        current = storage_usage(s)["total_bytes"] - (projected_saving if dry_run else 0)
        for day, day_dir in _day_dirs(s):
            if current <= limit or (today - day).days < KEEP_RECENT_DAYS:
                break
            size = _dir_bytes(day_dir) if dry_run else _evict_day(s, day.isoformat(), day_dir)
            budget["days_evicted"].append(day.isoformat())
            budget["bytes_freed"] += size
            current -= size
            if not dry_run:
                state["days"].pop(day.isoformat(), None)
    report["budget"] = budget

    if not dry_run:
        report["db"] = _maintain_db(s)
        state["last_run"] = datetime.now().isoformat(timespec="seconds")
        _save_state(s, state)
        after = storage_usage(s)["total_bytes"]
    else:
        after = before["total_bytes"] - budget["bytes_freed"] - sum(
            v["bytes_before"] - v["bytes_after"] for v in report["tiers"].values())
    report["after_bytes"] = after
    report["saved_bytes"] = before["total_bytes"] - after
    log.info("storage optimise: %s", {k: v for k, v in report.items() if k != "tiers"})
    return report
