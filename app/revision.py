"""Spaced revision. After every check each ayah gets a due date:
- said with a mistake or a hesitation -> due again tomorrow, streak reset;
- said cleanly -> the gap grows: 1, 3, 7, 14, 30, then 60 days.
The daily cron turns due ayahs into a short plan of ranges to recite."""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg import Connection

from app.config import settings

INTERVALS = [1, 3, 7, 14, 30, 60]
PLAN_SIZE = 12  # ayahs per day: a plan you can finish beats a complete one


def today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def next_interval(streak: int) -> int:
    return INTERVALS[min(streak, len(INTERVALS)) - 1] if streak > 0 else 1


def record_check(conn: Connection, user_id: int, surah: int, ayah_results: list[dict], on: date | None = None) -> None:
    """Update every checked ayah's schedule from one attempt's per-ayah results."""
    on = on or today()
    for a in ayah_results:
        row = conn.execute(
            "SELECT streak FROM ayah_progress WHERE user_id = %s AND surah = %s AND ayah = %s",
            (user_id, surah, a["ayah"]),
        ).fetchone()
        streak = (row["streak"] + 1 if row else 1) if a["solid"] else 0
        interval = next_interval(streak)
        conn.execute(
            """INSERT INTO ayah_progress (user_id, surah, ayah, checks, streak, interval_days,
                                          last_checked_at, last_solid, last_mistakes, due_on)
               VALUES (%(u)s, %(s)s, %(a)s, 1, %(streak)s, %(i)s, now(), %(solid)s, %(m)s, %(due)s)
               ON CONFLICT (user_id, surah, ayah) DO UPDATE SET
                   checks = ayah_progress.checks + 1, streak = EXCLUDED.streak,
                   interval_days = EXCLUDED.interval_days, last_checked_at = now(),
                   last_solid = EXCLUDED.last_solid, last_mistakes = EXCLUDED.last_mistakes,
                   due_on = EXCLUDED.due_on""",
            {"u": user_id, "s": surah, "a": a["ayah"], "streak": streak, "i": interval,
             "solid": a["solid"], "m": a["mistakes"] + a["repeats"], "due": on + timedelta(days=interval)},
        )


def _ranges(rows: list[dict], on: date) -> list[dict]:
    """Merge consecutive due ayahs of one surah into ranges, so the plan reads like real revision."""
    items: list[dict] = []
    for r in sorted(rows, key=lambda r: (r["surah"], r["ayah"])):
        weak = not r["last_solid"]
        last = items[-1] if items else None
        if last and last["surah"] == r["surah"] and last["ayah_to"] == r["ayah"] - 1:
            last["ayah_to"] = r["ayah"]
            last["overdue_days"] = max(last["overdue_days"], (on - r["due_on"]).days)
        else:
            last = {"surah": r["surah"], "surah_name": r["name_en"], "surah_name_ar": r["name_ar"],
                    "ayah_from": r["ayah"], "ayah_to": r["ayah"], "weak_ayahs": [],
                    "overdue_days": (on - r["due_on"]).days}
            items.append(last)
        if weak:
            last["weak_ayahs"].append(r["ayah"])
    for it in items:
        it["weak"] = bool(it["weak_ayahs"])
        it["reason"] = ("Mistakes last time in ayah " + ", ".join(map(str, it["weak_ayahs"]))
                        if it["weak"] else "Due for review")
    # Weak ranges first, then the most overdue.
    return sorted(items, key=lambda it: (not it["weak"], -it["overdue_days"], it["surah"], it["ayah_from"]))


def build_plan(conn: Connection, user_id: int, on: date) -> list[dict]:
    rows = conn.execute(
        """SELECT p.surah, p.ayah, p.due_on, p.last_solid, s.name_en, s.name_ar
           FROM ayah_progress p JOIN surahs s ON s.number = p.surah
           WHERE p.user_id = %s AND p.due_on <= %s
           ORDER BY p.last_solid, p.due_on, p.last_mistakes DESC
           LIMIT %s""",
        (user_id, on, PLAN_SIZE),
    ).fetchall()
    items = _ranges(rows, on)
    conn.execute(
        "INSERT INTO revision_plans (user_id, plan_date, items) VALUES (%s, %s, %s::jsonb)"
        " ON CONFLICT (user_id, plan_date) DO UPDATE SET items = EXCLUDED.items, created_at = now()",
        (user_id, on, json.dumps(items, ensure_ascii=False)),
    )
    return items


def build_all_plans(conn: Connection, on: date | None = None) -> int:
    """The daily cron job: today's plan for every user who has checked anything."""
    on = on or today()
    users = conn.execute("SELECT DISTINCT user_id FROM ayah_progress").fetchall()
    for u in users:
        build_plan(conn, u["user_id"], on)
    conn.commit()
    return len(users)
