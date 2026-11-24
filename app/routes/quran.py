"""The Quran text is public and never changes, so these routes need no login and are cacheable.
Text is served in Uthmani script, the script of the printed mushaf."""
from fastapi import APIRouter, HTTPException, Path, Response

from app.db import connection
from app.quran import split_basmala

router = APIRouter(tags=["quran"])
CACHE = "public, max-age=86400"


@router.get("/surahs")
def list_surahs(response: Response):
    response.headers["Cache-Control"] = CACHE
    with connection() as conn:
        return conn.execute("SELECT * FROM surahs ORDER BY number").fetchall()


@router.get("/surahs/{number}")
def get_surah(response: Response, number: int = Path(ge=1, le=114)):
    with connection() as conn:
        surah = conn.execute("SELECT * FROM surahs WHERE number = %s", (number,)).fetchone()
        rows = conn.execute("SELECT ayah, coalesce(text_uthmani, text) AS text FROM ayahs WHERE surah = %s ORDER BY ayah",
                            (number,)).fetchall()
    if surah is None:
        raise HTTPException(404, f"Surah {number} not found")
    basmala, ayahs = None, []
    for r in rows:
        b, body = split_basmala(number, r["ayah"], r["text"])
        basmala = basmala or b
        ayahs.append({"ayah": r["ayah"], "text": body})
    response.headers["Cache-Control"] = CACHE
    return {**surah, "basmala": basmala, "ayahs": ayahs}
