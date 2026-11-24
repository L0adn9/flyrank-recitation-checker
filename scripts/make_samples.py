"""Build test recordings with known mistakes from public reference recitations.

Downloads Mishary Alafasy's per-ayah recordings from EveryAyah (everyayah.com) and cuts them into
recitations with a real slip in them: an ayah skipped, two ayahs swapped, an ayah repeated, a wrong
ayah said in place of the right one, stopping early. Output: samples/*.wav plus samples/cases.json,
which scripts/eval.py uses. Recordings are not committed to Git; run this to recreate them.

    python scripts/make_samples.py
"""
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"
AYAHS = OUT / "ayahs"
SOURCE = "https://everyayah.com/data/Alafasy_128kbps/{s:03d}{a:03d}.mp3"
UA = {"User-Agent": "recite-capstone-samples/1.0"}

# name: (surah, ayah_from, ayah_to that the reciter *meant*, what was actually said as [(surah, ayah)],
#        expected findings as [ayah, kind])
CASES = {
    "al-fil": (105, 1, 5, [(105, 1), (105, 2), (105, 3), (105, 4), (105, 5)], []),
    "al-fil-skipped-3": (105, 1, 5, [(105, 1), (105, 2), (105, 4), (105, 5)], [[3, "missed"]]),
    "al-fil-swapped-3-4": (105, 1, 5, [(105, 1), (105, 2), (105, 4), (105, 3), (105, 5)], [[4, "moved"]]),
    "al-fil-repeated-2": (105, 1, 5, [(105, 1), (105, 2), (105, 2), (105, 3), (105, 4), (105, 5)], [[2, "repeat"]]),
    "al-fil-wrong-ayah-3": (105, 1, 5, [(105, 1), (105, 2), (106, 3), (105, 4), (105, 5)], [[3, "wrong"]]),
    "al-fil-stopped-early": (105, 1, 5, [(105, 1), (105, 2), (105, 3)], [[4, "missed"], [5, "missed"]]),
    "quraysh": (106, 1, 4, [(106, 1), (106, 2), (106, 3), (106, 4)], []),
    "quraysh-skipped-2": (106, 1, 4, [(106, 1), (106, 3), (106, 4)], [[2, "missed"]]),
    "al-fatiha": (1, 1, 7, [(1, a) for a in range(1, 8)], []),
    "al-fatiha-repeated-3": (1, 1, 7, [(1, 1), (1, 2), (1, 3), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7)], [[3, "repeat"]]),
    "al-fatiha-swapped-5-6": (1, 1, 7, [(1, 1), (1, 2), (1, 3), (1, 4), (1, 6), (1, 5), (1, 7)], [[6, "moved"]]),
    "al-ikhlas": (112, 1, 4, [(112, a) for a in range(1, 5)], []),
    "al-ikhlas-skipped-3": (112, 1, 4, [(112, 1), (112, 2), (112, 4)], [[3, "missed"]]),
}


def fetch(s: int, a: int) -> Path:
    path = AYAHS / f"{s:03d}{a:03d}.mp3"
    if not path.exists():
        req = urllib.request.Request(SOURCE.format(s=s, a=a), headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            path.write_bytes(r.read())
    return path


def build(name: str, parts: list[tuple[int, int]]) -> Path:
    out = OUT / f"{name}.wav"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as lst:
        for s, a in parts:
            lst.write(f"file '{fetch(s, a)}'\n")
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst.name,
                    "-ar", "16000", "-ac", "1", str(out)], check=True)
    Path(lst.name).unlink()
    return out


if __name__ == "__main__":
    AYAHS.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, (surah, a, b, parts, expect) in CASES.items():
        path = build(name, parts)
        manifest[name] = {"file": path.name, "surah": surah, "ayah_from": a, "ayah_to": b, "expect": expect}
        print(f"{path.relative_to(ROOT)}")
    (OUT / "cases.json").write_text(json.dumps(manifest, indent=1))
    print(f"{len(manifest)} recordings in samples/ (source: EveryAyah, Mishary Alafasy)")
