"""Compile-on-first-use wrappers around the Swift helper binaries.

Each helper lives as a ``.swift`` source under ``retrace/native/swift``. On first
use it is compiled with ``swiftc`` into ``~/.retrace/bin/<name>`` and cached; it is
recompiled whenever the source hash changes. Helpers emit a single JSON object on
stdout and are designed to fail soft (``{"ok": false, ...}``) rather than crash the
Python caller.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings

log = logging.getLogger("retrace.native")

SWIFT_DIR = Path(__file__).parent / "swift"

KNOWN_HELPERS: tuple[str, ...] = (
    "retrace-capture",
    "retrace-context",
    "retrace-ocr",
    "retrace-present",
    "retrace-watch",
    "retrace-embed",
    "retrace-caption",
    "retrace-sensitivity",
    "retrace-menubar",
    "retrace-calendar",
)
# Note: activity ingest (knowledgeC / Safari / Chrome) reads SQLite directly from
# Python, so it needs no Swift helper.

_BUILD_TIMEOUT_S = 180


class HelperError(Exception):
    pass


def _swiftc() -> str | None:
    return shutil.which("swiftc")


def _source_path(name: str) -> Path:
    return SWIFT_DIR / f"{name}.swift"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SwiftHelper:
    """A single compile-on-first-use Swift helper binary."""

    def __init__(self, name: str, settings: Settings | None = None) -> None:
        self.name = name
        self._settings = settings or get_settings()
        self.source = _source_path(name)

    @property
    def binary(self) -> Path:
        return self._settings.bin_dir / self.name

    @property
    def _hash_file(self) -> Path:
        return self._settings.bin_dir / f"{self.name}.hash"

    def source_exists(self) -> bool:
        return self.source.is_file()

    def needs_build(self) -> bool:
        if not self.binary.exists():
            return True
        if not self._hash_file.exists():
            return True
        try:
            return self._hash_file.read_text().strip() != _sha256(self.source)
        except OSError:
            return True

    def build(self, *, force: bool = False) -> Path:
        if not self.source_exists():
            raise HelperError(f"no source for helper {self.name!r} at {self.source}")
        swiftc = _swiftc()
        if swiftc is None:
            raise HelperError("swiftc not found; install the Xcode command line tools")
        if not force and not self.needs_build():
            return self.binary

        self._settings.bin_dir.mkdir(parents=True, exist_ok=True)
        tmp_out = self._settings.bin_dir / f".{self.name}.build.{os.getpid()}"
        cmd = [swiftc, "-O", str(self.source), "-o", str(tmp_out)]
        log.info("compiling %s", self.name)
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_BUILD_TIMEOUT_S
            )
        except subprocess.TimeoutExpired as exc:  # pragma: no cover
            raise HelperError(f"compile of {self.name} timed out") from exc
        if proc.returncode != 0:
            tmp_out.unlink(missing_ok=True)
            raise HelperError(
                f"compile of {self.name} failed (exit {proc.returncode}):\n{proc.stderr}"
            )
        os.replace(tmp_out, self.binary)
        self.binary.chmod(0o755)
        self._hash_file.write_text(_sha256(self.source))
        return self.binary

    def ensure_built(self) -> Path:
        if self.needs_build():
            self.build()
        return self.binary

    def run(
        self,
        args: list[str] | None = None,
        *,
        timeout: float = 20.0,
        stdin: str | None = None,
    ) -> dict[str, Any] | None:
        """Run the helper and parse its JSON stdout. Returns ``None`` on any failure."""
        try:
            self.ensure_built()
        except HelperError as exc:
            log.warning("helper %s unavailable: %s", self.name, exc)
            return None

        cmd = [str(self.binary), *(args or [])]
        try:
            proc = subprocess.run(
                cmd,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            log.warning("helper %s timed out", self.name)
            return None
        except OSError as exc:
            log.warning("helper %s failed to launch: %s", self.name, exc)
            return None

        out = (proc.stdout or "").strip()
        if not out:
            log.warning("helper %s produced no output (stderr: %s)", self.name, proc.stderr.strip())
            return None
        # Helpers print one JSON object; tolerate trailing log lines by taking the last.
        line = out.splitlines()[-1]
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            log.warning("helper %s produced non-JSON output: %r", self.name, out[:200])
            return None


def get_helper(name: str, settings: Settings | None = None) -> SwiftHelper:
    return SwiftHelper(name, settings)


def build_all(settings: Settings | None = None, *, force: bool = False) -> dict[str, str]:
    """Compile every helper whose source exists. Returns name -> status."""
    s = settings or get_settings()
    s.ensure_dirs()
    results: dict[str, str] = {}
    for name in KNOWN_HELPERS:
        helper = SwiftHelper(name, s)
        if not helper.source_exists():
            results[name] = "no source (skipped)"
            continue
        try:
            helper.build(force=force)
            results[name] = "ok"
        except HelperError as exc:
            results[name] = f"FAILED: {exc}"
    return results


# --- typed convenience wrappers -------------------------------------------


def capture_frame(
    *,
    frame_path: str,
    thumb_path: str,
    max_edge: int,
    jpeg_quality: int,
    exclude_bundle_ids: list[str],
    display: str = "main",
    settings: Settings | None = None,
    timeout: float = 20.0,
) -> dict[str, Any] | None:
    cfg = {
        "frame_path": frame_path,
        "thumb_path": thumb_path,
        "max_edge": max_edge,
        "jpeg_quality": jpeg_quality,
        "exclude_bundle_ids": exclude_bundle_ids,
        "display": display,
    }
    return get_helper("retrace-capture", settings).run([json.dumps(cfg)], timeout=timeout)


def read_context(
    *,
    max_chars: int = 20000,
    max_nodes: int = 6000,
    timeout_ms: int = 1500,
    fetch_url: bool = True,
    fetch_page_text: bool = False,
    fetch_page_html: bool = False,
    settings: Settings | None = None,
    timeout: float = 10.0,
) -> dict[str, Any] | None:
    cfg = {
        "max_chars": max_chars,
        "max_nodes": max_nodes,
        "timeout_ms": timeout_ms,
        "fetch_url": fetch_url,
        "fetch_page_text": fetch_page_text,
        "fetch_page_html": fetch_page_html,
    }
    return get_helper("retrace-context", settings).run([json.dumps(cfg)], timeout=timeout)


def analyze_sensitivity(
    path: str, *, settings: Settings | None = None, timeout: float = 12.0
) -> dict[str, Any] | None:
    return get_helper("retrace-sensitivity", settings).run([path], timeout=timeout)


def ocr_image(path: str, *, settings: Settings | None = None, timeout: float = 30.0) -> dict[str, Any] | None:
    return get_helper("retrace-ocr", settings).run([path], timeout=timeout)


def get_presence(
    threshold_s: float = 120.0, *, settings: Settings | None = None, timeout: float = 5.0
) -> dict[str, Any] | None:
    return get_helper("retrace-present", settings).run([str(threshold_s)], timeout=timeout)


def embed_text(
    text: str, *, max_chars: int = 4000, settings: Settings | None = None, timeout: float = 15.0
) -> dict[str, Any] | None:
    if not text:
        return None
    return get_helper("retrace-embed", settings).run([text[:max_chars]], timeout=timeout)


def _main(argv: list[str] | None = None) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(prog="retrace.native.helpers")
    p.add_argument("--build-all", action="store_true", help="Compile all helper binaries.")
    p.add_argument("--force", action="store_true", help="Rebuild even if up to date.")
    args = p.parse_args(argv)

    if args.build_all:
        results = build_all(force=args.force)
        width = max(len(n) for n in results)
        for name, status in results.items():
            print(f"  {name.ljust(width)}  {status}")
        return 0 if all(v in ("ok", "no source (skipped)") for v in results.values()) else 1
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
