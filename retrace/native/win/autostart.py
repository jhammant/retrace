"""Start Retrace at Windows sign-in via a shortcut in the user's Startup folder.

The shortcut runs ``pythonw -m retrace.cli menubar``: the tray icon, which starts
the capture server in the background if it isn't already running. No admin rights
are needed, and Task Manager > Startup apps lists it so it can be switched off there.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ...platform import no_window, roaming_appdata

SHORTCUT_NAME = "Retrace.lnk"


def startup_dir() -> Path:
    return roaming_appdata() / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def windowless_python() -> str:
    """``pythonw.exe`` next to the running interpreter (no console window), if present."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def install() -> Path:
    target = windowless_python()
    link = shortcut_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    script = "; ".join([
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(str(link)) + ")",
        "$s.TargetPath = " + _ps_quote(target),
        "$s.Arguments = '-m retrace.cli menubar'",
        "$s.WorkingDirectory = " + _ps_quote(str(Path.home())),
        "$s.Description = 'Retrace: private, on-device rewind'",
        "$s.WindowStyle = 7",
        "$s.Save()",
    ])
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30, **no_window(),
    )
    if proc.returncode != 0 or not link.exists():
        raise RuntimeError(f"could not create {link}: {proc.stderr.strip() or proc.stdout.strip()}")
    return link


def remove() -> bool:
    link = shortcut_path()
    if link.exists():
        link.unlink()
        return True
    return False


def status() -> dict:
    link = shortcut_path()
    return {"installed": link.exists(), "shortcut": str(link), "runs": f"{windowless_python()} -m retrace.cli menubar"}
