"""Audio handling with ffmpeg: decode any upload to 16 kHz mono WAV, and split it at the reciter's pauses.

Why split: whisper decodes 30-second windows. Decoding a whole surah in one pass merged words across
windows and dropped phrases in our tests, while decoding each pause-separated phrase on its own was
word-perfect. Reciters naturally pause at the end of each ayah, so pauses are good cut points.
"""
import re
import subprocess
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


def speech_spans(wav: Path, total: float, noise_db: int = -35, min_pause: float = 0.45,
                 min_speech: float = 0.35) -> list[tuple[float, float]]:
    """Return (start, end) of each stretch of speech between pauses, using ffmpeg's silencedetect."""
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(wav), "-af", f"silencedetect=n={noise_db}dB:d={min_pause}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    spans, t = [], 0.0
    for i, s in enumerate(starts):
        s = max(0.0, s)
        if s - t >= min_speech:
            spans.append((t, s))
        t = ends[i] if i < len(ends) else total
    if total - t >= min_speech:
        spans.append((t, total))
    return spans


def split_at_pauses(wav: Path, out_dir: Path, max_segment: float = 28.0) -> list[Path]:
    """Cut wav into one file per phrase (with a little padding). Phrases longer than max_segment
    seconds are cut further so every piece fits in one whisper window."""
    total = duration(wav)
    spans = speech_spans(wav, total)
    pieces: list[tuple[float, float]] = []
    for a, b in spans:
        while b - a > max_segment:
            pieces.append((a, a + max_segment))
            a += max_segment
        pieces.append((a, b))
    out = []
    for n, (a, b) in enumerate(pieces):
        seg = out_dir / f"seg{n:03d}.wav"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(wav),
             "-ss", f"{max(0.0, a - 0.15):.3f}", "-to", f"{min(total, b + 0.15):.3f}", str(seg)],
            check=True,
        )
        out.append(seg)
    return out
