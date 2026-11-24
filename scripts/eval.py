"""Run the real pipeline (whisper.cpp + aligner) on the sample recordings and check every finding.

    python scripts/make_samples.py && python scripts/eval.py
    python scripts/eval.py --live      # stream each recording in real time through the live checker

A case passes when the set of (ayah, finding) pairs equals what the recording actually contains.
With --live it also measures how far behind the reciter the marks are while reciting (the age of the
newest audio a progress update covers, when it reaches the page) and the wait after the last word.
"""
import argparse
import asyncio
import json
import statistics
import wave
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import quran  # noqa: E402
from app.align import align  # noqa: E402
from app.audio import duration, to_wav16k  # noqa: E402
from app.quran import TEXT_FILE, UTHMANI_FILE, load_text_file  # noqa: E402
from app.live import RATE, LiveSession  # noqa: E402
from app.transcriber import WhisperCpp  # noqa: E402


async def stream(expected, wav: Path, asr, speed: float) -> tuple[dict, list[float], float, int]:
    """Feed wav to a LiveSession in 100 ms chunks at speed x real time, like the browser does."""
    with wave.open(str(wav), "rb") as w:
        pcm = w.readframes(w.getnframes())
    fed_at: dict[int, float] = {}   # samples fed -> when
    lags: list[float] = []
    updates = 0

    class Timed:
        name = asr.name

        async def decode(self, audio: bytes, partial: bool = False) -> str:
            covered = session.samples
            text = await asr.decode(audio, partial)
            if partial:
                lags.append(time.monotonic() - fed_at[covered])
            return text

    async def notify(msg: dict) -> None:
        nonlocal updates
        updates += msg["type"] == "progress"

    session = LiveSession(expected, Timed(), notify)
    runner = asyncio.create_task(session.run())
    chunk = RATE // 10 * 2
    t0 = time.monotonic()
    for k in range(0, len(pcm), chunk):
        session.feed(pcm[k:k + chunk])
        fed_at[session.samples] = time.monotonic()
        await asyncio.sleep(max(0.0, t0 + (k + chunk) / 2 / RATE / speed - time.monotonic()))
    t_end = time.monotonic()
    session.finish()
    await runner
    live_view = align(expected, session.heard())
    text = " ".join(await session.final_phrases())
    wait = time.monotonic() - t_end
    result = align(expected, quran.clean_heard(quran.norm_words(text), expected))
    return result, lags, wait, live_view


def findings(result: dict) -> set[tuple[int, str]]:
    found = {(w["ayah"], w["status"]) for w in result["words"] if w["status"] != "correct"}
    found |= {(e["ayah"], e["kind"]) for e in result["extras"] if e["kind"] in ("repeat", "extra")}
    # A moved ayah shows as moved words *and* a moved extra; report it once, on the words.
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="stream through the live checker")
    ap.add_argument("--speed", type=float, default=1.0, help="with --live: feed audio this many times faster")
    args = ap.parse_args()
    cases = json.loads((ROOT / "samples" / "cases.json").read_text())
    simple = {(s, a): t for s, a, t in load_text_file(TEXT_FILE)}
    uthmani = {(s, a): t for s, a, t in load_text_file(UTHMANI_FILE)}
    asr = WhisperCpp()
    rows, passed, audio_s, wall_s = [], 0, 0.0, 0.0
    all_lags: list[float] = []
    waits: list[float] = []
    live_ok = 0
    live_off: list[str] = []
    for name, c in cases.items():
        ayahs = [(a, simple[(c["surah"], a)], uthmani[(c["surah"], a)]) for a in range(c["ayah_from"], c["ayah_to"] + 1)]
        expected = quran.expected_words(c["surah"], ayahs)
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "in.wav"
            secs = to_wav16k(ROOT / "samples" / c["file"], wav)
            t0 = time.monotonic()
            if args.live:
                result, lags, took, live_view = asyncio.run(stream(expected, wav, asr, args.speed))
                all_lags += lags
                waits.append(took)
                if findings(live_view) == {tuple(x) for x in c["expect"]}:
                    live_ok += 1
                else:
                    live_off.append(f"{name} (showed {', '.join(f'{k} in {a}' for a, k in sorted(findings(live_view)))})")
            else:
                t = asr.transcribe(wav)
                took = time.monotonic() - t0
                result = align(expected, quran.clean_heard(quran.norm_words(t.text), expected))
        got = findings(result)
        want = {tuple(x) for x in c["expect"]}
        ok = got == want
        passed += ok
        audio_s += secs
        wall_s += took
        fmt = lambda s: ", ".join(f"{k} in {a}" for a, k in sorted(s)) or "none"  # noqa: E731
        rows.append(f"| {name} | {fmt(want)} | {fmt(got)} | {result['accuracy']:.0%} | {took:.1f} s | {'pass' if ok else 'FAIL'} |")
    print(f"| Recording | Contains | Recite found | Accuracy | {'Wait after last word' if args.live else 'Time'} | |")
    print("|---|---|---|---|---|---|")
    print("\n".join(rows))
    if args.live:
        q = statistics.quantiles(all_lags, n=10)
        if live_off:
            print("\nLive marks that the final result corrected: " + "; ".join(live_off))
        print(f"\n{passed}/{len(cases)} cases pass, streamed live at {args.speed:g}x real time with {asr.name}. "
              f"The marks shown at the end of reciting were already right in {live_ok}/{len(cases)}. "
              f"Marks trail the reciter by {statistics.median(all_lags):.2f} s (median; 90% within {q[8]:.2f} s) "
              f"over {len(all_lags)} updates. After the last word: {statistics.median(waits):.2f} s median, "
              f"{max(waits):.2f} s max.")
    else:
        print(f"\n{passed}/{len(cases)} cases pass. {audio_s:.0f} s of audio checked in {wall_s:.0f} s "
              f"({wall_s / audio_s:.2f}x real time) with {asr.name}.")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
