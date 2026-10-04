"""Security-question text -> answer.  Pure functions; nothing here logs a question or an answer."""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

from security_math import normalize, solve_general_math, solve_knowledge

_PREFIX = re.compile(r"^\s*سوال\s+امنیتی\s*:?\s*")


class UnsupportedQuestion(ValueError):
    """The text is not a supported format; the caller must not submit a guess."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SolvedQuestion:
    answer: str = ""
    kind: str = ""  # "knowledge" | "arithmetic"; never the question text itself

    def __repr__(self) -> str:  # keep the answer out of tracebacks/logs
        return f"SolvedQuestion(kind={self.kind!r})"


def solve_question_text(text: str | None) -> SolvedQuestion:
    cleaned = _PREFIX.sub("", normalize(text or ""))
    if not cleaned:
        raise UnsupportedQuestion("empty_question")
    try:
        value = solve_knowledge(cleaned)
        kind = "knowledge"
        if value is None:
            value, kind = solve_general_math(cleaned), "arithmetic"
    except (ValueError, ZeroDivisionError, RecursionError):
        raise UnsupportedQuestion("unsupported_question") from None
    if isinstance(value, Fraction):
        if value.denominator != 1:
            # The answer box is <input type=number>; never invent a format.
            raise UnsupportedQuestion("non_integer_answer")
        value = value.numerator
    return SolvedQuestion(str(int(value)), kind)


def extract_security_question(html: str) -> str | None:
    """Question of the real (non-popup) login form, or None."""
    from archive_login_form import parse_login_form
    form = parse_login_form(html or "")
    return form.question if form else None


def solve_security_question(html: str) -> str | None:
    """Convenience wrapper: the answer for the page's own question, or None."""
    try:
        return solve_question_text(extract_security_question(html)).answer
    except UnsupportedQuestion:
        return None
