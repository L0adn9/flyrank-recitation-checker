"""Compare what was heard with what was expected, word by word."""
import difflib

from app.quran import Word


def align(expected: list[Word], heard: list[str]) -> dict:
    marks = [{"i": w.i, "ayah": w.ayah, "text": w.text, "status": "correct"} for w in expected]
    extras = []
    sm = difflib.SequenceMatcher(a=[w.norm for w in expected], b=heard, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "delete":
            for i in range(i1, i2):
                marks[i]["status"] = "missed"
        elif op == "replace":
            for i in range(i1, i2):
                marks[i]["status"] = "wrong"
        elif op == "insert":
            extras.append({"after": i1 - 1, "words": heard[j1:j2], "kind": "extra"})
    correct = sum(m["status"] == "correct" for m in marks)
    return {
        "words": marks,
        "extras": extras,
        "correct": correct,
        "total": len(marks),
        "accuracy": round(correct / len(marks), 4) if marks else 0.0,
    }
