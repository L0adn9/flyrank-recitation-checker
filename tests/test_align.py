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


# ---- what counts as the same word, and going back ----
from app.align import close_enough  # noqa: E402
from app.quran import match_key, plausible  # noqa: E402


def key(text):
    return match_key(norm_words(text)[0])


def test_mushaf_and_modern_spelling_are_the_same_word():
    assert key("ٱلْعَٰلَمِينَ") == key("العالمين")
    assert key("ٱلرَّحْمَٰنِ") == key("الرحمن")
    assert key("ذَٰلِكَ") == key("ذلك")
    assert key("ٱلصَّلَوٰةَ") == key("الصلاة")


def test_sound_alike_letters_do_not_make_a_word_wrong():
    assert key("ٱلضَّآلِّينَ") == key("الظالين")
    assert key("ٱلدِّينِ") == key("التين")


def test_different_words_stay_different():
    for a, b in [("قال", "قل"), ("عليهم", "إليهم"), ("الرحمن", "الرحيم")]:
        assert key(a) != key(b) and not close_enough(key(a), key(b))
    # first and last letters carry the word's form: يعلمون / تعلمون, عليه / عليهم
    for a, b in [("يعلمون", "تعلمون"), ("عليهم", "عليه")]:
        assert not close_enough(key(a), key(b))


def test_a_word_heard_almost_right_is_right():
    assert close_enough(key("ٱلْمَغْضُوبِ"), key("المخبوب"))          # one letter inside a long word
    assert close_enough(key("ٱلرَّحْمَٰنِ"), key("ٱرْوَحْمَٰنِ"))         # "ar-rahman", as pronounced
    assert close_enough(key("ٱلصِّرَٰطَ"), key("صِرَاطَ"))              # silent alef of "al" after a vowel


def test_restart_heard_slightly_differently_is_a_repeat_not_a_mistake():
    first_try = AYAH[3][:3]
    first_try[1] = "عليم"                       # the first attempt was heard a little differently
    r = align(EXP, said(1, 2) + first_try + said(3, 4, 5))
    assert r["mistakes"] == 0 and r["accuracy"] == 1.0
    assert [e["kind"] for e in r["extras"]] == ["repeat"]
    assert all(a["solid"] for a in r["ayahs"])   # going back is not held against the ayah


def test_going_back_several_words_is_a_repeat():
    r = align(EXP, said(1, 2) + AYAH[1][3:] + said(3, 4, 5))   # back to the middle of ayah 1
    assert r["mistakes"] == 0 and {e["kind"] for e in r["extras"]} == {"repeat"}


def test_amin_after_al_fatiha_is_not_an_extra():
    fatiha = expected_words(1, [(7, "صِرَاطَ الَّذِينَ أَنْعَمْتَ عَلَيْهِمْ غَيْرِ الْمَغْضُوبِ عَلَيْهِمْ وَلَا الضَّالِّينَ")])
    heard = clean_heard(norm_words("صراط الذين انعمت عليهم غير المغضوب عليهم ولا الضالين آمين"), fatiha)
    assert align(fatiha, heard)["extras"] == []


def test_opening_letters_are_matched_however_they_are_written():
    baqarah = expected_words(2, [(1, "بِسْمِ اللَّهِ الرَّحْمَـٰنِ الرَّحِيمِ الم"), (2, "ذَٰلِكَ الْكِتَابُ لَا رَيْبَ ۛ فِيهِ ۛ هُدًى لِّلْمُتَّقِينَ")])
    heard = clean_heard(norm_words("فالفلمي ذلك الكتاب لا ريب فيه هدى للمتقين"), baqarah)
    assert align(baqarah, heard)["accuracy"] == 1.0


def test_made_up_text_is_rejected_by_its_length():
    assert plausible("الحمد لله رب العالمين", 3.0)
    assert not plausible("والله الرحمن الرحيم", 0.6)      # three words in an "amin"


def test_recurring_short_words_are_not_taken_for_going_back():
    ikhlas = expected_words(112, [(1, "بِسْمِ اللَّهِ الرَّحْمَـٰنِ الرَّحِيمِ قُلْ هُوَ اللَّهُ أَحَدٌ"), (2, "اللَّهُ الصَّمَدُ"),
                                  (3, "لَمْ يَلِدْ وَلَمْ يُولَدْ"), (4, "وَلَمْ يَكُن لَّهُ كُفُوًا أَحَدٌ")])
    r = align(ikhlas, norm_words("قل هو الله احد الله الصمد لم يلد ولم يولد ولم يكن له كفوا احد"))
    assert r["accuracy"] == 1.0 and r["extras"] == []


def test_aligning_part_of_a_passage():
    """Live checking aligns what is being said against the rest of the passage (a slice whose words keep
    their positions in the whole passage); a swapped pair there must not break anything."""
    rest = EXP[7:]   # from ayah 2 on
    r = align(rest, AYAH[3] + AYAH[2] + AYAH[4], prefix=True)
    st = [w["status"] for w in r["words"]]
    assert st[:5] == ["correct"] * 5 and st[5:9] == ["moved"] * 4   # ayah 2 said, ayah 3 out of order


def test_aligner_survives_anything():
    """Random recitations made of the passage's words with repeats, restarts, noise and skips, aligned
    in full and live (prefix) mode, against the whole passage and against parts of it. Never a crash,
    and every expected word gets exactly one status."""
    import random
    rnd = random.Random(7)
    words = [w for n in range(1, 6) for w in AYAH[n]]
    noise = ["قل", "والمومنين", "غ", "الله", "من"]
    for _ in range(2000):
        heard, i = [], 0
        while i < len(words) and len(heard) < 60:
            r = rnd.random()
            if r < 0.1:
                i = max(0, i - rnd.randint(1, 6))          # go back
            elif r < 0.15:
                heard.append(rnd.choice(noise))
            elif r < 0.2:
                i += rnd.randint(1, 4)                     # skip
            else:
                heard.append(words[i]); i += 1
        start = rnd.randint(0, 10)
        for exp in (EXP, EXP[start:]):
            for prefix in (False, True):
                r = align(exp, heard, prefix=prefix)
                assert len(r["words"]) == len(exp)
