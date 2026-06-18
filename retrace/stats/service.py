"""Time/usage analytics derived from ``activity_events``.

Chooses the best available source for per-app time (knowledgeC focus intervals
when present, else idle-aware ``active`` samples), apportions browser focus time
across domains by visit share, and produces daily/weekly rollups.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..capture.caption import domain_of
from ..config import Settings, get_settings
from ..db import session_scope
from ..models import ActivityEvent, Capture

BROWSER_BUNDLES = {
    "com.apple.Safari", "com.google.Chrome", "com.google.Chrome.canary",
    "com.brave.Browser", "com.microsoft.edgemac", "com.vivaldi.Vivaldi",
    "company.thebrowser.Browser",
}


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _day_bounds(date_str: str) -> tuple[datetime, datetime]:
    """Return UTC-naive [start, end) bounds for a local ``YYYY-MM-DD`` day."""
    d = datetime.fromisoformat(date_str)
    start_local = d.replace(tzinfo=_local_tz())
    end_local = start_local + timedelta(days=1)
    to_utc = lambda x: x.astimezone(timezone.utc).replace(tzinfo=None)
    return to_utc(start_local), to_utc(end_local)


def _range_bounds(start_str: str, end_str: str) -> tuple[datetime, datetime]:
    return _day_bounds(start_str)[0], _day_bounds(end_str)[1]


def _pretty_bundle(bundle: str) -> str:
    if not bundle:
        return "Unknown"
    tail = bundle.split(".")[-1]
    return tail[:1].upper() + tail[1:] if tail else bundle


def _bundle_name_map(session) -> dict[str, str]:
    rows = session.execute(
        select(Capture.bundle_id, Capture.app_name).where(Capture.bundle_id.isnot(None))
    ).all()
    m: dict[str, str] = {}
    for bid, name in rows:
        if bid and name and bid not in m:
            m[bid] = name
    return m


def time_per_app(start: datetime, end: datetime, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    with session_scope(s) as session:
        kc = session.execute(
            select(ActivityEvent.app, func.sum(ActivityEvent.seconds))
            .where(ActivityEvent.source == "knowledgec",
                   ActivityEvent.start_at >= start, ActivityEvent.start_at < end)
            .group_by(ActivityEvent.app)
        ).all()
        act = session.execute(
            select(ActivityEvent.app, func.sum(ActivityEvent.seconds))
            .where(ActivityEvent.source == "active",
                   ActivityEvent.start_at >= start, ActivityEvent.start_at < end)
            .group_by(ActivityEvent.app)
        ).all()
        name_map = _bundle_name_map(session)

    kc_total = sum((v or 0) for _, v in kc)
    active_total = sum((v or 0) for _, v in act)
    if kc_total > 0:
        # knowledgeC focus intervals can include away-from-keyboard time. When idle-aware
        # active samples have *substantial* coverage of the period, scale knowledgeC down
        # to the engaged total (excluding away time) while preserving per-app proportions.
        # When active coverage is thin (e.g. a fresh install), knowledgeC is the baseline —
        # otherwise a few minutes of samples would wrongly shrink days of history.
        source = "knowledgec"
        scale = 1.0
        if active_total > 0 and active_total < kc_total:
            coverage = active_total / kc_total
            if coverage >= 0.5:
                scale = coverage
                source = "knowledgec+active"
        items = [
            {"app": name_map.get(b, _pretty_bundle(b)), "bundle_id": b,
             "seconds": (v or 0.0) * scale}
            for b, v in kc
        ]
    else:
        source = "active"
        items = [{"app": a, "bundle_id": None, "seconds": v or 0.0} for a, v in act]

    total = sum(i["seconds"] for i in items)
    for i in items:
        i["share"] = (i["seconds"] / total) if total else 0.0
    items.sort(key=lambda i: i["seconds"], reverse=True)
    return {"source": source, "total_seconds": total, "apps": items}


def time_per_domain(start: datetime, end: datetime, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    with session_scope(s) as session:
        visits = session.execute(
            select(ActivityEvent.url, func.count())
            .where(ActivityEvent.source.in_(["safari", "chrome"]),
                   ActivityEvent.start_at >= start, ActivityEvent.start_at < end)
            .group_by(ActivityEvent.url)
        ).all()
        browser_focus = session.execute(
            select(func.sum(ActivityEvent.seconds))
            .where(ActivityEvent.source == "knowledgec",
                   ActivityEvent.app.in_(BROWSER_BUNDLES),
                   ActivityEvent.start_at >= start, ActivityEvent.start_at < end)
        ).scalar() or 0.0

    dom: dict[str, int] = {}
    for url, cnt in visits:
        d = domain_of(url) or "(unknown)"
        dom[d] = dom.get(d, 0) + cnt
    total_visits = sum(dom.values())

    domains = [
        {"domain": d, "visits": c,
         "seconds": (browser_focus * c / total_visits) if total_visits else 0.0}
        for d, c in dom.items()
    ]
    total_seconds = sum(x["seconds"] for x in domains)
    for x in domains:
        x["share"] = (x["seconds"] / total_seconds) if total_seconds else 0.0
    domains.sort(key=lambda x: x["visits"], reverse=True)
    return {"total_seconds": total_seconds, "total_visits": total_visits, "domains": domains}


def top(start_date: str, end_date: str, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    start, end = _range_bounds(start_date, end_date)
    apps = time_per_app(start, end, s)
    domains = time_per_domain(start, end, s)
    return {
        "start": start_date, "end": end_date,
        "source": apps["source"],
        "total_seconds": apps["total_seconds"],
        "apps": apps["apps"],
        "domains": domains["domains"],
    }


def daily(date_str: str, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    start, end = _day_bounds(date_str)
    apps = time_per_app(start, end, s)
    domains = time_per_domain(start, end, s)
    with session_scope(s) as session:
        active = session.execute(
            select(func.sum(ActivityEvent.seconds))
            .where(ActivityEvent.source == "active",
                   ActivityEvent.start_at >= start, ActivityEvent.start_at < end)
        ).scalar() or 0.0
        captures = session.execute(
            select(func.count()).select_from(Capture)
            .where(Capture.captured_at >= start, Capture.captured_at < end)
        ).scalar() or 0
    return {
        "date": date_str,
        "total_seconds": apps["total_seconds"],
        "active_seconds": active,
        "captures": captures,
        "apps": apps["apps"][:10],
        "domains": domains["domains"][:10],
    }


def system_series(date_str: str, settings: Settings | None = None) -> dict:
    """Return the CPU/memory time series (source='system') for a local day."""
    s = settings or get_settings()
    start, end = _day_bounds(date_str)
    with session_scope(s) as session:
        rows = session.execute(
            select(ActivityEvent.start_at, ActivityEvent.detail)
            .where(ActivityEvent.source == "system",
                   ActivityEvent.start_at >= start, ActivityEvent.start_at < end)
            .order_by(ActivityEvent.start_at)
        ).all()
    series = []
    for at, detail in rows:
        d = detail or {}
        series.append({
            "at": at.replace(tzinfo=timezone.utc).isoformat(),
            "cpu": d.get("cpu_percent"),
            "mem": d.get("mem_percent"),
            "load": d.get("load_1m"),
        })
    peaks = {
        "cpu_max": max((p["cpu"] for p in series if p["cpu"] is not None), default=0),
        "mem_max": max((p["mem"] for p in series if p["mem"] is not None), default=0),
    }
    return {"date": date_str, "count": len(series), "series": series, **peaks}


def weekly(week: str | None = None, settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    anchor = datetime.fromisoformat(week).date() if week else datetime.now().date()
    start_date = anchor - timedelta(days=6)
    days = []
    for i in range(7):
        d = (start_date + timedelta(days=i)).isoformat()
        ds, de = _day_bounds(d)
        secs = time_per_app(ds, de, s)["total_seconds"]
        days.append({"day": d, "seconds": secs})
    total = sum(d["seconds"] for d in days)
    rng = top(days[0]["day"], days[-1]["day"], s)
    return {
        "start": days[0]["day"], "end": days[-1]["day"],
        "days": days, "total_seconds": total,
        "top_apps": rng["apps"][:8], "top_domains": rng["domains"][:8],
    }
