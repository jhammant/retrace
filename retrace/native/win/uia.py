"""UI Automation reads for browsers: the address-bar URL and the accessible title.

Uses ``comtypes`` against ``UIAutomationCore.dll``. Each calling thread gets its
own MTA-initialised client, and UIA's own connection/transaction timeouts are
capped so a hung app can't stall the capture cycle. Every failure returns None.
"""

from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger("retrace.native.win.uia")

_UIA_ControlTypePropertyId = 30003
_UIA_EditControlTypeId = 50004
_UIA_ValueValuePropertyId = 30045
_TreeScope_Descendants = 4

_local = threading.local()


def _client():
    """(IUIAutomation, generated module) for this thread, or None if unavailable."""
    cached = getattr(_local, "client", False)
    if cached is not False:
        return cached
    client = None
    try:
        # comtypes initialises COM for the importing thread at import time; ask for
        # the multithreaded apartment UIA recommends for clients.
        if "comtypes" not in sys.modules:
            sys.coinit_flags = 0  # COINIT_MULTITHREADED
        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # already initialised on this thread (either apartment works for reads)
        mod = comtypes.client.GetModule("UIAutomationCore.dll")
        coclass = getattr(mod, "CUIAutomation8", None) or mod.CUIAutomation
        uia = comtypes.client.CreateObject(coclass, interface=mod.IUIAutomation)
        try:
            uia2 = uia.QueryInterface(mod.IUIAutomation2)
            uia2.ConnectionTimeout = 1000
            uia2.TransactionTimeout = 1500
        except Exception:
            pass  # pre-Windows 8: default timeouts
        client = (uia, mod)
    except Exception as exc:
        log.info("UI Automation unavailable: %s", exc)
    _local.client = client
    return client


def available() -> bool:
    return _client() is not None


def browser_snapshot(hwnd) -> dict:
    """``{"url": str|None, "accessible_title": str|None}`` for a browser window."""
    out: dict = {"url": None, "accessible_title": None}
    client = _client()
    if client is None:
        return out
    uia, _mod = client
    from .browser import normalize_address

    try:
        root = uia.ElementFromHandle(hwnd)
    except Exception:
        return out
    try:
        out["accessible_title"] = root.CurrentName or None
    except Exception:
        pass
    try:
        cond = uia.CreatePropertyCondition(_UIA_ControlTypePropertyId, _UIA_EditControlTypeId)
        # The address bar is the first edit control in tree order (toolbar before page).
        edit = root.FindFirst(_TreeScope_Descendants, cond)
        if edit:
            out["url"] = normalize_address(edit.GetCurrentPropertyValue(_UIA_ValueValuePropertyId))
    except Exception:
        log.debug("address bar read failed", exc_info=True)
    return out
