"""Browser knowledge for the Windows backend (pure Python, importable anywhere).

On macOS the context helper asks browsers for their URL and incognito state over
AppleScript. Windows has no equivalent, so the URL comes from the address bar via
UI Automation (see ``uia.py``) and private windows are recognised by the marker
each browser puts in its window title.
"""

from __future__ import annotations

import re

# Executable names (Retrace's Windows app ids) of browsers we read a URL from.
BROWSER_APP_IDS: frozenset[str] = frozenset({
    "chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe", "opera.exe",
    "opera_gx.exe", "arc.exe", "chromium.exe", "firefox.exe", "librewolf.exe",
    "waterfox.exe", "zen.exe", "floorp.exe",
})

# What some browsers put in a private window's title, e.g.
#   "Page - [InPrivate] - Microsoft Edge", "Page — Mozilla Firefox Private Browsing".
# Chrome's incognito title is unmarked ("Page - Google Chrome"); see _PRIVATE_BUTTON.
# Matching errs towards skipping: a false positive only drops one capture.
_PRIVATE_TITLE = re.compile(
    r"inprivate|incognito|private browsing|\(private\)|\[private\]|- private -",
    re.IGNORECASE,
)


# The toolbar's profile button in a private window: Chrome "Incognito" / "Incognito (2)",
# Edge "InPrivate", Brave "Private". Chrome marks incognito nowhere else.
_PRIVATE_BUTTON = re.compile(r"^\s*(incognito|inprivate|private)\b", re.IGNORECASE)


def button_says_private(names) -> bool:
    """True when a browser toolbar button is the private-profile indicator."""
    return any(_PRIVATE_BUTTON.match(n or "") for n in names)


def is_browser(app_id: str | None) -> bool:
    return bool(app_id) and app_id.lower() in BROWSER_APP_IDS


def title_says_private(app_id: str | None, title: str | None) -> bool:
    """True when a browser window's title marks it as private/incognito."""
    if not is_browser(app_id) or not title:
        return False
    return bool(_PRIVATE_TITLE.search(title))


def normalize_address(value: str | None) -> str | None:
    """Turn an address-bar value into a URL, or None if it isn't one.

    Chromium hides the scheme ("github.com/x"), and a half-typed search query is
    not a URL, so values with spaces are rejected.
    """
    v = (value or "").strip()
    if not v or any(ch.isspace() for ch in v):
        return None
    if re.match(r"^[a-z][a-z0-9+.-]*://", v, re.IGNORECASE):
        return v
    if re.match(r"^(about|chrome|edge|brave|vivaldi|opera|file|view-source):", v, re.IGNORECASE):
        return v
    host = v.split("/", 1)[0]
    if host.startswith("localhost") or re.match(r"^[\w-]+(\.[\w-]+)+(:\d+)?$", host):
        return "https://" + v
    return None
