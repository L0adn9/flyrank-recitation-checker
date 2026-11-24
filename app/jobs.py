"""Postgres-backed job queue: enqueue in the same transaction as the data it refers to,
claim with SKIP LOCKED, retry with exponential backoff, and recover jobs from crashed workers."""
import json

from psycopg import Connection

CHANNEL = "jobs"
BACKOFF_SECONDS = 5          # retry n waits 5 s, 10 s, 20 s, ...
STALE_AFTER = "10 minutes"   # a running job older than this belongs to a dead worker


class PermanentError(Exception):
    """The job cannot succeed however often it is retried (e.g. the upload is not audio)."""


def enqueue(conn: Connection, kind: str, payload: dict, max_attempts: int = 3) -> int:
    """Add a job. Call inside the caller's transaction: if it rolls back, the job never existed.
    NOTIFY is delivered on commit and wakes an idle worker immediately."""
    job_id = conn.execute(
        "INSERT INTO jobs (kind, payload, max_attempts) VALUES (%s, %s::jsonb, %s) RETURNING id",
        (kind, json.dumps(payload), max_attempts),
    ).fetchone()["id"]
    conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, str(job_id)))
    return job_id


def claim(conn: Connection, worker: str) -> dict | None:
    job = conn.execute(
        """UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_at = now(), locked_by = %s
           WHERE id = (SELECT id FROM jobs WHERE status = 'queued' AND run_at <= now()
                       ORDER BY run_at, id FOR UPDATE SKIP LOCKED LIMIT 1)
           RETURNING *""",
        (worker,),
    ).fetchone()
    conn.commit()
    return job


def complete(conn: Connection, job_id: int) -> None:
    conn.execute("UPDATE jobs SET status = 'done', finished_at = now(), last_error = NULL WHERE id = %s", (job_id,))
    conn.commit()


def fail(conn: Connection, job: dict, error: str, permanent: bool) -> bool:
    """Record a failure. Returns True if the job is now dead (no retries left)."""
    dead = permanent or job["attempts"] >= job["max_attempts"]
    if dead:
        conn.execute("UPDATE jobs SET status = 'failed', last_error = %s, finished_at = now() WHERE id = %s",
                     (error, job["id"]))
    else:
        delay = BACKOFF_SECONDS * 2 ** (job["attempts"] - 1)
        conn.execute(
            "UPDATE jobs SET status = 'queued', last_error = %s, locked_at = NULL, locked_by = NULL,"
            " run_at = now() + make_interval(secs => %s) WHERE id = %s",
            (error, delay, job["id"]),
        )
    conn.commit()
    return dead


def requeue_stale(conn: Connection) -> int:
    n = conn.execute(
        f"UPDATE jobs SET status = 'queued', locked_at = NULL, locked_by = NULL"
        f" WHERE status = 'running' AND locked_at < now() - interval '{STALE_AFTER}'"
    ).rowcount
    conn.commit()
    return n
