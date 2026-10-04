"""Fixture-only recognition tests. This is not imported by production auth code."""
from __future__ import annotations

import re

PERSIAN = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
WORDS = {"هفت": 7, "هشت": 8, "نه": 9, "ده": 10, "پانزده": 15, "بیست": 20,
         "سی": 30, "چهل": 40, "پنجاه": 50, "شصت": 60, "هفتاد": 70,
         "هشتاد": 80, "صد": 100, "دویست": 200, "پانصد": 500, "ششصد": 600}


def recognize_fixture_answer(question: str) -> int:
    """Fixture helper only: validates normalization, never submits an answer."""
    q = question.translate(PERSIAN).replace("ۀ", "ه")
    if "انگشتان دو دست" in q: return 10
    if "ماه\u200cهای یک سال" in q or "ماه های یک سال" in q: return 12
    if "عدد اول بعد از 10" in q: return 11
    if "نصف 100" in q: return 50
    words = "|".join(sorted(WORDS, key=len, reverse=True))
    values = [int(n) if n.isdigit() else WORDS[n] for n in re.findall(r"\d+|(?<![\w\u0600-\u06ff])(?:" + words + r")(?![\w\u0600-\u06ff])", q)]
    if "ضرب" in q: return values[0] * values[1]
    if "تقسیم" in q: return values[0] // values[1]
    if "منهای" in q: return values[0] - values[1]
    return sum(values[:2])


def test_persian_digits_and_written_numbers_are_normalized():
    assert recognize_fixture_answer("۶ در ۸ ضرب شود") == 48
    assert recognize_fixture_answer("ششصد بعلاوۀ دویست") == 800
    assert recognize_fixture_answer("۵۰ تقسیم بر ۵") == 10
    assert recognize_fixture_answer("هفتاد منهای پانزده") == 55
    assert recognize_fixture_answer("پانصد بعلاوۀ چهل") == 540


def test_knowledge_templates_are_fixture_only():
    assert recognize_fixture_answer("تعداد انگشتان دو دست چند است؟") == 10
    assert recognize_fixture_answer("تعداد ماه‌های یک سال چند است؟") == 12
    assert recognize_fixture_answer("عدد اول بعد از ۱۰ چیست؟") == 11
    assert recognize_fixture_answer("چه عددی نصف ۱۰۰ است؟") == 50
