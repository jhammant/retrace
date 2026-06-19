"""Denylist + private-context gating."""

from __future__ import annotations

from retrace.capture import privacy


def test_denylist_matches_known_password_managers(settings):
    assert privacy.is_denylisted("com.1password.1password", "1Password", settings)
    assert privacy.is_denylisted("com.bitwarden.desktop", "Bitwarden", settings)
    # substring/variant match
    assert privacy.is_denylisted("com.agilebits.onepassword7", "1Password 7", settings)


def test_denylist_allows_normal_apps(settings):
    assert not privacy.is_denylisted("com.apple.Safari", "Safari", settings)
    assert not privacy.is_denylisted("com.microsoft.VSCode", "Code", settings)


def test_denylist_by_app_name(settings):
    from retrace import config as cfg

    cfg.update_config({"denylist_app_names": ["Secret Notes"]})
    s = cfg.get_settings()
    assert privacy.is_denylisted("com.example.whatever", "Secret Notes", s)
    assert not privacy.is_denylisted("com.example.whatever", "Public Notes", s)


def test_private_context_skipped_by_default(settings):
    ctx = {"bundle_id": "com.google.Chrome", "app_name": "Chrome", "private_browsing": True}
    skip, reason = privacy.evaluate(ctx, settings)
    assert skip and reason == "private"


def test_private_context_allowed_when_opted_in(settings):
    from retrace import config as cfg

    cfg.update_config({"capture_private_browsing": True})
    s = cfg.get_settings()
    ctx = {"bundle_id": "com.google.Chrome", "app_name": "Chrome", "private_browsing": True}
    skip, _ = privacy.evaluate(ctx, s)
    assert skip is False


def test_sensitive_keyword_blocks(settings):
    ctx = {"bundle_id": "com.apple.Safari", "app_name": "Safari",
           "url": "https://forum.example.com/nsfw-thread", "window_title": "thread"}
    skip, reason = privacy.evaluate(ctx, settings)
    assert skip and reason == "sensitive"


def test_sensitive_domain_blocks(settings):
    from retrace import config as cfg

    cfg.update_config({"sensitive_domains": ["badsite.example"]})
    s = cfg.get_settings()
    ctx = {"url": "https://badsite.example/x", "window_title": "page"}
    skip, reason = privacy.evaluate(ctx, s)
    assert skip and reason == "sensitive"


def test_sensitive_allows_normal(settings):
    ctx = {"url": "https://news.ycombinator.com", "window_title": "Hacker News"}
    skip, _ = privacy.evaluate(ctx, settings)
    assert skip is False


def test_sensitive_disabled(settings):
    from retrace import config as cfg

    cfg.update_config({"block_sensitive_content": False})
    s = cfg.get_settings()
    ctx = {"url": "https://example.com/nsfw", "window_title": "x"}
    assert privacy.is_sensitive(ctx, s) is False
