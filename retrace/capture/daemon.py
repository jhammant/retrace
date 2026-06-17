"""The capture daemon: event-driven captures + periodic fallback + gating.

- A native ``retrace-watch`` helper streams frontmost-app change events; the
  dispatcher debounces them and triggers a capture.
- A fallback thread captures on a periodic tick (throttled on battery), records
  a cheap idle-aware "active" sample, and runs daily retention.
- Every capture funnels through :func:`capture_once`, which independently gates
  on enabled/presence/dedup/privacy — so the daemon can run continuously and the
  pipeline decides whether a frame is actually taken.

One failed cycle never kills a loop (broad try/except per tick).
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from datetime import datetime

from ..config import Settings, get_settings
from ..native.helpers import get_helper
from .pipeline import capture_once

log = logging.getLogger("retrace.daemon")


def power_state() -> dict:
    """Best-effort AC/battery + low-power-mode detection via ``pmset``."""
    on_battery = False
    low_power = False
    try:
        out = subprocess.run(
            ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3
        ).stdout
        if "Battery Power" in out:
            on_battery = True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.run(["pmset", "-g"], capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            if "lowpowermode" in line:
                low_power = line.strip().split()[-1] == "1"
    except (OSError, subprocess.SubprocessError):
        pass
    return {"on_battery": on_battery, "low_power": low_power}


class CaptureDaemon:
    """Background capture orchestrator. Safe to start/stop repeatedly."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        enable_watcher: bool = True,
        enable_fallback: bool = True,
    ) -> None:
        self._s = settings or get_settings()
        self._enable_watcher = enable_watcher
        self._enable_fallback = enable_fallback

        self._stop = threading.Event()
        self._pending = threading.Event()  # set on each watch event
        self._busy = threading.Lock()       # non-blocking guard against pile-ups
        self._started = False
        self._watch_proc: subprocess.Popen | None = None
        self._threads: list[threading.Thread] = []
        self._last_purge_day: str | None = None
        self._last_app: str | None = None

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        self._threads = []
        if self._enable_watcher:
            self._threads.append(threading.Thread(target=self._watch_loop, name="retrace-watch", daemon=True))
            self._threads.append(threading.Thread(target=self._dispatch_loop, name="retrace-dispatch", daemon=True))
        if self._enable_fallback:
            self._threads.append(threading.Thread(target=self._fallback_loop, name="retrace-fallback", daemon=True))
        for t in self._threads:
            t.start()
        log.info("daemon started (watcher=%s, fallback=%s)", self._enable_watcher, self._enable_fallback)

    def stop(self) -> None:
        if not self._started:
            return
        self._stop.set()
        self._pending.set()  # wake the dispatcher
        self._kill_watch()
        for t in self._threads:
            t.join(timeout=3)
        self._threads = []
        self._started = False
        log.info("daemon stopped")

    def _kill_watch(self) -> None:
        proc = self._watch_proc
        self._watch_proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        except (OSError, ValueError):
            pass

    # --- capture trigger ---------------------------------------------------
    def _trigger(self, reason: str):
        if self._stop.is_set():
            return None
        if not self._busy.acquire(blocking=False):
            return None  # a capture is already in flight
        try:
            res = capture_once(reason=reason, settings=self._s)
            if res.status == "stored":
                log.info("captured [%s] %s", reason, res.app)
            return res
        except Exception:  # never let a cycle kill the loop
            log.exception("daemon capture failed")
            return None
        finally:
            self._busy.release()

    # --- watcher -----------------------------------------------------------
    def _watch_loop(self) -> None:
        try:
            binary = get_helper("retrace-watch", self._s).ensure_built()
        except Exception as exc:
            log.warning("event watcher unavailable (%s); relying on fallback tick", exc)
            return
        while not self._stop.is_set():
            try:
                self._watch_proc = subprocess.Popen(
                    [str(binary)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1,
                )
            except OSError as exc:
                log.warning("could not start watcher: %s", exc)
                return
            try:
                assert self._watch_proc.stdout is not None
                for line in self._watch_proc.stdout:
                    if self._stop.is_set():
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("event") in ("app", "wake"):
                        self._last_app = evt.get("app_name") or self._last_app
                        self._pending.set()
            finally:
                self._kill_watch()
            if self._stop.is_set():
                break
            self._stop.wait(2.0)  # watcher died; brief backoff then restart

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            if not self._pending.wait(timeout=1.0):
                continue
            self._pending.clear()
            # debounce: collapse a burst of rapid app switches into one capture
            if self._stop.wait(self._s.event_debounce_s):
                break
            self._trigger("event")

    # --- fallback tick -----------------------------------------------------
    def _fallback_loop(self) -> None:
        while not self._stop.is_set():
            interval = self._effective_interval()
            if self._stop.wait(interval):
                break
            try:
                self._maybe_daily_jobs()
            except Exception:
                log.exception("daily jobs failed")
            self._trigger("tick")
            self._record_active(interval)

    def _effective_interval(self) -> float:
        base = self._s.capture_interval_s
        try:
            ps = power_state()
        except Exception:
            return base
        if ps["on_battery"] and ps["low_power"]:
            return base * 2.0
        if ps["on_battery"]:
            return base * 1.5
        return base

    def _record_active(self, interval: float) -> None:
        """Record an idle-aware active sample for time analytics (M6)."""
        try:
            from ..activity.service import record_active_sample
        except ModuleNotFoundError:
            return
        try:
            record_active_sample(interval, settings=self._s)
        except Exception:
            log.debug("active sample failed", exc_info=True)

    def _maybe_daily_jobs(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_purge_day == today:
            return
        self._last_purge_day = today
        from .retention import purge_older_than

        purge_older_than(self._s.retention_days, self._s)

        # Ingest app-plugin data (e.g. Claude Code history) once per day.
        try:
            from ..plugins.registry import run_collectors

            run_collectors(self._s)
        except Exception:
            log.debug("plugin collectors failed", exc_info=True)


def _main() -> int:  # pragma: no cover - manual run helper
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    from ..db import init_db

    init_db()
    daemon = CaptureDaemon()
    daemon.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        daemon.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
