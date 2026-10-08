"""Activity ingest: macOS focus history + browser history + active sampling.

On Windows there is no knowledgeC or Safari; Chrome, Edge and Brave history are
read from their Windows profile folders and per-app time comes from the
idle-aware active samples.

Reads three on-device SQLite sources read-only (all fail soft if missing/locked/
forbidden), plus records idle-aware "active" samples from the daemon. An
incremental cutoff per source avoids re-reading old rows.

Sources:
- knowledgeC.db  ``/app/inFocus`` stream  -> per-app focus intervals
- Safari History.db                        -> per-URL visits
- Chrome History (copied first; Chrome locks it) -> per-URL visits
- Edge / Brave History (Windows)                -> per-URL visits
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..browsers import chrome_history, extra_chromium_histories
from ..config import Settings, get_settings
from ..db import session_scope
from ..models import ActivityEvent, utcnow
from ..native.helpers import get_presence
from ..tmpcopy import temp_copy

log = logging.getLogger("retrace.activity")

# CFAbsoluteTime epoch (2001-01-01) to Unix epoch, in seconds.
MAC_OFFSET = 978307200
# Chrome/WebKit time: microseconds since 1601-01-01.
CHROME_OFFSET = 11644473600

_KNOWLEDGEC = Path.home() / "Library" / "Application Support" / "Knowledge" / "knowledgeC.db"
_SAFARI = Path.home() / "Library" / "Safari" / "History.db"
_CHROME_APP, _CHROME = chrome_history()
# Chromium browsers besides Chrome, as (source, app id, History path).
_EXTRA_CHROMIUM = extra_chromium_histories()


def _state_path(s: Settings) -> Path:
    return s.home / "activity_state.json"


def _load_state(s: Settings) -> dict:
    p = _state_path(s)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {"cutoffs": {}}


def _save_state(s: Settings, state: dict) -> None:
    _state_path(s).write_text(json.dumps(state, indent=2, default=str))


def _utc_from_ts(ts: float) -> datetime:
    """Naive-UTC datetime from a Unix timestamp."""
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def _local_day(dt_utc: datetime) -> str:
    return dt_utc.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d")


def credited_seconds(elapsed_s: float | None, interval_s: float) -> float:
    """Use elapsed time unless a long gap indicates sleep or missed samples."""
    if elapsed_s is None or elapsed_s <= 0 or elapsed_s > max(2 * interval_s, 150):
        return float(interval_s)
    return float(elapsed_s)


def _ro_connect(path: Path, *, immutable: bool = True) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro" + ("&immutable=1" if immutable else "")
    return sqlite3.connect(uri, uri=True, timeout=2)


# --- readers (each returns a list of event dicts; fail soft) ----------------

def read_knowledgec(cutoff: datetime | None) -> list[dict]:
    if not _KNOWLEDGEC.exists():
        return []
    cutoff_mac = (cutoff.replace(tzinfo=timezone.utc).timestamp() - MAC_OFFSET) if cutoff else 0
    rows: list[dict] = []
    try:
        conn = _ro_connect(_KNOWLEDGEC)
    except sqlite3.Error as exc:
        log.info("knowledgeC unavailable (%s) — Full Disk Access may be required", exc)
        return []
    try:
        # The app-focus stream is '/app/inFocus' on older macOS and '/app/usage'
        # on macOS 26+. Query both for cross-version compatibility.
        cur = conn.execute(
            """
            SELECT ZVALUESTRING, ZSTARTDATE, ZENDDATE
            FROM ZOBJECT
            WHERE ZSTREAMNAME IN ('/app/inFocus', '/app/usage') AND ZSTARTDATE > ?
            ORDER BY ZSTARTDATE
            """,
            (cutoff_mac,),
        )
        for bundle, zstart, zend in cur:
            if zstart is None or not bundle:
                continue
            start = _utc_from_ts(zstart + MAC_OFFSET)
            end = _utc_from_ts(zend + MAC_OFFSET) if zend else None
            seconds = max(0.0, (end - start).total_seconds()) if end else 0.0
            rows.append({
                "source": "knowledgec", "app": bundle, "url": "", "title": None,
                "start_at": start, "end_at": end, "seconds": seconds,
                "day": _local_day(start), "detail": None,
            })
    except sqlite3.Error as exc:
        log.warning("knowledgeC read failed: %s", exc)
    finally:
        conn.close()
    return rows


def read_safari(cutoff: datetime | None) -> list[dict]:
    if not _SAFARI.exists():
        return []
    cutoff_mac = (cutoff.replace(tzinfo=timezone.utc).timestamp() - MAC_OFFSET) if cutoff else 0
    rows: list[dict] = []
    try:
        conn = _ro_connect(_SAFARI)
    except sqlite3.Error as exc:
        log.info("Safari history unavailable (%s)", exc)
        return []
    try:
        cur = conn.execute(
            """
            SELECT hi.url, hv.visit_time, hv.title
            FROM history_visits hv JOIN history_items hi ON hi.id = hv.history_item
            WHERE hv.visit_time > ?
            ORDER BY hv.visit_time
            """,
            (cutoff_mac,),
        )
        for url, vtime, title in cur:
            if vtime is None or not url:
                continue
            start = _utc_from_ts(vtime + MAC_OFFSET)
            rows.append({
                "source": "safari", "app": "com.apple.Safari", "url": url, "title": title,
                "start_at": start, "end_at": None, "seconds": 0.0,
                "day": _local_day(start), "detail": None,
            })
    except sqlite3.Error as exc:
        log.warning("Safari read failed: %s", exc)
    finally:
        conn.close()
    return rows


def read_chrome(cutoff: datetime | None) -> list[dict]:
    return read_chromium(cutoff, _CHROME, "chrome", _CHROME_APP)


def read_chromium(cutoff: datetime | None, path: Path, source: str, app: str) -> list[dict]:
    """Visits from any Chromium-family History database."""
    if not path.exists():
        return []
    cutoff_chrome = ((cutoff.replace(tzinfo=timezone.utc).timestamp() + CHROME_OFFSET) * 1_000_000) if cutoff else 0
    rows: list[dict] = []
    # Chrome locks its live DB; copy to a temp file first. temp_copy owns and
    # closes the descriptor -- the old inline mkstemp leaked one fd per scan.
    try:
        with temp_copy(path, suffix=f".{source}.db") as tmp:
            conn = sqlite3.connect(str(tmp), timeout=2)
            try:
                cur = conn.execute(
                    """
                    SELECT u.url, v.visit_time, u.title
                    FROM visits v JOIN urls u ON u.id = v.url
                    WHERE v.visit_time > ?
                    ORDER BY v.visit_time
                    """,
                    (cutoff_chrome,),
                )
                for url, vtime, title in cur:
                    if not vtime or not url:
                        continue
                    start = _utc_from_ts(vtime / 1_000_000 - CHROME_OFFSET)
                    rows.append({
                        "source": source, "app": app, "url": url, "title": title,
                        "start_at": start, "end_at": None, "seconds": 0.0,
                        "day": _local_day(start), "detail": None,
                    })
            except sqlite3.Error as exc:
                log.warning("%s history read failed: %s", source, exc)
            finally:
                conn.close()
    except OSError as exc:
        log.info("%s history copy failed (%s)", source, exc)
        return []
    return rows


# --- upsert + scan ----------------------------------------------------------

# Each row binds ~10 columns; keep chunks well under SQLite's variable limit
# (999 on old builds, 32766 on modern) so ingest works everywhere.
_UPSERT_CHUNK = 90


def _upsert(session, events: list[dict]) -> int:
    if not events:
        return 0
    now = utcnow()
    inserted = 0
    for i in range(0, len(events), _UPSERT_CHUNK):
        chunk = events[i : i + _UPSERT_CHUNK]
        stmt = sqlite_insert(ActivityEvent).values(
            [{**e, "created_at": now} for e in chunk]
        ).on_conflict_do_nothing(index_elements=["source", "app", "url", "start_at"])
        inserted += session.execute(stmt).rowcount or 0
    return inserted


def scan_and_upsert(full: bool = False, settings: Settings | None = None) -> dict:
    """Ingest new focus/browser activity. Returns per-source counts."""
    s = settings or get_settings()
    state = _load_state(s)
    cutoffs = {} if full else state.get("cutoffs", {})

    def cutoff_for(src: str) -> datetime | None:
        raw = cutoffs.get(src)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    readers = {
        "knowledgec": read_knowledgec,
        "safari": read_safari,
        "chrome": read_chrome,
    }
    for src, app, path in _EXTRA_CHROMIUM:
        readers[src] = lambda c, _p=path, _s=src, _a=app: read_chromium(c, _p, _s, _a)
    counts: dict[str, int] = {}
    new_cutoffs = dict(state.get("cutoffs", {}))
    total_upserted = 0

    with session_scope(s) as session:
        for src, reader in readers.items():
            events = reader(cutoff_for(src))
            counts[src] = len(events)
            if events:
                total_upserted += _upsert(session, events)
                latest = max(e["start_at"] for e in events)
                new_cutoffs[src] = latest.isoformat()

    state["cutoffs"] = new_cutoffs
    state["last_scan"] = utcnow().isoformat()
    _save_state(s, state)

    return {"upserted": total_upserted, "read": counts, "cutoffs": new_cutoffs, "full": full}


def record_active_sample(
    interval_s: float, app: str | None = None, settings: Settings | None = None,
    *, elapsed_s: float | None = None,
) -> bool:
    """Record an idle-aware active-time sample (source='active'). Returns True if stored."""
    s = settings or get_settings()
    pres = get_presence(s.idle_threshold_s, settings=s)
    if pres and pres.get("ok") and pres.get("present") is False:
        return False  # user away from keyboard
    idle = (pres or {}).get("idle_seconds")
    now = utcnow()
    seconds = credited_seconds(elapsed_s, interval_s)
    start = now - timedelta(seconds=seconds)
    event = {
        "source": "active", "app": app or "unknown", "url": "", "title": None,
        "start_at": start, "end_at": now, "seconds": seconds,
        "day": _local_day(start), "detail": {"idle_seconds": idle},
    }
    with session_scope(s) as session:
        _upsert(session, [event])
    return True


def activity_status(settings: Settings | None = None) -> dict:
    """Summarize ingest state + per-source row counts."""
    s = settings or get_settings()
    state = _load_state(s)
    from sqlalchemy import func

    with session_scope(s) as session:
        by_source = dict(
            session.execute(
                select(ActivityEvent.source, func.count()).group_by(ActivityEvent.source)
            ).all()
        )
    return {
        "last_scan": state.get("last_scan"),
        "cutoffs": state.get("cutoffs", {}),
        "rows_by_source": by_source,
        "sources_available": {
            "knowledgec": _KNOWLEDGEC.exists(),
            "safari": _SAFARI.exists(),
            "chrome": _CHROME.exists(),
            **{src: path.exists() for src, _app, path in _EXTRA_CHROMIUM},
        },
    }
