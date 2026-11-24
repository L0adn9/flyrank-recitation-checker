"""The mistake classifier on the cases that matter for hifz: skipped, wrong, repeated, swapped words and ayahs."""
from app.align import align
from app.quran import clean_heard, expected_words, norm_words

AL_FIL = [
    (1, "بِسْمِ اللَّهِ الرَّحْمَـٰنِ الرَّحِيمِ أَلَمْ تَرَ كَيْفَ فَعَلَ رَبُّكَ بِأَصْحَابِ الْفِيلِ"),
    (2, "أَلَمْ يَجْعَلْ كَيْدَهُمْ فِي تَضْلِيلٍ"),
    (3, "وَأَرْسَلَ عَلَيْهِمْ طَيْرًا أَبَابِيلَ"),
    (4, "تَرْمِيهِم بِحِجَارَةٍ مِّن سِجِّيلٍ"),
    (5, "فَجَعَلَهُمْ كَعَصْفٍ مَّأْكُولٍ"),
]
EXP = expected_words(105, AL_FIL)
AYAH = {n: norm_words(t) for n, t in AL_FIL}
AYAH[1] = AYAH[1][4:]  # without the basmala


def said(*ayahs):
    return [w for n in ayahs for w in AYAH[n]]


def statuses(r):
    return [w["status"] for w in r["words"]]


def test_basmala_is_split_off_and_not_expected():
    assert [w.norm for w in EXP[:3]] == ["الم", "تر", "كيف"]
    assert len(EXP) == 7 + 5 + 4 + 4 + 3


def test_normalisation_ignores_harakat_and_alef_forms():
    assert norm_words("الرَّحْمَـٰنِ بِأَصْحَابِ مَّأْكُولٍ") == ["الرحمن", "باصحاب", "ماكول"]


def test_perfect_recitation():
    r = align(EXP, said(1, 2, 3, 4, 5))
    assert r["accuracy"] == 1.0 and r["mistakes"] == 0 and r["extras"] == []
    assert all(a["solid"] for a in r["ayahs"])


def test_skipped_ayah_is_missed():
    r = align(EXP, said(1, 2, 4, 5))
    missed = [w["ayah"] for w in r["words"] if w["status"] == "missed"]
    assert missed == [3, 3, 3, 3]
    assert [a["ayah"] for a in r["ayahs"] if not a["solid"]] == [3]


def test_wrong_word_records_what_was_heard():
    heard = said(1, 2, 3, 4, 5)
    heard[heard.index("طيرا")] = "طيور"
    r = align(EXP, heard)
    wrong = [w for w in r["words"] if w["status"] == "wrong"]
    assert len(wrong) == 1 and wrong[0]["heard"] == "طيور" and wrong[0]["ayah"] == 3
    assert r["correct"] == r["total"] - 1


def test_repeated_ayah_is_a_repeat_not_a_mistake():
    r = align(EXP, said(1, 2, 2, 3, 4, 5))
    assert r["accuracy"] == 1.0 and r["mistakes"] == 0
    assert [e["kind"] for e in r["extras"]] == ["repeat"] and r["repeats"] == 1


def test_going_back_two_words_is_a_repeat():
    heard = said(1)[:3] + said(1)[1:3] + said(1)[3:] + said(2, 3, 4, 5)
    r = align(EXP, heard)
    assert [e["kind"] for e in r["extras"]] == ["repeat"]
    assert r["accuracy"] == 1.0


def test_swapped_ayahs_are_moved():
    r = align(EXP, said(1, 2, 4, 3, 5))
    moved = {w["ayah"] for w in r["words"] if w["status"] == "moved"}
    assert moved in ({3}, {4})
    assert [e["kind"] for e in r["extras"]] == ["moved"]
    assert "missed" not in statuses(r)


def test_two_adjacent_words_swapped():
    heard = said(1, 2, 3, 4, 5)
    k = heard.index("كيف")
    heard[k], heard[k + 1] = heard[k + 1], heard[k]
    r = align(EXP, heard)
    assert [w["text"] for w in r["words"] if w["status"] == "moved"] == ["كَيْفَ", "فَعَلَ"]
    assert r["correct"] == r["total"] - 2


def test_extra_word_inside_an_ayah():
    heard = said(1, 2, 3, 4, 5)
    heard.insert(heard.index("سجيل"), "كبير")
    r = align(EXP, heard)
    assert [(e["kind"], e["words"], e["ayah"]) for e in r["extras"]] == [("extra", ["كبير"], 4)]
    assert r["accuracy"] == 1.0 and r["mistakes"] == 1


def test_stopping_early_marks_the_rest_missed():
    r = align(EXP, said(1, 2))
    assert statuses(r)[:12] == ["correct"] * 12 and set(statuses(r)[12:]) == {"missed"}


def test_nothing_heard():
    r = align(EXP, [])
    assert r["accuracy"] == 0.0 and set(statuses(r)) == {"missed"}


def test_istiadha_basmala_and_closing_are_ignored():
    heard = norm_words("أعوذ بالله من الشيطان الرجيم بسم الله الرحمن الرحيم") + said(1, 2, 3, 4, 5) \
        + norm_words("صدق الله العظيم")
    assert align(EXP, clean_heard(heard, EXP))["accuracy"] == 1.0


def test_fatiha_keeps_its_basmala():
    fatiha = expected_words(1, [(1, "بِسْمِ اللَّهِ الرَّحْمَـٰنِ الرَّحِيمِ"), (2, "الْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ")])
    heard = norm_words("بسم الله الرحمن الرحيم الحمد لله رب العالمين")
    assert len(fatiha) == 8
    assert align(fatiha, clean_heard(heard, fatiha))["accuracy"] == 1.0


def test_repeat_next_to_a_wrong_ayah_is_kept_apart():
    # Recited ayah 2 twice, then a different ayah (Quraysh 3) in place of ayah 3.
    wrong_ayah = norm_words("فليعبدوا رب هذا البيت")
    r = align(EXP, said(1, 2, 2) + wrong_ayah + said(4, 5))
    assert [(e["kind"], e["ayah"]) for e in r["extras"]] == [("repeat", 2)]
    assert [(w["ayah"], w["status"]) for w in r["words"] if w["status"] != "correct"] == [(3, "wrong")] * 4
    assert r["correct"] == r["total"] - 4


def test_restart_mid_ayah_is_one_hesitation():
    a1 = said(1)  # الم تر كيف فعل ربك باصحاب الفيل
    heard = a1[:4] + a1[2:4] + a1[4:] + said(2, 3, 4, 5)  # "... كيف فعل, كيف فعل ربك ..."
    r = align(EXP, heard)
    assert [(e["kind"], e["words"]) for e in r["extras"]] == [("repeat", ["كيف", "فعل"])]
    assert r["accuracy"] == 1.0 and r["repeats"] == 1 and r["mistakes"] == 0


def test_words_doubled_in_the_text_itself_are_not_hesitations():
    fajr = expected_words(89, [(21, "كَلَّا إِذَا دُكَّتِ الْأَرْضُ دَكًّا دَكًّا")])
    r = align(fajr, norm_words("كلا إذا دكت الأرض دكا دكا"))
    assert r["accuracy"] == 1.0 and r["extras"] == []


# ---- live (prefix) alignment: the reciter is still going ----

def test_prefix_leaves_the_rest_pending_not_missed():
    r = align(EXP, said(1) + AYAH[2][:2], prefix=True)
    st = statuses(r)
    assert st[:9] == ["correct"] * 9 and set(st[9:]) == {"pending"}
    assert r["reached"] == 8 and r["mistakes"] == 0


def test_prefix_marks_a_skip_as_soon_as_the_next_words_come():
    r = align(EXP, said(1) + AYAH[3][:2], prefix=True)
    st = statuses(r)
    assert st[7:12] == ["missed"] * 5 and st[12:14] == ["correct"] * 2 and st[14] == "pending"


def test_prefix_wrong_word_inside_is_wrong():
    heard = said(1)
    heard[3] = "قال"
    r = align(EXP, heard, prefix=True)
    assert statuses(r)[3] == "wrong" and r["reached"] == 6


def test_prefix_with_nothing_heard():
    r = align(EXP, [], prefix=True)
    assert set(statuses(r)) == {"pending"} and r["reached"] == -1
