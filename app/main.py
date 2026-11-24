"""Recite API (walking skeleton): upload a recitation, get the mistakes back."""
import hashlib
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from app import quran
from app.align import align
from app.audio import to_wav16k
from app.config import settings
from app.db import close_pool, connection, migrate
from app.seed_quran import load_quran
from app.transcriber import get_transcriber


@asynccontextmanager
async def lifespan(_: FastAPI):
    migrate()
    load_quran()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    yield
    close_pool()


app = FastAPI(title="Recite", lifespan=lifespan)


@app.get("/health")
def health():
    with connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/surahs")
def surahs():
    with connection() as conn:
        return conn.execute("SELECT * FROM surahs ORDER BY number").fetchall()


@app.post("/attempts", status_code=201)
def create_attempt(surah: int = Form(), ayah_from: int = Form(), ayah_to: int = Form(),
                   audio: UploadFile = File()):
    data = audio.file.read()
    digest = hashlib.sha256(data).hexdigest()
    path = settings.uploads_dir / digest
    path.write_bytes(data)
    with connection() as conn:
        rows = conn.execute(
            "SELECT ayah, text FROM ayahs WHERE surah = %s AND ayah BETWEEN %s AND %s ORDER BY ayah",
            (surah, ayah_from, ayah_to),
        ).fetchall()
    if not rows:
        raise HTTPException(400, "No such ayahs")
    expected = quran.expected_words(surah, [(r["ayah"], r["text"]) for r in rows])
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "in.wav"
        to_wav16k(path, wav)
        transcript = get_transcriber().transcribe(wav)
    heard = quran.clean_heard(quran.norm_words(transcript.text), expected)
    result = align(expected, heard)
    with connection() as conn:
        row = conn.execute(
            "INSERT INTO attempts (surah, ayah_from, ayah_to, status, audio_path, transcript, result,"
            " accuracy, word_count, mistake_count, finished_at)"
            " VALUES (%s, %s, %s, 'done', %s, %s, %s::jsonb, %s, %s, %s, now()) RETURNING id",
            (surah, ayah_from, ayah_to, str(path), transcript.text, json.dumps(result, ensure_ascii=False),
             result["accuracy"], result["total"], result["total"] - result["correct"]),
        ).fetchone()
        conn.commit()
    return {"id": row["id"], "transcript": transcript.text, **result}
