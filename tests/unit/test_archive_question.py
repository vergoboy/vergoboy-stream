"""Question parsing/solving: pure functions, no HTTP."""
from __future__ import annotations

import pytest

from archive_question import UnsupportedQuestion, solve_question_text, solve_security_question
from security_math import normalize, parse_number_words, solve_general_math


def answer(text: str) -> str:
    return solve_question_text(text).answer


# --- Persian / Arabic digits -------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("۶ در ۸ ضرب شود", "48"),            # Persian digits
    ("٦ در ٨ ضرب شود", "48"),            # Arabic-Indic digits
    ("۱۲ بعلاوه ٨", "20"),               # mixed digit systems in one question
    ("۵۰ تقسیم بر ۵", "10"),
])
def test_persian_and_arabic_digits(text, expected):
    assert answer(text) == expected


def test_normalize_maps_both_digit_systems_and_letter_variants():
    assert normalize("۰۱۲۳۴۵۶۷۸۹ ٠١٢٣٤٥٦٧٨٩") == "0123456789 0123456789"
    assert normalize("بعلاوۀ") == "بعلاوه"
    assert normalize("ماه\u200cهای") == "ماه های"


# --- Persian number words ----------------------------------------------------
@pytest.mark.parametrize("words,value", [
    ("چهل", 40), ("پانصد", 500), ("ششصد", 600), ("بیست و پنج", 25),
    ("صد و بیست و سه", 123), ("هزار و دویست", 1200), ("پانزده", 15),
])
def test_number_words(words, value):
    assert parse_number_words(words) == value


@pytest.mark.parametrize("text,expected", [
    ("پانصد بعلاوۀ چهل", "540"),
    ("ششصد بعلاوۀ دویست", "800"),
    ("هفتاد منهای پانزده", "55"),
    ("بیست و پنج بعلاوه سه", "28"),
    ("هزار و دویست منهای صد", "1100"),
    ("سه در چهار", "12"),
])
def test_number_word_arithmetic(text, expected):
    assert answer(text) == expected


# --- supported arithmetic ----------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("۸ ضرب در ۵ چند می‌شود؟", "40"),
    ("۹ منهای ۲", "7"),
    ("جمع ۳ و ۵", "8"),
    ("۳ به علاوه ۴", "7"),
    ("۲ بعلاوه ۳ در ۴", "14"),           # multiplication binds tighter
    ("۳ منهای ۸", "-5"),
    ("سوال امنیتی: ۶ در ۸", "48"),      # prefix tolerated
])
def test_supported_arithmetic(text, expected):
    assert answer(text) == expected


# --- supported knowledge questions ------------------------------------------
@pytest.mark.parametrize("text,expected,kind", [
    ("تعداد انگشتان دو دست چند است؟", "10", "knowledge"),
    ("تعداد ماه‌های یک سال چند است؟", "12", "knowledge"),
    ("یک سال چند ماه دارد؟", "12", "knowledge"),
    ("عدد اول بعد از ۱۰ چیست؟", "11", "knowledge"),
    ("عدد اول بعد از بیست چیست؟", "23", "knowledge"),
    ("چه عددی نصف ۱۰۰ است؟", "50", "knowledge"),
    ("نصف دویست", "100", "knowledge"),
])
def test_supported_knowledge_questions(text, expected, kind):
    solved = solve_question_text(text)
    assert (solved.answer, solved.kind) == (expected, kind)


# --- unknown / malformed: refuse, never guess --------------------------------
@pytest.mark.parametrize("text", [
    None, "", "   ", "سوال امنیتی:",
    "رنگ آسمان چیست؟",                   # unknown knowledge question
    "پایتخت فرانسه کجاست",
    "ماه و سال و خورشید",                # mentions months/years but is not the template
    "چند ماه و چند سال",
    "انگشت دست چپ و راست",
    "۵ تقسیم بر ۰",                      # division by zero
    "۱ بعلاوه",                          # truncated expression
    "۶ در ۸ در",                         # dangling operator form with no result words
    "<script>alert(1)</script>",
    "۱۲۳۴۵ ۱۲ ۳۴",                       # numbers with no operator
])
def test_unknown_or_malformed_questions_are_refused(text):
    with pytest.raises(UnsupportedQuestion):
        solve_question_text(text)


@pytest.mark.parametrize("text", ["۷ تقسیم بر ۲", "نصف ۷"])
def test_non_integer_answers_are_refused_not_formatted(text):
    with pytest.raises(UnsupportedQuestion) as exc:
        solve_question_text(text)
    assert exc.value.reason == "non_integer_answer"


def test_solved_question_repr_never_contains_the_answer():
    assert "540" not in repr(solve_question_text("پانصد بعلاوۀ چهل"))


def test_solve_general_math_rejects_substring_operator_matches():
    # "در" inside a larger word must not become a multiplication operator.
    with pytest.raises(ValueError):
        solve_general_math("دروازه ۲ ۳")


def test_html_wrapper_uses_only_the_real_form_not_the_popup():
    html = ('<div class="popup_box"><form><input type="hidden" name="secureq_key" value="9">'
            '<span>سوال امنیتی: ۱ بعلاوه ۱</span><input name="secureq_ans"><input type="password" name="p"></form></div>'
            '<form><input type="password" name="password"><input type="hidden" name="secureq_key" value="1">'
            '<span>سوال امنیتی: ۶ در ۸</span><input name="secureq_ans"></form>')
    assert solve_security_question(html) == "48"
    assert solve_security_question("<html>no form</html>") is None
