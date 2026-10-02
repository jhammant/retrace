"""The Windows notification-area (tray) icon: the ``retrace-menubar`` equivalent.

Same menu as the macOS menu bar item, driven by the same HTTP API. Closing the
icon leaves capture running. ``tray_view`` is pure so it is tested on any OS;
``run`` needs Windows and ``pystray``.
"""

from __future__ import annotations

import json
import threading
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone

# Icon colours per state (orange = recording, matching the project badge).
_COLOURS = {
    "recording": (255, 122, 69),
    "paused": (140, 140, 140),
    "hidden": (128, 90, 213),
    "away": (70, 130, 200),
    "offline": (220, 160, 40),
    "loading": (140, 140, 140),
}


@dataclass
class TrayView:
    state: str
    header: str
    counts: str
    focus: str
    last: str
    enabled: bool
    snoozed: bool


def _snoozed(value) -> bool:
    if not value:
        return False
    if value == "indefinite":
        return True
    try:
        until = datetime.fromisoformat(str(value))
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


def _relative(iso: str | None, now: datetime | None = None) -> str:
    if not iso:
        return "—"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    secs = int(((now or datetime.now(timezone.utc)) - then).total_seconds())
    if secs < 45:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h ago"


def tray_view(status: dict | None, now: datetime | None = None) -> TrayView:
    """What the tray shows for a ``/capture/status`` payload (None = server down)."""
    if not status:
        return TrayView("offline", "Retrace — offline", "Server not running", "", "", False, False)
    enabled = bool(status.get("enabled"))
    snoozed = _snoozed(status.get("snooze_until"))
    presence = status.get("presence") or {}
    counters = status.get("counters") or {}
    if not enabled:
        state, label = "paused", "Paused"
    elif snoozed:
        state, label = "hidden", "Hidden mode"
    elif presence.get("screen_locked") is True:
        state, label = "away", "Screen locked"
    elif presence.get("display_asleep") is True:
        state, label = "away", "Display asleep"
    elif presence.get("present") is False:
        state, label = "away", "Away (idle)"
    else:
        state, label = "recording", "Recording"
    skipped = sum(int(counters.get(k) or 0) for k in
                  ("skipped_dupe", "skipped_denylist", "skipped_gated", "skipped_sensitive"))
    return TrayView(
        state=state,
        header=f"Retrace — {label}",
        counts=f"Today: {int(counters.get('stored') or 0)} captured · {skipped} skipped",
        focus=f"Focus: {status.get('last_app') or '—'}",
        last=f"Last: {_relative(status.get('last_capture_at'), now)}",
        enabled=enabled,
        snoozed=snoozed,
    )


def icon_image(state: str, size: int = 64):
    """A simple ring-and-dot icon, coloured by state."""
    from PIL import Image, ImageDraw

    colour = _COLOURS.get(state, _COLOURS["loading"])
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size // 10
    d.ellipse((pad, pad, size - pad, size - pad), outline=colour + (255,), width=max(3, size // 10))
    inner = size // 3
    if state == "recording":
        d.ellipse((inner, inner, size - inner, size - inner), fill=colour + (255,))
    elif state in ("paused", "hidden"):
        bar = size // 10
        d.rectangle((inner, inner, inner + bar, size - inner), fill=colour + (255,))
        d.rectangle((size - inner - bar, inner, size - inner, size - inner), fill=colour + (255,))
    return img


class _Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def status(self) -> dict | None:
        try:
            with urllib.request.urlopen(self.base + "/capture/status", timeout=2) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def post(self, path: str) -> None:
        try:
            req = urllib.request.Request(self.base + path, method="POST", data=b"")
            urllib.request.urlopen(req, timeout=8).close()
        except Exception:
            pass


def build(base: str, refresh_s: float = 3.0):
    """The configured ``pystray.Icon`` (not yet shown). ``icon.run()`` shows it."""
    import pystray

    api = _Api(base)
    view = tray_view(api.status())
    stop = threading.Event()
    icon = pystray.Icon("retrace", icon_image(view.state), view.header)

    def refresh() -> None:
        nonlocal view
        view = tray_view(api.status())
        icon.icon = icon_image(view.state)
        icon.title = f"{view.header}\n{view.counts}"
        icon.update_menu()

    def act(path: str):
        def _do(_icon=None, _item=None):
            api.post(path)
            refresh()
        return _do

    def toggle_capture(_icon=None, _item=None):
        act("/capture/stop" if view.enabled else "/capture/start")()

    def toggle_hidden(_icon=None, _item=None):
        act("/capture/resume" if view.snoozed else "/capture/pause")()

    def quit_tray(_icon=None, _item=None):
        stop.set()
        icon.stop()

    Item, Menu = pystray.MenuItem, pystray.Menu
    icon.menu = Menu(
        Item(lambda _i: view.header, None, enabled=False),
        Item(lambda _i: view.counts, None, enabled=False),
        Item(lambda _i: view.focus, None, enabled=False),
        Item(lambda _i: view.last, None, enabled=False),
        Menu.SEPARATOR,
        Item("Capture now", act("/capture/tick?force=true")),
        Item(lambda _i: "Pause capture" if view.enabled else "Start capture", toggle_capture),
        Item("Hidden mode", toggle_hidden, checked=lambda _i: view.snoozed),
        Menu.SEPARATOR,
        Item("Open Dashboard", lambda *_: webbrowser.open(base), default=True),
        Item("Settings…", lambda *_: webbrowser.open(base + "/#/settings")),
        Menu.SEPARATOR,
        Item("Quit tray icon", quit_tray),
    )

    def poll() -> None:
        while not stop.wait(refresh_s):
            try:
                refresh()
            except Exception:
                pass

    def setup(ic) -> None:
        ic.visible = True
        refresh()
        threading.Thread(target=poll, name="retrace-tray-poll", daemon=True).start()

    icon.retrace_setup = setup  # what run() passes to icon.run()
    icon.retrace_stop = quit_tray
    return icon


def run(base: str, refresh_s: float = 3.0) -> None:
    """Show the tray icon until the user picks "Quit tray icon"."""
    icon = build(base, refresh_s)
    icon.run(setup=icon.retrace_setup)
