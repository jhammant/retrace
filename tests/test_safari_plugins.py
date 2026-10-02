"""Safari history, Reading List, and the opt-in page-content backfill."""

from __future__ import annotations

import plistlib
import sqlite3
from datetime import datetime, timezone

from retrace.db import session_scope
from retrace.models import Capture
from retrace.plugins.builtin import page_backfill as pb
from retrace.plugins.builtin.page_backfill import PageBackfillPlugin, eligible, html_to_text
from retrace.plugins.builtin.reading_list import ReadingListPlugin
from retrace.plugins.builtin.safari_history import MAC_EPOCH_OFFSET, SafariHistoryPlugin

ARTICLE = (
    "Quantum error correction is the set of techniques that protect fragile quantum "
    "information from decoherence and operational noise by encoding a logical qubit "
    "across many physical qubits. Surface codes are the leading practical approach "
    "because they tolerate relatively high physical error rates and need only local "
    "two-qubit interactions on a planar grid of qubits, which hardware can provide."
)


# --- Safari history ---------------------------------------------------------

def _make_history_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT);
        CREATE TABLE history_visits (id INTEGER PRIMARY KEY, history_item INTEGER,
                                     visit_time REAL, title TEXT);
        """
    )
    now_cf = datetime.now(timezone.utc).timestamp() - MAC_EPOCH_OFFSET
    conn.executemany("INSERT INTO history_items VALUES (?,?)", [
        (1, "https://example.com/page"),
        (2, "https://example.com/nsfw-clip"),
    ])
    conn.executemany("INSERT INTO history_visits VALUES (?,?,?,?)", [
        (10, 1, now_cf - 100, "Example Page"),
        (11, 2, now_cf - 50, "a clip"),
    ])
    conn.commit()
    conn.close()


def test_safari_history_ingests_and_skips_sensitive(settings, tmp_path):
    db = tmp_path / "History.db"
    _make_history_db(db)

    result = SafariHistoryPlugin(db_path=db).collect(settings)
    assert result["ingested"] == 1               # nsfw visit dropped
    assert result.get("skipped_sensitive") == 1

    with session_scope(settings) as s:
        row = s.query(Capture).filter(Capture.text_source == "safari-history").one()
        assert row.url == "https://example.com/page"
        assert row.window_title == "Example Page"
        assert row.app_name == "Safari"
        assert row.caption.startswith("🧭")


def test_safari_history_missing_db_is_soft(settings, tmp_path):
    result = SafariHistoryPlugin(db_path=tmp_path / "nope.db").collect(settings)
    assert result["ingested"] == 0


# --- Reading List -----------------------------------------------------------

def _write_bookmarks(path, archives_dir=None):
    item = {
        "WebBookmarkType": "WebBookmarkTypeLeaf",
        "URLString": "https://example.com/article",
        "URIDictionary": {"title": "Cool Article"},
        "WebBookmarkUUID": "UUID-1",
        "ReadingList": {
            "DateAdded": datetime(2026, 6, 15, 12, 0, 0),
            "PreviewText": "A short preview snippet.",
        },
    }
    sensitive = {
        "WebBookmarkType": "WebBookmarkTypeLeaf",
        "URLString": "https://example.com/nsfw-post",
        "URIDictionary": {"title": "x"},
        "WebBookmarkUUID": "UUID-2",
        "ReadingList": {"DateAdded": datetime(2026, 6, 15, 12, 0, 0), "PreviewText": "p"},
    }
    tree = {"Children": [{"Title": "com.apple.ReadingList", "Children": [item, sensitive]}]}
    with open(path, "wb") as fh:
        plistlib.dump(tree, fh, fmt=plistlib.FMT_BINARY)

    if archives_dir is not None:
        folder = archives_dir / "UUID-1"
        folder.mkdir(parents=True)
        arch = {"WebMainResource": {
            "WebResourceMIMEType": "text/html",
            "WebResourceData": f"<html><body><article>{ARTICLE}</article></body></html>".encode(),
        }}
        with open(folder / "Page.webarchive", "wb") as fh:
            plistlib.dump(arch, fh, fmt=plistlib.FMT_BINARY)


def test_reading_list_ingests_with_offline_archive_text(settings, tmp_path):
    bm = tmp_path / "Bookmarks.plist"
    archives = tmp_path / "ReadingListArchives"
    _write_bookmarks(bm, archives)

    result = ReadingListPlugin(bookmarks_path=bm, archives_dir=archives).collect(settings)
    assert result["ingested"] == 1               # nsfw item dropped
    assert result.get("skipped_sensitive") == 1

    with session_scope(settings) as s:
        row = s.query(Capture).filter(Capture.text_source == "reading-list").one()
        assert row.url == "https://example.com/article"
        assert "Surface codes" in row.text       # full content came from the webarchive
        assert row.caption.startswith("📖")


def test_reading_list_falls_back_to_preview_without_archive(settings, tmp_path):
    bm = tmp_path / "Bookmarks.plist"
    _write_bookmarks(bm, archives_dir=None)
    result = ReadingListPlugin(bookmarks_path=bm, archives_dir=tmp_path / "empty").collect(settings)
    assert result["ingested"] == 1
    with session_scope(settings) as s:
        row = s.query(Capture).filter(Capture.text_source == "reading-list").one()
        assert "preview snippet" in row.text


# --- page backfill ----------------------------------------------------------

def _seed_history(settings, url, title="t", chash="h1"):
    with session_scope(settings) as s:
        s.add(Capture(
            captured_at=datetime.now(timezone.utc).replace(tzinfo=None),
            app_name="Safari", bundle_id="com.apple.Safari",
            window_title=title, url=url, text=f"{title}\n{url}", text_len=10,
            text_source="safari-history", content_hash=chash,
        ))


class _FetchSpy:
    def __init__(self, response):
        self.calls = []
        self._response = response

    def __call__(self, url):
        self.calls.append(url)
        return self._response(url) if callable(self._response) else self._response


def test_backfill_disabled_by_default_makes_no_request(settings):
    _seed_history(settings, "https://example.com/post")
    spy = _FetchSpy({"status": 200, "url": "x", "html": "x"})
    result = PageBackfillPlugin(fetch=spy).collect(settings)
    assert result["ingested"] == 0
    assert "disabled" in result.get("note", "")
    assert spy.calls == []                        # never reached the network


def test_backfill_fills_public_page(settings):
    from retrace import config as cfg
    cfg.update_config({"backfill_page_content": True})
    s = cfg.get_settings()
    _seed_history(s, "https://example.com/quantum", title="Quantum", chash="q1")

    html = f"<html><body><article>{ARTICLE}</article></body></html>"
    spy = _FetchSpy({"status": 200, "url": "https://example.com/quantum", "html": html})
    result = PageBackfillPlugin(fetch=spy).collect(s)

    assert result["ingested"] == 1
    assert spy.calls == ["https://example.com/quantum"]
    with session_scope(s) as sess:
        row = sess.query(Capture).filter(Capture.url == "https://example.com/quantum").one()
        assert row.text_source == "page-backfill"
        assert "Surface codes" in row.text


def test_backfill_skips_login_wall_and_remembers_attempt(settings):
    from retrace import config as cfg
    cfg.update_config({"backfill_page_content": True})
    s = cfg.get_settings()
    _seed_history(s, "https://example.com/walled", chash="w1")

    spy = _FetchSpy({"status": 200, "url": "https://example.com/walled",
                     "html": "<html><body>Please sign in</body></html>"})
    plugin = PageBackfillPlugin(fetch=spy)
    first = plugin.collect(s)
    assert first["ingested"] == 0 and first["skipped"] == 1
    assert len(spy.calls) == 1
    # second pass must not re-fetch a URL already attempted
    plugin.collect(s)
    assert len(spy.calls) == 1


def test_backfill_guardrails_block_unsafe_urls(settings):
    from retrace import config as cfg
    cfg.update_config({"backfill_page_content": True})
    s = cfg.get_settings()
    _seed_history(s, "https://example.com/account?action=logout", chash="a1")  # action
    _seed_history(s, "https://x.com/home", chash="a2")                          # auth domain
    _seed_history(s, "https://localhost/admin", chash="a3")                     # local host
    _seed_history(s, "https://example.com/nsfw", chash="a4")                    # sensitive
    _seed_history(s, "http://example.com/insecure", chash="a5")                 # not https

    spy = _FetchSpy({"status": 200, "url": "x", "html": "x"})
    result = PageBackfillPlugin(fetch=spy).collect(s)
    assert result["ingested"] == 0
    assert spy.calls == []                        # nothing unsafe was ever fetched
    # The http:// row is excluded by the candidate query (https only); the other
    # four reach eligible() and are each rejected before any fetch.
    assert result["skipped"] == 4


def test_eligible_unit_cases(settings):
    assert eligible("https://en.wikipedia.org/wiki/Qubit", "Qubit", settings)[0] is True
    assert eligible("https://mail.google.com/u/0", "Inbox", settings) == (False, "auth-domain")
    assert eligible("https://10.0.0.5/x", "", settings) == (False, "local-host")
    assert eligible("https://site.com/unsubscribe?id=5", "", settings) == (False, "action-url")
    assert eligible("http://site.com/x", "", settings) == (False, "not-https")


def test_html_to_text_strips_scripts():
    out = html_to_text("<html><head><style>.a{}</style></head><body>"
                        "<script>evil()</script><p>Hello world</p></body></html>")
    assert "Hello world" in out
    assert "evil" not in out
