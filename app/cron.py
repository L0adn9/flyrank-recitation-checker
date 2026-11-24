"""Scheduled jobs, run by the worker. `python -m app.cron revision_plan` runs one by hand."""
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from app.config import settings
from app.db import connection
from app.revision import build_all_plans

log = logging.getLogger("cron")

SCHEDULE = {
    "revision_plan": (lambda: settings.revision_cron, build_all_plans),
}


def tick(now: datetime | None = None) -> list[str]:
    """Run every job whose latest scheduled slot has not run yet. Called every few seconds by the worker.
    Claiming a slot is an INSERT on (name, scheduled_for), so a slot runs exactly once even with several
    workers, and a slot missed while the worker was down is caught up when it starts."""
    now = now or datetime.now(ZoneInfo(settings.timezone))
    ran = []
    for name, (expr, fn) in SCHEDULE.items():
        slot = croniter(expr(), now).get_prev(datetime)
        with connection() as conn:
            claimed = conn.execute(
                "INSERT INTO cron_runs (name, scheduled_for) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING name",
                (name, slot),
            ).fetchone()
            conn.commit()
            if not claimed:
                continue
            try:
                result = fn(conn)
                conn.execute("UPDATE cron_runs SET status = 'done', finished_at = now(), detail = %s"
                             " WHERE name = %s AND scheduled_for = %s", (f"{result}", name, slot))
                log.info("cron %s for %s done: %s", name, slot.isoformat(), result)
            except Exception as e:  # noqa: BLE001 - recorded; the next slot will try again
                conn.rollback()
                conn.execute("UPDATE cron_runs SET status = 'failed', finished_at = now(), detail = %s"
                             " WHERE name = %s AND scheduled_for = %s", (str(e), name, slot))
                log.exception("cron %s failed", name)
            conn.commit()
            ran.append(name)
    return ran


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    name = sys.argv[1] if len(sys.argv) > 1 else "revision_plan"
    with connection() as c:
        print(f"{name}: {SCHEDULE[name][1](c)}")
