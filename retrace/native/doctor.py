"""``retrace doctor`` — permission + capability diagnostics.

Compiles the helpers, checks permissions, probes on-device capabilities
(embeddings, Foundation Models captions), and reports DB/status — all locally.
"""

from __future__ import annotations

import json
import shutil

from ..config import Settings, get_settings
from .helpers import build_all, embed_text, get_helper, get_presence
from .permissions import check_all


def _check_embeddings(s: Settings) -> dict:
    res = embed_text("retrace capability check", settings=s)
    if res and res.get("ok"):
        return {"available": True, "model": res.get("model"), "dim": res.get("dim")}
    return {"available": False}


def _check_foundation_models(s: Settings) -> dict:
    helper = get_helper("retrace-caption", s)
    if not helper.source_exists():
        return {"available": False, "reason": "no source"}
    res = helper.run([json.dumps({"app": "Finder", "window": "Desktop", "url": "", "text": "files"})],
                     timeout=30.0)
    if res and res.get("ok"):
        return {"available": True, "model": res.get("model")}
    return {"available": False, "reason": (res or {}).get("error", "unavailable")}


def run_doctor(settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    s.ensure_dirs()

    helpers = build_all(s)
    permissions = check_all(s)

    pres = get_presence(s.idle_threshold_s, settings=s) or {}
    capabilities = {
        "screen_recording": pres.get("screen_recording"),
        "accessibility": pres.get("accessibility"),
        "embeddings": _check_embeddings(s),
        "foundation_models_caption": _check_foundation_models(s),
    }

    # DB + status
    db = {"captures": 0, "activity_events": 0, "embeddings": 0}
    try:
        from ..db import session_scope
        from ..models import ActivityEvent, Capture, CaptureEmbedding
        from sqlalchemy import func, select

        with session_scope(s) as session:
            db["captures"] = session.execute(select(func.count()).select_from(Capture)).scalar() or 0
            db["activity_events"] = session.execute(select(func.count()).select_from(ActivityEvent)).scalar() or 0
            db["embeddings"] = session.execute(select(func.count()).select_from(CaptureEmbedding)).scalar() or 0
    except Exception as exc:  # DB may not be initialized yet
        db["error"] = str(exc)

    from ..status import StatusLedger
    snap = StatusLedger(s).snapshot()

    return {
        "home": str(s.home),
        "swiftc": shutil.which("swiftc"),
        "helpers": helpers,
        "permissions": permissions,
        "capabilities": capabilities,
        "database": db,
        "capture_enabled": snap.get("enabled"),
        "counters_today": snap.get("counters"),
    }


def _mark(ok: bool | None) -> str:
    return {True: "✓", False: "✗", None: "?"}[ok]


def format_report(report: dict) -> str:
    lines: list[str] = []
    lines.append("Retrace doctor")
    lines.append("=" * 52)
    lines.append(f"Home        : {report['home']}")
    lines.append(f"swiftc      : {report['swiftc'] or '✗ not found (install Xcode CLT)'}")
    lines.append(f"Capture     : {'ENABLED' if report.get('capture_enabled') else 'off'}")

    lines.append("\nNative helpers")
    for name, status in report["helpers"].items():
        ok = status == "ok" or status.startswith("no source")
        lines.append(f"  {_mark(ok)} {name.ljust(22)} {status}")

    lines.append("\nPermissions")
    for name, c in report["permissions"].items():
        state = c["state"]
        mark = "✓" if state == "granted" else ("✗" if state == "denied" else "?")
        req = " (required)" if c["required"] else ""
        lines.append(f"  {mark} {name.replace('_', ' ').ljust(20)} {state}{req}")
        if state != "granted" and c.get("guidance"):
            lines.append(f"      ↳ {c['guidance']}")

    caps = report["capabilities"]
    lines.append("\nCapabilities")
    lines.append(f"  {_mark(caps.get('screen_recording'))} screen recording")
    lines.append(f"  {_mark(caps.get('accessibility'))} accessibility text")
    emb = caps.get("embeddings", {})
    lines.append(f"  {_mark(emb.get('available'))} semantic embeddings  {emb.get('model', '')}")
    fm = caps.get("foundation_models_caption", {})
    fm_extra = fm.get("model") or fm.get("reason", "")
    lines.append(f"  {_mark(fm.get('available'))} foundation models caption  {fm_extra}")

    db = report["database"]
    lines.append("\nDatabase")
    lines.append(f"  captures={db.get('captures')}  activity={db.get('activity_events')}  embeddings={db.get('embeddings')}")

    return "\n".join(lines)
