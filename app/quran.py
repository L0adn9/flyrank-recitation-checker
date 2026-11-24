"""Quran text: loading the Tanzil file, Arabic normalisation, and the expected word list for a range.

The Tanzil text is stored verbatim (its licence forbids changes). Everything here only *reads* it:
the basmala that Tanzil prefixes to ayah 1 of most surahs is split off at read time, because it is
recited before the surah, not checked as part of it.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import ROOT

TEXT_FILE = ROOT / "data" / "quran-simple.txt"
SURAHS_FILE = ROOT / "data" / "surahs.json"

# Harakat, Quranic annotation signs, superscript alef, tatweel, BOM.
_MARKS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭـ﻿]")
_NOT_LETTER = re.compile("[^ء-ي]")
_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})

BASMALA = ["بسم", "الله", "الرحمن", "الرحيم"]
ISTIADHA = ["اعوذ", "بالله", "من", "الشيطان", "الرجيم"]
CLOSING = ["صدق", "الله", "العظيم"]


def norm_word(word: str) -> str:
    """One word in its comparison form: no harakat, one alef, ya/ta-marbuta/hamza seats folded."""
    w = _MARKS.sub("", word).translate(_FOLD)
    return _NOT_LETTER.sub("", w)


def norm_words(text: str) -> list[str]:
    return [w for w in (norm_word(t) for t in text.split()) if w]


@dataclass(frozen=True)
class Word:
    i: int          # position in the expected sequence
    ayah: int
    text: str       # as written in the mushaf text, with harakat
    norm: str       # comparison form


def split_basmala(surah: int, ayah: int, text: str) -> tuple[str | None, str]:
    """Tanzil prefixes the basmala to ayah 1 of every surah except 1 (where it *is* ayah 1) and 9."""
    if surah in (1, 9) or ayah != 1:
        return None, text
    tokens = text.split()
    if [norm_word(t) for t in tokens[:4]] == BASMALA:
        return " ".join(tokens[:4]), " ".join(tokens[4:])
    return None, text


def expected_words(surah: int, ayahs: list[tuple[int, str]]) -> list[Word]:
    """[(ayah, text), ...] -> the words a reciter is expected to say, in order."""
    words: list[Word] = []
    for ayah, text in ayahs:
        _, body = split_basmala(surah, ayah, text)
        for token in body.split():
            n = norm_word(token)
            if n:  # skips standalone marks such as ۞ and ۩
                words.append(Word(len(words), ayah, token, n))
    return words


def _strip_prefix(heard: list[str], phrase: list[str]) -> list[str]:
    return heard[len(phrase):] if heard[: len(phrase)] == phrase else heard


def clean_heard(heard: list[str], expected: list[Word]) -> list[str]:
    """Drop what reciters customarily add around a recitation and that is not part of the range:
    the isti'adha and basmala before it, and 'sadaqa Allahu al-azim' after it."""
    heard = _strip_prefix(heard, ISTIADHA)
    if [w.norm for w in expected[:4]] != BASMALA:
        heard = _strip_prefix(heard, BASMALA)
    if heard[-3:] == CLOSING and [w.norm for w in expected[-3:]] != CLOSING:
        heard = heard[:-3]
    return heard


def load_text_file(path: Path = TEXT_FILE) -> list[tuple[int, int, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        surah, ayah, text = line.split("|", 2)
        rows.append((int(surah), int(ayah), text))
    return rows


def load_surahs(path: Path = SURAHS_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))
