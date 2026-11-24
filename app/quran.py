"""Quran text: loading the Tanzil files, Arabic normalisation, and the expected word list for a range.

Two Tanzil editions are used, both stored verbatim (the licence forbids changes):
- "simple" spelling is what recitations are compared against: its words map cleanly onto speech
  recognition output once harakat are removed;
- "Uthmani" script is what people see, because it is the script of the printed mushaf.
The two split a few phrases differently (the mushaf writes "يا قوم" as one word, يَـٰقَوْمِ), so each
simple word is mapped to the Uthmani word it is part of. The basmala that Tanzil prefixes to ayah 1 of
most surahs is split off at read time, because it is recited before the surah, not checked as part of it.
"""
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from app.config import ROOT

TEXT_FILE = ROOT / "data" / "quran-simple.txt"
UTHMANI_FILE = ROOT / "data" / "quran-uthmani.txt"
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
    text: str       # as shown, in Uthmani script ('' when written joined to the word before)
    norm: str       # comparison form, from the simple spelling


def split_basmala(surah: int, ayah: int, text: str) -> tuple[str | None, str]:
    """Tanzil prefixes the basmala to ayah 1 of every surah except 1 (where it *is* ayah 1) and 9."""
    if surah in (1, 9) or ayah != 1:
        return None, text
    tokens = text.split()
    if [norm_word(t) for t in tokens[:4]] == BASMALA:
        return " ".join(tokens[:4]), " ".join(tokens[4:])
    return None, text


def display_for(simple: list[str], uthmani: list[str]) -> list[str]:
    """The Uthmani word to show for each simple-spelling word. Where the mushaf joins several simple
    words into one (يا قوم -> يَـٰقَوْمِ), the first gets the Uthmani word and the rest get ''.
    Where the counts differ, each Uthmani word takes the 1-3 simple words it resembles most."""
    if len(simple) == len(uthmani):
        return list(uthmani)
    out, i, j = [], 0, 0
    while i < len(simple):
        if j >= len(uthmani):
            out.append(simple[i])
            i += 1
            continue
        extra = (len(simple) - i) - (len(uthmani) - j)
        target = norm_word(uthmani[j])
        best_k, best = 1, -1.0
        for k in range(1, min(3, extra + 1) + 1):
            r = SequenceMatcher(a="".join(norm_word(t) for t in simple[i:i + k]), b=target).ratio()
            if r > best + 0.05:
                best_k, best = k, r
        out += [uthmani[j]] + [""] * (best_k - 1)
        i += best_k
        j += 1
    return out


def _tokens(text: str) -> list[str]:
    return [t for t in text.split() if norm_word(t)]  # drops standalone marks such as ۞ and ۩


def display_tokens(text: str) -> list[str]:
    """Mushaf words with their signs kept on them: a pause mark (ۚ ۖ ...) sits on the word before it,
    a sajdah sign follows its word, and a hizb sign (۞) leads the word after it."""
    out: list[str] = []
    lead = ""
    for t in text.split():
        if norm_word(t):
            out.append(lead + t)
            lead = ""
        elif out and "\u06d6" <= t[0] <= "\u06dc":
            out[-1] += t
        elif out and t != "۞":
            out[-1] += " " + t
        else:
            lead += t + " "
    return out


def expected_words(surah: int, ayahs: list[tuple]) -> list[Word]:
    """[(ayah, simple_text[, uthmani_text]), ...] -> the words a reciter is expected to say, in order."""
    words: list[Word] = []
    for ayah, simple, *rest in ayahs:
        tokens = _tokens(split_basmala(surah, ayah, simple)[1])
        uthmani = rest[0] if rest and rest[0] else None
        shown = display_for(tokens, display_tokens(split_basmala(surah, ayah, uthmani)[1])) if uthmani else tokens
        for token, display in zip(tokens, shown):
            words.append(Word(len(words), ayah, display, norm_word(token)))
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
