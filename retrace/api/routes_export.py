"""Export your captures / activity as JSON or CSV (your data, portable)."""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta, timezone

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import select

from ..db import session_scope
from ..models import ActivityEvent, Capture, utcnow

router = APIRouter(prefix="/export", tags=["export"])


def _iso(dt) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt else ""


def _csv_response(rows: list[dict], filename: str) -> Response:
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _json_response(rows: list[dict], filename: str) -> Response:
    return Response(
        content=json.dumps(rows, indent=2, default=str), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/captures")
def export_captures(
    format: str = Query("json", pattern="^(json|csv)$"),
    days: int = Query(30, ge=1, le=3650),
    limit: int = Query(20000, ge=1, le=200000),
) -> Response:
    cutoff = utcnow() - timedelta(days=days)
    with session_scope() as s:
        rows = s.execute(
            select(Capture).where(Capture.captured_at >= cutoff)
            .order_by(Capture.captured_at.desc()).limit(limit)
        ).scalars().all()
        data = [{
            "id": r.id, "captured_at": _iso(r.captured_at), "app": r.app_name or "",
            "bundle_id": r.bundle_id or "", "window": r.window_title or "",
            "url": r.url or "", "caption": r.caption or "", "text_source": r.text_source,
            "text_len": r.text_len, "text": r.text or "",
        } for r in rows]
    fn = f"retrace-captures.{format}"
    return _csv_response(data, fn) if format == "csv" else _json_response(data, fn)


@router.get("/activity")
def export_activity(
    format: str = Query("json", pattern="^(json|csv)$"),
    days: int = Query(30, ge=1, le=3650),
    limit: int = Query(200000, ge=1, le=1000000),
) -> Response:
    cutoff = utcnow() - timedelta(days=days)
    with session_scope() as s:
        rows = s.execute(
            select(ActivityEvent).where(ActivityEvent.start_at >= cutoff)
            .order_by(ActivityEvent.start_at.desc()).limit(limit)
        ).scalars().all()
        data = [{
            "id": r.id, "source": r.source, "app": r.app, "title": r.title or "",
            "url": r.url or "", "start_at": _iso(r.start_at), "end_at": _iso(r.end_at),
            "seconds": r.seconds, "day": r.day,
            "detail": json.dumps(r.detail) if r.detail else "",
        } for r in rows]
    fn = f"retrace-activity.{format}"
    return _csv_response(data, fn) if format == "csv" else _json_response(data, fn)
