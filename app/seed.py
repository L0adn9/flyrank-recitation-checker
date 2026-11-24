"""Demo data: `python -m app.seed` creates the demo account with three weeks of practice history.

The history is generated, not recorded: for each practice session the expected words are taken from the
real Quran text, a realistic slip is injected (a wrong word, a skipped ayah, a restart, swapped ayahs),
and the result is scored by the same aligner the app uses. So the demo account shows what a few weeks of
use looks like: an accuracy trend, most-missed words, and a revision plan for today.

Safe to run on every start: it does nothing if the demo account already has attempts (use --reset to rebuild).
"""
import json
import random
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import quran
from app.align import align
from app.auth import hash_password
from app.checker import expected_for
from app.config import settings
from app.db import close_pool, connection, migrate
from app.revision import build_plan, record_check, today
from app.seed_quran import load_quran

# Slips people really make: a similar-sounding or similar-looking word in place of the right one.
CONFUSIONS = {
    "طيرا": "طيور", "سجيل": "سجين", "كعصف": "كعصفه", "الشتاء": "الشتا", "اطعمهم": "اطعمكم",
    "الكوثر": "الكوكب", "وانحر": "واصبر", "الابتر": "الاكبر", "خسر": "خسران", "بالصبر": "بالحق",
    "الصمد": "الواحد", "كفوا": "كفؤا", "نستعين": "نستغفر", "المغضوب": "المغصوب", "انعمت": "انعمنا",
}

# (days ago, hour, surah, ayah_from, ayah_to, slips)
SESSIONS = [
    (20, 7, 1, 1, 7, []),
    (20, 7, 112, 1, 4, ["wrong"]),
    (19, 21, 105, 1, 5, ["wrong", "skip"]),
    (18, 7, 105, 1, 5, ["wrong"]),
    (17, 8, 112, 1, 4, []),
    (16, 21, 105, 1, 5, ["restart"]),
    (15, 7, 106, 1, 4, ["wrong", "wrong"]),
    (14, 7, 106, 1, 4, ["swap"]),
    (13, 22, 105, 1, 5, []),
    (12, 7, 106, 1, 4, ["wrong"]),
    (11, 7, 1, 1, 7, ["wrong"]),
    (10, 21, 108, 1, 3, ["wrong"]),
    (9, 7, 108, 1, 3, []),
    (8, 7, 106, 1, 4, []),
    (7, 21, 103, 1, 3, ["skip"]),
    (6, 7, 103, 1, 3, ["wrong"]),
    (5, 7, 1, 1, 7, []),
    (4, 21, 105, 1, 5, []),
    (3, 7, 103, 1, 3, []),
    (2, 7, 108, 1, 3, ["restart"]),
    (1, 21, 106, 1, 4, ["wrong"]),
    (1, 21, 112, 1, 4, []),
]


def slip(expected: list[quran.Word], kind: str, rng: random.Random) -> list[str]:
    """A structural slip, applied to the clean recitation."""
    heard = [w.norm for w in expected]
    if kind == "skip":  # a whole ayah forgotten
        ayahs = sorted({w.ayah for w in expected})
        gone = rng.choice(ayahs[1:]) if len(ayahs) > 1 else ayahs[0]
        heard = [w.norm for w in expected if w.ayah != gone]
    elif kind == "restart":  # went back two words and carried on
        k = rng.randrange(2, max(3, len(heard) - 1))
        heard = heard[:k] + heard[k - 2:k] + heard[k:]
    elif kind == "swap":  # two neighbouring ayahs in the wrong order
        ayahs = sorted({w.ayah for w in expected})
        a = rng.choice(ayahs[1:-1] or ayahs[:1])
        first = [w.norm for w in expected if w.ayah == a]
        second = [w.norm for w in expected if w.ayah == a + 1]
        before = [w.norm for w in expected if w.ayah < a]
        after = [w.norm for w in expected if w.ayah > a + 1]
        heard = before + second + first + after
    return heard


def seed(reset: bool = False) -> str:
    if not settings.demo_password:
        return "DEMO_PASSWORD is not set: no demo account created"
    migrate()
    load_quran()
    tz = ZoneInfo(settings.timezone)
    with connection() as conn:
        user = conn.execute("SELECT id FROM users WHERE email = %s", (settings.demo_email,)).fetchone()
        if user and reset:
            conn.execute("DELETE FROM users WHERE id = %s", (user["id"],))
            user = None
        if user is None:
            user = conn.execute(
                "INSERT INTO users (email, name, password_hash) VALUES (%s, %s, %s) RETURNING id",
                (settings.demo_email, "Demo", hash_password(settings.demo_password)),
            ).fetchone()
        elif conn.execute("SELECT 1 FROM attempts WHERE user_id = %s LIMIT 1", (user["id"],)).fetchone():
            conn.commit()
            return f"demo account {settings.demo_email} already has data"

        rng = random.Random(7)
        now = datetime.now(tz)
        for days_ago, hour, surah, a, b, slips in SESSIONS:
            at = (now - timedelta(days=days_ago)).replace(hour=hour, minute=rng.randrange(0, 60), second=0, microsecond=0)
            if at > now:
                at = now - timedelta(minutes=5)
            expected = expected_for(conn, surah, a, b)
            heard = [w.norm for w in expected]
            for kind in (k for k in slips if k != "wrong"):  # at most one structural slip per session
                heard = slip(expected, kind, rng)
            for _ in range(slips.count("wrong")):
                heard = wrong_word(heard, rng)
            result = align(expected, heard)
            conn.execute(
                """INSERT INTO attempts (user_id, surah, ayah_from, ayah_to, status, transcript, result, accuracy,
                                         word_count, mistake_count, recognition_seconds, created_at, finished_at)
                   VALUES (%s, %s, %s, %s, 'done', %s, %s::jsonb, %s, %s, %s, %s, %s, %s)""",
                (user["id"], surah, a, b, " ".join(heard), json.dumps(result, ensure_ascii=False),
                 result["accuracy"], result["total"], result["mistakes"], round(rng.uniform(2.2, 5.5), 1),
                 at, at + timedelta(seconds=4)),
            )
            record_check(conn, user["id"], surah, result["ayahs"], at=at)
        items = build_plan(conn, user["id"], today())
        conn.commit()
    return f"demo account {settings.demo_email}: {len(SESSIONS)} checks over 3 weeks, {len(items)} ranges due today"


def wrong_word(heard: list[str], rng: random.Random) -> list[str]:
    """One confusable word replaced, on top of whatever slips are already in `heard`."""
    heard = list(heard)
    options = [k for k, w in enumerate(heard) if w in CONFUSIONS] or list(range(len(heard)))
    k = rng.choice(options)
    heard[k] = CONFUSIONS.get(heard[k], heard[(k + 3) % len(heard)])
    return heard


if __name__ == "__main__":
    print(seed(reset="--reset" in sys.argv))
    close_pool()
