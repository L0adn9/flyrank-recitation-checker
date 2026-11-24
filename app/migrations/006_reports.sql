-- "My progress" PDF reports, built by the worker.
CREATE TABLE reports (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    days         INTEGER NOT NULL CHECK (days BETWEEN 7 AND 365),
    status       TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'processing', 'done', 'failed')),
    file_path    TEXT,
    pages        INTEGER,
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);
CREATE INDEX reports_user_created ON reports (user_id, created_at DESC);
