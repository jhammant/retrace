"""Base class for Retrace app plugins."""

from __future__ import annotations

import sys

from ..config import Settings


class RetracePlugin:
    """Subclass to add per-app behavior. All hooks are optional and fail-soft.

    Override ``bundle_ids`` to enrich captures of specific apps, and/or override
    ``collect`` to ingest an app's own data store on a schedule.
    """

    #: Stable identifier (used for enable/disable and reporting).
    name: str = "plugin"
    #: One-line description shown in the UI / CLI.
    description: str = ""
    #: Bundle ids whose captures this plugin enriches (empty = no enrichment).
    #: On Windows the app id is the executable name, e.g. ``"spotify.exe"``.
    bundle_ids: tuple[str, ...] = ()
    #: ``sys.platform`` values this plugin runs on (empty = every platform).
    platforms: tuple[str, ...] = ()

    def supported(self) -> bool:
        return not self.platforms or sys.platform in self.platforms

    def enrich(self, context: dict, settings: Settings) -> dict | None:
        """Augment a capture of a matching frontmost app.

        Return a dict with any of: ``text`` (replace), ``text_append`` (append),
        ``caption`` (override), ``window_title``, ``url``, ``doc_path``. Return
        ``None`` for no change.
        """
        return None

    def collect(self, settings: Settings) -> dict:
        """Independently ingest this app's data. Return a summary dict."""
        return {"name": self.name, "ingested": 0}

    def poll(self, settings: Settings) -> None:
        """Called by the daemon on every fallback tick (~once per capture interval).

        Use for lightweight periodic sampling — e.g. the currently-playing track or
        system CPU/memory. The plugin instance is reused across ticks, so it can keep
        state (like the last-seen track). Only runs while capture is enabled and not
        in Hidden mode.
        """
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RetracePlugin {self.name}>"
