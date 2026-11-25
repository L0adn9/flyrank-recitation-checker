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
import atexit
import io
import math
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
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


def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


class _Server:
    """A whisper-server process with one model loaded, started on first use. Saves loading the model for
    every phrase, which is most of the time for short phrases."""

    def __init__(self, binary: str, model: Path, threads: int):
        self.binary, self.model, self.threads = binary, model, threads
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.lock = threading.Lock()   # one recognition at a time, as the server does anyway
        self.work = 0.0                 # how long a recognition takes here, smoothed (waiting not counted)

    def _start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        try:
            self.proc = subprocess.Popen(
                [self.binary, "-m", str(self.model), "-l", "ar", "-t", str(self.threads), "--host", "127.0.0.1",
                 "--port", str(self.port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            raise TranscriberError(f"whisper-server not found at '{self.binary}' (set WHISPER_SERVER)") from None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise TranscriberError(f"whisper-server exited with {self.proc.returncode} loading {self.model.name}")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=1).close()
                return
            except OSError:
                time.sleep(0.2)
        raise TranscriberError(f"whisper-server did not start for {self.model.name}")

    def infer(self, wav: bytes, prompt: str) -> str:
        self._start()
        boundary = uuid.uuid4().hex
        fields = {"response_format": "text", "language": "ar", "temperature": "0"}
        if prompt:
            fields["prompt"] = prompt
        body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                        for k, v in fields.items())
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="phrase.wav"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n").encode() + wav + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/inference", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with self.lock:
            t0 = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    text = " ".join(r.read().decode().split())
            except OSError as e:
                raise TranscriberError(f"whisper-server failed: {e}") from None
            took = time.monotonic() - t0
            self.work = took if not self.work else 0.6 * self.work + 0.4 * took
        return text

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()


class WhisperCpp:
    def __init__(self, cli: str | None = None, model: Path | None = None, fast_model: Path | None = None,
                 threads: int | None = None):
        self.cli = cli or settings.whisper_cli
        self.model = model or settings.whisper_model
        self.fast_model = fast_model or settings.whisper_fast_model
        self.threads = threads or settings.whisper_threads
        self.name = f"whisper.cpp/{self.model.name}+{self.fast_model.name}"
        self.servers: dict[Path, _Server] = {}
        if settings.whisper_server:
            self.servers = {m: _Server(settings.whisper_server, m, self.threads) for m in (self.model, self.fast_model)}
            atexit.register(self.close)

    def close(self) -> None:
        for server in self.servers.values():
            server.stop()

    def warm(self) -> None:
        """Start the resident servers now and run each once (loading the models and, on a GPU, compiling
        its shaders takes seconds), so the first phrase is as quick as the rest."""
        for server in self.servers.values():
            if server.proc is None or server.proc.poll() is not None:
                server._start()
                server.infer(wav_bytes(b"\0\0" * 16000), "")

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
        if model in self.servers:
            if not model.exists():
                raise TranscriberError(f"model file missing: {model} (run scripts/get_model.sh)")
            return await asyncio.to_thread(self.servers[model].infer, wav_bytes(pcm), prompt)
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

    async def look(self, pcm: bytes, expected: list[Word], heard: list[str]) -> str:
        """A quick look at the phrase being said. With resident servers (a GPU) this is a full decision, so the
        words are judged as they are said; otherwise the fast model alone, which only follows along."""
        if self.judges_while_reciting:
            return await self.decide(pcm, expected, heard, keep=False)
        return await self.decode(pcm, partial=True)

    @property
    def look_seconds(self) -> float:
        """How long a look with both models takes (they run side by side), queueing not counted."""
        return max((srv.work for srv in self.servers.values()), default=0.0)

    @property
    def judges_while_reciting(self) -> bool:
        """With resident servers (set WHISPER_SERVER only for a GPU build: there both models together take
        about 0.4 s), every look is a full decision. Without them, looks are the fast model alone."""
        return bool(self.servers)

    async def decide(self, pcm: bytes, expected: list[Word], heard: list[str], keep: bool = True) -> str:
        """A finished phrase: both models, the passage as context for the accurate one; keep the better fit."""
        from app.align import best_fit, prompt_for
        if self.servers:
            await asyncio.to_thread(self.warm)   # loading time is not counted
        hyps = await asyncio.gather(self._arun(pcm, self.model, prompt_for(expected, heard)),
                                    self._arun(pcm, self.fast_model))
        text = best_fit(expected, heard, list(hyps), len(pcm) / 32000 - 0.3)
        if not keep:   # a look at a phrase still being said: no phrase-level filtering yet
            return text
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

    async def decide(self, pcm: bytes, expected: list[Word], heard: list[str], keep: bool = True) -> str:
        return await self.decode(pcm)

    async def look(self, pcm: bytes, expected: list[Word], heard: list[str]) -> str:
        return await self.decode(pcm, partial=True)

    judges_while_reciting = False


_default: Transcriber | None = None


def get_transcriber() -> Transcriber:
    global _default
    if _default is None:
        _default = WhisperCpp()
    return _default


def set_transcriber(t: Transcriber | None) -> None:
    global _default
    _default = t
