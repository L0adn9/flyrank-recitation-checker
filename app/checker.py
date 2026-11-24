"""The core job: turn one uploaded recitation into a word-by-word result."""
import hashlib
import io
import json
import tempfile
import wave
from pathlib import Path

from psycopg import Connection

from app import quran
from app.align import align
from app.audio import AudioError, to_wav16k
from app.config import settings
from app.db import connection
from app.revision import record_check
from app.transcriber import Transcript, get_transcriber


def expected_for(conn, surah: int, ayah_from: int, ayah_to: int) -> list[quran.Word]:
    rows = conn.execute(
        "SELECT ayah, text, text_uthmani FROM ayahs WHERE surah = %s AND ayah BETWEEN %s AND %s ORDER BY ayah",
        (surah, ayah_from, ayah_to),
    ).fetchall()
    return quran.expected_words(surah, [(r["ayah"], r["text"], r["text_uthmani"]) for r in rows])


def cache_model(attempt: dict) -> str:
    """The cache key's model part. The passage is in it, because the passage is the model's context:
    the same recording checked against another passage is recognised again."""
    return f"{get_transcriber().name} {attempt['surah']}:{attempt['ayah_from']}-{attempt['ayah_to']}"


def cached_transcript(conn: Connection, attempt: dict) -> Transcript | None:
    """Look up the cache and count the hit. Returns None on a miss."""
    row = conn.execute(
        "UPDATE transcripts SET hits = hits + 1, last_hit_at = now()"
        " WHERE audio_sha256 = %s AND model = %s RETURNING text, segments",
        (attempt["audio_sha256"], cache_model(attempt)),
    ).fetchone()
    return Transcript(row["text"], row["segments"], 0.0) if row else None


def store_transcript(conn: Connection, attempt: dict, t: Transcript) -> None:
    conn.execute(
        "INSERT INTO transcripts (audio_sha256, model, text, segments, seconds) VALUES (%s, %s, %s, %s::jsonb, %s)"
        " ON CONFLICT (audio_sha256, model) DO NOTHING",
        (attempt["audio_sha256"], cache_model(attempt), t.text, json.dumps(t.segments, ensure_ascii=False), t.seconds),
    )


def score(conn: Connection, attempt: dict, t: Transcript, cache_hit: bool) -> dict:
    """Align a transcript against the attempt's ayahs and store the result. Cheap (milliseconds)."""
    expected = expected_for(conn, attempt["surah"], attempt["ayah_from"], attempt["ayah_to"])
    heard = quran.clean_heard(quran.norm_words(t.text), expected)
    result = align(expected, heard)
    conn.execute(
        "UPDATE attempts SET status = 'done', transcript = %s, result = %s::jsonb, accuracy = %s, word_count = %s,"
        " mistake_count = %s, cache_hit = %s, recognition_seconds = %s, error = NULL, finished_at = now()"
        " WHERE id = %s",
        (t.text, json.dumps(result, ensure_ascii=False), result["accuracy"], result["total"],
         result["mistakes"], cache_hit, round(t.seconds, 2), attempt["id"]),
    )
    record_check(conn, attempt["user_id"], attempt["surah"], result["ayahs"])
    return result


def score_from_cache(conn: Connection, attempt: dict) -> bool:
    """Used by the API right after upload: if this exact recording was heard before, finish now."""
    t = cached_transcript(conn, attempt)
    if t is None:
        return False
    score(conn, attempt, t, cache_hit=True)
    return True


def check_attempt(attempt_id: int) -> dict:
    """Worker job: transcribe (or reuse the cache) and score one attempt."""
    with connection() as conn:
        a = conn.execute(
            "UPDATE attempts SET status = 'processing' WHERE id = %s RETURNING *", (attempt_id,)
        ).fetchone()
        if a is None:  # deleted by its owner while queued
            conn.commit()
            return {}
        t = cached_transcript(conn, a)
        expected = expected_for(conn, a["surah"], a["ayah_from"], a["ayah_to"])
        conn.commit()
    hit = t is not None
    if not hit:
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "in.wav"
            seconds = to_wav16k(Path(a["audio_path"]), wav)
            if seconds > settings.max_audio_seconds:
                raise AudioError(f"This recording is {seconds:.0f}s long; the limit is {settings.max_audio_seconds}s.")
            t = get_transcriber().transcribe(wav, expected)
    with connection() as conn:
        if not hit:
            store_transcript(conn, a, t)
        result = score(conn, a, t, cache_hit=hit)
        conn.commit()
    return result


def fail_attempt(attempt_id: int, message: str) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE attempts SET status = 'failed', error = %s, finished_at = now() WHERE id = %s",
            (message, attempt_id),
        )
        conn.commit()


def save_live_attempt(user_id: int, surah: int, ayah_from: int, ayah_to: int, pcm: bytes,
                      phrases: list[str], seconds: float) -> int:
    """A live check is already recognised when the person presses stop: store the audio (as WAV) and
    its transcript, and score it right away. The transcript also goes into the cache, so uploading the
    same recording later is instant."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(pcm)
    data = buf.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    path = settings.uploads_dir / digest
    if not path.exists():
        path.write_bytes(data)
    t = Transcript(" ".join(phrases), phrases, seconds)
    with connection() as conn:
        attempt = conn.execute(
            "INSERT INTO attempts (user_id, surah, ayah_from, ayah_to, audio_path, audio_sha256, status, live)"
            " VALUES (%s, %s, %s, %s, %s, %s, 'processing', true) RETURNING *",
            (user_id, surah, ayah_from, ayah_to, str(path), digest),
        ).fetchone()
        store_transcript(conn, attempt, t)
        score(conn, attempt, t, cache_hit=False)
        conn.commit()
    return attempt["id"]
