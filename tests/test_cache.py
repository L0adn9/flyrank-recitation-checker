from app.db import connection
from tests.conftest import run_jobs, signup, silence_wav
from tests.test_attempts import AL_FIL, upload


def test_same_recording_is_scored_from_cache_without_the_model(client, fake):
    fake.text = AL_FIL
    h = signup(client)
    first = upload(client, h)
    assert first.status_code == 202
    run_jobs()
    assert fake.calls == 1

    second = upload(client, h)  # identical bytes
    assert second.status_code == 201
    body = second.json()
    assert body["status"] == "done" and body["cache_hit"] is True and body["accuracy"] == 1.0
    assert fake.calls == 1  # the model did not run again
    with connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 1
        assert conn.execute("SELECT hits FROM transcripts").fetchone()["hits"] == 1


def test_cache_is_shared_but_scoring_follows_the_chosen_range(client, fake):
    fake.text = AL_FIL
    upload(client, signup(client))
    run_jobs()
    other = signup(client, email="omar@example.com", name="Omar")
    r = upload(client, other, a=1, b=2)  # same audio, different range: cached text, new score
    assert r.status_code == 201 and r.json()["word_count"] == 12


def test_different_recording_is_a_miss(client, fake):
    fake.text = AL_FIL
    h = signup(client)
    upload(client, h)
    run_jobs()
    assert upload(client, h, data=silence_wav(2.0)).status_code == 202
    run_jobs()
    assert fake.calls == 2
