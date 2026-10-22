"""Username/password rules and deriving a username from an email."""
from __future__ import annotations

import pytest

import account_names as an


def free(_):
    return False


@pytest.mark.parametrize("email,want", [
    ("very.good.booyy@gmail.com", "very.good.booyy"),     # the reported bug: the domain must NOT stay in the name
    ("alice@example.org", "alice"),
    ("Ali_Reza-99@mail.ir", "Ali_Reza-99"),
    ("john+newsletter@x.com", "johnnewsletter"),          # '+' is not allowed in a username
    ("a@x.com", "auser"),                                  # too short -> padded to a valid length
    ("..a..@x.com", "auser"),
    ("علی@x.com", "user"),                                 # nothing usable left
    ("", "user"),
    (None, "user"),
    ("noatsign", "noatsign"),
])
def test_username_from_email(email, want):
    got = an.username_from_email(email, free)
    assert got == want
    assert an.username_error(got) is None


def test_the_domain_part_never_leaks_into_the_name():
    got = an.username_from_email("someone@gmail.com", free)
    assert "gmail" not in got and "@" not in got


def test_very_long_local_part_is_cut_to_the_limit():
    got = an.username_from_email("x" * 80 + "@y.com", free)
    assert len(got) == an.USERNAME_MAX and an.username_error(got) is None


def test_collisions_get_a_numeric_suffix_and_still_fit():
    used = {"alice"}
    got = an.username_from_email("alice@x.com", lambda n: n in used)
    assert got != "alice" and got.startswith("alice") and got[5:].isdigit() and an.username_error(got) is None
    long_base = "y" * 30
    used = {"y" * 24}
    got = an.username_from_email(long_base + "@x.com", lambda n: n in used)
    assert len(got) <= an.USERNAME_MAX and an.username_error(got) is None and got != "y" * 24


def test_it_keeps_trying_when_the_first_suffixes_are_also_taken():
    calls = []

    def taken(n):
        calls.append(n)
        return len(calls) <= 30          # the base and the next 29 candidates are all taken

    got = an.username_from_email("bob@x.com", taken)
    assert an.username_error(got) is None and len(calls) > 3


def test_if_everything_is_taken_a_random_valid_name_is_returned():
    got = an.username_from_email("bob@x.com", lambda n: n.startswith("bob"))
    assert an.username_error(got) is None and not got.startswith("bob")


@pytest.mark.parametrize("name,ok", [
    ("abc", True), ("a" * 24, True), ("ab", False), ("a" * 25, False), ("نام", False),
    ("a b", False), ("a@b", False), ("a.b_c-d", True), ("", False),
])
def test_username_rules(name, ok):
    assert (an.username_error(name) is None) is ok


@pytest.mark.parametrize("pw,ok", [("123456", True), ("12345", False), ("", False), ("a" * 200, True)])
def test_password_rules(pw, ok):
    assert (an.password_error(pw) is None) is ok
