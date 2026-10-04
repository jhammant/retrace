"""Where Chromium-family browsers keep their History database on each OS.

Used by activity ingest (visits) and the downloads plugin. Each entry is
``(source, app_id, path)``: ``source`` names the activity_events source, and
``app_id`` is the browser's bundle id on macOS or executable name on Windows.
"""

from __future__ import annotations

from pathlib import Path

from .platform import IS_WINDOWS, local_appdata


def chrome_history() -> tuple[str, Path]:
    """(app id, History path) for Google Chrome's default profile."""
    if IS_WINDOWS:
        return "chrome.exe", local_appdata() / "Google" / "Chrome" / "User Data" / "Default" / "History"
    return "com.google.Chrome", (
        Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "History"
    )


def extra_chromium_histories() -> list[tuple[str, str, Path]]:
    """Other Chromium browsers read alongside Chrome. Edge ships with Windows."""
    if IS_WINDOWS:
        base = local_appdata()
        return [
            ("edge", "msedge.exe", base / "Microsoft" / "Edge" / "User Data" / "Default" / "History"),
            ("brave", "brave.exe", base / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default" / "History"),
        ]
    return []
