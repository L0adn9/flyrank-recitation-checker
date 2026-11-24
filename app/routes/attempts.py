import hashlib

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from app import jobs
from app.auth import current_user
from app.checker import score_from_cache
from app.config import settings
from app.db import connection

router = APIRouter(prefix="/attempts", tags=["attempts"])

AUDIO_TYPES = {"video/webm", "video/ogg", "video/mp4", "application/octet-stream", ""}
COLUMNS = ("a.id, a.surah, s.name_en AS surah_name, s.name_ar AS surah_name_ar, a.ayah_from, a.ayah_to, a.status,"
           " a.accuracy, a.word_count, a.mistake_count, a.cache_hit, a.recognition_seconds, a.error,"
           " a.created_at, a.finished_at")


def _read_upload(audio: UploadFile) -> bytes:
    ctype = (audio.content_type or "").split(";")[0].strip().lower()
    if not (ctype.startswith("audio/") or ctype in AUDIO_TYPES):
        raise HTTPException(415, f"Expected an audio file, got '{ctype}'")
    data = audio.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"Recording is larger than {settings.max_upload_bytes // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(400, "The recording is empty")
    return data


def _validate_range(conn, surah: int, ayah_from: int, ayah_to: int) -> None:
    row = conn.execute("SELECT ayah_count, name_en FROM surahs WHERE number = %s", (surah,)).fetchone()
    if row is None:
        raise HTTPException(400, "surah must be between 1 and 114")
    if not 1 <= ayah_from <= ayah_to <= row["ayah_count"]:
        raise HTTPException(400, f"{row['name_en']} has ayahs 1 to {row['ayah_count']}; "
                                 f"got {ayah_from} to {ayah_to}")
    if ayah_to - ayah_from + 1 > settings.max_ayahs_per_attempt:
        raise HTTPException(400, f"Check at most {settings.max_ayahs_per_attempt} ayahs at a time")


def get_owned(conn, attempt_id: int, user_id: int, full: bool = True) -> dict:
    extra = ", a.transcript, a.result" if full else ""
    row = conn.execute(
        f"SELECT {COLUMNS}{extra} FROM attempts a JOIN surahs s ON s.number = a.surah"
        " WHERE a.id = %s AND a.user_id = %s", (attempt_id, user_id),
    ).fetchone()
    if row is None:  # also for other users' attempts: don't reveal that they exist
        raise HTTPException(404, f"Attempt {attempt_id} not found")
    return row


@router.post("", status_code=202)
def create_attempt(
    response: Response,
    surah: int = Form(), ayah_from: int = Form(), ayah_to: int = Form(),
    audio: UploadFile = File(), user: dict = Depends(current_user),
):
    """Accept a recording. Speech recognition takes seconds, so normally the attempt is queued for the
    worker (202; poll GET /attempts/{id} until status is 'done' or 'failed'). If this exact recording
    was transcribed before, the cached transcript is scored right away (201, status 'done')."""
    data = _read_upload(audio)
    digest = hashlib.sha256(data).hexdigest()
    with connection() as conn:
        _validate_range(conn, surah, ayah_from, ayah_to)
        path = settings.uploads_dir / digest
        if not path.exists():
            path.write_bytes(data)
        attempt = conn.execute(
            "INSERT INTO attempts (user_id, surah, ayah_from, ayah_to, audio_path, audio_sha256)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
            (user["id"], surah, ayah_from, ayah_to, str(path), digest),
        ).fetchone()
        attempt_id = attempt["id"]
        if score_from_cache(conn, attempt):
            response.status_code = 201
        else:
            jobs.enqueue(conn, "check_attempt", {"attempt_id": attempt_id})
        conn.commit()
        response.headers["Location"] = f"/attempts/{attempt_id}"
        return get_owned(conn, attempt_id, user["id"])


@router.get("")
def list_attempts(user: dict = Depends(current_user), limit: int = Query(20, ge=1, le=100),
                  before: int | None = Query(None, description="id to page from")):
    with connection() as conn:
        return conn.execute(
            f"SELECT {COLUMNS} FROM attempts a JOIN surahs s ON s.number = a.surah"
            " WHERE a.user_id = %s AND (%s::bigint IS NULL OR a.id < %s) ORDER BY a.id DESC LIMIT %s",
            (user["id"], before, before, limit),
        ).fetchall()


@router.get("/{attempt_id}")
def get_attempt(attempt_id: int, user: dict = Depends(current_user)):
    with connection() as conn:
        return get_owned(conn, attempt_id, user["id"])


@router.delete("/{attempt_id}", status_code=204)
def delete_attempt(attempt_id: int, user: dict = Depends(current_user)):
    with connection() as conn:
        get_owned(conn, attempt_id, user["id"], full=False)
        conn.execute("DELETE FROM attempts WHERE id = %s", (attempt_id,))
        conn.commit()
