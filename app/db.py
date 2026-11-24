"""Postgres access: one connection pool per process, plus a tiny migration runner."""
from contextlib import contextmanager
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

MIGRATIONS = Path(__file__).parent / "migrations"
_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection():
    """A pooled connection; commits on success, rolls back on error."""
    with pool().connection() as conn:
        yield conn


def migrate() -> list[str]:
    """Apply migrations/NNN_*.sql in order, once each. Safe to run from several processes:
    an advisory lock makes the API and the worker take turns."""
    applied = []
    with connection() as conn:
        conn.execute("SELECT pg_advisory_lock(727001)")
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
            for f in sorted(MIGRATIONS.glob("*.sql")):
                if f.name in done:
                    continue
                conn.execute(f.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
                applied.append(f.name)
            conn.commit()
        finally:
            conn.execute("SELECT pg_advisory_unlock(727001)")
            conn.commit()
    return applied
