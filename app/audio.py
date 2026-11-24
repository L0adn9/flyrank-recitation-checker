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


def recited_part(levels: list[float]) -> list[float]:
    """The frames from the first sound to the last (anything within 40 dB of the speech level).
    Silence before starting and after finishing says nothing about the pauses between ayahs, and a long
    stretch of it (pressing stop late) would otherwise pull the threshold down below them."""
    if len(levels) < 50:
        return levels
    loud = sorted(levels)[int(len(levels) * 0.95)] - 40
    first = next(i for i, lv in enumerate(levels) if lv >= loud)
    last = len(levels) - next(i for i, lv in enumerate(reversed(levels)) if lv >= loud)
    part = levels[first:last]
    return part if len(part) >= 50 else levels


def threshold_for(levels: list[float], trim: bool = True) -> float:
    """Loudness (dBFS) that separates speech from pause in this recording: 12 dB above its own noise floor
    (the 10th percentile frame of the recited part), or halfway between floor and speech level when that
    is lower. A recording with no loudness changes at all has no speech: then nothing is above it."""
    ordered = sorted(recited_part(levels) if trim else levels)
    floor = ordered[len(ordered) // 10]
    peak = ordered[int(len(ordered) * 0.95)]
    if peak - floor < 6:  # flat: silence or steady noise all the way through, no speech in it
        return peak + 1
    return min(floor + 12, (floor + peak) / 2)


def speech_level(levels: list[float]) -> float:
    """How loud the reciter's voice is: the 95th percentile frame of the recited part."""
    part = sorted(recited_part(levels))
    return part[int(len(part) * 0.95)] if part else -100.0


def _recover(levels: list[float], spans: list[tuple[int, int]], voice: float) -> list[tuple[int, int]]:
    """Speech quieter than the threshold: some reciters say the basmala 10 dB softer than the rest, and in a
    recording with short pauses the threshold ends up above it. Stretches outside the phrases that stay
    12 dB over the real noise floor (3rd percentile) for 1.2 s or more, and reach within 15 dB of the
    voice, are phrases too. (Measured on 150 recordings with known ayah boundaries: no ayah lost, instead
    of 4, for 12 more splits inside an ayah.)"""
    o = sorted(recited_part(levels))
    floor = o[len(o) * 3 // 100]
    inside = [False] * len(levels)
    for a, b in spans:
        for i in range(a, min(len(levels), b)):
            inside[i] = True
    out, start, gap = list(spans), None, 0
    for i in range(len(levels) + 20):
        loud = i < len(levels) and not inside[i] and levels[i] >= floor + 12
        if loud:
            start = i if start is None else start
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= 15 or (i < len(levels) and inside[i]):
                end = i - gap + 1
                if end - start >= 60 and max(levels[start:end]) >= voice - 15:
                    out.append((start, end))
                start, gap = None, 0
    return sorted(out)


def speech_spans(levels: list[float], min_pause: float = 0.45, min_speech: float = 0.3,
                 max_segment: float = 24.0, threshold: float | None = None,
                 voice: float | None = None, recover: bool = True) -> list[tuple[float, float]]:
    """(start, end) seconds of each phrase between pauses.

    The loudness threshold adapts to the recording (threshold_for), so a quiet room, a noisy one, or a
    studio recording with reverb all work. A pause must last min_pause: long enough to skip a breath
    inside an ayah, so segments are whole ayahs, which is what the model was trained on (0.4-0.45 s
    scored 13/13 in scripts/eval.py; 0.35 s split ayahs at breaths and longer, multi-ayah segments lost
    words). A phrase longer than max_segment is cut at its quietest moment. A "phrase" whose loudest
    moment stays 15 dB under the voice (speech_level, or `voice`) is a breath, a chair or a click, not
    words: it is dropped, because the speech model would otherwise make up words for it."""
    if not levels:
        return []
    if threshold is None:
        threshold = threshold_for(levels)
    if voice is None:
        voice = speech_level(levels)
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
                if end - start >= min_speech_f and max(levels[start:end]) >= voice - 15:
                    spans.append((start, end))
                start = None

    if recover:
        spans = _recover(levels, spans, voice)
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
