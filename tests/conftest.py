"""API tests run against a real Postgres database (recite_test) and a fake speech recogniser."""
import io
import os
import wave

import psycopg
import pytest
from dotenv import load_dotenv

# Same server as the app (from .env), separate database, so tests never touch your data.
load_dotenv()
SERVER = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/recite").rsplit("/", 1)[0]
BASE = SERVER + "/postgres"
os.environ["DATABASE_URL"] = SERVER + "/recite_test"
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
os.environ["STORAGE_DIR"] = "/tmp/recite-test-storage"  # never the real storage

TABLES = "reports, cron_runs, revision_plans, ayah_progress, jobs, transcripts, attempts, users"


def _ensure_database():
    with psycopg.connect(BASE, autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = 'recite_test'").fetchone():
            conn.execute("CREATE DATABASE recite_test")


@pytest.fixture(scope="session")
def app():
    _ensure_database()
    from app.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
def fake():
    from app.transcriber import FakeTranscriber, set_transcriber
    t = FakeTranscriber()
    set_transcriber(t)
    yield t
    set_transcriber(None)


@pytest.fixture
def client(app, fake):
    from fastapi.testclient import TestClient
    from app.db import connection
    with TestClient(app) as c:
        with connection() as conn:
            conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
            conn.commit()
        yield c


def silence_wav(seconds: float = 1.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * seconds))
    return buf.getvalue()


def signup(client, email="sara@example.com", password="correct-horse", name="Sara"):
    r = client.post("/auth/signup", json={"email": email, "password": password, "name": name})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def run_jobs(ignore_backoff: bool = True) -> int:
    """Run queued jobs like the worker would. With ignore_backoff, delayed retries run now."""
    from app.db import connection
    from app.worker import drain
    total = 0
    while True:
        if ignore_backoff:
            with connection() as conn:
                conn.execute("UPDATE jobs SET run_at = now() WHERE status = 'queued'")
                conn.commit()
        n = drain()
        total += n
        if n == 0 or not ignore_backoff:
            return total
