"""Recite API: upload a recitation of some ayahs, get every mistake back, word by word."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import close_pool, connection, migrate
from app.routes import attempts, auth, live, quran, reports, revision
from app.seed_quran import load_quran


@asynccontextmanager
async def lifespan(_: FastAPI):
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET must be set to a random string of at least 32 characters")
    migrate()
    load_quran()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    if settings.whisper_server:   # load the models into the resident servers before the first recitation
        import threading
        from app.transcriber import get_transcriber
        threading.Thread(target=lambda: getattr(get_transcriber(), "warm", lambda: None)(), daemon=True).start()
    yield
    close_pool()
    from app.transcriber import get_transcriber
    getattr(get_transcriber(), "close", lambda: None)()


logging.basicConfig(format="%(levelname)s:     %(name)s: %(message)s")
logging.getLogger("recite").setLevel(logging.INFO)

app = FastAPI(title="Recite", version="1.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(quran.router)
app.include_router(attempts.router)
app.include_router(revision.router)
app.include_router(reports.router)
app.include_router(live.router)


@app.middleware("http")
async def fresh_page_code(request: Request, call_next):
    """The page's own code and style are always revalidated (a quick 304 when unchanged), so a browser
    never keeps running an old version after an update. Fonts may be cached."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") and not path.startswith("/static/fonts/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    fields = {".".join(str(p) for p in e["loc"][1:]) or "body": e["msg"] for e in exc.errors()}
    first = next(iter(fields.items()))
    return JSONResponse({"error": f"{first[0]}: {first[1]}", "fields": fields}, status_code=400)


STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/health")
def health():
    with connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
