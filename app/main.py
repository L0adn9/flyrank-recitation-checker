"""Recite API: upload a recitation of some ayahs, get every mistake back, word by word."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import close_pool, connection, migrate
from app.routes import attempts, auth, quran, revision
from app.seed_quran import load_quran


@asynccontextmanager
async def lifespan(_: FastAPI):
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET must be set to a random string of at least 32 characters")
    migrate()
    load_quran()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    yield
    close_pool()


app = FastAPI(title="Recite", version="1.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(quran.router)
app.include_router(attempts.router)
app.include_router(revision.router)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    fields = {".".join(str(p) for p in e["loc"][1:]) or "body": e["msg"] for e in exc.errors()}
    first = next(iter(fields.items()))
    return JSONResponse({"error": f"{first[0]}: {first[1]}", "fields": fields}, status_code=400)


@app.get("/health")
def health():
    with connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
