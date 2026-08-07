import json
from datetime import datetime, timedelta, timezone

from .database import db_conn

ONLINE_THRESHOLD_SECONDS = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def register_heartbeat(
    worker_id: str,
    cpu_load: float,
    ram_free_gb: float,
    disk_free_gb: float,
    active_tasks: int,
    queued_tasks: int,
    capabilities: list[str],
    on_battery: bool,
    max_parallel: int,
    max_queue: int,
):
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO workers
                (worker_id, last_seen, cpu_load, ram_free_gb, disk_free_gb,
                 active_tasks, queued_tasks, capabilities, on_battery, max_parallel, max_queue)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                last_seen = excluded.last_seen,
                cpu_load = excluded.cpu_load,
                ram_free_gb = excluded.ram_free_gb,
                disk_free_gb = excluded.disk_free_gb,
                active_tasks = excluded.active_tasks,
                queued_tasks = excluded.queued_tasks,
                capabilities = excluded.capabilities,
                on_battery = excluded.on_battery,
                max_parallel = excluded.max_parallel,
                max_queue = excluded.max_queue
            """,
            (
                worker_id, _now_iso(), cpu_load, ram_free_gb, disk_free_gb,
                active_tasks, queued_tasks, json.dumps(capabilities),
                int(on_battery), max_parallel, max_queue,
            ),
        )


def get_all_workers() -> list[dict]:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=ONLINE_THRESHOLD_SECONDS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    with db_conn() as conn:
        rows = conn.execute(
            "SELECT *, (last_seen > ?) AS online FROM workers ORDER BY last_seen DESC",
            (cutoff,),
        ).fetchall()

    result = []
    for row in rows:
        w = dict(row)
        w["capabilities"] = json.loads(w["capabilities"])
        w["online"] = bool(w["online"])
        w["on_battery"] = bool(w["on_battery"])
        result.append(w)
    return result


def is_worker_available(worker_id: str) -> bool:
    """Check if a specific worker is online and has capacity."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=ONLINE_THRESHOLD_SECONDS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT active_tasks, queued_tasks, max_parallel, max_queue, last_seen
            FROM workers
            WHERE worker_id = ?
            """,
            (worker_id,),
        ).fetchone()

    if not row:
        return False
    if row["last_seen"] < cutoff:
        return False
    return (
        row["active_tasks"] < row["max_parallel"]
        and row["queued_tasks"] < row["max_queue"]
    )
