"""The Retrace FastAPI application.

Binds locally, serves the JSON API + the static web UI, and (from Milestone 5)
runs the capture daemon for the lifetime of the process.
"""

from __future__ import annotations

import importlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..capture.retention import clean_tmp
from ..config import get_settings
from ..db import init_db
from . import routes_capture, routes_config, routes_permissions

log = logging.getLogger("retrace.api")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Routers added in later milestones; included if present.
_OPTIONAL_ROUTERS = (
    "retrace.api.routes_activity",
    "retrace.api.routes_stats",
    "retrace.api.routes_search",
    "retrace.api.routes_plugins",
    "retrace.api.routes_export",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    s.ensure_dirs()
    init_db(s)
    removed = clean_tmp(s)
    if removed:
        log.info("cleaned %d stray temp frame(s) on startup", removed)

    daemon = None
    app.state.daemon = None
    if not os.environ.get("RETRACE_DISABLE_DAEMON"):
        try:
            from ..capture.daemon import CaptureDaemon  # available from M5

            daemon = CaptureDaemon(s)
            daemon.start()
            app.state.daemon = daemon
            log.info("capture daemon started")
        except ModuleNotFoundError:
            app.state.daemon = None

    try:
        yield
    finally:
        if daemon is not None:
            daemon.stop()
            log.info("capture daemon stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Retrace",
        version=__version__,
        description="Private, on-device macOS rewind. 100% local, no telemetry.",
        lifespan=lifespan,
    )

    app.include_router(routes_capture.router)
    app.include_router(routes_permissions.router)
    app.include_router(routes_config.router)

    for modpath in _OPTIONAL_ROUTERS:
        try:
            mod = importlib.import_module(modpath)
        except ModuleNotFoundError:
            continue
        app.include_router(mod.router)

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        """Liveness, not just readiness.

        ``ok`` is False when the capture loop has stopped touching the status
        ledger (it writes every cycle, even gated ones), when the daemon thread
        is gone, or when descriptors are nearly exhausted. A process that is up
        but not capturing is DOWN.
        """
        from datetime import datetime, timezone

        from ..status import StatusLedger

        s = get_settings()
        out: dict = {"ok": True, "name": "retrace", "version": __version__, "problems": []}
        daemon = getattr(app.state, "daemon", None)
        out["daemon"] = "disabled" if daemon is None else ("alive" if daemon.is_alive() else "dead")
        if daemon is not None and not daemon.is_alive():
            out["problems"].append("daemon thread dead")

        snap = StatusLedger(s).snapshot()
        out["enabled"] = bool(snap.get("enabled"))
        out["last_capture_at"] = snap.get("last_capture_at")
        out["last_error"] = snap.get("last_error")
        out["last_gate"] = snap.get("last_gate")
        stale_after = max(300.0, s.capture_interval_s * 6)
        try:
            updated = datetime.fromisoformat(snap["updated_at"])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - updated).total_seconds()
        except (KeyError, TypeError, ValueError):
            age = None
        out["ledger_age_s"] = None if age is None else round(age)
        if daemon is not None and out["enabled"] and (age is None or age > stale_after):
            out["problems"].append(f"capture loop stale: ledger last written {out['ledger_age_s']}s ago")

        try:
            from ..capture.daemon import fd_usage

            used, limit = fd_usage()
            out["fds"] = {"open": used, "limit": limit}
            if limit and used >= limit * 0.8:
                out["problems"].append(f"fd pressure {used}/{limit}")
        except (OSError, ModuleNotFoundError):
            pass

        out["ok"] = not out["problems"]
        return out

    # Mount the static web UI last so API routes take precedence.
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


app = create_app()
