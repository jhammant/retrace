"""On-device OCR with Windows' built-in engine (``Windows.Media.Ocr``).

The frame is decoded in Python and handed to WinRT as an in-memory bitmap, so no
WinRT stream ever holds the temp file open: the pipeline must be able to delete
the raw frame the moment OCR returns.
"""

from __future__ import annotations

import asyncio
import logging
import threading

log = logging.getLogger("retrace.native.win.ocr")

INSTALL_HINT = (
    "Add an OCR language: Settings > Time & language > Language & region > add a language "
    "(or, as admin: Add-WindowsCapability -Online -Name \"Language.OCR~~~en-US~0.0.1.0\")."
)

_engine_lock = threading.Lock()
_engine = None
_engine_checked = False


def _get_engine():
    """The OCR engine for the user's languages (English as a fallback), cached."""
    global _engine, _engine_checked
    with _engine_lock:
        if not _engine_checked:
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine

            _engine_checked = True
            engine = OcrEngine.try_create_from_user_profile_languages()
            if engine is None:
                for tag in ("en-US", "en-GB", "en"):
                    lang = Language(tag)
                    if OcrEngine.is_language_supported(lang):
                        engine = OcrEngine.try_create_from_language(lang)
                        break
            _engine = engine
        return _engine


def _run(awaitable):
    """Resolve a WinRT async operation from synchronous code."""
    async def _await():
        return await awaitable

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await())
    # Called from inside an event loop: resolve on a private thread instead.
    box: dict = {}
    t = threading.Thread(target=lambda: box.update(r=asyncio.run(_await())))
    t.start()
    t.join()
    return box.get("r")


def available() -> tuple[bool, str | None]:
    try:
        engine = _get_engine()
    except ImportError as exc:
        return False, f"WinRT OCR packages missing ({exc.name}); reinstall retrace-cli on Windows"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if engine is None:
        return False, "no OCR language installed. " + INSTALL_HINT
    return True, engine.recognizer_language.language_tag


def ocr_image(path: str) -> dict:
    """Same JSON shape as the macOS ``retrace-ocr`` helper."""
    try:
        from PIL import Image
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
    except ImportError as exc:
        return {"ok": False, "error": f"WinRT OCR unavailable: {exc}"}

    try:
        engine = _get_engine()
        if engine is None:
            return {"ok": False, "error": "no OCR language installed", "hint": INSTALL_HINT}

        with Image.open(path) as im:
            img = im.convert("RGBA")  # decoded into memory; the file is closed here
        limit = int(OcrEngine.max_image_dimension)
        if max(img.size) > limit:
            img.thumbnail((limit, limit))

        writer = DataWriter()
        writer.write_bytes(img.tobytes())
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.RGBA8, img.width, img.height
        )
        result = _run(engine.recognize_async(bitmap))

        lines = []
        for line in result.lines:
            words = list(line.words)
            bbox = None
            if words:
                xs = [wd.bounding_rect.x for wd in words]
                ys = [wd.bounding_rect.y for wd in words]
                x2 = [wd.bounding_rect.x + wd.bounding_rect.width for wd in words]
                y2 = [wd.bounding_rect.y + wd.bounding_rect.height for wd in words]
                bbox = [min(xs), min(ys), max(x2) - min(xs), max(y2) - min(ys)]
            lines.append({"text": line.text, "bbox": bbox})
        return {
            "ok": True,
            "text": "\n".join(ln["text"] for ln in lines),
            "line_count": len(lines),
            "lines": lines,
            "engine": "windows-ocr",
            "language": engine.recognizer_language.language_tag,
        }
    except Exception as exc:
        log.debug("OCR failed", exc_info=True)
        return {"ok": False, "error": f"ocr: {type(exc).__name__}: {exc}"}
