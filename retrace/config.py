"""Configuration for Retrace.

Precedence: environment variables (``RETRACE_*``) > ``~/.retrace/config.toml`` > defaults.

All runtime data lives under ``RETRACE_HOME`` (default ``~/.retrace``). Override the
home directory with the ``RETRACE_HOME`` environment variable — this is also how the
test suite isolates itself from a real installation.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import tomli_w
from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


def _default_home() -> Path:
    """Resolve the Retrace home directory from the environment or the default."""
    env = os.environ.get("RETRACE_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".retrace"


# Apps that must NEVER be captured (password managers, secret stores, ...).
# Bundle ids are matched case-insensitively; substrings of the active app's bundle id
# also count as a match so vendor variants are covered.
_DEFAULT_DENYLIST_BUNDLE_IDS: list[str] = [
    "com.1password.1password",          # 1Password 8
    "com.agilebits.onepassword7",       # 1Password 7
    "com.agilebits.onepassword-osx",    # 1Password (older)
    "com.lastpass.LastPass",
    "com.bitwarden.desktop",
    "com.dashlane.Dashlane",
    "com.callpod.KeeperDesktop",
    "in.sinew.Enpass-Desktop",
    "com.apple.keychainaccess",
    "com.apple.Passwords",              # macOS Passwords app
    "com.nordvpn.macos",                # VPN/credential surfaces
]


class Settings(BaseSettings):
    """Runtime settings. Field names map to ``RETRACE_<UPPER>`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="RETRACE_",
        extra="ignore",
        validate_assignment=True,
    )

    # --- home / paths -------------------------------------------------------
    home: Path = Field(default_factory=_default_home)

    # --- network ------------------------------------------------------------
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765

    # --- capture timing -----------------------------------------------------
    capture_interval_s: float = 45.0     # periodic fallback tick cadence
    dedup_window_s: float = 300.0        # identical content within this window is skipped (5 min)
    idle_threshold_s: float = 120.0      # user considered "away" after this much idle
    pause_when_away: bool = True         # skip capture + time-tracking when idle/locked/asleep
    event_debounce_s: float = 1.5        # debounce app/window-switch driven captures

    # --- text / thumbnail ---------------------------------------------------
    min_ax_text_len: int = 40            # AX text shorter than this triggers OCR fallback
    thumb_max_edge: int = 1280           # longest edge of stored thumbnail (px)
    thumb_jpeg_quality: int = 70         # 0-100

    # --- retention ----------------------------------------------------------
    retention_days: int = 30             # purge captures/thumbnails older than this

    # --- features -----------------------------------------------------------
    enable_semantic_search: bool = True  # compute & store NL embeddings
    enable_caption: bool = True          # Foundation Models caption
    enable_vlm_caption: bool = False     # optional multimodal image caption (off by default)
    enable_calendar: bool = True         # EventKit correlation

    # --- privacy ------------------------------------------------------------
    denylist_bundle_ids: list[str] = Field(default_factory=lambda: list(_DEFAULT_DENYLIST_BUNDLE_IDS))
    denylist_app_names: list[str] = Field(default_factory=list)
    capture_private_browsing: bool = False  # never capture incognito/private windows

    # Sensitive-content blocking (adult/NSFW). Two layers, both default on:
    block_sensitive_content: bool = True    # domain/keyword match on url/title
    block_sensitive_images: bool = True     # on-device SensitiveContentAnalysis image scan
    sensitive_domains: list[str] = Field(default_factory=list)  # user-editable exact/substring
    sensitive_keywords: list[str] = Field(
        default_factory=lambda: ["porn", "xxx", "nsfw", "xvideos", "pornhub", "onlyfans"]
    )

    # --- page capture (opt-in, needs "Allow JavaScript from Apple Events") ---
    capture_page_text: bool = False         # store full page innerText (shown + searchable)
    capture_page_html: bool = False         # also store raw HTML (hidden; not shown in UI)

    # --- plugins ------------------------------------------------------------
    enable_plugins: bool = True
    disabled_plugins: list[str] = Field(default_factory=list)  # plugin names to skip
    git_repo_roots: list[str] = Field(default_factory=lambda: ["~/dev", "~/Developer", "~/Projects", "~/code"])
    recent_files_days: int = 7
    log_clipboard: bool = False  # opt-in: clipboard history can contain sensitive text (passwords)

    # --- daemon -------------------------------------------------------------
    model_idle_unload_s: float = 600.0   # unload heavy models after this idle period

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence (first wins): explicit init args > env vars > config.toml > defaults.
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        toml_path = _default_home() / "config.toml"
        if toml_path.is_file():
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=toml_path))
        return tuple(sources)

    # --- derived paths ------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.home / "retrace.db"

    @property
    def thumbs_dir(self) -> Path:
        return self.home / "thumbs"

    @property
    def tmp_dir(self) -> Path:
        return self.home / "tmp"

    @property
    def bin_dir(self) -> Path:
        return self.home / "bin"

    @property
    def status_path(self) -> Path:
        return self.home / "status.json"

    @property
    def config_path(self) -> Path:
        return self.home / "config.toml"

    def ensure_dirs(self) -> None:
        """Create the on-disk directory tree if missing."""
        for p in (self.home, self.thumbs_dir, self.tmp_dir, self.bin_dir):
            p.mkdir(parents=True, exist_ok=True)

    def thumb_dir_for_day(self, day: str) -> Path:
        """Thumbnail subdirectory for a ``YYYY-MM-DD`` day, created on demand."""
        d = self.thumbs_dir / day
        d.mkdir(parents=True, exist_ok=True)
        return d


# A curated subset of settings that are safe and meaningful to expose/edit via config.toml
# and the web Settings panel. (home/bind are intentionally omitted from the editable web set.)
EDITABLE_KEYS: tuple[str, ...] = (
    "capture_interval_s",
    "dedup_window_s",
    "idle_threshold_s",
    "pause_when_away",
    "min_ax_text_len",
    "thumb_max_edge",
    "retention_days",
    "enable_semantic_search",
    "enable_caption",
    "enable_vlm_caption",
    "enable_calendar",
    "denylist_bundle_ids",
    "denylist_app_names",
    "capture_private_browsing",
    "block_sensitive_content",
    "block_sensitive_images",
    "sensitive_domains",
    "sensitive_keywords",
    "capture_page_text",
    "capture_page_html",
    "enable_plugins",
    "disabled_plugins",
    "git_repo_roots",
    "recent_files_days",
    "log_clipboard",
)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()


def reload_settings() -> Settings:
    """Clear the cache and rebuild settings (used after config edits and in tests)."""
    get_settings.cache_clear()
    return get_settings()


def write_default_config(path: Path | None = None, *, overwrite: bool = False) -> Path:
    """Write a documented default ``config.toml`` and return its path."""
    s = get_settings()
    target = path or s.config_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        return target
    data: dict[str, object] = {k: getattr(s, k) for k in EDITABLE_KEYS}
    # Coerce Paths/sets to TOML-friendly primitives.
    target.write_bytes(tomli_w.dumps(data).encode("utf-8"))
    return target


def update_config(updates: dict[str, object]) -> Settings:
    """Merge ``updates`` into config.toml (only EDITABLE_KEYS) and reload settings."""
    s = get_settings()
    current: dict[str, object] = {k: getattr(s, k) for k in EDITABLE_KEYS}
    for key, value in updates.items():
        if key not in EDITABLE_KEYS:
            raise KeyError(f"{key!r} is not an editable setting")
        current[key] = value
    s.config_path.parent.mkdir(parents=True, exist_ok=True)
    s.config_path.write_bytes(tomli_w.dumps(current).encode("utf-8"))
    return reload_settings()
