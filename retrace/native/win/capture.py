"""Grab the primary display on Windows (the ``retrace-capture`` equivalent).

macOS's ScreenCaptureKit can leave denylisted apps out of the frame. GDI can't, so
here every visible window owned by a denylisted app is painted black before the
frame or thumbnail is written. That errs towards hiding too much: a denylisted
window partly covered by another is still blacked out in full.
"""

from __future__ import annotations

import contextlib
import logging

log = logging.getLogger("retrace.native.win.capture")


def _matches(app_id: str | None, exclude: list[str]) -> bool:
    """Same rule as the privacy denylist: exact or substring, case-insensitive."""
    aid = (app_id or "").lower()
    if not aid:
        return False
    for entry in exclude:
        e = entry.lower().strip()
        if e and (e == aid or e in aid):
            return True
    return False


@contextlib.contextmanager
def _physical_pixels():
    """Per-monitor DPI awareness for this thread, so rectangles match the grab."""
    from . import _win32 as w

    old = None
    if w.SetThreadDpiAwarenessContext is not None:
        old = w.SetThreadDpiAwarenessContext(w.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        if not old:
            old = w.SetThreadDpiAwarenessContext(w.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE)
    try:
        yield
    finally:
        if old:
            w.SetThreadDpiAwarenessContext(old)


def denied_window_rects(exclude: list[str]) -> list[tuple[int, int, int, int]]:
    """Screen rectangles of every visible window owned by a denylisted app."""
    if not exclude:
        return []
    from . import _win32 as w
    from .apps import window_owner

    rects = []
    for hwnd in w.enum_windows():
        if not w.IsWindowVisible(hwnd) or w.IsIconic(hwnd) or w.is_cloaked(hwnd):
            continue
        r = w.window_rect(hwnd)
        if not r or r[2] <= r[0] or r[3] <= r[1]:
            continue
        if _matches(window_owner(hwnd).app_id, exclude):
            rects.append(r)
    return rects


def redact(img, rects, origin: tuple[int, int] = (0, 0)) -> int:
    """Paint ``rects`` (screen coordinates) black on ``img``. Returns how many touched it."""
    from PIL import ImageDraw

    ox, oy = origin
    draw = ImageDraw.Draw(img)
    hit = 0
    for left, top, right, bottom in rects:
        box = (max(0, left - ox), max(0, top - oy),
               min(img.width, right - ox) - 1, min(img.height, bottom - oy) - 1)
        if box[2] < box[0] or box[3] < box[1]:
            continue  # on another monitor
        draw.rectangle(box, fill=(0, 0, 0))
        hit += 1
    return hit


def write_outputs(img, *, frame_path: str, thumb_path: str, max_edge: int, jpeg_quality: int) -> tuple[bool, bool]:
    """Write the full frame (PNG) and the downscaled thumbnail (JPEG)."""
    from PIL import Image

    wrote_frame = wrote_thumb = False
    if frame_path:
        img.save(frame_path, "PNG", compress_level=1)  # temp file; speed over size
        wrote_frame = True
    if thumb_path:
        thumb = img.convert("RGB")
        if max(thumb.size) > max_edge > 0:
            thumb.thumbnail((max_edge, max_edge), Image.LANCZOS)
        thumb.save(thumb_path, "JPEG", quality=int(jpeg_quality), optimize=True)
        wrote_thumb = True
    return wrote_frame, wrote_thumb


def capture_frame(*, frame_path: str, thumb_path: str, max_edge: int, jpeg_quality: int,
                  exclude_bundle_ids: list[str], display: str = "main") -> dict:
    """Same JSON shape as the macOS ``retrace-capture`` helper."""
    try:
        from PIL import ImageGrab

        with _physical_pixels():
            img = ImageGrab.grab(include_layered_windows=True)  # primary display
            rects = denied_window_rects(exclude_bundle_ids)
        excluded = redact(img, rects)
        wrote_frame, wrote_thumb = write_outputs(
            img, frame_path=frame_path, thumb_path=thumb_path,
            max_edge=max_edge, jpeg_quality=jpeg_quality,
        )
        return {
            "ok": (not frame_path or wrote_frame) and (not thumb_path or wrote_thumb),
            "frame_path": frame_path,
            "thumb_path": thumb_path,
            "width": img.width,
            "height": img.height,
            "excluded_windows": excluded,
        }
    except OSError as exc:  # e.g. no interactive desktop (service session, locked)
        return {"ok": False, "error": f"capture: {exc}"}
    except Exception as exc:
        log.debug("capture failed", exc_info=True)
        return {"ok": False, "error": f"capture: {type(exc).__name__}: {exc}"}
