"""App plugin architecture: registry, Claude Code collector, enrich hook."""

from __future__ import annotations

import json
from datetime import datetime

from retrace.db import session_scope
from retrace.models import Capture
from retrace.plugins import registry
from retrace.plugins.builtin.claude_code import ClaudeCodePlugin


def test_registry_includes_builtin(settings):
    assert "claude-code" in [p.name for p in registry.load_plugins(settings)]


def test_disabled_plugins_respected(settings):
    from retrace import config as cfg

    cfg.update_config({"disabled_plugins": ["claude-code"]})
    s = cfg.get_settings()
    assert "claude-code" not in [p.name for p in registry.load_plugins(s)]


def _write_transcript(projects_dir):
    proj = projects_dir / "-Users-me-dev-proj"
    proj.mkdir(parents=True)
    lines = [
        {"type": "ai-title", "aiTitle": "Build feature X", "sessionId": "s1"},
        {"type": "user", "uuid": "u1", "timestamp": "2026-06-10T10:00:00Z",
         "cwd": "/Users/me/dev/proj",
         "message": {"role": "user", "content": "How do I add tests?"}},
        {"type": "assistant", "uuid": "a1", "timestamp": "2026-06-10T10:00:05Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Use pytest."}]}},
        {"type": "user", "uuid": "u2", "timestamp": "2026-06-10T10:01:00Z",
         "cwd": "/Users/me/dev/proj",
         "message": {"role": "user", "content": "Thanks!"}},
    ]
    (proj / "s1.jsonl").write_text("\n".join(json.dumps(x) for x in lines))


def test_claude_code_collect_ingests_turns(settings, tmp_path):
    projects = tmp_path / "claude-projects"
    _write_transcript(projects)
    plugin = ClaudeCodePlugin(projects_dir=projects)

    result = plugin.collect(settings)
    assert result["ingested"] == 2

    with session_scope(settings) as s:
        rows = s.query(Capture).filter(Capture.bundle_id == "com.anthropic.claude-code").all()
        assert len(rows) == 2
        first = sorted(rows, key=lambda r: r.captured_at)[0]
        assert "How do I add tests?" in first.text
        assert "Use pytest." in first.text
        assert first.caption == "Build feature X"
        assert first.app_name == "Claude Code"
        assert first.window_title == "proj"


def test_claude_code_collect_is_idempotent(settings, tmp_path):
    projects = tmp_path / "claude-projects"
    _write_transcript(projects)
    plugin = ClaudeCodePlugin(projects_dir=projects)
    plugin.collect(settings)

    # Re-collect after wiping incremental state -> dedup by uuid still prevents dupes.
    (settings.home / "plugin_claude_code.json").unlink()
    again = plugin.collect(settings)
    assert again["ingested"] == 0
    with session_scope(settings) as s:
        assert s.query(Capture).filter(Capture.bundle_id == "com.anthropic.claude-code").count() == 2


def test_user_plugin_enriches_capture(settings, tmp_path, monkeypatch):
    # Drop a user plugin into ~/.retrace/plugins and confirm the pipeline applies it.
    plugins_dir = settings.home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    (plugins_dir / "myplug.py").write_text(
        "from retrace.plugins import RetracePlugin\n"
        "class P(RetracePlugin):\n"
        "    name = 'test-enricher'\n"
        "    bundle_ids = ('com.test.app',)\n"
        "    def enrich(self, context, settings):\n"
        "        return {'caption': 'ENRICHED', 'text_append': 'plugin-added-text'}\n"
        "PLUGIN = P()\n"
    )
    assert "test-enricher" in [p.name for p in registry.load_plugins(settings)]

    from retrace.capture import pipeline
    from retrace.status import StatusLedger

    monkeypatch.setattr(pipeline, "get_presence", lambda *a, **k: {"ok": True, "present": True})
    monkeypatch.setattr(pipeline, "read_context", lambda settings=None, **k: {
        "ok": True, "app_name": "TestApp", "bundle_id": "com.test.app",
        "window_title": "W", "text": "base text here that is long enough",
        "text_source": "accessibility", "private_browsing": False})
    monkeypatch.setattr(pipeline, "analyze_sensitivity", lambda *a, **k: {"available": False})

    def _cap(*, frame_path, thumb_path, **k):
        from pathlib import Path
        Path(frame_path).write_bytes(b"x"); Path(thumb_path).write_bytes(b"y")
        return {"ok": True, "frame_path": frame_path, "thumb_path": thumb_path}

    monkeypatch.setattr(pipeline, "capture_frame", _cap)
    StatusLedger(settings).set_enabled(True)

    res = pipeline.capture_once(settings=settings, force=True)
    assert res.status == "stored"
    with session_scope(settings) as s:
        row = s.query(Capture).filter(Capture.app_name == "TestApp").one()
        assert row.caption == "ENRICHED"
        assert "plugin-added-text" in row.text
        assert row.caption_model == "plugin"
