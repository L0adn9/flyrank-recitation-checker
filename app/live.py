"""Live checking: words are marked while the person recites, not after.

The browser streams microphone audio (16 kHz mono 16-bit PCM, in ~100 ms chunks) over a WebSocket.
A LiveSession cuts that stream into phrases at the reciter's pauses as it arrives, using the same rule
as uploaded recordings (app/audio.py). It can only look backwards, so the loudness threshold is
estimated from the audio so far; when a phrase ends, it is checked once more for pauses with the
threshold known by then, which matters in the first seconds.

- While a phrase is being said, it is looked at again and again, back to back (transcriber.look). With
  the models resident and fast enough (a GPU), each look is a full decision by both models, and the
  words are judged as they are said; on a slower machine the fast model alone follows along, and words
  are judged when the reciter pauses. Either way the page only ever moves forward (progress).
- When the reciter pauses, the phrase is closed and decided: both models, the better fit kept
  (app/transcriber.py, WhisperCpp.decide). Only this final text marks words wrong or missed, and it never
  changes again, so marks do not flicker. The two run side by side, so the words of the next phrase
  keep appearing while the last one is being judged.

After every pass the client gets each word's status: correct / wrong / missed / moved from the final
texts (aligned with the start of the passage, align prefix=True), then the words just confirmed by the
quick look, 'current' for the word being said, 'pending' for the rest.

On stop, the result is made from the live phrases, plus any speech the live pause detection missed and
the whole recording shows (final_phrases), so it is there about as soon as the last phrase is decided.
"""
import asyncio
import logging
import math
import sys
import time
from array import array
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app import quran
from app.align import align
from app.audio import FRAME as FRAME_SECONDS, recited_part, speech_level, speech_spans, threshold_for
from app.transcriber import Transcriber

log = logging.getLogger("recite.live")

RATE = 16000
FRAME = 320                 # samples per 20 ms loudness frame
PAD = int(0.15 * RATE)      # audio kept on each side of a phrase
MIN_PAUSE = 0.4             # seconds of quiet that end a phrase (uploads use 0.45; both scored 13/13 there,
                            # and live it is better to cut once too often than to merge two ayahs)
MIN_SPEECH = 0.3            # shorter sounds are clicks or breaths, not words
MIN_WORDS = 0.5             # live, phrases shorter than this are not sent to the models
MAX_PHRASE = 24.0           # longer phrases are cut at their quietest moment
PARTIAL_START = 0.5         # a phrase gets its first partial pass after this much audio
PARTIAL_EVERY = 0.2         # and another one whenever this much new audio has arrived (back to back,
                            # in practice: a look takes about as long)
PRIOR = [-60.0] * 15        # assumed quiet frames, so the first seconds are not judged on speech alone


@dataclass
class Phrase:
    start: int              # sample where the phrase starts
    end: int | None = None  # sample where it ends; None while it is still being said
    text: str = ""          # the accurate model's text, once final
    final: bool = False
    partial: str = ""       # the fast model's latest look while it is being said
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
        self._shown: dict[int, dict] = {}   # what the page was last sent for each word it has shown
        self._next = 0                      # the place the page was last sent (it never moves back)
        self._wake_final = asyncio.Event()
        self._wake_partial = asyncio.Event()

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
        self._wake()

    def _wake(self) -> None:
        self._wake_final.set()
        self._wake_partial.set()

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
                              max_segment=MAX_PHRASE, threshold=self.threshold, voice=speech_level(self.levels),
                              recover=False)
        new = [Phrase(start=p.start + round(a / FRAME_SECONDS) * FRAME, end=p.start + round(b / FRAME_SECONDS) * FRAME)
               for a, b in pieces]
        if new:
            new[0].partial = p.partial  # what was heard so far, until the final pass replaces it
        self.phrases[i:i + 1] = new
        log.info("phrase %.2f-%.2f s closed: %d piece(s)", p.start / RATE, end / RATE, len(new))

    def finish(self) -> None:
        """No more audio: the phrase being said (if any) ends here."""
        if self.open is not None:
            self._close(self.samples)
        self.ended = True
        self._wake()

    # ---------- recognition ----------
    def _audio(self, start: int, end: int) -> bytes:
        return bytes(self.pcm[max(0, start - PAD) * 2: min(self.samples, end + PAD) * 2])

    async def run(self) -> None:
        """Recognise phrases until finish() and the last phrase is final. Sends a progress message after
        each pass. Raises TranscriberError if recognition fails."""
        await asyncio.gather(self._finals(), self._partials())

    async def _finals(self) -> None:
        """Closed phrases, in order, by the accurate model with the passage as context."""
        while True:
            p = next((p for p in self.phrases if p.end is not None and not p.final), None)
            if p is None:
                if self.ended and self.open is None:
                    return
                self._wake_final.clear()
                await self._wake_final.wait()
                continue
            t0 = time.monotonic()
            if p.end - p.start < MIN_WORDS * RATE:   # a click or a breath: not worth holding up the queue
                p.final = True
                continue
            text = await self.transcriber.decide(self._audio(p.start, p.end), self.expected, self.heard())
            self.decode_seconds += time.monotonic() - t0
            p.text, p.final = text, True
            log.info("phrase %.2f-%.2f s decided in %.1f s: %s", p.start / RATE, p.end / RATE,
                     time.monotonic() - t0, text)
            await self.notify(self.progress())
            if not self.complete_sent and self.recited_all():
                self.complete_sent = True
                log.info("whole passage recited at %.1f s", self.seconds)
                await self.notify({"type": "complete"})

    async def _partials(self) -> None:
        """Quick looks at the phrase being said, by the fast model."""
        while not self.ended:
            p = self.open
            if p is None or self.samples - p.start < PARTIAL_START * RATE \
                    or self.samples - p.partial_at < PARTIAL_EVERY * RATE:
                self._wake_partial.clear()
                await self._wake_partial.wait()
                continue
            upto = self.samples
            p.partial = await self.transcriber.look(self._audio(p.start, upto), self.expected, self.heard())
            p.partial_at = upto
            if not p.final:
                await self.notify(self.progress())

    # ---------- results ----------
    def heard(self) -> list[str]:
        """What the accurate model heard so far (final phrases only), normalised."""
        text = " ".join(p.text for p in self.phrases if p.final and p.text)
        return quran.clean_heard(quran.norm_words(text), self.expected)

    def progress(self) -> dict:
        """Each expected word's status so far, from the decided phrases and the looks at the phrase being said.

        When the looks are full decisions (both models, fast enough: transcriber.judges_while_reciting), the
        words are judged as they are said: correct, wrong, missed. Only the word being said is held back
        ('current'): it may be cut off mid-word. Otherwise the looks only follow along: words heard said are
        shown, and right or wrong is decided when the reciter pauses.

        The page never goes backwards: a word it has shown is not taken away and the place never moves
        back, even when a later decision reads the audio differently (a shown word can still change mark)."""
        r = align(self.expected, self.heard(), prefix=True)
        words = r["words"]
        at = r["reached"] + 1
        looking = [p for p in self.phrases if not p.final and p.partial]
        if looking and at < len(self.expected):
            quick = quran.norm_words(" ".join(p.partial for p in looking))
            if self.transcriber.judges_while_reciting:
                finals = " ".join(p.text for p in self.phrases if p.final and p.text)
                full = align(self.expected, quran.clean_heard(quran.norm_words(finals) + quick, self.expected),
                             prefix=True)
                last = full["reached"]
                for k in range(at, last + 1):
                    words[k] = full["words"][k]
                if last >= at and words[last]["status"] == "wrong":
                    words[last]["status"] = "current"   # still being said: may be cut off mid-word
                at = max(at, last + 1 if words[last]["status"] != "current" else last)
            else:
                ahead = align(self.expected[at:], quick, prefix=True)
                for w, q in zip(words[at: at + ahead["reached"] + 1], ahead["words"]):
                    if q["status"] in ("correct", "wrong"):   # said (maybe misheard): show it now
                        w["status"] = "correct"
                    # skipped or out of order: stays open until the phrase is decided, never shown as said
                at += ahead["reached"] + 1
        # Never backwards.
        for k, w in enumerate(words):
            if w["status"] in ("pending", "current") and k in self._shown:
                words[k] = dict(self._shown[k])
        at = max(at, self._next)
        while at < len(words) and at in self._shown:
            at += 1
        if at < len(words) and any(not p.final for p in self.phrases):
            words[at]["status"] = "current"
        self._next = at
        for k, w in enumerate(words):
            if w["status"] not in ("pending", "current"):
                self._shown[k] = {"status": w["status"], **({"heard": w["heard"]} if "heard" in w else {})}
        return {
            "type": "progress",
            "words": [{"status": w["status"], **({"heard": w["heard"]} if "heard" in w else {})} for w in words],
            "next": at,
            "seconds": round(self.seconds, 1),
        }

    def recited_all(self) -> bool:
        """The final texts reach the last word of the passage, said correctly, and no ayah was skipped
        entirely (if one was, the reciter may still come back to it)."""
        if not self.expected:
            return False
        r = align(self.expected, self.heard(), prefix=True)
        if r["reached"] != len(self.expected) - 1 or r["words"][-1]["status"] != "correct":
            return False
        by_ayah: dict[int, list[str]] = {}
        for w in r["words"]:
            by_ayah.setdefault(w["ayah"], []).append(w["status"])
        return not any(all(s == "missed" for s in st) for st in by_ayah.values())

    async def final_phrases(self) -> list[str]:
        """The phrase texts for the stored result: the live phrases, plus any speech they missed. With the
        whole recording known, pause detection can find speech the live threshold passed over (a basmala
        said much softer than the rest); stretches no live phrase covers are recognised now. Phrases that
        were only cut a little differently are not redone: measured, that changed no result and made
        people wait several seconds after their last word."""
        if not self.levels or not any(p.final for p in self.phrases):
            return []
        pieces = [(p.start // FRAME, p.end // FRAME, p.text) for p in self.phrases if p.final]
        for a, b in speech_spans(self.levels):
            fa, fb = round(a / FRAME_SECONDS), round(b / FRAME_SECONDS)
            if any(min(fb, lb) - max(fa, la) > 0 for la, lb, _ in pieces):
                continue
            before = [t for la, _, t in sorted(pieces) if la < fa]
            t0 = time.monotonic()
            text = await self.transcriber.decide(self._audio(fa * FRAME, fb * FRAME), self.expected,
                                                 quran.norm_words(" ".join(before)))
            self.decode_seconds += time.monotonic() - t0
            self.redone += 1
            pieces.append((fa, fb, text))
        return [t for _, _, t in sorted(pieces) if t]
