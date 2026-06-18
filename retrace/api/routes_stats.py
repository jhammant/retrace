"""Stats endpoints: daily / weekly / top rollups."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query

from ..stats.service import daily, system_series, top, weekly

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/daily")
def stats_daily(day: str | None = Query(None, alias="date")) -> dict:
    return daily(day or date.today().isoformat())


@router.get("/system")
def stats_system(day: str | None = Query(None, alias="date")) -> dict:
    return system_series(day or date.today().isoformat())


@router.get("/weekly")
def stats_weekly(week: str | None = None) -> dict:
    return weekly(week)


@router.get("/top")
def stats_top(start: str | None = None, end: str | None = None) -> dict:
    today = date.today()
    end_d = end or today.isoformat()
    start_d = start or (today - timedelta(days=6)).isoformat()
    return top(start_d, end_d)
