"""App plugin endpoints: list installed plugins and run collectors."""

from __future__ import annotations

from fastapi import APIRouter

from ..plugins.registry import list_plugins, run_collectors

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("")
def plugins_list() -> dict:
    return {"plugins": list_plugins()}


@router.post("/collect")
def plugins_collect() -> dict:
    return {"results": run_collectors()}
