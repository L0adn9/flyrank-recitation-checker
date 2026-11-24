"""Load the Quran text and surah list into Postgres (once)."""
from app.db import connection
from app.quran import load_surahs, load_text_file


def load_quran() -> int:
    with connection() as conn:
        if conn.execute("SELECT count(*) AS n FROM ayahs").fetchone()["n"] > 0:
            return 0
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO surahs (number, name_ar, name_en, meaning, ayah_count) VALUES (%s, %s, %s, %s, %s)",
                [(s["number"], s["name_ar"], s["name_en"], s["meaning"], s["ayahs"]) for s in load_surahs()],
            )
            rows = load_text_file()
            with cur.copy("COPY ayahs (surah, ayah, text) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
        conn.commit()
        return len(rows)
