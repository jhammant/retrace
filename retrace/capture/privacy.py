"""Privacy gating: app denylist + private/incognito exclusion.

Denylisted apps are never captured. Private/incognito browser contexts are skipped
unless the user has explicitly opted in via ``capture_private_browsing``.
"""

from __future__ import annotations

from ..config import Settings, get_settings


def is_denylisted(
    bundle_id: str | None, app_name: str | None, settings: Settings | None = None
) -> bool:
    """True if the active app matches the bundle-id or app-name denylist.

    Matching is case-insensitive; a denylist entry that is a substring of the
    bundle id also matches (covers vendor variants like ``...onepassword7``).
    """
    s = settings or get_settings()
    bid = (bundle_id or "").lower()
    name = (app_name or "").lower()

    for deny in s.denylist_bundle_ids:
        d = deny.lower().strip()
        if d and (d == bid or (bid and d in bid)):
            return True
    for deny in s.denylist_app_names:
        d = deny.lower().strip()
        if d and (d == name or (name and d in name)):
            return True
    return False


def is_private_context(context: dict, settings: Settings | None = None) -> bool:
    """True if this is a private/incognito context we must not capture.

    Incognito is detected natively for Chrome-family browsers (window ``mode``);
    Safari private windows are not reliably detectable via public APIs.
    """
    s = settings or get_settings()
    if s.capture_private_browsing:
        return False
    return bool(context.get("private_browsing"))


def is_sensitive(context: dict, settings: Settings | None = None) -> bool:
    """True if the URL/title looks like adult/sensitive content (domain/keyword layer)."""
    from .caption import domain_of

    s = settings or get_settings()
    if not s.block_sensitive_content:
        return False

    url = (context.get("url") or "").lower()
    title = (context.get("window_title") or "").lower()
    dom = (domain_of(url) or "").lower()

    for d in s.sensitive_domains:
        d = d.lower().strip()
        if d and dom and (d == dom or d in dom):
            return True

    haystack = f"{dom} {url} {title}"
    for kw in s.sensitive_keywords:
        kw = kw.lower().strip()
        if kw and kw in haystack:
            return True
    return False


def evaluate(context: dict, settings: Settings | None = None) -> tuple[bool, str | None]:
    """Return ``(skip, reason)`` for a captured context dict.

    ``reason`` is ``"denylist"``, ``"private"``, or ``"sensitive"`` when skipping.
    """
    s = settings or get_settings()
    if is_denylisted(context.get("bundle_id"), context.get("app_name"), s):
        return True, "denylist"
    if is_private_context(context, s):
        return True, "private"
    if is_sensitive(context, s):
        return True, "sensitive"
    return False, None
