-- Accounts. Every attempt now belongs to a user.
CREATE TABLE users (
    id             BIGSERIAL PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE CHECK (email = lower(email)),
    name           TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Attempts made before accounts existed have no owner; drop them.
DELETE FROM attempts;
ALTER TABLE attempts ADD COLUMN user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX attempts_user_created ON attempts (user_id, created_at DESC);
