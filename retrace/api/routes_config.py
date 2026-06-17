"""Read/update the editable subset of configuration (drives the Settings panel)."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from ..config import EDITABLE_KEYS, get_settings, update_config

router = APIRouter(prefix="/config", tags=["config"])


def _current() -> dict:
    s = get_settings()
    return {k: getattr(s, k) for k in EDITABLE_KEYS}


@router.get("")
def read_config() -> dict:
    return {"config": _current(), "editable_keys": list(EDITABLE_KEYS)}


@router.post("")
def write_config(updates: dict = Body(...)) -> dict:
    try:
        update_config(updates)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # invalid value/type
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"config": _current()}
