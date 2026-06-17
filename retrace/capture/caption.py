"""Caption generation: a short "what the user is doing" summary.

For now this produces a deterministic template caption. Milestone 8 plugs an
on-device Foundation Models call in front of the template (with the template as
the graceful fallback when the framework is unavailable).
"""

from __future__ import annotations

from urllib.parse import urlparse

from ..config import Settings, get_settings


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        netloc = urlparse(url).netloc
    except ValueError:
        return None
    if not netloc:
        return None
    return netloc[4:] if netloc.startswith("www.") else netloc


def template_caption(app: str | None, window: str | None, url: str | None) -> str:
    """A readable one-liner derived purely from metadata (no model needed)."""
    dom = domain_of(url)
    app = app or "an app"
    if dom:
        tail = f" — {window}" if window else ""
        return f"Viewing {dom}{tail} in {app}."
    if window:
        return f"Working in {app}: {window}."
    return f"Using {app}."


def make_caption(
    *,
    app: str | None,
    window: str | None,
    url: str | None,
    text: str,
    settings: Settings | None = None,
) -> tuple[str | None, str]:
    """Return ``(caption, caption_model)``.

    Milestone 8 will try the native Foundation Models helper here first.
    """
    s = settings or get_settings()
    if not s.enable_caption:
        return None, "disabled"

    if s.enable_caption:
        from .caption_native import native_caption  # local import: optional path

        caption = native_caption(app=app, window=window, url=url, text=text, settings=s)
        if caption:
            return caption, "foundation-models"

    return template_caption(app, window, url), "template"
