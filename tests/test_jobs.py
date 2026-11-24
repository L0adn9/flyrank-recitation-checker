from app.db import connection
from tests.conftest import run_jobs, signup
from tests.test_attempts import AL_FIL, upload


def job_rows():
    with connection() as conn:
        return conn.execute("SELECT kind, status, attempts, last_error FROM jobs ORDER BY id").fetchall()


def test_upload_returns_202_and_the_worker_finishes_it(client, fake):
    fake.text = AL_FIL
    h = signup(client)
    r = upload(client, h)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert r.headers["location"] == f"/attempts/{r.json()['id']}"
    assert run_jobs() == 1
    done = client.get(r.headers["location"], headers=h).json()
    assert done["status"] == "done" and done["accuracy"] == 1.0 and done["mistake_count"] == 0
    assert done["result"]["total"] == 23


def test_transient_failure_is_retried(client, fake):
    fake.text, fake.fail_times = AL_FIL, 1
    h = signup(client)
    attempt_id = upload(client, h).json()["id"]
    run_jobs(ignore_backoff=False)
    [job] = job_rows()
    assert job["status"] == "queued" and job["attempts"] == 1 and "simulated" in job["last_error"]
    run_jobs()
    assert job_rows()[0]["status"] == "done"
    assert client.get(f"/attempts/{attempt_id}", headers=h).json()["status"] == "done"


def test_gives_up_after_max_attempts(client, fake):
    fake.fail_times = 99
    h = signup(client)
    attempt_id = upload(client, h).json()["id"]
    run_jobs()
    [job] = job_rows()
    assert job["status"] == "failed" and job["attempts"] == 3 and fake.calls == 3
    a = client.get(f"/attempts/{attempt_id}", headers=h).json()
    assert a["status"] == "failed" and a["error"] == "Speech recognition failed. Please try again later."


def test_unreadable_audio_fails_at_once_without_retries(client, fake):
    h = signup(client)
    attempt_id = upload(client, h, data=b"this is not audio at all").json()["id"]
    run_jobs()
    [job] = job_rows()
    assert job["status"] == "failed" and job["attempts"] == 1 and fake.calls == 0
    a = client.get(f"/attempts/{attempt_id}", headers=h).json()
    assert a["status"] == "failed" and "Could not read this recording" in a["error"]


def test_jobs_of_a_crashed_worker_are_requeued(client, fake):
    from app import jobs
    fake.text = AL_FIL
    upload(client, signup(client))
    with connection() as conn:
        jobs.claim(conn, "dead-worker")
        conn.execute("UPDATE jobs SET locked_at = now() - interval '1 hour'")
        conn.commit()
        assert jobs.requeue_stale(conn) == 1
    assert run_jobs() == 1 and job_rows()[0]["status"] == "done"
