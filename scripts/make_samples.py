"""Build test recordings with known mistakes from public reference recitations, by many reciters.

Downloads per-ayah recordings from EveryAyah (everyayah.com) and cuts them into recitations with a
real slip in them: an ayah skipped, two ayahs swapped, an ayah repeated, a restart (half an ayah, a
pause, then the whole ayah again), a wrong ayah said in place of the right one, stopping early.

Ten reciters with different styles: slow and measured (Husary, Minshawi), the melodic mujawwad style
with long elongations (Abdul Basit), fast Saudi styles (Sudais, Ghamdi, Muaiqly), and two Iranian
reciters (Parhizgar, Mansoori). Output: samples/<reciter>/*.wav plus samples/cases.json, which
scripts/eval.py uses. Recordings are not committed to Git; run this to recreate them.

    python scripts/make_samples.py                      # all reciters
    python scripts/make_samples.py --reciters alafasy   # just one
"""
import argparse
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"
SOURCE = "https://everyayah.com/data/{folder}/{s:03d}{a:03d}.mp3"
UA = {"User-Agent": "recite-capstone-samples/1.0"}

RECITERS = {
    "alafasy": "Alafasy_128kbps",
    "husary": "Husary_128kbps",
    "minshawi": "Minshawy_Murattal_128kbps",
    "abdul-basit-mujawwad": "Abdul_Basit_Mujawwad_128kbps",
    "sudais": "Abdurrahmaan_As-Sudais_192kbps",
    "ghamdi": "Ghamadi_40kbps",
    "ayyoub": "Muhammad_Ayyoub_128kbps",
    "muaiqly": "Maher_AlMuaiqly_64kbps",
    "parhizgar": "Parhizgar_48kbps",
    "mansoori": "Karim_Mansoori_40kbps",
}

HALF = "half"  # a part ("half", surah, ayah): the first half of the ayah, then the reciter stops

# name: (surah, ayah_from, ayah_to that the reciter *meant*, what was actually said, expected findings [ayah, kind])
CASES = {
    "al-fil": (105, 1, 5, [(105, 1), (105, 2), (105, 3), (105, 4), (105, 5)], []),
    "al-fil-skipped-3": (105, 1, 5, [(105, 1), (105, 2), (105, 4), (105, 5)], [[3, "missed"]]),
    "al-fil-swapped-3-4": (105, 1, 5, [(105, 1), (105, 2), (105, 4), (105, 3), (105, 5)], [[4, "moved"]]),
    "al-fil-repeated-2": (105, 1, 5, [(105, 1), (105, 2), (105, 2), (105, 3), (105, 4), (105, 5)], [[2, "repeat"]]),
    "al-fil-restart-3": (105, 1, 5, [(105, 1), (105, 2), (HALF, 105, 3), (105, 3), (105, 4), (105, 5)], [[3, "repeat"]]),
    "al-fil-wrong-ayah-3": (105, 1, 5, [(105, 1), (105, 2), (106, 3), (105, 4), (105, 5)], [[3, "wrong"]]),
    "al-fil-stopped-early": (105, 1, 5, [(105, 1), (105, 2), (105, 3)], [[4, "missed"], [5, "missed"]]),
    "quraysh": (106, 1, 4, [(106, 1), (106, 2), (106, 3), (106, 4)], []),
    "quraysh-skipped-2": (106, 1, 4, [(106, 1), (106, 3), (106, 4)], [[2, "missed"]]),
    "al-fatiha": (1, 1, 7, [(1, a) for a in range(1, 8)], []),
    "al-fatiha-repeated-3": (1, 1, 7, [(1, 1), (1, 2), (1, 3), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7)], [[3, "repeat"]]),
    "al-fatiha-restart-7": (1, 1, 7, [(1, a) for a in range(1, 7)] + [(HALF, 1, 7), (1, 7)], [[7, "repeat"]]),
    "al-fatiha-swapped-5-6": (1, 1, 7, [(1, 1), (1, 2), (1, 3), (1, 4), (1, 6), (1, 5), (1, 7)], [[6, "moved"]]),
    "al-ikhlas": (112, 1, 4, [(112, a) for a in range(1, 5)], []),
    "al-ikhlas-skipped-3": (112, 1, 4, [(112, 1), (112, 2), (112, 4)], [[3, "missed"]]),
}


def run(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *args], check=True)


def fetch(folder: str, s: int, a: int, cache: Path) -> Path:
    """The ayah as 16 kHz mono WAV (downloaded once)."""
    wav = cache / f"{s:03d}{a:03d}.wav"
    if not wav.exists():
        mp3 = cache / f"{s:03d}{a:03d}.mp3"
        req = urllib.request.Request(SOURCE.format(folder=folder, s=s, a=a), headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            mp3.write_bytes(r.read())
        run("-i", str(mp3), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav))
        mp3.unlink()
    return wav


def half(wav: Path) -> Path:
    """The first half of an ayah followed by a short pause: the reciter stopped to go back."""
    out = wav.with_name(wav.stem + "-half.wav")
    if not out.exists():
        secs = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                     str(wav)], capture_output=True, text=True, check=True).stdout)
        run("-i", str(wav), "-f", "lavfi", "-t", "0.8", "-i", "anullsrc=r=16000:cl=mono",
            "-filter_complex", f"[0]atrim=0:{secs / 2:.2f}[a];[a][1]concat=n=2:v=0:a=1", "-c:a", "pcm_s16le", str(out))
    return out


def build(reciter: str, name: str, parts: list) -> Path:
    folder = RECITERS[reciter]
    cache = OUT / "ayahs" / reciter
    cache.mkdir(parents=True, exist_ok=True)
    out = OUT / reciter / f"{name}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    files = [half(fetch(folder, p[1], p[2], cache)) if p[0] == HALF else fetch(folder, *p, cache) for p in parts]
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as lst:
        lst.writelines(f"file '{f}'\n" for f in files)
    run("-f", "concat", "-safe", "0", "-i", lst.name, "-c:a", "pcm_s16le", str(out))
    Path(lst.name).unlink()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reciters", default=",".join(RECITERS), help=f"comma-separated, from: {', '.join(RECITERS)}")
    reciters = ap.parse_args().reciters.split(",")
    manifest = {}
    for reciter in reciters:
        for name, (surah, a, b, parts, expect) in CASES.items():
            path = build(reciter, name, parts)
            manifest[f"{reciter}/{name}"] = {"file": str(path.relative_to(OUT)), "reciter": reciter, "surah": surah,
                                             "ayah_from": a, "ayah_to": b, "expect": expect}
        print(f"{reciter}: {len(CASES)} recordings")
    (OUT / "cases.json").write_text(json.dumps(manifest, indent=1))
    print(f"{len(manifest)} recordings in samples/ from {len(reciters)} reciters (source: EveryAyah)")
