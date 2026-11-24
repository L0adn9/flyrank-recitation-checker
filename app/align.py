"""Compare what was heard with what was expected, word by word, and name each mistake.

1. A weighted edit-distance alignment pairs heard words with expected words.
   Costs: match 0, adjacent transposition 1, insertion 1, deletion 1, substitution 1.1
   (0.8 when the two words look alike). Substitution costs a little more than 1 so that a
   block said in the wrong place is explained as "missing here, extra there" rather than as a
   run of wrong words, which lets step 3 recognise it as moved.
2. Every expected word becomes correct / wrong (with what was heard) / missed.
   Every heard word with no partner becomes an extra.
3. Extras are then explained where possible:
   - repeat: the same words as just before (or just after) the spot, i.e. the reciter went back;
   - moved: the same words as a run that is missing elsewhere, i.e. ayahs or words swapped.

Before aligning, immediate restarts ("A B C, B C D ...") are taken out of what was heard and kept
as hesitations. Otherwise a repeat next to a real mistake lets the alignment smear both into a run
of odd extras and wrong words. A doubled run is only treated as a restart when the text itself does
not contain it doubled (some ayahs do repeat words, e.g. 89:21).
"""
from difflib import SequenceMatcher

from app.quran import Word

SUB, SUB_SIMILAR, INS, DEL, SWAP = 1.1, 0.8, 1.0, 1.0, 1.0


def _sub_cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return SUB_SIMILAR if SequenceMatcher(a=a, b=b).ratio() >= 0.6 else SUB


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


def strip_restarts(heard: list[str], exp: list[str], max_len: int = 12) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """Remove immediately repeated runs. Returns (cleaned, [(index in cleaned of the word the repeat
    follows, repeated words)])."""
    exp_text = " " + " ".join(exp) + " "
    out: list[str] = []
    restarts: list[tuple[int, list[str]]] = []
    j = 0
    while j < len(heard):
        for k in range(min(max_len, len(out), len(heard) - j), 0, -1):
            run = heard[j:j + k]
            if out[-k:] == run and f" {' '.join(run + run)} " not in exp_text:
                restarts.append((len(out) - 1, run))
                j += k
                break
        else:
            out.append(heard[j])
            j += 1
    return out, restarts


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
    if exp[max(0, reached - tail + 1): reached + 1] == run:  # said again: a repeat, not a jump
        return ops
    for q in range(reached + 1, len(exp) - 1):
        if exp[q:q + 2] == run[:2]:
            return edit_ops(exp[:min(len(exp), q + tail)], heard)
    return ops


def _runs(indices: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for k in indices:
        if runs and k == runs[-1][-1] + 1:
            runs[-1].append(k)
        else:
            runs.append([k])
    return runs


def align(expected: list[Word], heard: list[str], prefix: bool = False) -> dict:
    """prefix=True is for live checking while the reciter is still going: expected words past the
    point they have reached get status 'pending' instead of 'missed', and 'reached' is the index of
    the last expected word they got to (-1 before the first)."""
    exp = [w.norm for w in expected]
    said = heard
    heard, restarts = strip_restarts(heard, exp)
    ops = edit_ops(exp, heard, prefix)
    if prefix:
        ops = _skip_ahead(exp, heard, ops)
    reached = _reached(ops) if prefix else len(exp) - 1
    words = [{"i": w.i, "ayah": w.ayah, "text": w.text, "status": "correct" if w.i <= reached else "pending"}
             for w in expected]
    inserts: list[tuple[int, int]] = []  # (after expected index, heard index)
    after_of = [-1] * len(heard)          # heard index -> expected index it sits at or after
    last = -1
    for op, i, j in ops:
        if op == "match":
            last = i
        elif op == "sub":
            words[i]["status"] = "wrong"
            words[i]["heard"] = heard[j]
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
        e["words"] = [heard[j] for j in e.pop("_j")]
        e["kind"] = "extra"

    # Moved: an extra run that matches a missed run elsewhere.
    missed_runs = _runs([w["i"] for w in words if w["status"] == "missed"])
    for e in extras:
        for run in missed_runs:
            if [exp[k] for k in run] == e["words"]:
                for k in run:
                    words[k]["status"] = "moved"
                e["kind"] = "moved"
                missed_runs.remove(run)
                break

    # Repeat: the reciter said again the words just before (or just after) this spot.
    for e in extras:
        if e["kind"] != "extra":
            continue
        k, a = len(e["words"]), e["after"]
        before = exp[max(0, a - k + 1): a + 1] if a >= 0 else []
        after = exp[a + 1: a + 1 + k]
        if e["words"] in (before, after):
            e["kind"] = "repeat"

    for pos, run in restarts:
        extras.append({"after": after_of[pos] if pos >= 0 else -1, "words": run, "kind": "repeat"})
    extras.sort(key=lambda e: e["after"])

    for e in extras:
        a = e["after"]
        e["ayah"] = expected[a].ayah if a >= 0 else (expected[0].ayah if expected else None)

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
        s["solid"] = s["mistakes"] == 0 and s["repeats"] == 0

    correct = sum(w["status"] == "correct" for w in words)
    return {
        "words": words,
        "extras": extras,
        "ayahs": list(ayahs.values()),
        "heard": said,
        "correct": correct,
        "total": len(words),
        "mistakes": sum(s["mistakes"] for s in ayahs.values()),
        "repeats": sum(s["repeats"] for s in ayahs.values()),
        "accuracy": round(correct / len(words), 4) if words else 0.0,
        "reached": reached,
    }
