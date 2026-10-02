"""Leak-proof temporary copies of locked SQLite files.

Chrome holds an exclusive lock on its History DB, so readers must copy it before
opening. The naive form of that copy leaks an OS-level file descriptor::

    fd, path = tempfile.mkstemp(...)      # fd is never closed
    Path(path).write_bytes(src.read_bytes())

``mkstemp`` returns a raw descriptor that the caller owns. Writing through the
*path* opens a second descriptor and closes that one, leaving the first open for
the lifetime of the process. Unlinking the path does not release it — the
descriptor keeps the inode alive, invisible to ``ls`` but plainly there in
``lsof``. At one leak per scan the daemon reaches RLIMIT_NOFILE (256 on
launchd-spawned processes) in about a day and a half, after which every
subsequent open fails while the process stays up: a silent outage.

``temp_copy`` closes the descriptor it owns on every path and removes the file
on the way out.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def temp_copy(src: Path, suffix: str = ".db") -> Iterator[Path]:
    """Yield a path to a throwaway copy of ``src``; always closed and unlinked.

    Raises ``OSError`` if the copy cannot be made (caller decides how to fail
    soft). The descriptor is closed and the file removed even on that path.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    tmp = Path(tmp_path)
    try:
        # Own the descriptor explicitly: write through it, never through the
        # path, so there is exactly one descriptor and `with` guarantees close.
        with os.fdopen(fd, "wb") as dst, open(src, "rb") as fh:
            shutil.copyfileobj(fh, dst)
    except BaseException:
        # fdopen failed before taking ownership, or the copy blew up mid-way.
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    try:
        yield tmp
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
