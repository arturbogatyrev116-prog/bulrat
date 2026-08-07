import json
import logging
from datetime import datetime, timedelta, timezone

from .database import db_conn

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lease_until(minutes: int) -> str:
    t = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def create_task(
    task_id: str,
    type_: str,
    payload: dict,
    priority: int = 50,
    source: str = "api",
) -> dict:
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO tasks
                (task_id, type, status, payload, priority, source, created_at)
            VALUES (?, ?, 'new', ?, ?, ?, ?)
            """,
            (task_id, type_, json.dumps(payload), priority, source, _now_iso()),
        )
    return {"task_id": task_id, "status": "new"}


def get_task(task_id: str) -> dict | None:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
    return dict(row) if row else None


def list_tasks(status: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list, int]:
    with db_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC, created_at ASC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = ?", (status,)
            ).fetchone()[0]
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY priority DESC, created_at ASC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    return [dict(r) for r in rows], total


def claim_task(worker_id: str, capabilities: list[str], lease_minutes: int = 10) -> dict | None:
    """
    Atomically assign the highest-priority eligible task to this worker.
    Returns task dict or None.
    """
    caps_set = set(capabilities)

    with db_conn() as conn:
        candidates = conn.execute(
            """
            SELECT * FROM tasks
            WHERE status IN ('new', 'triage')
            ORDER BY priority DESC, created_at ASC
            LIMIT 100
            """
        ).fetchall()

        for row in candidates:
            task = dict(row)
            required = set(json.loads(task.get("required_capabilities") or "[]"))
            if required and not required.issubset(caps_set):
                continue

            now = _now_iso()
            conn.execute(
                """
                UPDATE tasks
                SET status = 'assigned',
                    assigned_to = ?,
                    assigned_at = ?,
                    lease_until = ?
                WHERE task_id = ? AND status IN ('new', 'triage')
                """,
                (worker_id, now, _lease_until(lease_minutes), task["task_id"]),
            )
            if conn.execute(
                "SELECT changes()"
            ).fetchone()[0] > 0:
                task["status"] = "assigned"
                task["assigned_to"] = worker_id
                task["lease_until"] = _lease_until(lease_minutes)
                return task

    return None


def extend_lease(task_id: str, worker_id: str, lease_minutes: int = 10) -> str | None:
    new_lease = _lease_until(lease_minutes)
    with db_conn() as conn:
        n = conn.execute(
            """
            UPDATE tasks SET lease_until = ?
            WHERE task_id = ? AND assigned_to = ? AND status IN ('assigned', 'processing')
            """,
            (new_lease, task_id, worker_id),
        ).rowcount
    return new_lease if n > 0 else None


def mark_processing(task_id: str, worker_id: str):
    with db_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'processing' WHERE task_id = ? AND assigned_to = ?",
            (task_id, worker_id),
        )


def mark_done(task_id: str, worker_id: str, result: dict, note_path: str):
    result["note_path"] = note_path
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET status = 'done',
                completed_at = ?,
                result = ?,
                last_error = NULL
            WHERE task_id = ? AND assigned_to = ?
            """,
            (_now_iso(), json.dumps(result), task_id, worker_id),
        )


def mark_failed(task_id: str, worker_id: str, error: str, max_attempts: int = 3) -> dict:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT attempts FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return {"status": "not_found"}

        new_attempts = row["attempts"] + 1
        if new_attempts >= max_attempts:
            new_status = "failed"
        else:
            new_status = "new"

        conn.execute(
            """
            UPDATE tasks
            SET status = ?,
                attempts = ?,
                last_error = ?,
                assigned_to = NULL,
                lease_until = NULL
            WHERE task_id = ?
            """,
            (new_status, new_attempts, error, task_id),
        )

    return {
        "status": new_status,
        "attempts": new_attempts,
        "will_retry": new_status == "new",
    }


def retry_task(task_id: str) -> dict:
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET status = 'new', attempts = 0, last_error = NULL,
                assigned_to = NULL, lease_until = NULL
            WHERE task_id = ?
            """,
            (task_id,),
        )
    return {"status": "new", "attempts": 0}


def set_triage_data(task_id: str, triage_data: dict, required_capabilities: list[str]):
    with db_conn() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET triage_data = ?,
                required_capabilities = ?,
                status = 'triage',
                triaged_at = ?
            WHERE task_id = ?
            """,
            (
                json.dumps(triage_data),
                json.dumps(required_capabilities),
                _now_iso(),
                task_id,
            ),
        )


def requeue_expired(max_attempts: int = 3):
    """Background task: reclaim tasks with expired leases."""
    with db_conn() as conn:
        expired = conn.execute(
            """
            SELECT task_id, attempts FROM tasks
            WHERE status IN ('assigned', 'processing')
              AND lease_until < datetime('now')
            """
        ).fetchall()

        for row in expired:
            task_id = row["task_id"]
            new_attempts = row["attempts"] + 1
            new_status = "failed" if new_attempts >= max_attempts else "new"
            conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    attempts = ?,
                    assigned_to = NULL,
                    lease_until = NULL,
                    last_error = COALESCE(last_error, 'lease_expired')
                WHERE task_id = ?
                """,
                (new_status, new_attempts, task_id),
            )
            log.warning("Lease expired for %s → %s (attempt %d)", task_id, new_status, new_attempts)
