from __future__ import annotations

from guru.translate import MarianTranslator, extract_numbers, normalize_digits, normalize_source


def test_bengali_digits_are_normalised():
    assert normalize_digits("তাপমাত্রা ১০০ ডিগ্রি") == "তাপমাত্রা 100 ডিগ্রি"
    assert extract_numbers("at 40 degrees and ৪৫ percent") == ["40", "৪৫"]


def test_normalize_source_collapses_whitespace():
    assert normalize_source("  hello \n\t world  ") == "hello world"


def _translator_with_output(settings, output: str) -> MarianTranslator:
    tr = MarianTranslator(settings)
    tr._translator = object()  # bypass model loading
    tr._tokenizer = object()
    tr._translate_one = lambda text, beam: output  # type: ignore[method-assign]
    return tr


def test_numbers_lost_are_reported(settings):
    tr = _translator_with_output(settings, "পানির তাপমাত্রা বেশি")
    res = tr.translate_batch(["Water boils at 100 degrees"])[0]
    assert res.text == "পানির তাপমাত্রা বেশি"
    assert res.numbers_preserved is False
    assert res.missing_numbers == ["100"]


def test_bengali_numeral_counts_as_preserved(settings):
    tr = _translator_with_output(settings, "সমুদ্রপৃষ্ঠে ১০০ ডিগ্রি সেলসিয়াস")
    res = tr.translate_batch(["Water boils at 100 degrees"])[0]
    assert res.numbers_preserved is True
    assert res.missing_numbers == []


def test_math_only_text_is_skipped(settings):
    tr = _translator_with_output(settings, "irrelevant")
    res = tr.translate_batch(["100 + 200 = 300"])[0]
    assert res.skipped is True
    assert res.text == "100 + 200 = 300"


def test_overlong_input_is_skipped(settings):
    settings.translate.max_input_chars = 20
    tr = _translator_with_output(settings, "x")
    res = tr.translate_batch(["a" * 50])[0]
    assert res.skipped is True
    assert "too long" in (res.reason or "")


def test_translation_error_does_not_raise(settings):
    tr = MarianTranslator(settings)
    tr._translator = object()
    tr._tokenizer = object()

    def boom(text, beam):
        raise RuntimeError("model exploded")

    tr._translate_one = boom  # type: ignore[method-assign]
    res = tr.translate_batch(["some sentence"])[0]
    assert res.skipped is True
    assert "translation error" in (res.reason or "")
