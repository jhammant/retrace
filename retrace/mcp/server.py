"""Read-only MCP server for Retrace.

Exposes the timeline, search, and stats to other agents/assistants over MCP.
There are **no write or destructive tools** — nothing here can start/stop capture,
purge, or change config. It reads the same on-device SQLite store as the API.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from ..api.serializers import activity_brief, capture_brief, capture_full
from ..config import get_settings
from ..db import init_db, session_scope
from ..models import Capture
from ..search.service import search as _search
from ..stats.service import _range_bounds, daily as _daily, time_per_app, time_per_domain, weekly as _weekly

mcp = FastMCP("retrace")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    try:
        if len(v) == 10:  # YYYY-MM-DD -> local midnight
            d = datetime.fromisoformat(v).replace(tzinfo=datetime.now().astimezone().tzinfo)
            return d.astimezone(timezone.utc).replace(tzinfo=None)
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


@mcp.tool()
def retrace_search(query: str, mode: str = "hybrid", start: str | None = None,
                   end: str | None = None, app: str | None = None, limit: int = 25) -> dict:
    """Search your captured history.

    mode: 'text' (keywords/FTS), 'semantic' (meaning), or 'hybrid' (both).
    start/end: optional ISO dates/datetimes. app: optional app-name filter.
    Returns ranked matches with id, time, app, window, caption, and a snippet.
    """
    if mode not in ("text", "semantic", "hybrid"):
        mode = "hybrid"
    return _search(query, mode=mode, start=start, end=end, app=app, limit=limit)


@mcp.tool()
def retrace_timeline(start: str, end: str, app: str | None = None, limit: int = 100) -> dict:
    """Return captures within a time window, in chronological order.

    start/end are ISO dates or datetimes. app optionally filters by app name.
    """
    s = get_settings()
    lo, hi = _parse_dt(start), _parse_dt(end)
    with session_scope(s) as session:
        stmt = select(Capture)
        if lo:
            stmt = stmt.where(Capture.captured_at >= lo)
        if hi:
            stmt = stmt.where(Capture.captured_at < hi)
        if app:
            stmt = stmt.where(Capture.app_name == app)
        stmt = stmt.order_by(Capture.captured_at).limit(limit)
        rows = session.execute(stmt).scalars().all()
        return {"start": start, "end": end, "count": len(rows),
                "captures": [capture_brief(r) for r in rows]}


@mcp.tool()
def retrace_get_capture(capture_id: int) -> dict:
    """Get the full record for one capture: text, caption, metadata, thumbnail URL."""
    s = get_settings()
    with session_scope(s) as session:
        row = session.get(Capture, capture_id)
        if not row:
            return {"error": "not found", "id": capture_id}
        return capture_full(row)


@mcp.tool()
def retrace_what_was_i_doing(at: str, window_minutes: int = 15) -> dict:
    """Summarize what you were doing around a timestamp.

    Returns the captures within +/- window_minutes of ``at``, the closest one,
    the apps seen, and any correlated calendar events.
    """
    s = get_settings()
    at_dt = _parse_dt(at)
    if not at_dt:
        return {"error": "could not parse 'at'", "at": at}
    lo = at_dt - timedelta(minutes=window_minutes)
    hi = at_dt + timedelta(minutes=window_minutes)
    with session_scope(s) as session:
        rows = session.execute(
            select(Capture).where(Capture.captured_at >= lo, Capture.captured_at <= hi)
            .order_by(Capture.captured_at)
        ).scalars().all()
        captures = [capture_brief(r) for r in rows]
        closest = None
        if rows:
            closest = min(rows, key=lambda r: abs((r.captured_at - at_dt).total_seconds()))
        apps = sorted({r.app_name for r in rows if r.app_name})
        events = sorted({r.calendar_event for r in rows if r.calendar_event})
    return {
        "at": at, "window_minutes": window_minutes,
        "closest": capture_brief(closest) if closest else None,
        "apps_seen": apps, "calendar_events": events,
        "count": len(captures), "captures": captures,
    }


@mcp.tool()
def retrace_stats(start: str, end: str, group_by: str = "app") -> dict:
    """Time analytics for a date range. group_by: 'app', 'domain', or 'day'."""
    s = get_settings()
    lo, hi = _range_bounds(start[:10], end[:10])
    if group_by == "domain":
        return {"start": start, "end": end, "group_by": "domain", **time_per_domain(lo, hi, s)}
    if group_by == "day":
        wk = _weekly(end[:10], s)
        return {"start": start, "end": end, "group_by": "day", "days": wk["days"],
                "total_seconds": wk["total_seconds"]}
    return {"start": start, "end": end, "group_by": "app", **time_per_app(lo, hi, s)}


@mcp.tool()
def retrace_now() -> dict:
    """Return the most recent capture (what you're doing right now)."""
    s = get_settings()
    with session_scope(s) as session:
        row = session.execute(
            select(Capture).order_by(Capture.captured_at.desc()).limit(1)
        ).scalars().first()
        return capture_full(row) if row else {"error": "no captures yet"}


@mcp.tool()
def retrace_list_apps(start: str | None = None, end: str | None = None) -> dict:
    """List apps seen in the timeline (optionally within a range), with capture counts."""
    s = get_settings()
    lo, hi = _parse_dt(start), _parse_dt(end)
    with session_scope(s) as session:
        stmt = select(Capture.app_name, func.count()).group_by(Capture.app_name)
        if lo:
            stmt = stmt.where(Capture.captured_at >= lo)
        if hi:
            stmt = stmt.where(Capture.captured_at < hi)
        rows = session.execute(stmt).all()
    apps = sorted(
        [{"app": a, "captures": n} for a, n in rows if a],
        key=lambda x: x["captures"], reverse=True,
    )
    return {"start": start, "end": end, "apps": apps}


def main() -> None:
    """Entry point for ``retrace mcp`` — runs the stdio MCP server."""
    init_db()
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
