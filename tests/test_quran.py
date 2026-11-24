"""The two Tanzil editions: every simple-spelling word must map onto the mushaf word it belongs to."""
from difflib import SequenceMatcher

from app.quran import UTHMANI_FILE, display_for, expected_words, load_text_file, norm_word, split_basmala


def test_vocative_is_one_word_in_the_mushaf():
    shown = display_for("يَا قَوْمِ اعْبُدُوا اللَّهَ".split(), "يَـٰقَوْمِ ٱعْبُدُوا۟ ٱللَّهَ".split())
    assert shown == ["يَـٰقَوْمِ", "", "ٱعْبُدُوا۟", "ٱللَّهَ"]


def test_three_words_can_be_one():
    assert display_for("يَا ابْنَ أُمَّ لَا".split(), "يَبْنَؤُمَّ لَا".split()) == ["يَبْنَؤُمَّ", "", "", "لَا"]


def test_expected_words_compare_simple_and_show_uthmani():
    words = expected_words(105, [(2, "أَلَمْ يَجْعَلْ كَيْدَهُمْ فِي تَضْلِيلٍ", "أَلَمْ يَجْعَلْ كَيْدَهُمْ فِى تَضْلِيلٍ")])
    assert [w.norm for w in words] == ["الم", "يجعل", "كيدهم", "في", "تضليل"]
    assert words[3].text == "فِى"


def test_every_ayah_of_the_quran_maps():
    simple = {(s, a): t for s, a, t in load_text_file()}
    uthmani = {(s, a): t for s, a, t in load_text_file(UTHMANI_FILE)}
    assert len(simple) == len(uthmani) == 6236
    for key, text in simple.items():
        st = [t for t in split_basmala(*key, text)[1].split() if norm_word(t)]
        ut = [t for t in split_basmala(*key, uthmani[key])[1].split() if norm_word(t)]
        shown = display_for(st, ut)
        assert [w for w in shown if w] == ut, key
        for i, w in enumerate(shown):
            if not w:
                continue
            n = 1
            while i + n < len(shown) and shown[i + n] == "":
                n += 1
            joined = "".join(norm_word(t) for t in st[i:i + n])
            assert SequenceMatcher(a=joined, b=norm_word(w)).ratio() >= 0.5, (key, st[i:i + n], w)


def test_pause_marks_stay_on_their_word():
    from app.quran import display_tokens
    assert display_tokens("شُعَيْبًا ۚ قَالَ يَـٰقَوْمِ") == ["شُعَيْبًاۚ", "قَالَ", "يَـٰقَوْمِ"]
    assert display_tokens("۞ وَإِلَىٰ مَدْيَنَ") == ["۞ وَإِلَىٰ", "مَدْيَنَ"]
    assert display_tokens("وَٱسْجُدُوا۟ ۩") == ["وَٱسْجُدُوا۟ ۩"]
