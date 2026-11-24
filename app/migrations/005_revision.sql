-- Spaced revision: one row per (user, ayah) that has ever been checked.
CREATE TABLE ayah_progress (
    user_id          BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    surah            SMALLINT NOT NULL,
    ayah             SMALLINT NOT NULL,
    checks           INTEGER NOT NULL DEFAULT 0,
    streak           INTEGER NOT NULL DEFAULT 0,   -- solid checks in a row
    interval_days    INTEGER NOT NULL DEFAULT 0,
    last_checked_at  TIMESTAMPTZ NOT NULL,
    last_solid       BOOLEAN NOT NULL,
    last_mistakes    INTEGER NOT NULL DEFAULT 0,
    due_on           DATE NOT NULL,
    PRIMARY KEY (user_id, surah, ayah),
    FOREIGN KEY (surah, ayah) REFERENCES ayahs (surah, ayah)
);
CREATE INDEX ayah_progress_due ON ayah_progress (user_id, due_on);

-- What the daily cron decided each user should revise that day.
CREATE TABLE revision_plans (
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_date   DATE NOT NULL,
    items       JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, plan_date)
);

-- One row per scheduled cron slot. The primary key makes each slot run once,
-- even with several workers or after a restart.
CREATE TABLE cron_runs (
    name           TEXT NOT NULL,
    scheduled_for  TIMESTAMPTZ NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    status         TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed')),
    detail         TEXT,
    PRIMARY KEY (name, scheduled_for)
);
