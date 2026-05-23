import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any


class JobRegistry:
    """Luu trang thai job ben vung bang SQLite de VE3 va server khong mat lien ket."""

    ACTIVE_STATES = ("queued", "processing", "recovering")
    TERMINAL_STATES = ("completed", "failed", "lost")

    def __init__(self, db_path: Path, log_fn=None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = log_fn or (lambda msg, level="INFO": None)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self):
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    task_id TEXT UNIQUE,
                    job_type TEXT,
                    vm_id TEXT,
                    prompt_preview TEXT,
                    payload_hash TEXT,
                    state TEXT NOT NULL,
                    worker_id TEXT,
                    queue_position INTEGER,
                    error TEXT,
                    result_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    completed_at REAL,
                    last_heartbeat REAL,
                    server_instance TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_task_id ON jobs(task_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state)"
            )

    def upsert_submitted_job(
        self,
        *,
        job_id: str,
        task_id: str,
        job_type: str,
        vm_id: str,
        prompt_preview: str,
        payload_hash: str = "",
        queue_position: Optional[int] = None,
        server_instance: str = "",
    ):
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO jobs (
                    job_id, task_id, job_type, vm_id, prompt_preview, payload_hash,
                    state, worker_id, queue_position, error, result_json,
                    created_at, updated_at, started_at, completed_at, last_heartbeat,
                    server_instance, attempt
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', NULL, ?, NULL, NULL, ?, ?, NULL, NULL, ?, ?, 0)
                ON CONFLICT(job_id) DO UPDATE SET
                    task_id=excluded.task_id,
                    job_type=excluded.job_type,
                    vm_id=excluded.vm_id,
                    prompt_preview=excluded.prompt_preview,
                    payload_hash=excluded.payload_hash,
                    state='queued',
                    worker_id=NULL,
                    queue_position=excluded.queue_position,
                    error=NULL,
                    result_json=NULL,
                    updated_at=excluded.updated_at,
                    started_at=NULL,
                    completed_at=NULL,
                    last_heartbeat=excluded.last_heartbeat,
                    server_instance=excluded.server_instance
                """,
                (
                    job_id, task_id, job_type, vm_id, prompt_preview[:200], payload_hash[:120],
                    queue_position, now, now, now, server_instance,
                ),
            )

    def touch(
        self,
        *,
        job_id: Optional[str] = None,
        task_id: Optional[str] = None,
        state: Optional[str] = None,
        worker_id: Optional[str] = None,
        queue_position: Optional[int] = None,
        error: Optional[str] = None,
        result: Any = None,
        heartbeat: bool = True,
    ):
        now = time.time()
        clauses = ["updated_at = ?"]
        params = [now]
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        if worker_id is not None:
            clauses.append("worker_id = ?")
            params.append(worker_id)
        if queue_position is not None:
            clauses.append("queue_position = ?")
            params.append(queue_position)
        if error is not None:
            clauses.append("error = ?")
            params.append(error[:500])
        if result is not None:
            clauses.append("result_json = ?")
            params.append(json.dumps(result, ensure_ascii=False))
        if heartbeat:
            clauses.append("last_heartbeat = ?")
            params.append(now)
        if state == "processing":
            clauses.append("started_at = COALESCE(started_at, ?)")
            params.append(now)
            clauses.append("attempt = attempt + 1")
        if state in self.TERMINAL_STATES:
            clauses.append("completed_at = ?")
            params.append(now)
        if state in ("queued", "lost", "failed"):
            clauses.append("worker_id = NULL")

        if job_id:
            where = "job_id = ?"
            params.append(job_id)
        elif task_id:
            where = "task_id = ?"
            params.append(task_id)
        else:
            raise ValueError("job_id or task_id required")

        with self._lock, self._conn:
            self._conn.execute(f"UPDATE jobs SET {', '.join(clauses)} WHERE {where}", params)

    def lookup(self, *, job_id: Optional[str] = None, task_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not job_id and not task_id:
            raise ValueError("job_id or task_id required")
        with self._lock:
            if job_id:
                row = self._conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            else:
                row = self._conn.execute("SELECT * FROM jobs WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get("result_json"):
            try:
                result["result"] = json.loads(result["result_json"])
            except Exception:
                result["result"] = None
        else:
            result["result"] = None
        return result

    def mark_inflight_jobs_lost(self, reason: str, server_instance: str):
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"""
                UPDATE jobs
                SET state = 'lost',
                    error = ?,
                    worker_id = NULL,
                    updated_at = ?,
                    completed_at = ?,
                    last_heartbeat = ?,
                    server_instance = ?
                WHERE state IN ({','.join(['?'] * len(self.ACTIVE_STATES))})
                """,
                (reason[:500], now, now, now, server_instance, *self.ACTIVE_STATES),
            )
        return cur.rowcount

    def cleanup_old(self, cutoff_ts: float):
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"DELETE FROM jobs WHERE updated_at < ? AND state IN ({','.join(['?'] * len(self.TERMINAL_STATES))})",
                (cutoff_ts, *self.TERMINAL_STATES),
            )
        return cur.rowcount
