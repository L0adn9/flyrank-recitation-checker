-- Quran text (Tanzil, verbatim) and recitation attempts.
CREATE TABLE surahs (
    number      SMALLINT PRIMARY KEY CHECK (number BETWEEN 1 AND 114),
    name_ar     TEXT NOT NULL,
    name_en     TEXT NOT NULL,
    meaning     TEXT NOT NULL,
    ayah_count  SMALLINT NOT NULL
);

CREATE TABLE ayahs (
    surah   SMALLINT NOT NULL REFERENCES surahs(number),
    ayah    SMALLINT NOT NULL,
    text    TEXT NOT NULL,
    PRIMARY KEY (surah, ayah)
);

CREATE TABLE attempts (
    id             BIGSERIAL PRIMARY KEY,
    surah          SMALLINT NOT NULL REFERENCES surahs(number),
    ayah_from      SMALLINT NOT NULL,
    ayah_to        SMALLINT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'processing', 'done', 'failed')),
    audio_path     TEXT,
    transcript     TEXT,
    result         JSONB,
    accuracy       REAL,
    word_count     INTEGER,
    mistake_count  INTEGER,
    error          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    CHECK (ayah_from >= 1 AND ayah_to >= ayah_from)
);
