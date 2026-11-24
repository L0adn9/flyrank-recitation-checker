"""Speech to text. The real transcriber shells out to whisper.cpp running Tarteel's open Quran model
(tarteel-ai/whisper-base-ar-quran, Apache-2.0), fully local: no API, no cost, audio never leaves the machine."""
import subprocess
import tempfile
import time
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


class FakeTranscriber:
    """For tests: returns a fixed transcript (or raises) without running a model."""

    name = "fake"

    def __init__(self, text: str = "", fail_times: int = 0):
        self.text = text
        self.fail_times = fail_times
        self.calls = 0

    def transcribe(self, wav: Path) -> Transcript:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TranscriberError("simulated recognition failure")
        return Transcript(self.text, [self.text], 0.01)


_default: Transcriber | None = None


def get_transcriber() -> Transcriber:
    global _default
    if _default is None:
        _default = WhisperCpp()
    return _default


def set_transcriber(t: Transcriber | None) -> None:
    global _default
    _default = t
