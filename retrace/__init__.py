"""Retrace — a private, on-device macOS rewind. 100% local, no telemetry."""

__version__ = "0.1.0"

from .config import Settings, get_settings, reload_settings

__all__ = ["Settings", "get_settings", "reload_settings", "__version__"]
