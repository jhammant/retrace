"""System stats logger — samples CPU and memory each daemon tick.

Stored as ``activity_events`` rows (source='system') with the metrics in ``detail``,
so they can power a system-load view and be queried over time. On-device, no network.
"""

from __future__ import annotations

import logging
from datetime import timezone

from ...config import Settings
from ...db import session_scope
from ...models import ActivityEvent, utcnow
from ..base import RetracePlugin

log = logging.getLogger("retrace.plugins.system")

try:
    import psutil
except Exception:  # pragma: no cover - dependency missing
    psutil = None


def _local_day(dt) -> str:
    return dt.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d")


class SystemStatsPlugin(RetracePlugin):
    name = "system-stats"
    description = "Sample CPU and memory usage over time."

    def poll(self, settings: Settings) -> None:
        if psutil is None:
            return
        try:
            cpu = psutil.cpu_percent(interval=None)  # since last call -> daemon-interval avg
            vm = psutil.virtual_memory()
            try:
                load1 = psutil.getloadavg()[0]
            except (OSError, AttributeError):
                load1 = None
        except Exception:
            log.debug("psutil sample failed", exc_info=True)
            return

        now = utcnow()
        detail = {
            "cpu_percent": round(float(cpu), 1),
            "mem_percent": round(float(vm.percent), 1),
            "mem_used_gb": round(vm.used / 1e9, 2),
            "mem_total_gb": round(vm.total / 1e9, 2),
        }
        if load1 is not None:
            detail["load_1m"] = round(float(load1), 2)

        with session_scope(settings) as s:
            s.add(ActivityEvent(
                source="system", app="system", url="", title=None,
                start_at=now, end_at=now, seconds=0.0,
                day=_local_day(now), detail=detail,
            ))
