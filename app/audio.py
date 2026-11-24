"""Audio handling with ffmpeg: decode any upload to 16 kHz mono WAV, and split it at the reciter's pauses.

Why split: whisper decodes 30-second windows. Decoding a whole surah in one pass merged words across
windows and dropped phrases in our tests, while decoding each pause-separated phrase on its own was
word-perfect. Reciters naturally pause at the end of each ayah, so pauses are good cut points.
Pauses are found from the recording's own loudness, not a fixed dB value, because recordings differ:
a phone in a quiet room, a laptop mic with a fan, a studio recitation with reverb.
"""
import math
import subprocess
import sys
import wave
from array import array
from pathlib import Path


class AudioError(Exception):
    """The file is not usable audio. Retrying will not help."""


def to_wav16k(src: Path, dst: Path) -> float:
    """Decode src to 16 kHz mono 16-bit WAV at dst. Returns the duration in seconds."""
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not dst.exists():
        raise AudioError("Could not read this recording. Please upload a normal audio file (webm, ogg, mp3, m4a or wav).")
    return duration(dst)


def duration(wav: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(wav)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise AudioError("Could not read the length of this recording.") from None


FRAME = 0.02  # seconds per energy frame


def frame_levels(wav: Path) -> list[float]:
    """Loudness of each 20 ms frame in dBFS, for a 16 kHz mono 16-bit WAV."""
    with wave.open(str(wav), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise AudioError("Expected 16-bit mono audio")
        n = int(w.getframerate() * FRAME)
        samples = array("h", w.readframes(w.getnframes()))
    if sys.byteorder == "big":
        samples.byteswap()
    levels = []
    for i in range(0, len(samples) - n + 1, n):
        chunk = samples[i:i + n]
        rms = math.sqrt(sum(x * x for x in chunk) / n)
        levels.append(20 * math.log10(rms / 32768) if rms > 0 else -100.0)
    return levels


def speech_spans(levels: list[float], min_pause: float = 0.45, min_speech: float = 0.3,
                 max_segment: float = 24.0) -> list[tuple[float, float]]:
    """(start, end) seconds of each phrase between pauses.

    The loudness threshold adapts to the recording: 12 dB above its own noise floor (the 10th percentile
    frame), so a quiet room, a noisy one, or a studio recording with reverb all work. A pause must last
    min_pause: long enough to skip a breath inside an ayah, so segments are whole ayahs, which is what
    the model was trained on (0.4-0.45 s scored 13/13 in scripts/eval.py; 0.35 s split ayahs at breaths
    and longer, multi-ayah segments lost words). A phrase longer than max_segment is cut at its quietest moment."""
    if not levels:
        return []
    floor = sorted(levels)[len(levels) // 10]
    peak = sorted(levels)[int(len(levels) * 0.95)]
    threshold = min(floor + 12, (floor + peak) / 2)
    quiet = [lv < threshold for lv in levels]
    min_pause_f, min_speech_f, max_f = int(min_pause / FRAME), int(min_speech / FRAME), int(max_segment / FRAME)

    spans: list[tuple[int, int]] = []
    start, run = None, 0
    for i, q in enumerate(quiet + [True] * (min_pause_f + 1)):
        if not q:
            if start is None:
                start = i
            run = 0
        else:
            run += 1
            if start is not None and run >= min_pause_f:
                end = i - run + 1
                if end - start >= min_speech_f:
                    spans.append((start, end))
                start = None

    pieces: list[tuple[int, int]] = []
    for a, b in spans:
        while b - a > max_f:
            lo, hi = a + max_f // 2, a + max_f          # cut somewhere in the second half of the window
            cut = min(range(lo, hi), key=lambda k: levels[k])
            pieces.append((a, cut))
            a = cut
        pieces.append((a, b))
    return [(a * FRAME, b * FRAME) for a, b in pieces]


def split_at_pauses(wav: Path, out_dir: Path) -> list[Path]:
    """Cut wav into one file per phrase, with a little padding on each side."""
    total = duration(wav)
    out = []
    for n, (a, b) in enumerate(speech_spans(frame_levels(wav))):
        seg = out_dir / f"seg{n:03d}.wav"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(wav),
             "-ss", f"{max(0.0, a - 0.15):.3f}", "-to", f"{min(total, b + 0.15):.3f}", str(seg)],
            check=True,
        )
        out.append(seg)
    return out
