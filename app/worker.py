"""The worker process: `python -m app.worker`. Runs queued jobs off the request path.

It sleeps on Postgres LISTEN, so a new upload starts processing within milliseconds,
and polls every few seconds anyway so delayed retries are picked up."""
import logging
import os
import signal
import socket
import time

import psycopg

from app import jobs
from app.audio import AudioError
from app.checker import check_attempt, fail_attempt
from app.config import settings
from app.db import close_pool, connection, migrate

log = logging.getLogger("worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def _check_attempt(payload: dict) -> None:
    try:
        check_attempt(payload["attempt_id"])
    except AudioError as e:
        raise jobs.PermanentError(str(e)) from e


def _check_attempt_failed(payload: dict, error: str) -> None:
    fail_attempt(payload["attempt_id"], error if "recording" in error.lower()
                 else "Speech recognition failed. Please try again later.")


HANDLERS = {"check_attempt": _check_attempt}
ON_FAILURE = {"check_attempt": _check_attempt_failed}


def run_one() -> bool:
    """Claim and run one ready job. Returns False when the queue has nothing ready."""
    with connection() as conn:
        job = jobs.claim(conn, WORKER_ID)
    if job is None:
        return False
    started = time.monotonic()
    try:
        HANDLERS[job["kind"]](job["payload"])
    except Exception as e:  # noqa: BLE001 - every failure is recorded on the job
        permanent = isinstance(e, (jobs.PermanentError, KeyError))
        with connection() as conn:
            dead = jobs.fail(conn, job, f"{type(e).__name__}: {e}", permanent)
        log.warning("job %s %s attempt %s failed%s: %s", job["id"], job["kind"], job["attempts"],
                    " (giving up)" if dead else ", will retry", e)
        if dead and job["kind"] in ON_FAILURE:
            ON_FAILURE[job["kind"]](job["payload"], str(e))
        return True
    with connection() as conn:
        jobs.complete(conn, job["id"])
    log.info("job %s %s done in %.1fs", job["id"], job["kind"], time.monotonic() - started)
    return True


def drain() -> int:
    """Run jobs until none are ready (used by tests and the seed script)."""
    n = 0
    while run_one():
        n += 1
    return n


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    migrate()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
        log.info("stopping after the current job")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("worker %s ready", WORKER_ID)
    with psycopg.connect(settings.database_url, autocommit=True) as listener:
        listener.execute(f"LISTEN {jobs.CHANNEL}")
        last_housekeeping = 0.0
        while not stopping:
            if time.monotonic() - last_housekeeping > 30:
                with connection() as conn:
                    if n := jobs.requeue_stale(conn):
                        log.warning("requeued %s stale job(s)", n)
                last_housekeeping = time.monotonic()
            while not stopping and run_one():
                pass
            for _ in listener.notifies(timeout=5, stop_after=1):
                pass
    close_pool()


if __name__ == "__main__":
    main()
