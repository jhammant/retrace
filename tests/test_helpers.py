"""Compile-on-first-use logic and JSON/fail-soft behavior of the native wrappers.

These tests use fake executable stand-ins so they never invoke ``swiftc``.
"""

from __future__ import annotations

import pytest

from retrace.native import helpers as H


def test_known_helpers_have_sources_for_current_milestones():
    # The four M2 helpers must have source on disk.
    for name in ("retrace-capture", "retrace-context", "retrace-ocr", "retrace-present"):
        assert H.SwiftHelper(name).source_exists(), name


def test_needs_build_hash_logic(settings):
    h = H.SwiftHelper("retrace-present", settings)
    settings.bin_dir.mkdir(parents=True, exist_ok=True)
    assert h.needs_build() is True  # no binary yet

    h.binary.write_text("fake binary")
    assert h.needs_build() is True  # binary but no hash recorded

    h._hash_file.write_text(H._sha256(h.source))
    assert h.needs_build() is False  # matching hash

    h._hash_file.write_text("0" * 64)
    assert h.needs_build() is True  # stale hash -> rebuild


def test_run_parses_json(settings, monkeypatch):
    h = H.SwiftHelper("retrace-present", settings)
    settings.bin_dir.mkdir(parents=True, exist_ok=True)
    h.binary.write_text('#!/bin/bash\necho \'{"ok":true,"idle_seconds":3}\'\n')
    h.binary.chmod(0o755)
    monkeypatch.setattr(h, "needs_build", lambda: False)

    out = h.run(["5"])
    assert out is not None
    assert out["ok"] is True
    assert out["idle_seconds"] == 3


def test_run_fail_soft_on_non_json(settings, monkeypatch):
    h = H.SwiftHelper("retrace-present", settings)
    settings.bin_dir.mkdir(parents=True, exist_ok=True)
    h.binary.write_text("#!/bin/bash\necho not-json-output\n")
    h.binary.chmod(0o755)
    monkeypatch.setattr(h, "needs_build", lambda: False)

    assert h.run([]) is None


def test_run_returns_none_when_unbuildable(settings, monkeypatch):
    # No swiftc + missing binary -> build fails -> run() returns None, never raises.
    monkeypatch.setattr(H, "_swiftc", lambda: None)
    h = H.SwiftHelper("retrace-present", settings)
    assert h.run(["5"]) is None


def test_build_missing_source_raises(settings):
    h = H.SwiftHelper("does-not-exist", settings)
    assert h.source_exists() is False
    with pytest.raises(H.HelperError):
        h.build()


def test_takes_last_line_when_warnings_precede_json(settings, monkeypatch):
    h = H.SwiftHelper("retrace-present", settings)
    settings.bin_dir.mkdir(parents=True, exist_ok=True)
    h.binary.write_text('#!/bin/bash\necho "warning: noise"\necho \'{"ok":true}\'\n')
    h.binary.chmod(0o755)
    monkeypatch.setattr(h, "needs_build", lambda: False)

    out = h.run([])
    assert out == {"ok": True}
