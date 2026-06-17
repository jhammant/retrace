"""The capture pipeline — one full cycle is :func:`capture_once`.

Cycle (see the project spec, §5):

1. Gate on enabled / presence.
2. Read on-screen context (app, window, URL, doc, AX text).
3. Privacy: skip denylisted apps and private/incognito contexts entirely
   (no frame is ever grabbed for those).
4. Dedup: skip unchanged screens within the dedup window.
5. Grab the frame (with denylisted apps natively excluded) + write the thumbnail.
6. Text: prefer Accessibility text; fall back to OCR when it is too thin.
7. Caption (template now; Foundation Models later).
8. Persist the row (+ FTS via triggers).
9. **Always delete the raw frame in a ``finally`` block.**
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import uuid4

from ..config import Settings, get_settings
from ..db import session_scope
from ..models import Capture, CaptureHtml, utcnow
from ..native.helpers import (
    analyze_sensitivity,
    capture_frame,
    get_presence,
    ocr_image,
    read_context,
)
from ..status import StatusLedger
from . import privacy
from .caption import make_caption
from .retention import content_hash, is_duplicate


def _image_is_sensitive(settings: Settings, frame_path) -> bool:
    """On-device sensitivity scan of the frame. False if disabled/unavailable."""
    if not settings.block_sensitive_images or not frame_path.exists():
        return False
    res = analyze_sensitivity(str(frame_path), settings=settings)
    return bool(res and res.get("available") and res.get("sensitive"))

log = logging.getLogger("retrace.pipeline")

# Serialize capture cycles process-wide: the daemon's event + fallback threads and
# the API's manual tick all funnel through here; only one ScreenCaptureKit grab +
# DB write should run at a time.
_CAPTURE_LOCK = threading.Lock()
_LOCK_TIMEOUT_S = 25.0


@dataclass
class CaptureResult:
    ok: bool
    status: str  # 'stored' | 'skipped' | 'error'
    reason: str | None = None
    capture_id: int | None = None
    app: str | None = None
    window: str | None = None
    url: str | None = None
    text_len: int = 0
    text_source: str = "none"
    caption: str | None = None
    thumb_path: str | None = None
    frame_deleted: bool | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _skipped(reason: str, **kw) -> CaptureResult:
    return CaptureResult(ok=True, status="skipped", reason=reason, **kw)


def capture_once(
    *, force: bool = False, reason: str = "tick", settings: Settings | None = None
) -> CaptureResult:
    """Run a single capture cycle (serialized process-wide). Never raises."""
    if not _CAPTURE_LOCK.acquire(timeout=_LOCK_TIMEOUT_S):
        return _skipped("busy")
    try:
        return _run_capture(force=force, reason=reason, settings=settings)
    finally:
        _CAPTURE_LOCK.release()


def _run_capture(
    *, force: bool = False, reason: str = "tick", settings: Settings | None = None
) -> CaptureResult:
    s = settings or get_settings()
    s.ensure_dirs()
    ledger = StatusLedger(s)

    # 1a. enabled gate
    if not force and not ledger.is_enabled():
        ledger.record_skip("gated")
        return _skipped("disabled")

    # 1a'. hidden mode (snooze): a one-click "don't record right now".
    if not force and ledger.is_snoozed():
        ledger.set_gate("hidden")
        ledger.record_skip("gated")
        return _skipped("hidden")

    # 1b. presence gate. A logged-in-but-away machine (idle, screen locked, or
    #     display asleep) is never captured. Unknown presence => treat as present.
    pres = get_presence(s.idle_threshold_s, settings=s)
    if pres and pres.get("ok"):
        ledger.set_presence(pres)
        if not force and s.pause_when_away:
            if pres.get("screen_locked"):
                ledger.set_gate("locked"); ledger.record_skip("gated")
                return _skipped("locked")
            if pres.get("display_asleep"):
                ledger.set_gate("asleep"); ledger.record_skip("gated")
                return _skipped("asleep")
            if pres.get("present") is False:
                ledger.set_gate("idle"); ledger.record_skip("gated")
                return _skipped("idle")

    # 2. context — also our only reliable read of the *active* app. If we cannot
    #    determine it, we must not capture (can't verify the denylist). Optionally
    #    pull full page text/HTML for browsers (opt-in).
    context = read_context(
        fetch_page_text=s.capture_page_text,
        fetch_page_html=s.capture_page_html,
        settings=s,
    )
    if not context or not context.get("ok"):
        ledger.record_skip("gated")
        return _skipped("no-context")

    app = context.get("app_name")
    bundle = context.get("bundle_id")
    window = context.get("window_title")
    url = context.get("url")
    doc_path = context.get("doc_path")
    ax_text = (context.get("text") or "").strip()
    page_text = (context.get("page_text") or "").strip()
    page_html = context.get("page_html")
    # Full page text (when captured) is richer than AX text; prefer it.
    primary_text = page_text or ax_text
    primary_source = "page" if page_text else ("accessibility" if ax_text else "none")

    # 3. privacy gate (denylist / incognito / sensitive domain+keyword)
    skip, why = privacy.evaluate(context, s)
    if skip:
        ledger.record_skip(why or "gated")
        return _skipped(why or "private", app=app, window=window)

    # 3b. plugin enrichment for the active app (augment text/caption/metadata).
    caption_override: str | None = None
    if s.enable_plugins:
        from ..plugins import registry as plugin_registry

        for plugin in plugin_registry.enrichers_for(bundle, s):
            try:
                extra = plugin.enrich(context, s) or {}
            except Exception:
                log.debug("plugin %s enrich failed", plugin.name, exc_info=True)
                continue
            if extra.get("text"):
                primary_text = str(extra["text"]).strip()
            if extra.get("text_append"):
                primary_text = f"{primary_text}\n{extra['text_append']}".strip()
            if extra.get("caption"):
                caption_override = str(extra["caption"])
            if extra.get("window_title"):
                window = str(extra["window_title"])
            if extra.get("url"):
                url = str(extra["url"])
            if extra.get("doc_path"):
                doc_path = str(extra["doc_path"])

    # 4. dedup (computed from primary text + app + window, before any expensive work)
    chash = content_hash(primary_text, app, window)
    if not force and is_duplicate(ledger, chash, s.dedup_window_s):
        ledger.record_skip("dupe")
        return _skipped("duplicate", app=app, window=window)

    # 5-9 with the raw-frame-deletion invariant.
    cap_uuid = uuid4().hex
    day = datetime.now().strftime("%Y-%m-%d")
    frame_path = s.tmp_dir / f"{cap_uuid}.png"
    thumb_abs = s.thumb_dir_for_day(day) / f"{cap_uuid}.jpg"
    thumb_rel = f"{day}/{cap_uuid}.jpg"

    text = primary_text
    text_source = primary_source
    result: CaptureResult | None = None

    try:
        cap = capture_frame(
            frame_path=str(frame_path),
            thumb_path=str(thumb_abs),
            max_edge=s.thumb_max_edge,
            jpeg_quality=s.thumb_jpeg_quality,
            exclude_bundle_ids=s.denylist_bundle_ids,
            settings=s,
        )
        if not cap or not cap.get("ok"):
            err = (cap or {}).get("error") or "no capture result"
            ledger.record_error(f"capture failed: {err}")
            result = CaptureResult(
                ok=False, status="error", reason="capture-failed",
                app=app, window=window, error=err,
            )
        elif _image_is_sensitive(s, frame_path):
            # On-device sensitivity scan flagged adult/sensitive content: retain
            # nothing image-derived and skip the capture entirely.
            try:
                if thumb_abs.exists():
                    thumb_abs.unlink()
            except OSError:
                pass
            ledger.record_skip("sensitive")
            result = CaptureResult(
                ok=True, status="skipped", reason="sensitive-image", app=app, window=window
            )
        else:
            stored_thumb_rel = thumb_rel if thumb_abs.exists() else None

            # 6. OCR fallback when Accessibility text is too thin.
            if len(text) < s.min_ax_text_len and frame_path.exists():
                ocr = ocr_image(str(frame_path), settings=s)
                ocr_text = ((ocr or {}).get("text") or "").strip()
                if ocr_text:
                    if text:
                        text = f"{text}\n{ocr_text}"
                        text_source = "mixed"
                    else:
                        text = ocr_text
                        text_source = "ocr"

            # 7. caption (plugin override wins)
            if caption_override:
                caption, caption_model = caption_override, "plugin"
            else:
                caption, caption_model = make_caption(
                    app=app, window=window, url=url, text=text, settings=s
                )

            # 8. persist
            with session_scope(s) as session:
                row = Capture(
                    captured_at=utcnow(),
                    app_name=app, bundle_id=bundle, window_title=window,
                    url=url, doc_path=doc_path,
                    text=text, text_len=len(text), text_source=text_source,
                    caption=caption, caption_model=caption_model,
                    content_hash=chash, thumb_path=stored_thumb_rel,
                )
                session.add(row)
                session.flush()
                cap_id = row.id

                # 8b. raw page HTML in a side table (stored, never shown in the UI).
                if s.capture_page_html and page_html:
                    import gzip

                    session.add(CaptureHtml(
                        capture_id=cap_id,
                        length=len(page_html),
                        html_gz=gzip.compress(page_html.encode("utf-8")),
                    ))

                # 9. semantic embedding (fully on-device) for later search.
                if s.enable_semantic_search and text:
                    try:
                        from ..search.service import store_capture_embedding

                        store_capture_embedding(session, cap_id, text, s)
                    except Exception:
                        log.debug("embedding failed", exc_info=True)

            ledger.record_capture(content_hash=chash, app=app, window=window, stored=True)
            ledger.set_gate(None)
            result = CaptureResult(
                ok=True, status="stored", reason=reason, capture_id=cap_id,
                app=app, window=window, url=url, text_len=len(text),
                text_source=text_source, caption=caption, thumb_path=stored_thumb_rel,
            )
    except Exception as exc:  # never let one cycle crash the caller
        log.exception("capture_once failed")
        ledger.record_error(f"{type(exc).__name__}: {exc}")
        result = CaptureResult(
            ok=False, status="error", reason="exception", error=str(exc),
            app=app, window=window,
        )
    finally:
        # THE core privacy invariant: the raw full-resolution frame never survives.
        deleted = False
        try:
            if frame_path.exists():
                frame_path.unlink()
            deleted = True  # removed, or never written
        except OSError:
            deleted = False
            log.error("FAILED to delete raw frame %s", frame_path)
        if result is not None:
            result.frame_deleted = deleted

    return result
