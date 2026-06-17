"""The ``retrace`` command-line entry point.

Subcommands: ``init``, ``serve``, ``mcp``, ``tick``, ``doctor``, ``start``,
``stop``, ``status``, ``scan``, ``purge``, ``version``.

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
    """Show the macOS menu bar item. Starts the server if it isn't already running."""
    import subprocess
    import sys
    import time
    import urllib.request

    from .config import get_settings
    from .native.helpers import get_helper

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
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(30):
            if server_up():
                break
            time.sleep(0.5)
        if not server_up():
            print("Server did not come up in time; the menu bar will show 'offline'.")

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


def cmd_purge(args: argparse.Namespace) -> int:
    from .capture.retention import purge_older_than
    from .config import get_settings
    from .db import init_db

    init_db()
    days = args.days if args.days is not None else get_settings().retention_days
    result = purge_older_than(days)
    _print_json(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="retrace", description="Private, on-device macOS rewind.")
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

    sp = sub.add_parser("scan", help="Ingest activity (knowledgeC / Safari / Chrome).")
    sp.add_argument("--full", action="store_true", help="Full rescan instead of incremental.")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("purge", help="Delete captures + thumbnails older than N days.")
    sp.add_argument("--days", type=int, default=None)
    sp.set_defaults(func=cmd_purge)

    sp = sub.add_parser("collect", help="Run app plugin collectors (e.g. Claude Code history).")
    sp.set_defaults(func=cmd_collect)

    sp = sub.add_parser("plugins", help="List installed app plugins.")
    sp.set_defaults(func=cmd_plugins)

    sp = sub.add_parser("menubar", help="Show the macOS menu bar item (starts the server if needed).")
    sp.set_defaults(func=cmd_menubar)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
