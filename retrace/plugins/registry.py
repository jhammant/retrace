"""Plugin discovery + loading (built-in and user-provided)."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

from ..config import Settings, get_settings
from .base import RetracePlugin

log = logging.getLogger("retrace.plugins")


def _builtin() -> list[RetracePlugin]:
    from .builtin.claude_code import ClaudeCodePlugin

    return [ClaudeCodePlugin()]


def _user(settings: Settings) -> list[RetracePlugin]:
    plugin_dir = settings.home / "plugins"
    if not plugin_dir.is_dir():
        return []
    found: list[RetracePlugin] = []
    for path in sorted(plugin_dir.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(f"retrace_user_plugin_{path.stem}", path)
            if not spec or not spec.loader:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "PLUGIN") and isinstance(mod.PLUGIN, RetracePlugin):
                found.append(mod.PLUGIN)
            for p in getattr(mod, "PLUGINS", []) or []:
                if isinstance(p, RetracePlugin):
                    found.append(p)
        except Exception:  # never let a bad user plugin break the app
            log.exception("failed to load user plugin %s", path)
    return found


def load_plugins(settings: Settings | None = None) -> list[RetracePlugin]:
    s = settings or get_settings()
    if not s.enable_plugins:
        return []
    disabled = set(s.disabled_plugins)
    return [p for p in (_builtin() + _user(s)) if p.name not in disabled]


def enrichers_for(bundle_id: str | None, settings: Settings | None = None) -> list[RetracePlugin]:
    if not bundle_id:
        return []
    return [p for p in load_plugins(settings) if bundle_id in p.bundle_ids]


def run_collectors(settings: Settings | None = None) -> list[dict]:
    s = settings or get_settings()
    results = []
    for plugin in load_plugins(s):
        try:
            results.append(plugin.collect(s))
        except Exception as exc:
            log.exception("collector %s failed", plugin.name)
            results.append({"name": plugin.name, "error": str(exc)})
    return results


def list_plugins(settings: Settings | None = None) -> list[dict]:
    return [
        {"name": p.name, "description": p.description, "bundle_ids": list(p.bundle_ids),
         "collects": type(p).collect is not RetracePlugin.collect}
        for p in load_plugins(settings)
    ]
