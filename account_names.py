"""Usernames and passwords: one place for the rules, and a safe way to derive a username from an email.

`username_from_email` replaces an inline expression that stripped every non-ASCII-word character
from the WHOLE email (including the '@') before splitting on '@', so "very.good.booyy@gmail.com"
became "very.good.booyygmail.com". It now takes the local part first.
"""
from __future__ import annotations

import re
import secrets
from typing import Callable

USERNAME_MIN = 3
USERNAME_MAX = 24
PASSWORD_MIN = 6
_VALID = re.compile(r"^[A-Za-z0-9_.\-]+$")
_INVALID_CHARS = re.compile(r"[^A-Za-z0-9_.\-]")


def username_error(name: str) -> str | None:
    """Persian message for the first problem, or None when the name is acceptable."""
    if len(name) < USERNAME_MIN or len(name) > USERNAME_MAX:
        return "نام کاربری باید ۳ تا ۲۴ حرف باشد"
    if not _VALID.match(name):
        return "نام کاربری فقط حروف انگلیسی، عدد، _ و - می‌تواند باشد"
    return None


def password_error(password: str) -> str | None:
    if len(password) < PASSWORD_MIN:
        return "رمز عبور باید حداقل ۶ کاراکتر باشد"
    return None


def username_from_email(email: str | None, taken: Callable[[str], bool]) -> str:
    """A valid, free username derived from the part of `email` before '@'.

    `taken(name)` says whether a name is already used. Always returns a name that passes
    `username_error`, even for empty/unicode/very short local parts and under heavy collisions."""
    local = (email or "").split("@", 1)[0]
    base = _INVALID_CHARS.sub("", local).strip("._-")
    if len(base) < USERNAME_MIN:
        base = (base + "user")[:USERNAME_MAX] if base else "user"
    base = base[:USERNAME_MAX]
    if not taken(base):
        return base
    for digits in (2, 3, 4, 5):
        for _ in range(12):
            suffix = "".join(secrets.choice("0123456789") for _ in range(digits))
            candidate = base[: USERNAME_MAX - digits] + suffix
            if not taken(candidate):
                return candidate
    return "user" + secrets.token_hex(8)  # 20 chars; collisions are astronomically unlikely
