from __future__ import annotations

import logging
import re

from fractions import Fraction
from security_math import solve_general_math

LOG = logging.getLogger("archive.question")


def extract_security_question(html: str) -> str | None:
    html_norm = html
    # look for question text
    patterns = [
        r'سوال\s+امنیتی\s*:?\s*([^\<\n]+)',
        r'secureq_ans[^\<]*</?label[^>]*>([^\<]+)',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.I)
        if m:
            q = m.group(1).strip()
            # clean extra chars
            q = re.sub(r'\s+', ' ', q)
            return q
    return None


def solve_security_question(html: str) -> str | None:
    q = extract_security_question(html)
    if not q:
        LOG.debug("AUTH_CHALLENGE_SOLVE parse_success=false submitted=false")
        return None
    parsed = q
    parsed_clean = re.sub(r'^\s*سوال\s+امنیتی\s*:?\s*', '', parsed, flags=re.I)
    try:
        res = solve_general_math(parsed_clean)
        answer = str(res) if isinstance(res, (int, Fraction)) else str(res)
        LOG.debug("AUTH_CHALLENGE_SOLVE question=%r parsed=%r answer=%r parse_success=true submitted=false", q, parsed_clean, answer)
        return answer
    except Exception as exc:
        try:
            res = solve_general_math(q)
            answer = str(res)
            LOG.debug("AUTH_CHALLENGE_SOLVE question=%r parsed=%r answer=%r parse_success=true submitted=false", q, q, answer)
            return answer
        except Exception:
            LOG.debug("AUTH_CHALLENGE_SOLVE question=%r parse_success=false submitted=false", q)
            return None
