"""Background worker: claims queued tasks (Postgres SKIP LOCKED) and runs periodic jobs.

Run several worker processes for concurrency; claiming is safe across processes.
"""

import logging
import os
import signal
import socket
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text

from app.agents.runner import run_task
from app.config import get_settings
from app.core.runtime import is_paused, record_error
from app.db.models import Report, Task
from app.db.session import scoped_session
from app.orchestrator import approvals, reports, services

log = logging.getLogger("agency.worker")

CLAIM_SQL = text(
    """
    UPDATE tasks SET status = 'running', locked_by = :worker, locked_at = now(), attempts = attempts + 1,
                     updated_at = now()
    WHERE id = (
        SELECT t.id FROM tasks t
        JOIN agents a ON a.id = t.assigned_agent_id AND a.status = 'active'
        WHERE t.status = 'queued' AND t.next_run_at <= now()
          AND NOT EXISTS (
              SELECT 1 FROM task_dependencies d JOIN tasks dt ON dt.id = d.depends_on_id
              WHERE d.task_id = t.id AND dt.status <> 'completed')
        ORDER BY t.priority, t.created_at
        FOR UPDATE OF t SKIP LOCKED
        LIMIT 1)
    RETURNING id
    """
)


def claim_next(worker_id: str) -> UUID | None:
    with scoped_session(owner=True) as s:
        if is_paused(s):
            return None
        return s.execute(CLAIM_SQL, {"worker": worker_id}).scalar()


def recover_stale_locks() -> int:
    """Tasks stuck in 'running' (worker crashed) go back to the queue as a failed attempt."""
    cutoff = datetime.now(UTC) - timedelta(minutes=get_settings().task_lock_timeout_minutes)
    with scoped_session(owner=True) as s:
        stuck = s.execute(select(Task).where(Task.status == "running", Task.locked_at < cutoff)).scalars().all()
        from app.orchestrator.lifecycle import schedule_retry

        for task in stuck:
            schedule_retry(s, task, "Worker stopped responding while running this task.")
        return len(stuck)


def run_periodic() -> None:
    settings = get_settings()
    with scoped_session(owner=True) as s:
        approvals.expire_stale(s)
        services.create_checkin_if_needed(s)
        now = datetime.now(UTC)
        if now.hour >= settings.daily_report_hour_utc:
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            exists = s.execute(select(Report.id).where(Report.kind == "daily", Report.created_at >= day_start)).first()
            pending = s.execute(select(Task.id).where(
                Task.kind == "report", Task.created_at >= day_start, Task.status.in_(("queued", "running", "completed"))
            )).first()
            if not exists and not pending:
                reports.request_report(s, "daily", end=now)
    recover_stale_locks()


def run_once(worker_id: str = "inline") -> bool:
    """Claim and run a single task. Returns False when nothing was ready."""
    task_id = claim_next(worker_id)
    if task_id is None:
        return False
    run_task(task_id, worker_id)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stopping = False

    def _stop(*_):
        nonlocal stopping
        stopping = True
        log.info("stopping after the current task")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker %s started (llm_provider=%s)", worker_id, settings.llm_provider)
    last_periodic = 0.0
    while not stopping:
        try:
            if time.monotonic() - last_periodic > 30:
                run_periodic()
                last_periodic = time.monotonic()
            if not run_once(worker_id):
                time.sleep(settings.worker_poll_seconds)
        except Exception as exc:  # keep the worker alive, but never hide the error
            log.exception("worker loop error")
            try:
                with scoped_session(owner=True) as s:
                    record_error(s, "worker", f"{type(exc).__name__}: {exc}")
            except Exception:
                log.exception("could not record worker error")
            time.sleep(5)


if __name__ == "__main__":
    main()
