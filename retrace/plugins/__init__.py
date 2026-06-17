"""App plugin architecture: per-app capture enrichers + independent collectors.

A plugin can do either or both of:
- **enrich(context)** — when a matching app (by ``bundle_ids``) is frontmost during
  a capture, augment the stored capture (append text, override caption).
- **collect()** — independently ingest an app's own data (like the browser-history
  ingest), e.g. Claude Code session transcripts.

Built-in plugins live under ``retrace/plugins/builtin``; users can drop ``*.py``
files exposing ``PLUGIN`` (an instance) or ``PLUGINS`` (a list) into
``~/.retrace/plugins/``.
"""

from .base import RetracePlugin

__all__ = ["RetracePlugin"]
