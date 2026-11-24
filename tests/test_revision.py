from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import cron
from app.db import connection
from app.revision import build_all_plans, next_interval, today
from tests.conftest import run_jobs, signup, silence_wav
from tests.test_attempts import AL_FIL, upload

WITH_MISTAKE = AL_FIL.replace("طيرا", "طيور")  # one wrong word in ayah 3


def progress_rows():
    with connection() as conn:
        return {r["ayah"]: r for r in conn.execute("SELECT * FROM ayah_progress ORDER BY ayah")}


def test_intervals_grow_and_reset():
    assert [next_interval(s) for s in range(0, 8)] == [1, 1, 3, 7, 14, 30, 60, 60]


def test_each_check_schedules_every_ayah(client, fake):
    fake.text = WITH_MISTAKE
    h = signup(client)
    upload(client, h)
    run_jobs()
    rows = progress_rows()
    assert sorted(rows) == [1, 2, 3, 4, 5]
    assert rows[3]["last_solid"] is False and rows[3]["streak"] == 0
    assert rows[3]["due_on"] == today() + timedelta(days=1)
    assert rows[1]["streak"] == 1 and rows[1]["due_on"] == today() + timedelta(days=1)

    fake.text = AL_FIL  # a new recording (new bytes, so no cache hit), this time without the mistake
    upload(client, h, data=silence_wav(1.5))
    run_jobs()
    rows = progress_rows()
    assert rows[1]["streak"] == 2 and rows[1]["due_on"] == today() + timedelta(days=3)
    assert rows[3]["streak"] == 1 and rows[3]["checks"] == 2


def test_plan_merges_neighbours_and_puts_weak_ranges_first(client, fake):
    from tests.test_attempts import QURAYSH
    h = signup(client)
    fake.text = QURAYSH
    upload(client, h, surah=106, a=1, b=4)
    run_jobs()
    fake.text = WITH_MISTAKE
    upload(client, h, data=silence_wav(1.5))
    run_jobs()
    with connection() as conn:  # pretend the due dates have arrived
        conn.execute("UPDATE ayah_progress SET due_on = due_on - 1")
        conn.commit()
        assert build_all_plans(conn) == 1
    plan = client.get("/revision/today", headers=h).json()
    assert [(i["surah"], i["ayah_from"], i["ayah_to"], i["weak_ayahs"]) for i in plan["items"]] == \
        [(105, 1, 5, [3]), (106, 1, 4, [])]
    assert plan["items"][0]["reason"] == "Mistakes last time in ayah 3" and plan["items"][0]["done"] is False


def test_cron_slot_runs_once_and_catches_up(client, fake):
    fake.text = AL_FIL
    upload(client, signup(client))
    run_jobs()
    tz = ZoneInfo("UTC")
    morning = datetime(2026, 11, 24, 5, 0, 30, tzinfo=tz)
    assert cron.tick(morning) == ["revision_plan"]
    assert cron.tick(morning + timedelta(minutes=5)) == []      # same slot: already ran
    assert cron.tick(morning + timedelta(days=2)) == ["revision_plan"]  # worker was down a day: one catch-up run
    with connection() as conn:
        runs = conn.execute("SELECT status FROM cron_runs ORDER BY scheduled_for").fetchall()
    assert [r["status"] for r in runs] == ["done", "done"]


def test_progress_summary(client, fake):
    fake.text = WITH_MISTAKE
    h = signup(client)
    upload(client, h)
    run_jobs()
    p = client.get("/progress", headers=h).json()
    assert p["attempts"] == 1 and p["surahs"][0]["checked"] == 5 and p["surahs"][0]["solid"] == 4


def test_plan_item_is_done_after_a_clean_recitation_today(client, fake):
    fake.text = WITH_MISTAKE
    h = signup(client)
    upload(client, h)
    run_jobs()
    with connection() as conn:
        conn.execute("UPDATE ayah_progress SET due_on = due_on - 1")
        conn.commit()
        build_all_plans(conn)
    assert client.get("/revision/today", headers=h).json()["items"][0]["done"] is False
    fake.text = AL_FIL
    upload(client, h, data=silence_wav(1.5))
    run_jobs()
    assert client.get("/revision/today", headers=h).json()["items"][0]["done"] is True
