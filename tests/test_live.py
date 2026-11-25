"""Live checking: phrases cut from a stream as it arrives, words marked while reciting, and the WebSocket."""
import asyncio
import json
import math
from array import array

import pytest

from app.align import align
from app.live import LiveSession
from app.quran import expected_words, norm_words
from app.transcriber import FakeTranscriber
from tests.conftest import signup
from tests.test_align import AL_FIL, AYAH, EXP

PHRASES = [" ".join(AYAH[n]) for n in range(1, 6)]


def tone(seconds: float, amp: float = 0.3) -> bytes:
    n = int(16000 * seconds)
    return array("h", (int(amp * 32767 * math.sin(2 * math.pi * 220 * k / 16000)) for k in range(n))).tobytes()


def quiet(seconds: float) -> bytes:
    return b"\0\0" * int(16000 * seconds)


def recitation(phrases: int = 5) -> bytes:
    """Lead-in silence, then one 2.5 s "phrase" per ayah with 0.8 s pauses: shaped like a recitation."""
    return quiet(0.6) + b"".join(tone(2.5) + quiet(0.8) for _ in range(phrases))


async def run_session(audio: bytes, phrases: list[str], chunk: int = 3200):
    msgs = []

    async def notify(m):
        msgs.append(m)

    fake = FakeTranscriber(phrases=phrases)
    s = LiveSession(EXP, fake, notify)
    runner = asyncio.create_task(s.run())
    for k in range(0, len(audio), chunk):
        s.feed(audio[k:k + chunk])
        await asyncio.sleep(0)
    s.finish()
    await runner
    return s, msgs, fake


def test_stream_is_cut_into_one_phrase_per_ayah():
    s, msgs, fake = asyncio.run(run_session(recitation(), PHRASES))
    assert len(s.phrases) == 5 and all(p.final for p in s.phrases)
    assert [p.text for p in s.phrases] == PHRASES
    assert fake.partials > 0, "words should be shown while a phrase is still being said"
    assert align(EXP, s.heard())["accuracy"] == 1.0


def test_progress_marks_words_as_they_are_said_and_signals_completion():
    s, msgs, _ = asyncio.run(run_session(recitation(), PHRASES))
    progress = [m for m in msgs if m["type"] == "progress"]
    nexts = [m["next"] for m in progress]
    assert nexts == sorted(nexts) and nexts[-1] == len(EXP)   # only ever moves forward
    mid = next(m for m in progress if 0 < m["next"] < len(EXP))
    assert mid["words"][-1]["status"] == "pending"
    assert {"type": "complete"} in msgs


def test_the_word_being_said_is_not_called_wrong_yet():
    async def go():
        msgs = []

        async def notify(m):
            msgs.append(m)

        s = LiveSession(EXP, FakeTranscriber(), notify)
        s.feed(quiet(0.5) + tone(1.0))
        s.phrases[0].partial = " ".join(AYAH[1][:3] + ["فع"])   # cut off mid-word, still being said
        return s.progress()

    p = asyncio.run(go())
    st = [w["status"] for w in p["words"]]
    assert "wrong" not in st                       # never judged before the pause
    assert st[:4] == ["correct"] * 4 and st[4] == "current" and p["next"] == 4   # shown where they are


def test_skipped_ayah_does_not_end_the_check_early():
    # Ayahs 4 and 5 swapped: after "5" the end of the passage was reached, but ayah 4 is still to come.
    s, msgs, _ = asyncio.run(run_session(recitation(4), [PHRASES[0], PHRASES[1], PHRASES[2], PHRASES[4]]))
    assert {"type": "complete"} not in msgs


def test_the_result_is_made_from_the_live_phrases():
    s, _, fake = asyncio.run(run_session(recitation(), PHRASES))
    assert asyncio.run(s.final_phrases()) == PHRASES and s.redone == 0


# ---------- WebSocket /live ----------

def live_check(client, headers, audio: bytes, start: dict | None = None, end: str = "stop"):
    token = headers["Authorization"].split()[1]
    msgs = []
    with client.websocket_connect("/live") as ws:
        ws.send_text(json.dumps(start or {"type": "start", "token": token, "surah": 105, "ayah_from": 1, "ayah_to": 5}))
        first = ws.receive_json()
        msgs.append(first)
        if first["type"] != "ready":
            return msgs
        for k in range(0, len(audio), 3200):
            ws.send_bytes(audio[k:k + 3200])
        ws.send_text(json.dumps({"type": end}))
        if end == "cancel":
            return msgs
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] in ("result", "error"):
                return msgs


@pytest.fixture
def phrases(fake):
    fake.phrases = PHRASES
    return fake


def test_live_check_end_to_end(client, phrases):
    h = signup(client)
    msgs = live_check(client, h, recitation())
    ready, result = msgs[0], msgs[-1]
    assert ready["type"] == "ready" and len(ready["words"]) == len(EXP)
    assert any(m["type"] == "progress" for m in msgs)
    assert result["type"] == "result"
    a = result["attempt"]
    assert a["status"] == "done" and a["live"] is True and a["accuracy"] == 1.0
    assert client.get(f"/attempts/{a['id']}", headers=h).json()["result"]["total"] == len(EXP)
    # It counts like any other check: the revision schedule has the ayahs.
    assert client.get("/progress", headers=h).json()


def test_live_recording_is_cached_like_an_upload(client, phrases):
    import io, wave
    h = signup(client)
    audio = recitation()
    live_check(client, h, audio)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(audio)
    r = client.post("/attempts", headers=h, data={"surah": 105, "ayah_from": 1, "ayah_to": 5},
                    files={"audio": ("r.wav", buf.getvalue(), "audio/wav")})
    assert r.status_code == 201 and r.json()["accuracy"] == 1.0


def test_live_mistakes_are_found(client, fake):
    fake.phrases = [PHRASES[0], PHRASES[2], PHRASES[3], PHRASES[4]]   # ayah 2 skipped
    h = signup(client)
    a = live_check(client, h, recitation(4))[-1]["attempt"]
    words = a["result"]["words"]
    assert {w["status"] for w in words if w["ayah"] == 2} == {"missed"}
    assert a["mistake_count"] == 5 and a["accuracy"] < 1


def test_live_needs_a_valid_token_and_range(client, phrases):
    h = signup(client)
    bad = live_check(client, h, b"", {"type": "start", "token": "nope", "surah": 105, "ayah_from": 1, "ayah_to": 5})
    assert bad == [{"type": "error", "status": 401, "error": "Invalid token"}]
    token = h["Authorization"].split()[1]
    bad = live_check(client, h, b"", {"type": "start", "token": token, "surah": 105, "ayah_from": 1, "ayah_to": 9})
    assert bad[0]["status"] == 400 and "1 to 5" in bad[0]["error"]
    bad = live_check(client, h, b"", {"type": "hello"})
    assert bad[0]["status"] == 400


def test_silence_is_not_saved(client, phrases):
    h = signup(client)
    msgs = live_check(client, h, quiet(3))
    assert msgs[-1]["type"] == "error" and msgs[-1]["status"] == 422
    assert client.get("/attempts", headers=h).json() == []


def test_cancel_saves_nothing(client, phrases):
    h = signup(client)
    live_check(client, h, recitation(2), end="cancel")
    assert client.get("/attempts", headers=h).json() == []


def test_silence_before_and_after_does_not_hide_the_pauses():
    """A studio-like recording: speech at -18 dB, pauses between ayahs only down to -35 dB (reverb). Long
    true silence before starting and after the last word (pressing stop late) must not pull the threshold
    below the pauses, or every ayah would merge into one phrase."""
    from app.audio import speech_spans
    ayah, pause = [-18.0] * 150, [-35.0] * 30          # 3 s ayahs, 0.6 s pauses (20 ms frames)
    recited = ayah + pause + ayah + pause + ayah
    plain = speech_spans(recited)
    padded = speech_spans([-100.0] * 50 + recited + [-100.0] * 400)
    assert len(plain) == 3
    assert [(round(a - 1.0, 2), round(b - 1.0, 2)) for a, b in padded] == [(round(a, 2), round(b, 2)) for a, b in plain]


def test_a_quiet_noise_is_not_a_phrase():
    """A breath or a chair after the last ayah (25 dB under the voice) must not reach the speech model,
    which would make words up for it."""
    from app.audio import speech_spans
    lv = [-100.0] * 20 + [-18.0] * 150 + [-100.0] * 40 + [-45.0] * 20 + [-100.0] * 40
    assert len(speech_spans(lv)) == 1
