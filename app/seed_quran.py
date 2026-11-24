"""Load the Quran text and surah list into Postgres. Idempotent: fills only what is missing."""
import re

from app.db import connection
from app.quran import UTHMANI_FILE, load_surahs, load_text_file

_SIGNS = re.compile("[\u064b-\u0652\u0656-\u065f\u0670\u06d6-\u06ed\u0640]")


def clean_name(name_ar: str) -> str:
    """Surah names come with harakat and Uthmani small signs (قُرَيۡشٍ); titles read better plain (قريش)."""
    return _SIGNS.sub("", name_ar).replace("ٱ", "ا").strip()


def load_quran() -> int:
    loaded = 0
    with connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO surahs (number, name_ar, name_en, meaning, ayah_count) VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (number) DO UPDATE SET name_ar = EXCLUDED.name_ar",
                [(s["number"], clean_name(s["name_ar"]), s["name_en"], s["meaning"], s["ayahs"]) for s in load_surahs()],
            )
            if conn.execute("SELECT count(*) AS n FROM ayahs").fetchone()["n"] == 0:
                rows = load_text_file()
                with cur.copy("COPY ayahs (surah, ayah, text) FROM STDIN") as copy:
                    for row in rows:
                        copy.write_row(row)
                loaded = len(rows)
            if conn.execute("SELECT 1 FROM ayahs WHERE text_uthmani IS NULL LIMIT 1").fetchone():
                cur.execute("CREATE TEMP TABLE uthmani (surah SMALLINT, ayah SMALLINT, text TEXT) ON COMMIT DROP")
                with cur.copy("COPY uthmani (surah, ayah, text) FROM STDIN") as copy:
                    for row in load_text_file(UTHMANI_FILE):
                        copy.write_row(row)
                cur.execute("UPDATE ayahs a SET text_uthmani = u.text FROM uthmani u"
                            " WHERE a.surah = u.surah AND a.ayah = u.ayah")
        conn.commit()
    return loaded
