import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "coordinator.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def db_conn():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id             TEXT PRIMARY KEY,
            type                TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'new',
            payload             TEXT NOT NULL,
            priority            INTEGER DEFAULT 50,
            source              TEXT DEFAULT 'api',
            required_capabilities TEXT DEFAULT '[]',
            triage_data         TEXT DEFAULT '{}',
            created_at          TEXT NOT NULL,
            triaged_at          TEXT,
            assigned_to         TEXT,
            assigned_at         TEXT,
            lease_until         TEXT,
            completed_at        TEXT,
            attempts            INTEGER DEFAULT 0,
            last_error          TEXT,
            result              TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_queue
            ON tasks(status, priority DESC, created_at ASC);

        CREATE TABLE IF NOT EXISTS workers (
            worker_id       TEXT PRIMARY KEY,
            last_seen       TEXT NOT NULL,
            cpu_load        REAL DEFAULT 0,
            ram_free_gb     REAL DEFAULT 0,
            disk_free_gb    REAL DEFAULT 0,
            active_tasks    INTEGER DEFAULT 0,
            queued_tasks    INTEGER DEFAULT 0,
            capabilities    TEXT DEFAULT '[]',
            on_battery      INTEGER DEFAULT 0,
            max_parallel    INTEGER DEFAULT 1,
            max_queue       INTEGER DEFAULT 5
        );
        """)
