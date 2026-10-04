"""Pluggable security-challenge boundary for archive authentication.

``ManualOnlyChallengeHandler`` only detects a challenge and never answers it;
``AutomaticChallengeHandler`` (archive_challenge_auto) adds strict solving.

Detection is *structural*, not a substring search.  Digimoviez renders a dormant
login/register popup (with its security-question form) into every page, so a
naive search for "سوال امنیتی" reports a challenge on public pages that need no
authentication at all and would deadlock a perfectly valid session.  Challenge
markup is therefore only honoured when it appears outside such a popup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from archive_login_form import LoginForm


@dataclass(frozen=True)
class ChallengeInspection:
    present: bool
    kind: str | None = None


class UnsolvableChallenge(RuntimeError):
    """The challenge cannot be answered safely; ``reason`` is a non-secret code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ChallengeSolution:
    """An answer bound to the exact form (key + question) it was computed from."""
    key: str
    question: str
    answer_field: str
    answer: str = field(repr=False)  # a secret: excluded from repr/logs
    kind: str = ""


class ChallengeHandler(Protocol):
    def inspect(self, html: str) -> ChallengeInspection: ...
    def solve(self, form: "LoginForm") -> ChallengeSolution: ...


def _normalize(text: str) -> str:
    """Fold Arabic letter variants so Persian markup matches the markers."""
    return text.replace("ي", "ی").replace("ك", "ک").replace("‌", " ").lower()


_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
                        "source", "track", "wbr"})


class _OutsidePopup(HTMLParser):
    """Collect markup that is *not* nested inside a dormant login popup.

    ``HTMLParser`` gives real subtree tracking, so a popup's own challenge form
    is skipped without trying to balance ``<div>`` tags by hand.
    """

    def __init__(self, popup_markers: frozenset[str]) -> None:
        super().__init__(convert_charrefs=True)
        self._markers = popup_markers
        self._depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if self._depth:
            # Void elements (<input>, <br>, ...) never get an end tag; counting
            # them would leave the depth stuck and hide the rest of the page.
            if tag not in _VOID_TAGS:
                self._depth += 1
            return
        identity = attributes.get("id", "").lower()
        classes = attributes.get("class", "").lower().split()
        if identity in self._markers or any(name in self._markers for name in classes):
            self._depth = 1
            return
        # Start tags carry the widget markers (class="g-recaptcha",
        # data-sitekey, ...), so they are part of the searchable markup.
        self._parts.append(f"<{tag} {' '.join(f'{k}={v}' for k, v in attributes.items())}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._depth:
            return
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if attributes.get("id", "").lower() in self._markers:
            return
        self._parts.append(f"<{tag} {' '.join(f'{k}={v}' for k, v in attributes.items())}>")

    def handle_endtag(self, tag: str) -> None:
        if self._depth and tag not in _VOID_TAGS:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._depth:
            self._parts.append(data)

    @property
    def markup(self) -> str:
        return " ".join(self._parts)


class ManualOnlyChallengeHandler:
    """Recognizes common challenge markup and preserves the session for a user."""

    # Dormant login/register/forgot-password popups.  Verified on the live
    # theme: public search and post pages carry the security question inside
    # ``div.popup_box > div.inner_popup_box > div.body_popup > form.*_box_form``,
    # while the real login page carries it in ``form.dashboard_form`` with no
    # popup ancestor.  Class names are used because the popup's ``id`` is a
    # per-page WordPress block id and is not stable.
    POPUP_CONTAINERS = frozenset({
        "popup_box", "inner_popup_box", "body_popup", "box_tab", "box_tab2",
        "login_box", "register_box", "forgetpass_security_str",
    })
    # Security-question markers, matched only outside POPUP_CONTAINERS.
    QUESTION_MARKERS = ("سوال امنیتی", "سوال امنيتي", "secureq_ans", "secureq_key")
    # CAPTCHA/Turnstile are matched structurally.  A bare "captcha" substring
    # also appears in script/plugin URLs on ordinary pages, which would report
    # a challenge that no visitor is ever shown.
    CAPTCHA_MARKERS = ("g-recaptcha", "h-captcha", "cf-turnstile", "data-sitekey", "data-cf-turnstile", "grecaptcha")

    def inspect(self, html: str) -> ChallengeInspection:
        markup = self._outside_popups(html or "")
        for marker in self.CAPTCHA_MARKERS:
            if marker in markup:
                return ChallengeInspection(True, "captcha")
        for marker in self.QUESTION_MARKERS:
            if marker in markup:
                return ChallengeInspection(True, "security_question")
        return ChallengeInspection(False)

    def solve(self, form: "LoginForm") -> ChallengeSolution:
        raise UnsolvableChallenge("manual_only")

    def _outside_popups(self, html: str) -> str:
        parser = _OutsidePopup(self.POPUP_CONTAINERS)
        try:
            parser.feed(html)
            parser.close()
        except Exception:
            # Malformed markup must not be read as "no challenge"; fall back to
            # the whole document so the conservative answer wins.
            return _normalize(html)
        return _normalize(parser.markup)
