"""Storage optimiser: tiered compaction, idempotence, dry runs, time budget and disk budget."""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

from PIL import Image

from retrace.capture.optimize import STATE_FILENAME, optimize, storage_usage
from retrace.cli import build_parser
from retrace.db import session_scope
from retrace.models import Capture, utcnow

TODAY = date(2026, 10, 2)


def _fat_frame(path, seed: int) -> None:
    """A 1280x720 frame saved at high quality, like ImageIO's output."""
    rnd = random.Random(seed)
    im = Image.new("RGB", (1280, 720))
    px = im.load()
    for y in range(0, 720, 2):
        for x in range(0, 1280, 2):
            v = (x * 3 + y * 5 + rnd.randint(0, 40)) % 256
            for dx in (0, 1):
                for dy in (0, 1):
                    px[x + dx, y + dy] = (v, (v * 2) % 256, 255 - v)
    im.save(path, "JPEG", quality=95)


def _make_day(settings, age_days: int, n: int = 2) -> list:
    day = (TODAY - timedelta(days=age_days)).isoformat()
    d = settings.thumb_dir_for_day(day)
    files = []
    for i in range(n):
        f = d / f"f{age_days}-{i}.jpg"
        _fat_frame(f, seed=age_days * 10 + i)
        files.append(f)
    return files


def _sizes(files):
    return [f.stat().st_size for f in files]


def test_compacts_by_age_and_leaves_recent_days_alone(settings):
    recent = _make_day(settings, 0) + _make_day(settings, 1)
    compact = _make_day(settings, 5)
    deep = _make_day(settings, 20)
    recent_before, compact_before, deep_before = _sizes(recent), _sizes(compact), _sizes(deep)

    report = optimize(settings, today=TODAY)

    assert _sizes(recent) == recent_before
    assert all(a < b for a, b in zip(_sizes(compact), compact_before))
    assert all(a < b for a, b in zip(_sizes(deep), deep_before))
    for f in compact:
        with Image.open(f) as im:
            assert im.size == (1280, 720)
    for f in deep:
        with Image.open(f) as im:
            assert max(im.size) == settings.deep_compact_max_edge
    assert report["tiers"]["compact"]["days"] == 1
    assert report["tiers"]["deep"]["days"] == 1
    assert report["saved_bytes"] > 0
    state = json.loads((settings.home / STATE_FILENAME).read_text())
    assert state["days"] == {
        (TODAY - timedelta(days=5)).isoformat(): "compact",
        (TODAY - timedelta(days=20)).isoformat(): "deep",
    }


def test_second_run_does_nothing(settings):
    _make_day(settings, 5)
    optimize(settings, today=TODAY)
    again = optimize(settings, today=TODAY)
    assert all(t["frames"] == 0 for t in again["tiers"].values())


def test_compacted_day_moves_to_deep_tier_when_it_ages(settings):
    files = _make_day(settings, 5)
    optimize(settings, today=TODAY)
    optimize(settings, today=TODAY + timedelta(days=10))
    for f in files:
        with Image.open(f) as im:
            assert max(im.size) == settings.deep_compact_max_edge


def test_dry_run_projects_savings_without_writing(settings):
    files = _make_day(settings, 5) + _make_day(settings, 20)
    before = _sizes(files)
    report = optimize(settings, dry_run=True, today=TODAY)
    assert _sizes(files) == before
    assert not (settings.home / STATE_FILENAME).exists()
    assert report["dry_run"] is True
    assert report["saved_bytes"] > 0
    assert report["tiers"]["deep"]["frames"] == 2


def test_time_budget_stops_early_and_resumes(settings):
    files = _make_day(settings, 5, n=3)
    first = optimize(settings, max_seconds=1e-9, today=TODAY)
    assert first["incomplete"] is True
    state_path = settings.home / STATE_FILENAME
    assert json.loads(state_path.read_text())["days"] == {}
    before = _sizes(files)
    optimize(settings, today=TODAY)
    assert all(a <= b for a, b in zip(_sizes(files), before))
    assert json.loads(state_path.read_text())["days"]


def test_tiers_can_be_switched_off(settings):
    settings.compact_after_days = 0
    settings.deep_compact_after_days = 0
    files = _make_day(settings, 20)
    before = _sizes(files)
    report = optimize(settings, today=TODAY)
    assert _sizes(files) == before
    assert report["tiers"] == {}


def test_budget_evicts_oldest_thumbnails_but_keeps_rows(settings):
    settings.compact_after_days = 0
    settings.deep_compact_after_days = 0
    old_day = (TODAY - timedelta(days=25)).isoformat()
    old = _make_day(settings, 25)
    recent = _make_day(settings, 1)
    with session_scope(settings) as s:
        for f in old:
            s.add(Capture(captured_at=utcnow(), app_name="App", text="searchable", text_len=10,
                          thumb_path=f"{old_day}/{f.name}"))
    settings.max_storage_mb = max(1, storage_usage(settings)["total_bytes"] // (1024 * 1024))

    report = optimize(settings, today=TODAY)

    assert report["budget"]["days_evicted"] == [old_day]
    assert not any(f.exists() for f in old)
    assert all(f.exists() for f in recent)
    with session_scope(settings) as s:
        rows = s.query(Capture).all()
        assert len(rows) == 2
        assert all(r.thumb_path is None for r in rows)


def test_budget_never_evicts_today_or_yesterday(settings):
    settings.max_storage_mb = 1
    recent = _make_day(settings, 0) + _make_day(settings, 1)
    report = optimize(settings, today=TODAY)
    assert report["budget"]["days_evicted"] == []
    assert all(f.exists() for f in recent)


def test_cli_parses_optimize():
    args = build_parser().parse_args(["optimize", "--dry-run", "--max-seconds", "30"])
    assert args.dry_run is True
    assert args.max_seconds == 30
