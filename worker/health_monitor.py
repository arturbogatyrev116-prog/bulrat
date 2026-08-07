"""Local resource checks used to advertise and gate worker capacity."""

from __future__ import annotations

from typing import Any

import psutil


CPU_CLAIM_MAX = 80.0
RAM_CLAIM_MIN_GB = 2.0


def ram_free_gb() -> float:
    return psutil.virtual_memory().available / (1024 ** 3)


def is_on_battery() -> bool:
    battery = getattr(psutil, "sensors_battery", lambda: None)()
    return bool(battery and not battery.power_plugged)


def get_health(active_tasks: int = 0, queued_tasks: int = 0) -> dict[str, Any]:
    disk = psutil.disk_usage("/")
    return {
        "cpu_load": float(psutil.cpu_percent(interval=None)),
        "ram_free_gb": round(ram_free_gb(), 2),
        "disk_free_gb": round(disk.free / (1024 ** 3), 2),
        "active_tasks": active_tasks,
        "queued_tasks": queued_tasks,
        "on_battery": is_on_battery(),
    }


def can_claim_task() -> bool:
    """Return whether processing another task is safe for this machine."""
    return psutil.cpu_percent(interval=None) <= CPU_CLAIM_MAX and ram_free_gb() >= RAM_CLAIM_MIN_GB
