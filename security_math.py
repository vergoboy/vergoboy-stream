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


_QUESTION_TAIL = r"(?:\s+(?:چیست|کدام(?:\s+است)?|چند(?:\s+است|\s+می\s*شود|\s+تاست)?|چه\s+عددی(?:\s+است)?|است|هست))?"


def _strip_punctuation(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[؟?!.:]", " ", text)).strip()


def solve_knowledge(text: str) -> int | Fraction | None:
    """Answer only the exact knowledge templates seen on the login page.

    Every template is anchored (``fullmatch``).  A question that merely
    *mentions* months or fingers is never answered: returning ``None`` makes the
    caller refuse instead of submitting a guess.
    """
    text = _strip_punctuation(normalize(text))
    if re.fullmatch(r"(?:تعداد )?انگشت(?:ان|ها)? دو دست(?: انسان)?" + _QUESTION_TAIL, text):
        return 10
    if re.fullmatch(r"(?:تعداد )?ماه(?:ها| ها| های)? (?:یک|هر) سال(?: شمسی| میلادی| هجری)?" + _QUESTION_TAIL, text) \
            or re.fullmatch(r"یک سال (?:شمسی |میلادی |هجری )?چند ماه(?: دارد| است| هست)?", text):
        return 12
    prime = re.fullmatch(r"(?:چه )?(?:اولین )?عدد اول بعد از (.+?)" + _QUESTION_TAIL, text)
    if prime:
        try:
            return prime_after(parse_number_words(prime.group(1)))
        except ValueError:
            return None
    half = re.fullmatch(r"(?:چه عددی )?نصف (.+?)" + _QUESTION_TAIL, text)
    if half:
        try:
            return Fraction(parse_number_words(half.group(1)), 2)
        except ValueError:
            return None
    return None


def solve_general_math(text: str) -> int | Fraction:
    """Solve a supported knowledge or arithmetic question, else ``ValueError``."""
    known = solve_knowledge(text)
    if known is not None:
        return known.numerator if isinstance(known, Fraction) and known.denominator == 1 else known
    text = normalize(text)

    # The one idiom with a trailing verb: "6 در 8 ضرب شود".
    text = re.sub(r"(?<!\w)ضرب\s+(?:شود|کن|کنید)(?!\w)", " ", text)
    text = re.sub(r"(?<!\w)(ضرب\s+در|در|ضرب)(?!\w)", " * ", text)
    text = re.sub(r"(?<!\w)(تقسیم\s+بر|تقسیم)(?!\w)", " / ", text)
    text = re.sub(r"(?<!\w)(بعلاوه|به\s+علاوه|جمع)(?!\w)", " + ", text)
    text = re.sub(r"(?<!\w)(منهای|منها|کم\s+کن|تفریق)(?!\w)", " - ", text)

    text = re.sub(r"\bضرب\b", " ", text, flags=re.I)
    text = re.sub(r"\bشود\b", " ", text, flags=re.I)

    text = re.sub(r"\b(چند|می\s*شود|میشود|می\u200c*شود|میشه|است|چیست|کدام)\b.*$", "", text, flags=re.I | re.S)
    text = re.sub(r"^\s*جمع\b\s*", "", text, flags=re.I)
    text = re.sub(r"^\s*[+\-*/]\s*", "", text)
    text = re.sub(r"(\d+)\s*و\s*(\d+)", r"\1 + \2", text)
    text = text.strip()
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
