-- Speech recognition is the expensive step (seconds of CPU). Its output depends only on the audio
-- bytes and the model, so it is cached under (sha256 of the upload, model name).
CREATE TABLE transcripts (
    audio_sha256  TEXT NOT NULL,
    model         TEXT NOT NULL,
    text          TEXT NOT NULL,
    segments      JSONB NOT NULL DEFAULT '[]'::jsonb,
    seconds       REAL NOT NULL,
    hits          INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_hit_at   TIMESTAMPTZ,
    PRIMARY KEY (audio_sha256, model)
);

ALTER TABLE attempts ADD COLUMN audio_sha256 TEXT;
ALTER TABLE attempts ADD COLUMN cache_hit BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE attempts ADD COLUMN recognition_seconds REAL;
UPDATE attempts SET audio_sha256 = split_part(audio_path, '/', -1) WHERE audio_path IS NOT NULL;
