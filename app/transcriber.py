"""Speech to text. The real transcriber shells out to whisper.cpp running Tarteel's open Quran model
(tarteel-ai/whisper-base-ar-quran, Apache-2.0), fully local: no API, no cost, audio never leaves the machine."""
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


class TranscriberError(Exception):
    """Speech recognition itself failed (missing binary or model, crash). Worth retrying."""


@dataclass
class Transcript:
    text: str
    segments: list[str] = field(default_factory=list)
    seconds: float = 0.0


class Transcriber(Protocol):
    name: str

    def transcribe(self, wav: Path) -> Transcript: ...

    async def decode(self, pcm: bytes, partial: bool = False) -> str:
        """One phrase of 16 kHz mono 16-bit PCM, for live checking. partial=True means the phrase is
        still being said: a quick first look that will be replaced once the phrase ends."""
        ...


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(pcm)


# Live checks from several people share the CPU: at most this many recognitions run at once.
_live_slots: asyncio.Semaphore | None = None


def _slots(threads: int) -> asyncio.Semaphore:
    global _live_slots
    if _live_slots is None:
        _live_slots = asyncio.Semaphore(max(1, (os.cpu_count() or 4) // threads))
    return _live_slots


class WhisperCpp:
    def __init__(self, cli: str | None = None, model: Path | None = None, threads: int | None = None):
        self.cli = cli or settings.whisper_cli
        self.model = model or settings.whisper_model
        self.threads = threads or settings.whisper_threads
        self.name = f"whisper.cpp/{self.model.name}"

    def _run(self, wav: Path) -> str:
        try:
            r = subprocess.run(
                [self.cli, "-m", str(self.model), "-f", str(wav), "-l", "ar", "-nt", "-np",
                 "-t", str(self.threads)],
                capture_output=True, text=True, timeout=120,
            )
        except FileNotFoundError:
            raise TranscriberError(f"whisper-cli not found at '{self.cli}' (set WHISPER_CLI)") from None
        except subprocess.TimeoutExpired:
            raise TranscriberError("speech recognition timed out") from None
        if r.returncode != 0:
            raise TranscriberError(f"whisper-cli exited with {r.returncode}: {r.stderr.strip()[-300:]}")
        return " ".join(r.stdout.split())

    def transcribe(self, wav: Path) -> Transcript:
        if not self.model.exists():
            raise TranscriberError(f"model file missing: {self.model} (run scripts/get_model.sh)")
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory() as td:
            segments = [self._run(seg) for seg in split_at_pauses(wav, Path(td))]
        segments = [s for s in segments if s]
        return Transcript(" ".join(segments), segments, time.monotonic() - t0)

    async def decode(self, pcm: bytes, partial: bool = False) -> str:
        if not self.model.exists():
            raise TranscriberError(f"model file missing: {self.model} (run scripts/get_model.sh)")
        args = [self.cli, "-m", str(self.model), "-l", "ar", "-nt", "-np", "-t", str(self.threads)]
        if partial:
            # Whisper always encodes a 30 s window. For a quick look at a short phrase, encoding only
            # the part that holds audio is 2-3x faster; the final pass uses the full window.
            seconds = len(pcm) / 32000
            args += ["-ac", str(min(1500, max(256, math.ceil(seconds / 30 * 1500) + 64)))]
        async with _slots(self.threads):
            with tempfile.TemporaryDirectory() as td:
                wav = Path(td) / "phrase.wav"
                write_wav(wav, pcm)
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *args, "-f", str(wav), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                except FileNotFoundError:
                    raise TranscriberError(f"whisper-cli not found at '{self.cli}' (set WHISPER_CLI)") from None
                try:
                    out, err = await asyncio.wait_for(proc.communicate(), 60)
                except TimeoutError:
                    proc.kill()
                    raise TranscriberError("speech recognition timed out") from None
        if proc.returncode != 0:
            raise TranscriberError(f"whisper-cli exited with {proc.returncode}: {err.decode(errors='replace').strip()[-300:]}")
        return " ".join(out.decode().split())


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

    def transcribe(self, wav: Path) -> Transcript:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TranscriberError("simulated recognition failure")
        return Transcript(self.text, [self.text], 0.01)

    async def decode(self, pcm: bytes, partial: bool = False) -> str:
        await asyncio.sleep(0)
        text = self.phrases[self.finals] if self.finals < len(self.phrases) else ""
        if partial:  # a phrase still being said: its first half so far
            self.partials += 1
            words = text.split()
            return " ".join(words[: max(1, len(words) // 2)])
        self.finals += 1
        return text


_default: Transcriber | None = None


def get_transcriber() -> Transcriber:
    global _default
    if _default is None:
        _default = WhisperCpp()
    return _default


def set_transcriber(t: Transcriber | None) -> None:
    global _default
    _default = t
