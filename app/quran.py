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
AMIN = ["امين"]
# The disjoint letters that open some surahs (الم, يس, ...). They are said as letter names ("alif lam
# mim"), which speech recognition writes in all sorts of ways.
MUQATTAAT = {"الم", "المص", "الر", "المر", "كهيعص", "طه", "طسم", "طس", "يس", "ص", "حم", "عسق", "ق", "ن"}


def norm_word(word: str) -> str:
    """One word in its comparison form: no harakat, one alef, ya/ta-marbuta/hamza seats folded. The mushaf's
    waw with a small alef (ٱلصَّلَوٰةَ) is the alef of the usual spelling (الصلاة)."""
    w = _MARKS.sub("", word.replace("وٰ", "ا")).translate(_FOLD)
    return _NOT_LETTER.sub("", w)


def plausible(text: str, seconds: float) -> bool:
    """Could this have been said in this much audio? Speech models make up text for a cough, an "amin" or
    a breath, and loop on hard audio (ومن ومن ومن ومن). Nobody recites more than three words a second,
    and no word comes three times in a row in the Quran."""
    words = norm_words(text)
    if len(words) > 1 + 3 * seconds:
        return False
    return not any(words[i] == words[i + 1] == words[i + 2] for i in range(len(words) - 2))


def keep_phrase(text: str, seconds: float, before: list[str], expected: list["Word"]) -> bool:
    """Is a recognised phrase worth keeping? Not when it is implausible (above); not when it is a short echo
    of the end of the phrase before (a reciter holding the last syllable, سجيل ... يل, heard as the word
    again); and not when it is a single word that appears nowhere in the passage (noise after the end)."""
    words = norm_words(text)
    if not words or not plausible(text, seconds):
        return False
    if len(words) <= 2 and seconds < 1.5 and before[-len(words):] == words:
        return False
    if len(words) == 1 and match_key(words[0]) not in {match_key(w.norm) for w in expected}:
        return False
    return True


_PREFIXES = {"و", "ف", "ب", "ل", "ك"}


def norm_words(text: str) -> list[str]:
    """Normalised words. A lone و / ف / ب / ل / ك is a prefix the speech model wrote apart (وَ أَرْسَلَ):
    it is joined to the next word, as these letters are never words on their own. Another lone letter
    followed by a word starting with it is a false start (غَ غَيْرِ) and is dropped."""
    out: list[str] = []
    lead = ""
    words = [w for w in (norm_word(t) for t in text.split()) if w]
    for k, w in enumerate(words):
        if len(w) == 1 and k + 1 < len(words) and words[k + 1][0] == w and w not in _PREFIXES:
            continue   # a false start: غ غير
        if w in _PREFIXES:
            lead += w
            continue
        out.append(lead + w)
        lead = ""
    if lead:
        out.append(lead)
    return out


# Letters that sound alike and that speech recognition (and many reciters) confuse. Recite checks which
# words were said, not how they were pronounced, so a word is not wrong over one of these.
_SOUND = str.maketrans({"ظ": "ض", "ذ": "ز", "ث": "س", "ص": "س", "ط": "ت", "د": "ت", "ح": "ه", "ق": "ك", "غ": "خ"})
_SUN = set("تثدذرزسشصضطظلن")


def match_key(norm: str) -> str:
    """The form two words are compared in:
    - "al" before a sun letter as it is pronounced: الرحمن is said "ar-rahman", and the speech model
      sometimes writes it that way (ارحمن);
    - sound-alike letters folded;
    - in words of four letters or more, alefs after the first letter dropped, because the mushaf and
      modern spelling differ exactly there (الرحمن / الرحمان, العالمين / العلمين, ذلك / ذالك). Short words
      keep them: قال (he said) and قل (say) must stay different."""
    if len(norm) > 3 and norm.startswith("ال") and norm[2] in _SUN and norm != "الله":
        norm = "ا" + norm[2:]
    k = norm.translate(_SOUND)
    if len(k) >= 4:
        k = k[0] + k[1:].replace("ا", "")
    return k


@dataclass(frozen=True)
class Word:
    i: int          # position in the expected sequence
    ayah: int
    text: str       # as shown, in Uthmani script ('' when written joined to the word before)
    norm: str       # comparison form, from the simple spelling
    spoken: str = ""  # the simple spelling with its harakat, given to the speech model as context


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
            words.append(Word(len(words), ayah, display, norm_word(token), token))
    return words


def _strip_prefix(heard: list[str], phrase: list[str]) -> list[str]:
    """Drop the phrase from the start of heard. It may be heard a little differently (اعوزوا الله ما
    الشيطن الرجي): all but one of its words must match, each with at most one letter off."""
    from difflib import SequenceMatcher
    n = len(phrase)
    if len(heard) < n:
        return heard
    near = sum(SequenceMatcher(a=match_key(h), b=match_key(p)).ratio() >= 0.7 for h, p in zip(heard, phrase))
    return heard[n:] if near >= n - (1 if n >= 4 else 0) else heard


def clean_heard(heard: list[str], expected: list[Word]) -> list[str]:
    """Drop what reciters customarily add around a recitation and that is not part of the range:
    the isti'adha and basmala before it, and 'amin' (after Al-Fatiha) and 'sadaqa Allahu al-azim' after it."""
    heard = _strip_prefix(heard, ISTIADHA)
    if [w.norm for w in expected[:4]] != BASMALA:
        heard = _strip_prefix(heard, BASMALA)
    if len(expected) > 1 and expected[0].norm in MUQATTAAT:
        # Whatever was heard before the first word after the letters is the letters.
        nxt = match_key(expected[1].norm)
        j = next((j for j, h in enumerate(heard[:6]) if match_key(h) == nxt), None)
        if j:
            heard = [expected[0].norm] + heard[j:]
    for tail in (CLOSING, AMIN, CLOSING):
        if heard[-len(tail):] == tail and [w.norm for w in expected[-len(tail):]] != tail:
            heard = heard[:-len(tail)]
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
