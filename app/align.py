"""Compare what was heard with what was expected, word by word, and name each mistake.

Words are compared in their match_key form (app/quran.py): harakat ignored, sound-alike letters
folded, and the alef spelling differences between the mushaf and modern spelling ignored. Recite
checks which words were said, not how they were pronounced.

1. A weighted edit-distance alignment pairs heard words with expected words.
   Costs: match 0, adjacent transposition 1, insertion 1, deletion 1, substitution 1.1
   (0.8 when the two words look alike). Substitution costs a little more than 1 so that a
   block said in the wrong place is explained as "missing here, extra there" rather than as a
   run of wrong words, which lets step 3 recognise it as moved.
2. Every expected word becomes correct / wrong (with what was heard) / missed. A word heard almost
   right (one letter off inside a longer word, see close_enough) counts as correct: at that level, the
   difference is far more often the speech recogniser than the reciter.
   Every heard word with no partner becomes an extra.
3. Extras are then explained where possible:
   - repeat: words of the passage said again: the reciter went back (to fix a word, or to restart the
     ayah). Going back is not a mistake; it is noted as a hesitation and does not count against the ayah;
   - moved: the same words as a run that is missing elsewhere, i.e. ayahs or words swapped.

Before aligning, immediate restarts ("A B C, A B C D ...") are taken out of what was heard and kept
as repeats. Otherwise a repeat next to a real mistake lets the alignment smear both into a run of odd
extras and wrong words. The two attempts may be heard slightly differently, so a run of three or more
words counts as said again with one word different. A doubled run is only treated as a restart when
the text itself does not contain it doubled (some ayahs do repeat words, e.g. 89:21).
"""
from difflib import SequenceMatcher
from functools import lru_cache

from app.quran import Word, match_key

SUB, SUB_SIMILAR, INS, DEL, SWAP = 1.1, 0.8, 1.0, 1.0, 1.0
LOOK_BACK = 80          # how far back (in words) going back to repeat is recognised


@lru_cache(maxsize=65536)
def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(a=a, b=b).ratio()


def _sub_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return SUB_SIMILAR if _similarity(a, b) >= 0.6 else SUB


@lru_cache(maxsize=65536)
def close_enough(a: str, b: str) -> bool:
    """The same word with a silent opening alef dropped, or one letter different (changed, added or
    dropped) inside a word of five letters or more, not its first or last letter: المغضوب heard as المخبوب. The first and last letters are left out because
    that is where a different word form shows (يعلمون / تعلمون, عليه / عليهم)."""
    if a == b:
        return True
    if (a == "ا" + b or b == "ا" + a) and min(len(a), len(b)) >= 3:
        return True   # the opening alef of "al" or a hamzat al-wasl, silent after a vowel (اهدنا الصراط)
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1 or a[0] != b[0] or a[-1] != b[-1]:
        return False
    i = 0
    while a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        return a[i + 1:] == b[i + 1:]
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    return longer[i + 1:] == shorter[i:]


def _same_run(a: list[str], b: list[str]) -> bool:
    """The same words, allowing one to differ in runs of three or more (heard twice, heard differently),
    but not the first: a run that starts differently is a different stretch of text."""
    if len(a) != len(b) or not a:
        return False
    same = [x == y or close_enough(x, y) for x, y in zip(a, b)]
    return all(same) or (len(a) >= 3 and same[0] and sum(same) >= len(a) - 1)


def edit_ops(exp: list[str], heard: list[str], prefix: bool = False) -> list[tuple[str, int, int]]:
    """Return [(op, i, j)] with op in match/sub/del/ins/swap; i indexes exp, j indexes heard
    (-1 when not applicable). For 'swap', i and j are the first of the two transposed words.

    With prefix=True the reciter is still going: heard is aligned against the best-fitting start of
    exp, and the expected words after that are left out of the ops (not yet recited, not missed)."""
    n, m = len(exp), len(heard)
    inf = float("inf")
    d = [[inf] * (m + 1) for _ in range(n + 1)]
    d[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == j == 0:
                continue
            best = inf
            if i and j:
                best = d[i - 1][j - 1] + _sub_cost(exp[i - 1], heard[j - 1])
            if i:
                best = min(best, d[i - 1][j] + DEL)
            if j:
                best = min(best, d[i][j - 1] + INS)
            if (i > 1 and j > 1 and exp[i - 1] == heard[j - 2] and exp[i - 2] == heard[j - 1]
                    and exp[i - 1] != exp[i - 2]):
                best = min(best, d[i - 2][j - 2] + SWAP)
            d[i][j] = best

    ops: list[tuple[str, int, int]] = []
    i, j, eps = n, m, 1e-9
    if prefix:  # cheapest end point; on a tie the furthest one (a skipped word is likelier than an extra)
        i = min(range(n + 1), key=lambda k: (d[k][m], -k))
    while i or j:
        here = d[i][j]
        if i and j and exp[i - 1] == heard[j - 1] and abs(here - d[i - 1][j - 1]) < eps:
            ops.append(("match", i - 1, j - 1)); i -= 1; j -= 1
        elif (i > 1 and j > 1 and exp[i - 1] == heard[j - 2] and exp[i - 2] == heard[j - 1]
              and exp[i - 1] != exp[i - 2] and abs(here - (d[i - 2][j - 2] + SWAP)) < eps):
            ops.append(("swap", i - 2, j - 2)); i -= 2; j -= 2
        elif i and j and abs(here - (d[i - 1][j - 1] + _sub_cost(exp[i - 1], heard[j - 1]))) < eps:
            ops.append(("sub", i - 1, j - 1)); i -= 1; j -= 1
        elif i and abs(here - (d[i - 1][j] + DEL)) < eps:
            ops.append(("del", i - 1, -1)); i -= 1
        else:
            ops.append(("ins", -1, j - 1)); j -= 1
    ops.reverse()
    return ops


def _restarted(before: list[str], run: list[str]) -> bool:
    """`run` says `before` again: the same words (one may differ in runs of three or more), or, in runs of
    three or more, the same words up to the last word of the earlier attempt, which was cut off or wrong:
    the reciter stopped there and went back. Shorter runs must match exactly: short words such as الله
    and ولم recur all the time (ٱللَّهُ أَحَدٌ ٱللَّهُ ٱلصَّمَدُ) without anyone going back."""
    return _same_run(before, run) or (len(run) >= 3 and before[:-1] == run[:-1])


def strip_restarts(heard: list[str], exp: list[str], max_len: int = 30) -> tuple[list[int], list[tuple[int, list[int]]]]:
    """Find immediately repeated runs. The *later* attempt is kept: when a reciter goes back and says it
    again, that is their correction, and the earlier attempt is the repeat. Returns (indices of the heard
    words kept, [(position in the kept list of the word the repeat follows, indices of the repeated
    words)])."""
    exp_text = " " + " ".join(exp) + " "
    kept: list[int] = []
    restarts: list[tuple[int, list[int]]] = []
    j = 0
    while j < len(heard):
        for k in range(min(max_len, len(kept), len(heard) - j), 0, -1):
            run = heard[j:j + k]
            before = [heard[x] for x in kept[-k:]]
            if _restarted(before, run) and f" {' '.join(run + run)} " not in exp_text:
                restarts.append((len(kept) - 1, kept[-k:]))   # shown after the words it repeats
                kept[-k:] = range(j, j + k)
                j += k
                break
        else:
            back = _abandoned(heard, kept, j, exp)
            if back is not None:
                x, k = back
                restarts.append((x + k - 1, kept[x:]))   # shown after the new attempt's words
                kept[x:] = range(j, j + k)
                j += k
            else:
                kept.append(j)
                j += 1
    return kept, restarts


def _abandoned(heard: list[str], kept: list[int], j: int, exp: list[str]) -> tuple[int, int] | None:
    """The reciter went back a little, after an attempt that went off the text (a wrong word, or words the
    speech model made up when they stopped mid-ayah): three or more words starting at j were already said
    a few words ago, and what followed them then did not go on with the passage. Then everything from
    there is the abandoned attempt. Only for words that occur once in the passage, so a refrain
    (فبأي آلاء ربكما تكذبان) is never taken for going back. Returns (position in kept where the abandoned
    attempt starts, length of the run)."""
    for k in range(min(12, len(heard) - j), 2, -1):
        run = heard[j:j + k]
        at = [q for q in range(len(exp) - k + 1) if exp[q:q + k] == run]
        if len(at) != 1:
            continue
        after = exp[at[0] + k] if at[0] + k < len(exp) else None
        for x in range(len(kept) - k - 1, max(-1, len(kept) - 30), -1):
            if not all(heard[i] == w or close_enough(heard[i], w) for i, w in zip(kept[x:x + k], run)):
                continue   # every word must match here: passages repeat phrases (الرحمن الرحيم in 1:1 and 1:3)
            went_on = heard[kept[x + k]]
            if after is not None and (went_on == after or close_enough(went_on, after)):
                return None   # the first time, they carried on correctly: this is a plain repeat
            return x, k
    return None


def _reached(ops: list[tuple[str, int, int]]) -> int:
    return max((i + (op == "swap") for op, i, _ in ops if i >= 0), default=-1)


def _skip_ahead(exp: list[str], heard: list[str], ops: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Live: after a skipped passage, the words said next are counted as extras until more of them have
    been said than were skipped (inserting is cheaper than deleting a long run). If the trailing extras
    (two or more words) appear further on in the text, align up to there instead, so the skipped words
    show as missed straight away."""
    tail = 0
    for op, _, _ in reversed(ops):
        if op != "ins":
            break
        tail += 1
    if tail < 2:
        return ops
    run = heard[len(heard) - tail:]
    reached = _reached(ops)
    if _went_back(exp, run, reached):
        return ops
    for q in range(reached + 1, len(exp) - 1):
        if exp[q:q + 2] == run[:2]:
            return edit_ops(exp[:min(len(exp), q + tail)], heard)
    return ops


def _went_back(exp: list[str], words: list[str], at: int) -> bool:
    """The words are a stretch of the passage at or before position `at` (within LOOK_BACK words), or the
    words right after it: the reciter went back and said them again."""
    k = len(words)
    starts = range(max(0, at - LOOK_BACK), at + 2)
    return any(_same_run(exp[q:q + k], words) for q in starts if q + k <= len(exp))


def _runs(indices: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for k in indices:
        if runs and k == runs[-1][-1] + 1:
            runs[-1].append(k)
        else:
            runs.append([k])
    return runs


def align(expected: list[Word], heard: list[str], prefix: bool = False) -> dict:
    """heard: normalised words (app.quran.norm_words). prefix=True is for live checking while the
    reciter is still going: expected words past the point they have reached get status 'pending' instead
    of 'missed', and 'reached' is the index of the last expected word they got to (-1 before the first)."""
    exp = [match_key(w.norm) for w in expected]
    keys = [match_key(h) for h in heard]
    kept, restarts = strip_restarts(keys, exp)
    said = [heard[j] for j in kept]        # what was heard, as shown
    hk = [keys[j] for j in kept]           # the same, as compared
    ops = edit_ops(exp, hk, prefix)
    if prefix:
        ops = _skip_ahead(exp, hk, ops)
    reached = _reached(ops) if prefix else len(exp) - 1
    words = [{"i": w.i, "ayah": w.ayah, "text": w.text, "status": "correct" if w.i <= reached else "pending"}
             for w in expected]
    inserts: list[tuple[int, int]] = []  # (after expected index, heard index)
    after_of = [-1] * len(hk)            # heard index -> expected index it sits at or after
    last = -1
    for op, i, j in ops:
        if op == "match":
            last = i
        elif op == "sub":
            if not close_enough(exp[i], hk[j]):
                words[i]["status"] = "wrong"
                words[i]["heard"] = said[j]
            last = i
        elif op == "del":
            words[i]["status"] = "missed"
            last = i
        elif op == "swap":
            words[i]["status"] = words[i + 1]["status"] = "moved"
            after_of[j] = i + 1
            after_of[j + 1] = i + 1
            last = i + 1
            continue
        else:
            inserts.append((last, j))
        if j >= 0:
            after_of[j] = last

    # Group consecutive inserted heard words that sit at the same spot.
    extras: list[dict] = []
    for after, j in inserts:
        if extras and extras[-1]["after"] == after and extras[-1]["_j"][-1] == j - 1:
            extras[-1]["_j"].append(j)
        else:
            extras.append({"after": after, "_j": [j]})
    for e in extras:
        js = e.pop("_j")
        e["words"], e["_keys"] = [said[j] for j in js], [hk[j] for j in js]
        e["kind"] = "extra"

    # Moved: an extra run that matches a missed run elsewhere.
    missed_runs = _runs([k for k, w in enumerate(words) if w["status"] == "missed"])  # positions, not w.i
    for e in extras:
        for run in missed_runs:
            if _same_run([exp[k] for k in run], e["_keys"]):
                for k in run:
                    words[k]["status"] = "moved"
                e["kind"] = "moved"
                missed_runs.remove(run)
                break

    # Repeat: the reciter went back and said words of the passage again.
    for e in extras:
        if e["kind"] == "extra" and _went_back(exp, e["_keys"], e["after"]):
            e["kind"] = "repeat"
        del e["_keys"]

    for pos, js in restarts:
        pos = min(pos, len(after_of) - 1)   # a later restart may have shortened what was kept
        extras.append({"after": after_of[pos] if pos >= 0 else -1, "words": [heard[j] for j in js], "kind": "repeat"})
    extras.sort(key=lambda e: e["after"])

    for e in extras:
        a = e["after"]
        e["ayah"] = expected[a].ayah if a >= 0 else (expected[0].ayah if expected else None)
        # Extra words at the end of an ayah belong to the next one when it starts with a mistake: they are
        # part of the same slip (a different ayah said in its place, one word longer).
        if (e["kind"] == "extra" and a + 1 < len(words) and words[a + 1]["status"] in ("wrong", "missed")
                and (a < 0 or expected[a].ayah != expected[a + 1].ayah)):
            e["ayah"] = expected[a + 1].ayah

    ayahs: dict[int, dict] = {}
    for w in words:
        if w["status"] == "pending":
            continue
        s = ayahs.setdefault(w["ayah"], {"ayah": w["ayah"], "words": 0, "correct": 0, "mistakes": 0, "repeats": 0})
        s["words"] += 1
        if w["status"] == "correct":
            s["correct"] += 1
        else:
            s["mistakes"] += 1
    for e in extras:
        if e["ayah"] in ayahs and e["kind"] != "moved":  # a moved run is already counted on its words
            ayahs[e["ayah"]]["repeats" if e["kind"] == "repeat" else "mistakes"] += 1
    for s in ayahs.values():
        s["solid"] = s["mistakes"] == 0   # going back to repeat is a hesitation, not a mistake

    correct = sum(w["status"] == "correct" for w in words)
    return {
        "words": words,
        "extras": extras,
        "ayahs": list(ayahs.values()),
        "heard": heard,
        "correct": correct,
        "total": len(words),
        "mistakes": sum(s["mistakes"] for s in ayahs.values()),
        "repeats": sum(s["repeats"] for s in ayahs.values()),
        "accuracy": round(correct / len(words), 4) if words else 0.0,
        "reached": reached,
    }


PROMPT_BEFORE, PROMPT_AFTER = 8, 40


def prompt_for(expected: list[Word], heard: list[str]) -> str:
    """Context for the speech model before it hears the next phrase: the passage text around where the
    reciter has got to (a few words back, and what comes next). The model was trained on the Quran, so
    knowing the passage lets it settle on the right spelling of a word it half-heard, the way a listener
    who knows the surah would. It still writes what it hears: a skipped or wrong ayah stays wrong."""
    if not expected:
        return ""
    reached = align(expected, heard, prefix=True)["reached"]
    window = expected[max(0, reached + 1 - PROMPT_BEFORE): reached + 1 + PROMPT_AFTER]
    return " ".join(w.spoken or w.norm for w in window)


def best_fit(expected: list[Word], heard: list[str], hyps: list[str], seconds: float) -> str:
    """Of several transcripts of the same phrase, the one that fits the passage best after what was heard
    so far: most new words recited correctly, fewest new mistakes. The first wins a tie. Implausible
    transcripts (a loop, too many words for the audio) are passed over."""
    from app.quran import norm_words, plausible
    hyps = [h for h in hyps if plausible(h, seconds)] or [""]
    before = align(expected, heard, prefix=True)

    def score(h: str) -> float:
        r = align(expected, heard + norm_words(h), prefix=True)
        return (r["correct"] - before["correct"]) - (r["mistakes"] - before["mistakes"])

    return max(hyps, key=score)
