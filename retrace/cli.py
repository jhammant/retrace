"""The ``retrace`` command-line entry point.

Subcommands: ``init``, ``serve``, ``mcp``, ``tick``, ``doctor``, ``start``,
``stop``, ``status``, ``scan``, ``purge``, ``optimize``, ``collect``, ``plugins``,
``menubar`` (``tray``), ``autostart``, ``version``.

Handlers import heavy modules lazily so the CLI stays responsive and so a
half-built checkout can still run ``--help``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_init(args: argparse.Namespace) -> int:
    from .config import get_settings, write_default_config
    from .db import init_db

    s = get_settings()
    s.ensure_dirs()
    cfg = write_default_config(overwrite=args.force)
    init_db(s)
    print(f"Retrace home : {s.home}")
    print(f"Database     : {s.db_path}")
    print(f"Config       : {cfg}")
    print(f"Thumbnails   : {s.thumbs_dir}")
    print("Initialized. Capture is OFF by default — enable with `retrace start`.")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    from . import __version__

    print(f"retrace {__version__}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .config import get_settings
    from .db import init_db

    s = get_settings()
    s.ensure_dirs()
    init_db(s)
    if sys.stdout is None or sys.stderr is None:
        # Windowless (pythonw, e.g. started at sign-in): there is no console, and
        # uvicorn's logging needs real streams. Keep a log file instead.
        log = open(s.home / "server.log", "a", buffering=1, encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    host = args.host or s.bind_host
    port = args.port or s.bind_port
    print(f"Retrace API + web UI on http://{host}:{port}  (Ctrl-C to stop)")
    uvicorn.run(
        "retrace.api.app:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp.server import main as mcp_main

    mcp_main()
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    from .capture.pipeline import capture_once
    from .db import init_db

    init_db()
    result = capture_once(force=args.force, reason="cli-tick")
    _print_json(result.as_dict())
    return 0 if result.ok else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    from .native.doctor import run_doctor

    report = run_doctor()
    if args.json:
        _print_json(report)
    else:
        from .native.doctor import format_report

        print(format_report(report))
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    from .status import StatusLedger

    StatusLedger().set_enabled(True)
    print("Capture ENABLED.")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    from .status import StatusLedger

    StatusLedger().set_enabled(False)
    print("Capture DISABLED.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from .status import StatusLedger

    _print_json(StatusLedger().snapshot())
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from .activity.service import scan_and_upsert
    from .db import init_db

    init_db()
    result = scan_and_upsert(full=args.full)
    _print_json(result)
    return 0


def cmd_activity_repair(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .activity.repair import repair
    from .config import get_settings

    settings = get_settings()
    path = Path(args.db).expanduser() if args.db else settings.db_path
    before, after, changed = repair(path, interval_s=settings.capture_interval_s,
                                    recredit=args.recredit, dry_run=args.dry_run)
    print(f"Database: {path} ({'dry run' if args.dry_run else 'repaired'}, {changed} changed rows)")
    print("Local day     Before (s)   After (s)   Change")
    for day in sorted(before.keys() | after.keys()):
        old, new = before.get(day, 0.0), after.get(day, 0.0)
        print(f"{day}  {old:10.0f}  {new:10.0f}  {new - old:+8.0f}")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    from .db import init_db
    from .plugins.registry import run_collectors

    init_db()
    _print_json(run_collectors())
    return 0


def cmd_plugins(args: argparse.Namespace) -> int:
    from .plugins.registry import list_plugins

    _print_json(list_plugins())
    return 0


def cmd_menubar(args: argparse.Namespace) -> int:
    """Show the menu bar item (macOS) or tray icon (Windows).

    Starts the server if it isn't already running.
    """
    import subprocess
    import sys
    import time
    import urllib.request

    from .config import get_settings
    from .native.helpers import get_helper
    from .platform import IS_WINDOWS, detached

    s = get_settings()
    base = f"http://{s.bind_host}:{s.bind_port}"

    def server_up() -> bool:
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1)
            return True
        except Exception:
            return False

    if not server_up():
        print(f"Starting Retrace server on {base} …")
        # Detached so capture keeps running after the menu bar UI is closed.
        subprocess.Popen(
            [sys.executable, "-m", "retrace.cli", "serve"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **detached(),
        )
        for _ in range(30):
            if server_up():
                break
            time.sleep(0.5)
        if not server_up():
            print("Server did not come up in time; the icon will show 'offline'.")

    if IS_WINDOWS:
        from .native.win.tray import run as run_tray

        print("Retrace is now in the notification area (bottom-right; it may be under ^). "
              "Closing it leaves capture running.")
        try:
            run_tray(base)
        except ImportError as exc:
            print(f"Could not load the tray icon ({exc}); reinstall retrace-cli on Windows.")
            return 1
        except KeyboardInterrupt:
            pass
        return 0

    try:
        binary = get_helper("retrace-menubar", s).ensure_built()
    except Exception as exc:
        print(f"Could not build the menu bar app: {exc}")
        return 1

    print("Retrace is now in your menu bar (top-right). Closing it leaves capture running.")
    try:
        subprocess.run([str(binary), base])
    except KeyboardInterrupt:
        pass
    return 0


def cmd_autostart(args: argparse.Namespace) -> int:
    """Start Retrace (tray icon + capture server) when you sign in to Windows."""
    from .platform import IS_WINDOWS

    if not IS_WINDOWS:
        print("On macOS, use the launchd agent in scripts/com.retrace.daemon.plist "
              "(copy it to ~/Library/LaunchAgents and `launchctl load` it).")
        return 1
    from .native.win import autostart

    if args.action == "install":
        link = autostart.install()
        print(f"Retrace will start at sign-in: {link}")
    elif args.action == "remove":
        print("Removed." if autostart.remove() else "Autostart was not installed.")
    else:
        _print_json(autostart.status())
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    from .capture.retention import purge_older_than
    from .config import get_settings
    from .db import init_db

    init_db()
    days = args.days if args.days is not None else get_settings().retention_days
    result = purge_older_than(days)
    _print_json(result)
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    from .capture.optimize import optimize
    from .db import init_db

    init_db()
    report = optimize(dry_run=args.dry_run, max_seconds=args.max_seconds)
    if args.json:
        _print_json(report)
        return 0
    mb = lambda b: f"{b / 1048576:,.0f} MB"  # noqa: E731
    verb = "Would save" if report["dry_run"] else "Saved"
    print(f"Storage: {mb(report['before_bytes'])} -> {mb(report['after_bytes'])}  "
          f"({verb.lower()} {mb(report['saved_bytes'])})")
    th = report.get("thin") or {}
    if th.get("days"):
        print(f"  thin     {th['days']} day(s): {th['frames_dropped']} frame(s) dropped, "
              f"{mb(th['bytes_freed'])} (schedule {th['schedule']})")
    for name, t in report["tiers"].items():
        if t["days"]:
            print(f"  {name:8} {t['days']} day(s), {t['frames']} frame(s): "
                  f"{mb(t['bytes_before'])} -> {mb(t['bytes_after'])}  "
                  f"(after {t['after_days']} days, {t['max_edge']} px, quality {t['quality']})")
    b = report["budget"]
    if b["days_evicted"]:
        print(f"  budget   {len(b['days_evicted'])} oldest day(s) lose thumbnails, {mb(b['bytes_freed'])}")
    if report["incomplete"]:
        print("  time budget reached; the next run carries on")
    return 0


def _parse_when(value: str):
    """'14:05' (today), '2026-10-02 14:05', or an ISO datetime -> aware local datetime."""
    from datetime import datetime

    v = value.strip()
    now = datetime.now().astimezone()
    if len(v) <= 5 and ":" in v:
        h, m = (int(x) for x in v.split(":"))
        return now.replace(hour=h, minute=m, second=0, microsecond=0)
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.astimezone()


def _parse_span(value: str):
    """'30m', '2h', '90' (minutes) -> timedelta."""
    from datetime import timedelta

    v = value.strip().lower()
    if v.endswith("h"):
        return timedelta(hours=float(v[:-1]))
    return timedelta(minutes=float(v.rstrip("m")))


def cmd_patterns(args: argparse.Namespace) -> int:
    from .insights import load_events, mine

    report = mine(load_events(days=args.days), include_examples=args.text)
    if args.json:
        _print_json(report)
        return 0
    w, sm = report["window"], report["summary"]
    print(f"Last {args.days} days: {w['active_days']} active days, {sm['focus_hours']} h in focus, "
          f"{sm['switches']} switches, {sm['typed_prompts']} prompts to AI tools, "
          f"{sm['clipboard_copies']} copies (focus from {w['focus_source']})")
    cands = report["candidates"][: args.top]
    if not cands:
        print("\nNothing repeats often enough to suggest yet.")
        return 0
    print(f"\nAutomation candidates (top {len(cands)}, by estimated minutes a week):")
    for i, c in enumerate(cands, 1):
        ev = ", ".join(f"{k} {v}" for k, v in c["evidence"].items() if not isinstance(v, list))
        print(f"\n{i:2}. [{c['kind']}] {c['title']}  ~{c['weekly_minutes']:.0f} min/week")
        print(f"    {ev}")
        print(f"    → {c['suggestion']}")
        for ex in c.get("examples", [])[:2]:
            print(f"      e.g. {ex}")
    return 0


def cmd_steps(args: argparse.Namespace) -> int:
    from datetime import datetime

    from .insights import steps

    now = datetime.now().astimezone()
    if args.since:
        start = _parse_when(args.since)
        end = _parse_when(args.until) if args.until else now
    else:
        start, end = now - _parse_span(args.last), now
    out = steps(start=start, end=end, include_text=args.text)
    if args.json:
        _print_json(out)
        return 0
    print(f"{start:%H:%M}–{end:%H:%M}: {out['counts']['screen']} screen steps, {out['counts']['copy']} copies, "
          f"{out['counts']['ask']} prompts, {out['counts']['event']} other events")
    for st in out["steps"]:
        if st["type"] == "screen":
            extra = f"  ({', '.join(st['titles'][:2])})" if st["titles"] else ""
            print(f"  {st['start'][11:16]}  {st['minutes']:5.1f} min  {st['context']}{extra}")
        elif st["type"] == "copy":
            print(f"  {st['at'][11:16]}             copied {st['kind']} ({st['chars']} chars)")
        elif st["type"] == "ask":
            print(f"  {st['at'][11:16]}             asked {st['assistant']} ({st['words']} words)"
                  + (f": {st['prompt'][:80]}" if 'prompt' in st else ""))
        else:
            print(f"  {st['at'][11:16]}             {st.get('app') or st['source']} event")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="retrace", description="Private, on-device rewind for macOS and Windows.")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="Create ~/.retrace, write default config, init the database.")
    sp.add_argument("--force", action="store_true", help="Overwrite an existing config.toml.")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("version", help="Print the version.")
    sp.set_defaults(func=cmd_version)

    sp = sub.add_parser("serve", help="Run the HTTP API + web UI.")
    sp.add_argument("--host", default=None)
    sp.add_argument("--port", type=int, default=None)
    sp.add_argument("--reload", action="store_true")
    sp.add_argument("--log-level", default="info")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("mcp", help="Run the read-only MCP server (stdio).")
    sp.set_defaults(func=cmd_mcp)

    sp = sub.add_parser("tick", help="Run a single capture cycle now.")
    sp.add_argument("--force", action="store_true", help="Ignore gating (enabled/idle/dedup).")
    sp.set_defaults(func=cmd_tick)

    sp = sub.add_parser("doctor", help="Check permissions and native capabilities.")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("start", help="Enable capture (persists).")
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("stop", help="Disable capture (persists).")
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("status", help="Print the capture status ledger.")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("scan", help="Ingest activity (knowledgeC / Safari / Chrome / Edge).")
    sp.add_argument("--full", action="store_true", help="Full rescan instead of incremental.")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("activity", help="Activity maintenance commands.")
    activity_sub = sp.add_subparsers(dest="activity_command", required=True)
    repair_sp = activity_sub.add_parser("repair", help="Repair active sample times and local days.")
    repair_sp.add_argument("--db", default=None, help="SQLite database path.")
    repair_sp.add_argument("--dry-run", action="store_true", help="Show changes without writing.")
    repair_sp.add_argument("--recredit", action="store_true", help="Recompute seconds from sample gaps.")
    repair_sp.set_defaults(func=cmd_activity_repair)

    sp = sub.add_parser("purge", help="Delete captures + thumbnails older than N days.")
    sp.add_argument("--days", type=int, default=None)
    sp.set_defaults(func=cmd_purge)

    sp = sub.add_parser("optimize", help="Shrink ageing thumbnails (and enforce max_storage_mb).")
    sp.add_argument("--dry-run", action="store_true", help="Report projected savings; change nothing.")
    sp.add_argument("--max-seconds", type=float, default=None, help="Stop after this long; resumes next run.")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_optimize)

    sp = sub.add_parser("collect", help="Run app plugin collectors (e.g. Claude Code history).")
    sp.set_defaults(func=cmd_collect)

    sp = sub.add_parser("patterns", help="What you repeat, ranked as automation candidates.")
    sp.add_argument("--days", type=int, default=28)
    sp.add_argument("--top", type=int, default=15)
    sp.add_argument("--text", action="store_true", help="Include example prompts, copies and searches.")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_patterns)

    sp = sub.add_parser("steps", help="Replay a stretch of time as steps (capture a workflow).")
    sp.add_argument("--last", default="30m", help="How far back, e.g. 30m, 2h (default 30m).")
    sp.add_argument("--since", help="Start instead, e.g. 14:05 or 2026-10-02T14:05.")
    sp.add_argument("--until", help="End (default now).")
    sp.add_argument("--text", action="store_true", help="Include copied/typed text and on-screen samples.")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_steps)

    sp = sub.add_parser("plugins", help="List installed app plugins.")
    sp.set_defaults(func=cmd_plugins)

    sp = sub.add_parser("menubar", aliases=["tray"],
                        help="Show the menu bar item (macOS) or tray icon (Windows); starts the server if needed.")
    sp.set_defaults(func=cmd_menubar)

    sp = sub.add_parser("autostart", help="Windows: start Retrace at sign-in (install | remove | status).")
    sp.add_argument("action", nargs="?", choices=["install", "remove", "status"], default="status")
    sp.set_defaults(func=cmd_autostart)

    return p


def _utf8_output() -> None:
    """Write UTF-8 when output is piped or redirected.

    Windows otherwise encodes pipes with the ANSI code page (cp1252), which cannot
    represent the ✓/✗ marks and dashes Retrace prints, and the command crashes.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
