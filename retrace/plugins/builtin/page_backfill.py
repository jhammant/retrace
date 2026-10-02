"""Page-content backfill — the one feature that makes OUTBOUND requests.

Safari/iCloud history gives URLs but never page *content*. This optional plugin
re-fetches eligible history URLs and extracts their readable text so phone (and
Mac) browsing becomes full-text/semantically searchable.

Because it leaves the device, it is **off by default** (``backfill_page_content``)
and heavily guard-railed:
  * https + GET only; loopback/LAN/link-local hosts blocked (no SSRF);
  * authenticated/personal domains skipped (cookie-less fetch only hits a wall);
  * action/token-shaped URLs skipped (never re-trigger logout/unsubscribe/magic links);
  * sensitive URLs skipped (same gate as live capture);
  * login-wall / empty responses discarded; bounded count, size, and timeout per run;
  * every URL attempted at most once (state file), so nothing is re-fetched.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select

from ...config import Settings
from ...db import session_scope
from ...models import Capture
from ..base import RetracePlugin

log = logging.getLogger("retrace.plugins.page_backfill")

_USER_AGENT = "RetraceBackfill/0.1 (+https://github.com/jhammant/retrace; on-device personal archive)"
_MAX_BYTES = 2_000_000
_TIMEOUT_S = 10.0
_MIN_CONTENT_CHARS = 250          # below this we assume a wall / JS shell, not an article
_MAX_TEXT = 12000

# URL substrings that signal an action or one-time/credential link — never fetch.
_ACTION_PATTERNS = (
    "logout", "signout", "sign-out", "signin", "sign-in", "/login", "/auth/", "/oauth",
    "unsubscribe", "/delete", "/remove", "confirm", "verify", "reset", "magic",
    "token=", "auth_token", "access_token", "sessionid", "session_id", "otp=", "code=",
    "password", "/logout", "apikey", "api_key",
)


# --- HTML → text ------------------------------------------------------------

class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "head", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if self._depth == 0:
            t = data.strip()
            if t:
                self._parts.append(t)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self._parts)).strip()


def html_to_text(html: str) -> str:
    """Extract readable text. Prefers trafilatura when installed, else a stdlib strip."""
    if not html:
        return ""
    try:  # optional, much better extraction — `pip install retrace-cli[backfill]`
        import trafilatura

        extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
        if extracted and extracted.strip():
            return extracted.strip()[:_MAX_TEXT]
    except Exception:
        pass
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        return ""
    return p.text()[:_MAX_TEXT]


# --- guardrails -------------------------------------------------------------

def _host_blocked(host: str) -> bool:
    """Block loopback/LAN/link-local/.local — don't let backfill hit local services."""
    if not host:
        return True
    host = host.lower()
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        return False  # a normal hostname


def _domain_skipped(host: str, skip_domains) -> bool:
    host = (host or "").lower()
    for d in skip_domains:
        d = d.lower().strip()
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


def eligible(url: str, title: str, settings: Settings) -> tuple[bool, str | None]:
    """Return ``(ok, skip_reason)`` for a candidate URL."""
    from ...capture.privacy import is_sensitive

    parts = urlsplit(url)
    if parts.scheme != "https":
        return False, "not-https"
    if _host_blocked(parts.hostname or ""):
        return False, "local-host"
    if _domain_skipped(parts.hostname or "", settings.backfill_skip_domains):
        return False, "auth-domain"
    low = url.lower()
    if any(pat in low for pat in _ACTION_PATTERNS):
        return False, "action-url"
    if is_sensitive({"url": url, "window_title": title or ""}, settings):
        return False, "sensitive"
    return True, None


# --- fetch ------------------------------------------------------------------

def _default_fetch(url: str) -> dict | None:
    """GET ``url`` politely and return ``{"status", "url", "html"}`` or None."""
    import time

    import httpx

    try:
        with httpx.Client(
            follow_redirects=True, timeout=_TIMEOUT_S,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
        ) as client:
            resp = client.get(url)
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype.lower():
                return {"status": resp.status_code, "url": str(resp.url), "html": ""}
            html = resp.text[: _MAX_BYTES // 2]
            time.sleep(0.5)  # politeness between fetches (real network only)
            return {"status": resp.status_code, "url": str(resp.url), "html": html}
    except Exception:
        return None


def _looks_like_wall(html: str, text: str) -> bool:
    if len(text) < _MIN_CONTENT_CHARS:
        return True
    return 'type="password"' in html.lower()


# --- plugin -----------------------------------------------------------------

class PageBackfillPlugin(RetracePlugin):
    name = "page-backfill"
    description = "OPT-IN: re-fetch public history URLs to capture page text (outbound)."

    def __init__(self, fetch=None) -> None:
        self._fetch = fetch or _default_fetch

    def _state_path(self, s: Settings) -> Path:
        return s.home / "plugin_page_backfill.json"

    def _load_attempted(self, s: Settings) -> set[str]:
        p = self._state_path(s)
        if p.exists():
            try:
                return set(json.loads(p.read_text()))
            except (OSError, ValueError):
                return set()
        return set()

    def _save_attempted(self, s: Settings, attempted: set[str]) -> None:
        try:
            self._state_path(s).write_text(json.dumps(sorted(attempted)[-20000:]))
        except OSError:
            log.debug("could not persist backfill state", exc_info=True)

    def collect(self, settings: Settings) -> dict:
        if not settings.backfill_page_content:
            return {"name": self.name, "ingested": 0, "note": "disabled (opt-in)"}

        attempted = self._load_attempted(settings)
        limit = int(getattr(settings, "backfill_max_pages_per_run", 40) or 40)

        # Candidates: history rows that still lack fetched content.
        with session_scope(settings) as s:
            candidates = s.execute(
                select(Capture.id, Capture.content_hash, Capture.url, Capture.window_title)
                .where(Capture.text_source == "safari-history")
                .where(Capture.url.like("https://%"))
                .order_by(Capture.captured_at.desc())
                .limit(limit * 4)
            ).all()

        filled = skipped = 0
        for cid, chash, url, title in candidates:
            if filled >= limit:
                break
            if not chash or chash in attempted:
                continue
            ok, _reason = eligible(url, title or "", settings)
            attempted.add(chash)
            if not ok:
                skipped += 1
                continue
            result = self._fetch(url)
            if not result or result.get("status") != 200:
                skipped += 1
                continue
            # Re-check the *final* (post-redirect) URL against the guardrails.
            final_url = result.get("url") or url
            if final_url != url and not eligible(final_url, title or "", settings)[0]:
                skipped += 1
                continue
            text = html_to_text(result.get("html", ""))
            if _looks_like_wall(result.get("html", ""), text):
                skipped += 1
                continue
            body = "\n\n".join(p for p in ((title or "").strip(), text) if p)[:_MAX_TEXT]
            with session_scope(settings) as s:
                row = s.get(Capture, cid)
                if row is not None:
                    row.text = body
                    row.text_len = len(body)
                    row.text_source = "page-backfill"
            filled += 1

        self._save_attempted(settings, attempted)
        return {"name": self.name, "ingested": filled, "skipped": skipped}
