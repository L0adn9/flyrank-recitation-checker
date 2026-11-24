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
from app.transcriber import get_transcriber  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402


def recognise(asr, wav, expected):
    return asr.transcribe(wav, expected)


async def stream(expected, wav: Path, asr, speed: float) -> tuple[dict, list[float], float, int]:
    """Feed wav to a LiveSession in 100 ms chunks at speed x real time, like the browser does."""
    with wave.open(str(wav), "rb") as w:
        pcm = w.readframes(w.getnframes())
    fed_at: dict[int, float] = {}   # samples fed -> when
    lags: list[float] = []
    updates = 0

    class Timed:
        name = asr.name

        async def decode(self, audio: bytes, partial: bool = False, prompt: str = "") -> str:
            covered = session.samples
            text = await asr.decode(audio, partial, prompt)
            if partial:
                lags.append(time.monotonic() - fed_at[covered])
            return text

        async def decide(self, audio: bytes, expected, heard) -> str:
            return await asr.decide(audio, expected, heard)

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
    live_view = align(expected, session.heard())  # the marks on screen when the reciter finished
    text = " ".join(await session.final_phrases())
    wait = time.monotonic() - t_end
    result = align(expected, quran.clean_heard(quran.norm_words(text), expected))
    return result, lags, wait, live_view


def findings(result: dict) -> set[tuple[int, str]]:
    """The (ayah, kind) pairs a result reports. A moved ayah shows as moved words *and* a moved extra:
    reported once, on the words. Likewise an extra word in an ayah said wrong is part of that slip (a
    different ayah in its place, one word longer), not a second finding."""
    found = {(w["ayah"], w["status"]) for w in result["words"] if w["status"] != "correct"}
    wrong = {a for a, k in found if k == "wrong"}
    found |= {(e["ayah"], e["kind"]) for e in result["extras"]
              if e["kind"] == "repeat" or (e["kind"] == "extra" and e["ayah"] not in wrong)}
    return found


def check_case(name: str, c: dict, args, asr, simple, uthmani) -> dict:
    ayahs = [(a, simple[(c["surah"], a)], uthmani[(c["surah"], a)]) for a in range(c["ayah_from"], c["ayah_to"] + 1)]
    expected = quran.expected_words(c["surah"], ayahs)
    out = {"name": name, "reciter": c.get("reciter", ""), "want": {tuple(x) for x in c["expect"]}}
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "in.wav"
        out["secs"] = to_wav16k(ROOT / "samples" / c["file"], wav)
        t0 = time.monotonic()
        if args.live:
            result, out["lags"], out["took"], live_view = asyncio.run(stream(expected, wav, asr, args.speed))
            out["live_got"] = findings(live_view)
        else:
            t = recognise(asr, wav, expected)
            out["took"] = time.monotonic() - t0
            out["segments"] = t.segments
            result = align(expected, quran.clean_heard(quran.norm_words(t.text), expected))
    out["got"] = findings(result)
    out["accuracy"] = result["accuracy"]
    out["ok"] = out["got"] == out["want"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="stream through the live checker")
    ap.add_argument("--speed", type=float, default=1.0, help="with --live: feed audio this many times faster")
    ap.add_argument("--reciters", help="comma-separated: only these reciters")
    ap.add_argument("--jobs", type=int, default=2, help="cases checked at the same time")
    ap.add_argument("--dump", help="write every case's transcript (JSON) to this file")
    args = ap.parse_args()
    cases = json.loads((ROOT / "samples" / "cases.json").read_text())
    if args.reciters:
        keep = set(args.reciters.split(","))
        cases = {k: v for k, v in cases.items() if v.get("reciter") in keep}
    simple = {(s, a): t for s, a, t in load_text_file(TEXT_FILE)}
    uthmani = {(s, a): t for s, a, t in load_text_file(UTHMANI_FILE)}
    asr = get_transcriber()
    with ThreadPoolExecutor(args.jobs) as pool:
        res = list(pool.map(lambda kv: check_case(kv[0], kv[1], args, asr, simple, uthmani), cases.items()))

    if args.dump:
        Path(args.dump).write_text(json.dumps([{k: (sorted(v) if isinstance(v, set) else v) for k, v in r.items()}
                                               for r in res], ensure_ascii=False, indent=1))
    fmt = lambda s: ", ".join(f"{k} in {a}" for a, k in sorted(s)) or "none"  # noqa: E731
    print(f"| Recording | Contains | Recite found | Accuracy | {'Wait after last word' if args.live else 'Time'} | |")
    print("|---|---|---|---|---|---|")
    for r in res:
        print(f"| {r['name']} | {fmt(r['want'])} | {fmt(r['got'])} | {r['accuracy']:.0%} | {r['took']:.1f} s | {'pass' if r['ok'] else 'FAIL'} |")
    print("\n| Reciter | Cases passed |\n|---|---|")
    by: dict[str, list] = {}
    for r in res:
        by.setdefault(r["reciter"], []).append(r["ok"])
    for rec, oks in by.items():
        print(f"| {rec} | {sum(oks)}/{len(oks)} |")
    passed = sum(r["ok"] for r in res)
    audio_s, wall_s = sum(r["secs"] for r in res), sum(r["took"] for r in res)
    if args.live:
        lags = [x for r in res for x in r["lags"]]
        waits = [r["took"] for r in res]
        off = [f"{r['name']} (showed {fmt(r['live_got'])})" for r in res if r["live_got"] != r["want"]]
        q = statistics.quantiles(lags, n=10)
        if off:
            print("\nLive marks that the final result corrected: " + "; ".join(off))
        print(f"\n{passed}/{len(res)} cases pass, streamed live at {args.speed:g}x real time with {asr.name}. "
              f"The marks shown at the end of reciting were already right in {len(res) - len(off)}/{len(res)}. "
              f"Marks trail the reciter by {statistics.median(lags):.2f} s (median; 90% within {q[8]:.2f} s) "
              f"over {len(lags)} updates. After the last word: {statistics.median(waits):.2f} s median, "
              f"{max(waits):.2f} s max.")
    else:
        print(f"\n{passed}/{len(res)} cases pass. {audio_s:.0f} s of audio checked in {wall_s:.0f} s of recognition "
              f"({wall_s / audio_s:.2f}x real time) with {asr.name}.")
    return 0 if passed == len(res) else 1


if __name__ == "__main__":
    raise SystemExit(main())
