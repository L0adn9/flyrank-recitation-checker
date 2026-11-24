"""The core job: turn one uploaded recitation into a word-by-word result."""
import json
import tempfile
from pathlib import Path

from app import quran
from app.align import align
from app.audio import AudioError, to_wav16k
from app.config import settings
from app.db import connection
from app.transcriber import get_transcriber


def expected_for(conn, surah: int, ayah_from: int, ayah_to: int) -> list[quran.Word]:
    rows = conn.execute(
        "SELECT ayah, text FROM ayahs WHERE surah = %s AND ayah BETWEEN %s AND %s ORDER BY ayah",
        (surah, ayah_from, ayah_to),
    ).fetchall()
    return quran.expected_words(surah, [(r["ayah"], r["text"]) for r in rows])


def check_attempt(attempt_id: int) -> dict:
    """Transcribe and score one attempt, and store the result on it."""
    with connection() as conn:
        a = conn.execute(
            "UPDATE attempts SET status = 'processing' WHERE id = %s RETURNING *", (attempt_id,)
        ).fetchone()
        conn.commit()
        expected = expected_for(conn, a["surah"], a["ayah_from"], a["ayah_to"])

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "in.wav"
        seconds = to_wav16k(Path(a["audio_path"]), wav)
        if seconds > settings.max_audio_seconds:
            raise AudioError(f"Recording is {seconds:.0f}s long; the limit is {settings.max_audio_seconds}s.")
        transcript = get_transcriber().transcribe(wav)

    heard = quran.clean_heard(quran.norm_words(transcript.text), expected)
    result = align(expected, heard)
    with connection() as conn:
        conn.execute(
            "UPDATE attempts SET status = 'done', transcript = %s, result = %s::jsonb, accuracy = %s,"
            " word_count = %s, mistake_count = %s, error = NULL, finished_at = now() WHERE id = %s",
            (transcript.text, json.dumps(result, ensure_ascii=False), result["accuracy"],
             result["total"], result["mistakes"], attempt_id),
        )
        conn.commit()
    return result


def fail_attempt(attempt_id: int, message: str) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE attempts SET status = 'failed', error = %s, finished_at = now() WHERE id = %s",
            (message, attempt_id),
        )
        conn.commit()
