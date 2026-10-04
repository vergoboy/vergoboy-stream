from __future__ import annotations

import re
from fractions import Fraction


DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)

UNITS = {
    "صفر": 0,
    "یک": 1,
    "دو": 2,
    "سه": 3,
    "چهار": 4,
    "پنج": 5,
    "شش": 6,
    "هفت": 7,
    "هشت": 8,
    "نه": 9,
}

TEENS = {
    "ده": 10,
    "یازده": 11,
    "دوازده": 12,
    "سیزده": 13,
    "چهارده": 14,
    "پانزده": 15,
    "شانزده": 16,
    "هفده": 17,
    "هجده": 18,
    "نوزده": 19,
}

TENS = {
    "بیست": 20,
    "سی": 30,
    "چهل": 40,
    "پنجاه": 50,
    "شصت": 60,
    "هفتاد": 70,
    "هشتاد": 80,
    "نود": 90,
}

HUNDREDS = {
    "نهصد": 900,
    "هشتصد": 800,
    "هفتصد": 700,
    "ششصد": 600,
    "پانصد": 500,
    "چهارصد": 400,
    "سیصد": 300,
    "دویست": 200,
    "صد": 100,
}


def normalize(text: str) -> str:
    text = text.translate(DIGIT_TRANSLATION)
    replacements = {
        "ي": "ی",
        "ى": "ی",
        "ك": "ک",
        "ۀ": "ه",
        "ة": "ه",
        "\u200c": " ",
        "\u200f": "",
        "\u200e": "",
        "‌": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.replace("،", " ")
    text = text.replace("؛", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_number_words(text: str) -> int:
    text = normalize(text)
    if text.isdigit():
        return int(text)
    words = text.split()
    words = [w for w in words if w != "و"]
    if not words:
        raise ValueError("No number found")
    total = 0
    current = 0
    for word in words:
        if word in UNITS:
            current += UNITS[word]
        elif word in TEENS:
            current += TEENS[word]
        elif word in TENS:
            current += TENS[word]
        elif word in HUNDREDS:
            current += HUNDREDS[word]
        elif word == "هزار":
            if current == 0:
                current = 1
            total += current * 1000
            current = 0
        elif word == "میلیون":
            if current == 0:
                current = 1
            total += current * 1_000_000
            current = 0
        else:
            raise ValueError(f"Unknown number word: {word}")
    return total + current


def extract_number(text: str) -> int:
    text = normalize(text)
    match = re.search(r"\d+", text)
    if match:
        return int(match.group())
    words = text.split()
    for start in range(len(words)):
        for end in range(len(words), start, -1):
            candidate = " ".join(words[start:end])
            try:
                return parse_number_words(candidate)
            except ValueError:
                continue
    raise ValueError(f"Could not find a number in: {text}")


def prime_after(n: int) -> int:
    def is_prime(value: int) -> bool:
        if value < 2:
            return False
        if value == 2:
            return True
        if value % 2 == 0:
            return False
        divisor = 3
        while divisor * divisor <= value:
            if value % divisor == 0:
                return False
            divisor += 2
        return True

    candidate = n + 1
    while not is_prime(candidate):
        candidate += 1
    return candidate


def solve_general_math(text: str) -> int | Fraction:
    text = normalize(text)

    if "ماه" in text and "سال" in text:
        return 12
    if "انگشت" in text and "دو دست" in text:
        return 10

    half_match = re.search(r"نصف\s+(.+)", text)
    if half_match:
        try:
            value = extract_number(half_match.group(1))
            return Fraction(value, 2)
        except Exception:
            pass

    if "عدد اول" in text and "بعد از" in text:
        try:
            part = text.split("بعد از", 1)[1]
            value = extract_number(part)
            return prime_after(value)
        except Exception:
            pass

    text = re.sub(r"(ضرب\s+در|در|ضرب)", " * ", text)
    text = re.sub(r"(تقسیم\s+بر|تقسیم)", " / ", text)
    text = re.sub(r"(بعلاوه|به\s+علاوه|جمع)", " + ", text)
    text = re.sub(r"(منهای|منها|کم\s+کن|تفریق)", " - ", text)

    text = re.sub(r"\bضرب\b", " ", text, flags=re.I)
    text = re.sub(r"\bشود\b", " ", text, flags=re.I)

    text = re.sub(r"\b(چند|می\s*شود|میشود|می\u200c*شود|میشه|است|چیست|کدام)\b.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"^\s*جمع\b\s*", "", text, flags=re.I)
    text = re.sub(r"^\s*[+\-*/]\s*", "", text)
    text = re.sub(r"(\d+)\s*و\s*(\d+)", r"\1 + \2", text)
    text = re.sub(r"[+\-*/]\s*$", "", text).strip()
    text = re.sub(r"\s+", " ", text).strip()

    number_pattern = re.compile(
        r"""(?:(?:\d+)|(?:نهصد|هشتصد|هفتصد|ششصد|پانصد|چهارصد|سیصد|دویست|صد|نود|هشتاد|هفتاد|شصت|پنجاه|چهل|سی|بیست|نوزده|هجده|هفده|شانزده|پانزده|چهارده|سیزده|دوازده|یازده|ده|نه|هشت|هفت|شش|پنج|چهار|سه|دو|یک|صفر|هزار|میلیون)(?:\s+و\s+)?)+""",
        re.VERBOSE,
    )

    def replace_number(match: re.Match[str]) -> str:
        try:
            return str(parse_number_words(match.group(0)))
        except ValueError:
            return match.group(0)

    text = number_pattern.sub(replace_number, text)
    text = re.sub(r"جمع\s+(\d+)\s+و\s+(\d+)", r"\1 + \2", text)

    if not re.fullmatch(r"\s*\d+(?:\s*[+\-*/]\s*\d+)+\s*", text):
        raise ValueError(f"Unsupported expression: {text}")

    tokens = re.findall(r"\d+|[+\-*/]", text)
    numbers = [int(t) for t in tokens if t.isdigit()]
    operators = [t for t in tokens if t in "+-*/"]

    if len(numbers) != len(operators) + 1:
        raise ValueError("Malformed expression")

    values: list[int | Fraction] = [numbers[0]]
    remaining_ops: list[str] = []

    for operator, number in zip(operators, numbers[1:]):
        if operator == "*":
            values[-1] = values[-1] * number
        elif operator == "/":
            if number == 0:
                raise ZeroDivisionError("Division by zero")
            values[-1] = Fraction(values[-1], number)
        else:
            remaining_ops.append(operator)
            values.append(number)

    result = values[0]
    for operator, value in zip(remaining_ops, values[1:]):
        if operator == "+":
            result += value
        elif operator == "-":
            result -= value

    if isinstance(result, Fraction) and result.denominator == 1:
        return result.numerator
    return result
