"""Live checking: words are marked while the person recites, not after.

The browser streams microphone audio (16 kHz mono 16-bit PCM, in ~100 ms chunks) over a WebSocket.
A LiveSession cuts that stream into phrases at the reciter's pauses as it arrives, using the same rule
as uploaded recordings (app/audio.py). It can only look backwards, so the loudness threshold is
estimated from the audio so far; when a phrase ends, it is checked once more for pauses with the
threshold known by then, which matters in the first seconds.

- While a phrase is being said, it is re-recognised every few hundred milliseconds (a quick partial
  pass), so the words light up as they are said.
- When the reciter pauses, the phrase is closed and recognised once more with the full model window.
  That final text replaces the partial one and never changes again.

After every pass, everything heard so far is aligned against the start of the passage (align with
prefix=True), and the client gets each word's status. The last word of a phrase that is still being
said may be cut off mid-word, so it is never marked wrong: it shows as the current word until the
phrase is finished.

On stop, the stored result is made the same way as for an upload of the same audio: the recording is
cut with the whole recording's threshold, live phrases that were cut the same way are reused, and only
the others are recognised again (usually none, so the result is there right away).
"""
import asyncio
import math
import sys
import time
from array import array
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app import quran
from app.align import align
from app.audio import FRAME as FRAME_SECONDS, recited_part, speech_spans, threshold_for
from app.transcriber import Transcriber

RATE = 16000
FRAME = 320                 # samples per 20 ms loudness frame
PAD = int(0.15 * RATE)      # audio kept on each side of a phrase
MIN_PAUSE = 0.4             # seconds of quiet that end a phrase (uploads use 0.45; both scored 13/13 there,
                            # and live it is better to cut once too often than to merge two ayahs)
MIN_SPEECH = 0.3            # shorter sounds are clicks or breaths, not words
MAX_PHRASE = 24.0           # longer phrases are cut at their quietest moment
PARTIAL_START = 0.5         # a phrase gets its first partial pass after this much audio
PARTIAL_EVERY = 0.35        # and another one whenever this much new audio has arrived
PRIOR = [-60.0] * 15        # assumed quiet frames, so the first seconds are not judged on speech alone


@dataclass
class Phrase:
    start: int              # sample where the phrase starts
    end: int | None = None  # sample where it ends; None while it is still being said
    text: str = ""
    final: bool = False
    partial_at: int = 0     # samples covered by the last partial pass


class LiveSession:
    def __init__(self, expected: list[quran.Word], transcriber: Transcriber,
                 notify: Callable[[dict], Awaitable[None]]):
        self.expected = expected
        self.transcriber = transcriber
        self.notify = notify
        self.pcm = bytearray()
        self.levels: list[float] = []
        self.phrases: list[Phrase] = []
        self.open: Phrase | None = None
        self.quiet = 0                      # quiet frames in a row inside the open phrase
        self.threshold = -42.0
        self.ended = False
        self.complete_sent = False
        self.decode_seconds = 0.0
        self.redone = 0                     # phrases recognised again on stop
        self._wake = asyncio.Event()

    # ---------- audio in ----------
    @property
    def samples(self) -> int:
        return len(self.pcm) // 2

    @property
    def seconds(self) -> float:
        return self.samples / RATE

    def feed(self, chunk: bytes) -> None:
        """Add audio (16-bit little-endian PCM). Cheap: loudness and pause detection only."""
        if self.ended or not chunk:
            return
        first = len(self.levels)
        self.pcm += chunk[: len(chunk) - len(chunk) % 2]
        samples = array("h", self.pcm[first * FRAME * 2: (self.samples // FRAME) * FRAME * 2])
        if sys.byteorder == "big":
            samples.byteswap()
        for k in range(0, len(samples), FRAME):
            frame = samples[k:k + FRAME]
            rms = math.sqrt(sum(x * x for x in frame) / FRAME)
            self.levels.append(20 * math.log10(rms / 32768) if rms > 0 else -100.0)
        self._update_threshold()
        for f in range(first, len(self.levels)):
            self._step(f)
        self._wake.set()

    def _update_threshold(self) -> None:
        """app.audio.threshold_for on the audio so far. Before any clear speech (or in the first second,
        while the floor is unknown) it is never below -55 dBFS, so the hiss of a quiet room is not speech."""
        if len(self.levels) < 10:
            return
        levels = recited_part(self.levels) + PRIOR
        ordered = sorted(levels)
        floor, peak = ordered[len(ordered) // 10], ordered[int(len(ordered) * 0.95)]
        t = floor + 12 if peak - floor < 10 else threshold_for(levels, trim=False)
        self.threshold = max(t, -55.0)

    def _step(self, f: int) -> None:
        loud = self.levels[f] >= self.threshold
        if loud:
            if self.open is None:
                self.open = Phrase(start=f * FRAME)
                self.phrases.append(self.open)
            self.quiet = 0
        elif self.open is not None:
            self.quiet += 1
            if self.quiet >= MIN_PAUSE * RATE / FRAME:
                self._close((f - self.quiet + 1) * FRAME)
                return
        if self.open is not None and (f + 1) * FRAME - self.open.start > MAX_PHRASE * RATE:
            lo = (self.open.start // FRAME) + int(MAX_PHRASE * RATE / FRAME) // 2
            cut = min(range(lo, f + 1), key=lambda k: self.levels[k]) * FRAME
            self._close(cut)
            if cut < (f + 1) * FRAME:
                self.open = Phrase(start=cut)
                self.phrases.append(self.open)

    def _close(self, end: int) -> None:
        """End the open phrase. It is re-cut with the current threshold first: early in a recording the
        threshold is still settling, and a pause it missed then shows up now."""
        p, self.open, self.quiet = self.open, None, 0
        i = self.phrases.index(p)
        f0 = p.start // FRAME
        pieces = speech_spans(self.levels[f0:end // FRAME], min_pause=MIN_PAUSE, min_speech=MIN_SPEECH,
                              max_segment=MAX_PHRASE, threshold=self.threshold)
        new = [Phrase(start=p.start + round(a / FRAME_SECONDS) * FRAME, end=p.start + round(b / FRAME_SECONDS) * FRAME)
               for a, b in pieces]
        if new:
            new[0].text = p.text  # what was heard so far, until the final pass replaces it
        self.phrases[i:i + 1] = new

    def finish(self) -> None:
        """No more audio: the phrase being said (if any) ends here."""
        if self.open is not None:
            self._close(self.samples)
        self.ended = True
        self._wake.set()

    # ---------- recognition ----------
    def _audio(self, start: int, end: int) -> bytes:
        return bytes(self.pcm[max(0, start - PAD) * 2: min(self.samples, end + PAD) * 2])

    def _next_job(self) -> tuple[Phrase, bool] | None:
        for p in self.phrases:
            if p.end is not None and not p.final:
                return p, False
        p = self.open
        if p is not None and not self.ended:
            said = self.samples - p.start
            if said >= PARTIAL_START * RATE and self.samples - p.partial_at >= PARTIAL_EVERY * RATE:
                return p, True
        return None

    async def run(self) -> None:
        """Recognise phrases until finish() and the last phrase is final. Sends a progress message after
        each pass. Raises TranscriberError if recognition fails."""
        while True:
            job = self._next_job()
            if job is None:
                if self.ended:
                    return
                self._wake.clear()
                await self._wake.wait()
                continue
            p, partial = job
            upto = self.samples if partial else p.end
            t0 = time.monotonic()
            text = await self.transcriber.decode(self._audio(p.start, upto), partial=partial)
            if partial:
                p.partial_at = upto
            else:
                self.decode_seconds += time.monotonic() - t0
                p.final = True
            p.text = text
            await self.notify(self.progress())
            if not self.complete_sent and self.recited_all():
                self.complete_sent = True
                await self.notify({"type": "complete"})

    # ---------- results ----------
    def heard(self, finals_only: bool = False) -> list[str]:
        text = " ".join(p.text for p in self.phrases if p.text and (p.final or not finals_only))
        return quran.clean_heard(quran.norm_words(text), self.expected)

    def progress(self) -> dict:
        """Each expected word's status so far: correct / wrong / missed / moved, 'current' for the word
        being said, 'pending' for words not reached yet."""
        r = align(self.expected, self.heard(), prefix=True)
        words, extras = r["words"], r["extras"]
        reached = r["reached"]
        tentative = any(p.text and not p.final for p in self.phrases)
        if tentative:
            # The phrase being said may end mid-word: don't call its last word wrong yet, and don't show
            # extras at the very end (most likely the start of a word that isn't finished).
            if 0 <= reached and words[reached]["status"] == "wrong":
                words[reached]["status"] = "current"
            extras = [e for e in extras if e["after"] < reached]
        return {
            "type": "progress",
            "words": [{"status": w["status"], **({"heard": w["heard"]} if "heard" in w else {})} for w in words],
            "extras": [{"after": e["after"], "words": e["words"], "kind": e["kind"]} for e in extras],
            "next": reached + 1,
            "seconds": round(self.seconds, 1),
        }

    def recited_all(self) -> bool:
        """The final texts reach the last word of the passage, said correctly, and no ayah was skipped
        entirely (if one was, the reciter may still come back to it)."""
        if not self.expected:
            return False
        r = align(self.expected, self.heard(finals_only=True), prefix=True)
        if r["reached"] != len(self.expected) - 1 or r["words"][-1]["status"] != "correct":
            return False
        by_ayah: dict[int, list[str]] = {}
        for w in r["words"]:
            by_ayah.setdefault(w["ayah"], []).append(w["status"])
        return not any(all(s == "missed" for s in st) for st in by_ayah.values())

    async def final_phrases(self) -> list[str]:
        """The phrase texts for the stored result, cut exactly as an upload of this audio would be."""
        if not self.levels or not any(p.final for p in self.phrases):
            return []
        live = [(p.start // FRAME, p.end // FRAME, p.text) for p in self.phrases if p.final]
        spans = [(round(a / FRAME_SECONDS), round(b / FRAME_SECONDS)) for a, b in speech_spans(self.levels)]
        texts: list[str | None] = [
            next((t for la, lb, t in live if abs(la - fa) <= 5 and abs(lb - fb) <= 5), None) for fa, fb in spans]
        redo = [k for k, t in enumerate(texts) if t is None]
        if redo:
            t0 = time.monotonic()
            done = await asyncio.gather(*(self.transcriber.decode(self._audio(spans[k][0] * FRAME, spans[k][1] * FRAME))
                                          for k in redo))
            self.decode_seconds += time.monotonic() - t0
            self.redone = len(redo)
            for k, t in zip(redo, done):
                texts[k] = t
        return [t for t in texts if t]
