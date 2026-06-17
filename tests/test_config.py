"""Config precedence, paths, and editable-config round-trips."""

from __future__ import annotations

import tomllib

import pytest


def test_defaults(settings):
    assert settings.bind_host == "127.0.0.1"
    assert settings.bind_port == 8765
    assert settings.retention_days == 30
    assert settings.enable_semantic_search is True
    assert settings.capture_private_browsing is False
    # Password managers are denied by default.
    assert any("1password" in b.lower() for b in settings.denylist_bundle_ids)


def test_paths_under_home(settings):
    assert settings.db_path == settings.home / "retrace.db"
    assert settings.thumbs_dir == settings.home / "thumbs"
    assert settings.tmp_dir == settings.home / "tmp"
    assert settings.status_path == settings.home / "status.json"
    for p in (settings.home, settings.thumbs_dir, settings.tmp_dir, settings.bin_dir):
        assert p.is_dir()


def test_env_overrides_default(settings, monkeypatch):
    from retrace import config as cfg

    monkeypatch.setenv("RETRACE_RETENTION_DAYS", "7")
    monkeypatch.setenv("RETRACE_BIND_PORT", "9999")
    s = cfg.reload_settings()
    assert s.retention_days == 7
    assert s.bind_port == 9999


def test_toml_overrides_default_but_env_wins(settings, monkeypatch):
    from retrace import config as cfg

    # Write a config.toml that sets retention_days = 5
    settings.config_path.write_text("retention_days = 5\nthumb_max_edge = 800\n")
    s = cfg.reload_settings()
    assert s.retention_days == 5
    assert s.thumb_max_edge == 800

    # Env must take precedence over the TOML value.
    monkeypatch.setenv("RETRACE_RETENTION_DAYS", "99")
    s2 = cfg.reload_settings()
    assert s2.retention_days == 99
    # TOML-only value still applies.
    assert s2.thumb_max_edge == 800


def test_update_config_writes_toml_and_reloads(settings):
    from retrace import config as cfg

    cfg.update_config({"retention_days": 14, "denylist_app_names": ["Secret App"]})
    raw = tomllib.loads(settings.config_path.read_text())
    assert raw["retention_days"] == 14
    assert raw["denylist_app_names"] == ["Secret App"]
    assert cfg.get_settings().retention_days == 14


def test_update_config_rejects_unknown_key(settings):
    from retrace import config as cfg

    with pytest.raises(KeyError):
        cfg.update_config({"home": "/tmp/evil"})
