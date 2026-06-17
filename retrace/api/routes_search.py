"""Search endpoint: text / semantic / hybrid over captures."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..search.service import backfill_embeddings, search

router = APIRouter(tags=["search"])

_MODES = {"text", "semantic", "hybrid"}


@router.get("/search")
def search_route(
    q: str,
    mode: str = "hybrid",
    start: str | None = None,
    end: str | None = None,
    app: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    if mode not in _MODES:
        mode = "hybrid"
    return search(q, mode=mode, start=start, end=end, app=app, limit=limit)


@router.post("/search/backfill")
def backfill_route(limit: int = Query(500, ge=1, le=5000)) -> dict:
    return backfill_embeddings(limit)
