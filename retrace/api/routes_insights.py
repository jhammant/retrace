"""Insights over HTTP: what you repeat, steps for a stretch of time, and time per project.

Read-only and metadata-only: unlike the CLI and MCP tools there is no option to include
prompt, clipboard or screen text here.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from ..insights import mine, steps
from ..insights.projects import cached_events, project_activity, project_patterns, project_time

router = APIRouter(prefix="/insights", tags=["insights"])


def _when(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(400, f"{name} must be an ISO date or datetime") from exc
    return dt if dt.tzinfo else dt.astimezone()


@router.get("/patterns")
def patterns_route(days: int = Query(28, ge=1, le=120), limit: int = Query(25, ge=1, le=100)) -> dict:
    report = mine(cached_events(days=days))
    report["candidates"] = report["candidates"][:limit]
    return report


@router.get("/steps")
def steps_route(last_minutes: int = Query(30, ge=1, le=7 * 24 * 60), start: str | None = None,
                end: str | None = None) -> dict:
    now = datetime.now().astimezone()
    hi = _when(end, "end") or now
    lo = _when(start, "start") or hi - timedelta(minutes=last_minutes)
    return steps(start=lo, end=hi)


@router.get("/projects")
def projects_route(days: int = Query(7, ge=1, le=120)) -> dict:
    return project_time(days=days)


@router.get("/projects/{name}")
def project_route(name: str) -> dict:
    return project_activity(name)


@router.get("/projects/{name}/patterns")
def project_patterns_route(name: str, days: int = Query(28, ge=1, le=120)) -> dict:
    return project_patterns(name, days=days)
