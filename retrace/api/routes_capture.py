"""Capture endpoints: status, start/stop, manual tick, recent, image, purge."""

from __future__ import annotations

import gzip
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..capture.pipeline import capture_once
from ..capture.retention import purge_older_than
from ..config import get_settings
from ..models import Capture, CaptureHtml
from ..status import StatusLedger
from .deps import get_db
from .serializers import capture_brief, capture_full

router = APIRouter(prefix="/capture", tags=["capture"])


@router.get("/status")
def capture_status() -> dict:
    return StatusLedger().snapshot()


@router.post("/start")
def capture_start() -> dict:
    return StatusLedger().set_enabled(True)


@router.post("/stop")
def capture_stop() -> dict:
    return StatusLedger().set_enabled(False)


@router.post("/tick")
def capture_tick(force: bool = True) -> dict:
    """Run one capture cycle now. Denylist/private gating still applies."""
    return capture_once(force=force, reason="manual").as_dict()


@router.post("/pause")
def capture_pause(minutes: int | None = None) -> dict:
    """Hidden mode: stop recording for ``minutes`` (or indefinitely if omitted)."""
    led = StatusLedger()
    if minutes and minutes > 0:
        led.set_snooze(datetime.now(timezone.utc) + timedelta(minutes=minutes))
    else:
        led.set_snooze_indefinite()
    return led.snapshot()


@router.post("/resume")
def capture_resume() -> dict:
    """Exit hidden mode and resume recording."""
    led = StatusLedger()
    led.set_snooze(None)
    return led.snapshot()


@router.get("/recent")
def capture_recent(
    limit: int = Query(50, ge=1, le=500),
    app: str | None = None,
    q: str | None = None,
    before: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Capture)
    if app:
        stmt = stmt.where(Capture.app_name == app)
    if before:
        try:
            cursor = datetime.fromisoformat(before.replace("Z", "+00:00")).replace(tzinfo=None)
            stmt = stmt.where(Capture.captured_at < cursor)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid 'before' timestamp")
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Capture.text.ilike(like),
                Capture.caption.ilike(like),
                Capture.window_title.ilike(like),
            )
        )
    stmt = stmt.order_by(Capture.captured_at.desc(), Capture.id.desc()).limit(limit)
    rows = db.execute(stmt).scalars().all()
    return {"captures": [capture_brief(r) for r in rows], "count": len(rows)}


@router.post("/purge")
def capture_purge(days: int | None = None) -> dict:
    days = days if days is not None else get_settings().retention_days
    return purge_older_than(days)


@router.get("/{capture_id}/image")
def capture_image(capture_id: int, db: Session = Depends(get_db)) -> FileResponse:
    row = db.get(Capture, capture_id)
    if not row or not row.thumb_path:
        raise HTTPException(status_code=404, detail="no thumbnail")
    path = get_settings().thumbs_dir / row.thumb_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="thumbnail file missing")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{capture_id}/html")
def capture_html(capture_id: int, db: Session = Depends(get_db)) -> Response:
    """Return the stored raw page HTML as plain text (never rendered)."""
    row = db.get(CaptureHtml, capture_id)
    if not row:
        raise HTTPException(status_code=404, detail="no stored HTML")
    html = gzip.decompress(row.html_gz).decode("utf-8", errors="replace")
    # text/plain so the source is shown, not executed.
    return Response(content=html, media_type="text/plain; charset=utf-8")


@router.get("/{capture_id}")
def capture_detail(capture_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(Capture, capture_id)
    if not row:
        raise HTTPException(status_code=404, detail="capture not found")
    data = capture_full(row)
    data["has_html"] = db.get(CaptureHtml, capture_id) is not None
    return data
