"""Speech to text. The real transcriber shells out to whisper.cpp, fully local: no API, no cost, audio never
leaves the machine. Two open Quran models:

- WHISPER_MODEL: Habib-HF/tarbiyah-ai-v1-1, Whisper small fine-tuned on recitations (MIT), given the
  passage text around the reciter's place as context (prompt_for).
- WHISPER_FAST_MODEL: tarteel-ai/whisper-base-ar-quran (Apache-2.0), about 5x quicker.

Each finished phrase goes through both, and the transcript that fits the passage better is kept
(best_fit). Measured, neither wins everywhere: the small model is far better on a non-professional
voice on a laptop mic, the base model on professional studio recitations. A real mistake is heard by
both, so picking the better fit removes each model's misreadings without hiding the reciter's. Live, the
fast model alone follows along inside the phrase being said, and never marks a word wrong.
"""
import asyncio
import math
import os
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.audio import split_at_pauses
from app.config import settings
from app.quran import Word, keep_phrase, norm_words, plausible


class TranscriberError(Exception):
    """Speech recognition itself failed (missing binary or model, crash). Worth retrying."""


@dataclass
class Transcript:
    text: str
    segments: list[str] = field(default_factory=list)
    seconds: float = 0.0


class Transcriber(Protocol):
    name: str

    def transcribe(self, wav: Path, expected: list[Word] | None = None) -> Transcript:
        """A whole recording, cut at pauses. expected: the passage, used as context."""
        ...

    async def decode(self, pcm: bytes, partial: bool = False, prompt: str = "") -> str:
        """One phrase of 16 kHz mono 16-bit PCM, for live checking. partial=True means the phrase is
        still being said: a quick first look (fast model) that will be replaced once the phrase ends."""
        ...

    async def decide(self, pcm: bytes, expected: list[Word], heard: list[str]) -> str:
        """A finished phrase, for the result: see WhisperCpp.decide."""
        ...


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(pcm)


# Live checks from several people share the CPU: at most this many full recognitions run at once. The
# quick looks that make words appear while reciting have a lane of their own, so they never wait behind
# a phrase being decided.
_slots_full: asyncio.Semaphore | None = None
_slots_quick: asyncio.Semaphore | None = None


def _slots(threads: int, quick: bool) -> asyncio.Semaphore:
    global _slots_full, _slots_quick
    if _slots_full is None:
        _slots_full = asyncio.Semaphore(max(2, (os.cpu_count() or 4) // threads))
        _slots_quick = asyncio.Semaphore(max(1, (os.cpu_count() or 4) // threads // 2))
    return _slots_quick if quick else _slots_full


class WhisperCpp:
    def __init__(self, cli: str | None = None, model: Path | None = None, fast_model: Path | None = None,
                 threads: int | None = None):
        self.cli = cli or settings.whisper_cli
        self.model = model or settings.whisper_model
        self.fast_model = fast_model or settings.whisper_fast_model
        self.threads = threads or settings.whisper_threads
        self.name = f"whisper.cpp/{self.model.name}+{self.fast_model.name}"

    def _args(self, wav: Path, model: Path, prompt: str = "", quick: float | None = None) -> list[str]:
        if not model.exists():
            raise TranscriberError(f"model file missing: {model} (run scripts/get_model.sh)")
        threads = max(2, self.threads // 2) if quick is not None else self.threads   # leave room for decisions
        args = [self.cli, "-m", str(model), "-f", str(wav), "-l", "ar", "-nt", "-np", "-t", str(threads)]
        if quick is not None:
            # Whisper always encodes a 30 s window. For a quick look at a short phrase, encoding only the
            # part that holds audio is 2-3x faster (fine for the base model; final passes use the full window).
            args += ["-ac", str(min(1500, max(256, math.ceil(quick / 30 * 1500) + 64))), "-bs", "1", "-bo", "1"]
        if prompt:
            args += ["--prompt", prompt]
        return args

    def _check(self, returncode: int, stderr: str) -> None:
        if returncode != 0:
            raise TranscriberError(f"whisper-cli exited with {returncode}: {stderr.strip()[-300:]}")

    def _run(self, wav: Path, model: Path, prompt: str = "") -> str:
        try:
            r = subprocess.run(self._args(wav, model, prompt), capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            raise TranscriberError(f"whisper-cli not found at '{self.cli}' (set WHISPER_CLI)") from None
        except subprocess.TimeoutExpired:
            raise TranscriberError("speech recognition timed out") from None
        self._check(r.returncode, r.stderr)
        return " ".join(r.stdout.split())

    def transcribe(self, wav: Path, expected: list[Word] | None = None) -> Transcript:
        from app.align import best_fit, prompt_for  # app.align imports app.quran only
        t0 = time.monotonic()
        segments: list[str] = []
        with tempfile.TemporaryDirectory() as td:
            for seg in split_at_pauses(wav, Path(td)):
                heard = norm_words(" ".join(segments))
                with wave.open(str(seg)) as w:
                    seconds = w.getnframes() / w.getframerate() - 0.3   # without the padding
                if expected:
                    hyps = [self._run(seg, self.model, prompt_for(expected, heard)), self._run(seg, self.fast_model)]
                    text = best_fit(expected, heard, hyps, seconds)
                    if keep_phrase(text, seconds, heard, expected):
                        segments.append(text)
                else:
                    text = self._run(seg, self.model)
                    if text and plausible(text, seconds):
                        segments.append(text)
        return Transcript(" ".join(segments), segments, time.monotonic() - t0)

    async def _arun(self, pcm: bytes, model: Path, prompt: str = "", quick: bool = False) -> str:
        async with _slots(self.threads, quick):
            with tempfile.TemporaryDirectory() as td:
                wav = Path(td) / "phrase.wav"
                write_wav(wav, pcm)
                args = self._args(wav, model, prompt, len(pcm) / 32000 if quick else None)
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                except FileNotFoundError:
                    raise TranscriberError(f"whisper-cli not found at '{self.cli}' (set WHISPER_CLI)") from None
                try:
                    out, err = await asyncio.wait_for(proc.communicate(), 60)
                except TimeoutError:
                    proc.kill()
                    raise TranscriberError("speech recognition timed out") from None
        self._check(proc.returncode, err.decode(errors="replace"))
        return " ".join(out.decode().split())

    async def decode(self, pcm: bytes, partial: bool = False, prompt: str = "") -> str:
        if partial:
            return await self._arun(pcm, self.fast_model, quick=True)
        return await self._arun(pcm, self.model, prompt)

    async def decide(self, pcm: bytes, expected: list[Word], heard: list[str]) -> str:
        """A finished phrase: both models, the passage as context for the accurate one; keep the better fit."""
        from app.align import best_fit, prompt_for
        hyps = await asyncio.gather(self._arun(pcm, self.model, prompt_for(expected, heard)),
                                    self._arun(pcm, self.fast_model))
        text = best_fit(expected, heard, list(hyps), len(pcm) / 32000 - 0.3)
        return text if keep_phrase(text, len(pcm) / 32000 - 0.3, heard, expected) else ""


class FakeTranscriber:
    """For tests: returns a fixed transcript (or raises) without running a model."""

    name = "fake"

    def __init__(self, text: str = "", fail_times: int = 0, phrases: list[str] | None = None):
        self.text = text
        self.fail_times = fail_times
        self.calls = 0
        self.phrases = phrases or []  # live: what each phrase "says", in order
        self.finals = 0
        self.partials = 0

    def transcribe(self, wav: Path, expected: list[Word] | None = None) -> Transcript:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TranscriberError("simulated recognition failure")
        return Transcript(self.text, [self.text], 0.01)

    async def decode(self, pcm: bytes, partial: bool = False, prompt: str = "") -> str:
        await asyncio.sleep(0)
        text = self.phrases[self.finals] if self.finals < len(self.phrases) else ""
        if partial:  # a phrase still being said: its first half so far
            self.partials += 1
            words = text.split()
            return " ".join(words[: max(1, len(words) // 2)])
        self.finals += 1
        return text

    async def decide(self, pcm: bytes, expected: list[Word], heard: list[str]) -> str:
        return await self.decode(pcm)


_default: Transcriber | None = None


def get_transcriber() -> Transcriber:
    global _default
    if _default is None:
        _default = WhisperCpp()
    return _default


def set_transcriber(t: Transcriber | None) -> None:
    global _default
    _default = t
