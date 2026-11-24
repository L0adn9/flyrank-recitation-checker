-- A small job queue in Postgres. Workers claim jobs with FOR UPDATE SKIP LOCKED,
-- so several workers can run side by side without taking the same job twice.
CREATE TABLE jobs (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 3,
    run_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at     TIMESTAMPTZ,
    locked_by     TEXT,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX jobs_ready ON jobs (run_at, id) WHERE status = 'queued';
