"""All built-in plugins load, and collect()/poll() are fail-soft (never raise)."""

from __future__ import annotations

from retrace.plugins import registry


def test_all_builtins_load(settings):
    names = {p.name for p in registry.load_plugins(settings)}
    for expected in {
        "claude-code", "spotify", "apple-music", "system-stats", "calendar",
        "notifications", "git-commits", "clipboard", "mail", "downloads", "recent-files",
    }:
        assert expected in names, expected


def test_collectors_failsoft_with_absent_sources(settings, monkeypatch, tmp_path):
    # Data-source-backed collectors return {ingested: 0} (no raise) when the source
    # is missing — without touching real data or compiling native helpers.
    from pathlib import Path
    from retrace.plugins.builtin import notifications, mail, downloads

    monkeypatch.setattr(notifications, "KC", tmp_path / "nope.db")
    assert notifications.NotificationsPlugin().collect(settings)["ingested"] == 0

    monkeypatch.setattr(mail, "_find_index", lambda: None)
    assert mail.MailPlugin().collect(settings)["ingested"] == 0

    monkeypatch.setattr(downloads, "_CHROME", tmp_path / "nope")
    monkeypatch.setattr(downloads, "_SAFARI_DL", tmp_path / "nope.plist")
    assert downloads.DownloadsPlugin().collect(settings)["ingested"] == 0


def test_git_plugin_parses_a_real_repo(settings, tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "demo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com"}
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, env={**env, "PATH": "/usr/bin:/bin"})
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    (repo / "f.txt").write_text("hi")
    run("add", "-A")
    run("commit", "-m", "initial commit")

    monkeypatch.setattr(settings, "git_repo_roots", [str(tmp_path)])
    from retrace.plugins.builtin.git_activity import GitActivityPlugin

    out = GitActivityPlugin().collect(settings)
    assert out["ingested"] >= 1
    from retrace.db import session_scope
    from retrace.models import Capture
    with session_scope(settings) as s:
        row = s.query(Capture).filter(Capture.app_name == "Git").first()
        assert row is not None
        assert "initial commit" in row.text
