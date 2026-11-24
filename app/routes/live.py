"""WebSocket /live: check a recitation while it is being recited.

Protocol (JSON text messages, plus binary audio):
  client -> {"type": "start", "token": "<JWT>", "surah": 105, "ayah_from": 1, "ayah_to": 5}
  server -> {"type": "ready", "max_seconds": 180, "words": [{"text": ..., "ayah": ...}]}
            the passage's words; progress statuses below are listed in this order
  client -> binary frames: 16 kHz mono 16-bit little-endian PCM, any size (the page sends ~100 ms)
  server -> {"type": "progress", "words": [{"status": ..., "heard"?: ...}], "next": k}
            after every recognition pass; statuses as in a result, plus "current" (being said) and
            "pending" (not reached); wrong / missed only ever come from a finished phrase
  server -> {"type": "complete"} once the whole passage has been recited (the page then stops by itself)
  client -> {"type": "stop"}  (or {"type": "cancel"} to throw the recording away)
  server -> {"type": "result", "attempt": {...same as GET /attempts/{id}...}} and closes
Errors: {"type": "error", "status": 400|401|..., "error": "..."} and the socket closes.
A browser cannot set an Authorization header on a WebSocket, so the token travels in the first message.
"""
import asyncio
import contextlib
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder

from app.auth import user_from_token
from app.checker import expected_for, save_live_attempt
from app.config import settings
from app.db import connection
from app.live import LiveSession
from app.routes.attempts import _validate_range, get_owned
from app.transcriber import TranscriberError, get_transcriber

router = APIRouter(tags=["live"])
IDLE_SECONDS = 20


class Abort(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _prepare(start: dict) -> tuple[dict, dict, list]:
    try:
        user = user_from_token(str(start.get("token") or ""))
    except HTTPException as e:
        raise Abort(401, e.detail) from None
    try:
        passage = {k: int(start[k]) for k in ("surah", "ayah_from", "ayah_to")}
    except (KeyError, TypeError, ValueError):
        raise Abort(400, "start needs whole numbers surah, ayah_from and ayah_to") from None
    with connection() as conn:
        try:
            _validate_range(conn, passage["surah"], passage["ayah_from"], passage["ayah_to"])
        except HTTPException as e:
            raise Abort(400, e.detail) from None
        expected = expected_for(conn, passage["surah"], passage["ayah_from"], passage["ayah_to"])
    return user, passage, expected


def _load(attempt_id: int, user_id: int) -> dict:
    with connection() as conn:
        return get_owned(conn, attempt_id, user_id)


async def _receive(ws: WebSocket) -> dict:
    try:
        msg = await asyncio.wait_for(ws.receive(), IDLE_SECONDS)
    except TimeoutError:
        raise Abort(408, "No audio arrived for a while, so the check was stopped.") from None
    if msg["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(msg.get("code", 1000))
    return msg


@router.websocket("/live")
async def live(ws: WebSocket):
    await ws.accept()
    runner: asyncio.Task | None = None
    try:
        msg = await _receive(ws)
        try:
            start = json.loads(msg.get("text") or "")
        except ValueError:
            start = None
        if not isinstance(start, dict) or start.get("type") != "start":
            raise Abort(400, "The first message must be {\"type\": \"start\", ...}")
        user, passage, expected = await run_in_threadpool(_prepare, start)

        session = LiveSession(expected, get_transcriber(), ws.send_json)
        runner = asyncio.create_task(session.run())
        await ws.send_json({"type": "ready", "max_seconds": settings.max_audio_seconds,
                            "words": [{"text": w.text, "ayah": w.ayah} for w in expected]})

        while True:
            if runner.done():  # recognition failed
                runner.result()
            msg = await _receive(ws)
            if msg.get("bytes"):
                session.feed(msg["bytes"])
                if session.seconds >= settings.max_audio_seconds:
                    break
                continue
            try:
                kind = json.loads(msg.get("text") or "").get("type")
            except (ValueError, AttributeError):
                kind = None
            if kind == "stop":
                break
            if kind == "cancel":
                runner.cancel()
                await ws.close()
                return

        session.finish()
        await runner
        phrases = await session.final_phrases()
        if not phrases:
            raise Abort(422, "Recite didn't hear any recitation. Check that the right microphone is on and speak up a little.")
        attempt_id = await run_in_threadpool(
            save_live_attempt, user["id"], passage["surah"], passage["ayah_from"], passage["ayah_to"],
            bytes(session.pcm), phrases, session.decode_seconds)
        attempt = await run_in_threadpool(_load, attempt_id, user["id"])
        await ws.send_json({"type": "result", "attempt": jsonable_encoder(attempt)})
        await ws.close()
    except WebSocketDisconnect:
        pass
    except Abort as e:
        await _fail(ws, e.status, e.message)
    except TranscriberError as e:
        await _fail(ws, 503, f"Speech recognition failed: {e}")
    finally:
        if runner is not None and not runner.done():
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await runner


async def _fail(ws: WebSocket, status: int, message: str) -> None:
    with contextlib.suppress(Exception):
        await ws.send_json({"type": "error", "status": status, "error": message})
        await ws.close(code=4000 + status if status < 1000 else 1011)
