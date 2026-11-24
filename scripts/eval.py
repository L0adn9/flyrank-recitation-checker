"""Run the real pipeline (whisper.cpp + aligner) on the sample recordings and check every finding.

    python scripts/make_samples.py && python scripts/eval.py

A case passes when the set of (ayah, finding) pairs equals what the recording actually contains.
"""
import json
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
from app.transcriber import WhisperCpp  # noqa: E402


def findings(result: dict) -> set[tuple[int, str]]:
    found = {(w["ayah"], w["status"]) for w in result["words"] if w["status"] != "correct"}
    found |= {(e["ayah"], e["kind"]) for e in result["extras"] if e["kind"] in ("repeat", "extra")}
    # A moved ayah shows as moved words *and* a moved extra; report it once, on the words.
    return found


def main() -> int:
    cases = json.loads((ROOT / "samples" / "cases.json").read_text())
    simple = {(s, a): t for s, a, t in load_text_file(TEXT_FILE)}
    uthmani = {(s, a): t for s, a, t in load_text_file(UTHMANI_FILE)}
    asr = WhisperCpp()
    rows, passed, audio_s, wall_s = [], 0, 0.0, 0.0
    for name, c in cases.items():
        ayahs = [(a, simple[(c["surah"], a)], uthmani[(c["surah"], a)]) for a in range(c["ayah_from"], c["ayah_to"] + 1)]
        expected = quran.expected_words(c["surah"], ayahs)
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "in.wav"
            secs = to_wav16k(ROOT / "samples" / c["file"], wav)
            t0 = time.monotonic()
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
    print("| Recording | Contains | Recite found | Accuracy | Time | |")
    print("|---|---|---|---|---|---|")
    print("\n".join(rows))
    print(f"\n{passed}/{len(cases)} cases pass. {audio_s:.0f} s of audio checked in {wall_s:.0f} s "
          f"({wall_s / audio_s:.2f}x real time) with {asr.name}.")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
