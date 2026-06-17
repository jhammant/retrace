"""Activity ingest + per-app/per-domain time endpoints."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter

from ..activity.service import activity_status, scan_and_upsert
from ..stats.service import _range_bounds, time_per_app, time_per_domain

router = APIRouter(prefix="/activity", tags=["activity"])


def _default_range(start: str | None, end: str | None) -> tuple[str, str]:
    today = date.today()
    end_d = end or today.isoformat()
    start_d = start or (today - timedelta(days=6)).isoformat()
    return start_d, end_d


@router.post("/scan")
def activity_scan(full: bool = False) -> dict:
    return scan_and_upsert(full=full)


@router.get("/status")
def activity_status_route() -> dict:
    return activity_status()


@router.get("/apps")
def activity_apps(start: str | None = None, end: str | None = None) -> dict:
    start_d, end_d = _default_range(start, end)
    s, e = _range_bounds(start_d, end_d)
    return {"start": start_d, "end": end_d, **time_per_app(s, e)}


@router.get("/domains")
def activity_domains(start: str | None = None, end: str | None = None) -> dict:
    start_d, end_d = _default_range(start, end)
    s, e = _range_bounds(start_d, end_d)
    return {"start": start_d, "end": end_d, **time_per_domain(s, e)}
