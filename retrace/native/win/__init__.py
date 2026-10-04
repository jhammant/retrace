"""Windows backends: the Python equivalents of the macOS Swift helpers.

| macOS helper          | Windows module | built on                               |
|-----------------------|----------------|----------------------------------------|
| retrace-capture       | capture        | Pillow ImageGrab (GDI), denylist blacked out |
| retrace-context       | context        | Win32 foreground window + UI Automation |
| retrace-ocr           | ocr            | Windows.Media.Ocr (WinRT)              |
| retrace-present       | presence       | GetLastInputInfo + WTS session lock    |
| retrace-watch         | watch          | SetWinEventHook(EVENT_SYSTEM_FOREGROUND) |
| retrace-embed         | search.hashembed | hashed word + trigram vectors (lexical) |
| retrace-menubar       | tray           | pystray notification-area icon         |
| retrace-caption       | (none)         | template captions                      |
| retrace-sensitivity   | (none)         | domain/keyword blocking only           |
| retrace-calendar      | (none)         | calendar plugin is macOS-only          |

Modules import Win32 bindings lazily, so the pure parts (browser rules, tray
view, redaction) are importable and tested on any OS.
"""

from __future__ import annotations


def backend_status() -> dict[str, str]:
    """name -> status line, shown by ``retrace doctor`` in place of the Swift build."""
    out: dict[str, str] = {}
    try:
        from PIL import ImageGrab  # noqa: F401

        out["capture"] = "ok"
    except ImportError as exc:
        out["capture"] = f"FAILED: {exc}"
    try:
        from . import _win32  # noqa: F401

        out["context"] = "ok"
        out["presence"] = "ok"
        out["watch"] = "ok"
    except (ImportError, OSError, AttributeError) as exc:
        for k in ("context", "presence", "watch"):
            out[k] = f"FAILED: {exc}"
    try:
        from .uia import available as uia_available

        out["ui-automation"] = "ok" if uia_available() else "unavailable (browser URLs off)"
    except Exception as exc:
        out["ui-automation"] = f"unavailable: {exc}"
    try:
        from .ocr import available as ocr_available

        ok, detail = ocr_available()
        out["ocr"] = f"ok ({detail})" if ok else f"unavailable: {detail}"
    except Exception as exc:
        out["ocr"] = f"unavailable: {exc}"
    from ...search.hashembed import MODEL

    out["embeddings"] = f"ok ({MODEL}, lexical)"
    try:
        import pystray  # noqa: F401

        out["tray"] = "ok"
    except Exception as exc:
        out["tray"] = f"unavailable: {exc}"
    out["caption"] = "template (Foundation Models is macOS-only)"
    out["sensitivity"] = "n/a on Windows (domain/keyword blocking still applies)"
    return out
