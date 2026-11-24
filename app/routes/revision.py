from fastapi import APIRouter, Depends

from app.auth import current_user
from app.config import settings
from app.db import connection
from app.revision import today

router = APIRouter(tags=["revision"])


@router.get("/revision/today")
def todays_plan(user: dict = Depends(current_user)):
    """The plan the daily cron built for today, with each range marked done once it was recited cleanly today."""
    on = today()
    with connection() as conn:
        plan = conn.execute("SELECT items, created_at FROM revision_plans WHERE user_id = %s AND plan_date = %s",
                            (user["id"], on)).fetchone()
        items = plan["items"] if plan else []
        for it in items:
            row = conn.execute(
                "SELECT bool_and(last_solid AND (last_checked_at AT TIME ZONE %s)::date = %s) AS done"
                " FROM ayah_progress WHERE user_id = %s AND surah = %s AND ayah BETWEEN %s AND %s",
                (settings.timezone, on, user["id"], it["surah"], it["ayah_from"], it["ayah_to"]),
            ).fetchone()
            it["done"] = bool(row["done"])
    return {"date": on.isoformat(), "generated_at": plan["created_at"] if plan else None, "items": items}


@router.get("/progress")
def progress(user: dict = Depends(current_user)):
    """Per surah: how many ayahs were checked, how many are solid now, and how many are due."""
    on = today()
    with connection() as conn:
        surahs = conn.execute(
            """SELECT p.surah, s.name_en AS surah_name, s.name_ar AS surah_name_ar, s.ayah_count,
                      count(*) AS checked, count(*) FILTER (WHERE p.last_solid) AS solid,
                      count(*) FILTER (WHERE p.due_on <= %s) AS due, max(p.last_checked_at) AS last_checked_at
               FROM ayah_progress p JOIN surahs s ON s.number = p.surah
               WHERE p.user_id = %s GROUP BY p.surah, s.name_en, s.name_ar, s.ayah_count ORDER BY p.surah""",
            (on, user["id"]),
        ).fetchall()
        totals = conn.execute(
            "SELECT count(*) AS attempts, avg(accuracy) AS avg_accuracy FROM attempts WHERE user_id = %s AND status = 'done'",
            (user["id"],),
        ).fetchone()
    return {"surahs": surahs, "attempts": totals["attempts"],
            "avg_accuracy": round(totals["avg_accuracy"], 4) if totals["avg_accuracy"] is not None else None}
