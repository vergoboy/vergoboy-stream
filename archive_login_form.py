"""Pure parsing of a Digimoviez login page.  No HTTP, no session, no logging.

``parse_login_form`` turns one HTML response into one immutable ``LoginForm``.
The security question and its ``secureq_key`` are read from the *same* document
and the same container, so they can never be paired across two page loads.

Dormant login/register popups that the theme renders into every page are
skipped structurally (they carry their own decoy question and key).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterator, Mapping
from urllib.parse import urljoin, urlsplit

from archive_challenge import ManualOnlyChallengeHandler, _normalize

QUESTION_MARKER = "سوال امنیتی"
KEY_FIELD = "secureq_key"
ANSWER_FIELD = "secureq_ans"

_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
                   "source", "track", "wbr"})


class LoginFormError(RuntimeError):
    """The form cannot be submitted safely (e.g. it posts to another origin)."""


class ChallengeStaleError(LoginFormError):
    """A solution was about to be combined with a form it was not derived from."""


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    parent: "_Node | None" = None
    children: list["_Node"] = field(default_factory=list)
    text: str = ""  # only for tag == "#text"


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root")
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {k.lower(): (v or "") for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1].children.append(_Node(tag, {k.lower(): (v or "") for k, v in attrs}, self._stack[-1]))

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, 0, -1):  # tolerate unbalanced markup
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data):
        if data.strip():
            self._stack[-1].children.append(_Node("#text", parent=self._stack[-1], text=data))


def _is_popup(node: _Node) -> bool:
    classes = set(node.attrs.get("class", "").lower().split())
    return node.attrs.get("id", "").lower() in ManualOnlyChallengeHandler.POPUP_CONTAINERS \
        or bool(classes & ManualOnlyChallengeHandler.POPUP_CONTAINERS)


def _walk(node: _Node) -> Iterator[_Node]:
    """Document-order walk that never enters a dormant popup."""
    for child in node.children:
        if child.tag != "#text" and _is_popup(child):
            continue
        yield child
        yield from _walk(child)


@dataclass(frozen=True)
class LoginForm:
    """Everything needed to answer one specific login-page response."""
    action: str
    hidden_fields: Mapping[str, str]
    input_names: frozenset[str]
    has_password_input: bool
    secureq_key: str | None
    question: str | None
    answer_field: str | None
    submit: tuple[str, str] | None

    @property
    def has_challenge_fields(self) -> bool:
        return self.answer_field is not None or self.secureq_key is not None

    def is_login_form(self, username_field: str = "", password_field: str = "") -> bool:
        if not self.has_password_input:
            return False
        return not username_field or username_field in self.input_names

    def resolve_action(self, page_url: str, login_url: str) -> str:
        """Same-origin form target; credentials are never posted elsewhere."""
        target = urljoin(page_url, self.action) if self.action else login_url
        a, b = urlsplit(target), urlsplit(login_url)
        if (a.scheme, a.netloc.lower()) != (b.scheme, b.netloc.lower()):
            raise LoginFormError("form_action_cross_origin")
        return target


def _text_nodes(node: _Node) -> list[_Node]:
    return [n for n in _walk(node) if n.tag == "#text"]


def _question_for(key: _Node, scope: _Node) -> str | None:
    """Question text from the container nearest to *this* key (3 levels max)."""
    container = key.parent
    for _ in range(4):
        if container is None:
            return None
        hits = [t for t in _text_nodes(container) if QUESTION_MARKER in _normalize(t.text)]
        if len(hits) > 1:
            return None  # ambiguous: refuse rather than choose
        if hits:
            norm = _normalize(hits[0].text)
            after = norm.split(QUESTION_MARKER, 1)[1].lstrip(" :：").strip()
            if not after:  # "سوال امنیتی:" and the expression are separate nodes
                siblings = _text_nodes(container)
                position = siblings.index(hits[0])
                after = _normalize(siblings[position + 1].text).strip() if position + 1 < len(siblings) else ""
            return re.sub(r"\s+", " ", after) or None
        if container is scope:
            return None
        container = container.parent
    return None


def parse_login_form(html: str) -> LoginForm | None:
    """Parse the login form of one response; ``None`` if there is none."""
    builder = _TreeBuilder()
    try:
        builder.feed(html or "")
        builder.close()
    except Exception:  # malformed markup must not crash authentication
        return None
    root = builder.root

    def interesting(scope: _Node) -> bool:
        return any(n.tag == "input" and (n.attrs.get("type", "").lower() == "password"
                                         or n.attrs.get("name") == ANSWER_FIELD) for n in _walk(scope))

    scope = next((n for n in _walk(root) if n.tag == "form" and interesting(n)), None)
    if scope is None:
        # Some themes/fixtures omit or mangle the <form> tag; fall back to the
        # whole non-popup document, but only if it really holds login inputs.
        if not interesting(root):
            return None
        scope = root

    inputs = [n for n in _walk(scope) if n.tag == "input" and n.attrs.get("name")]
    hidden = {n.attrs["name"]: n.attrs.get("value", "") for n in inputs if n.attrs.get("type", "").lower() == "hidden"}
    key_nodes = [n for n in inputs if n.attrs["name"] == KEY_FIELD]
    key_node = key_nodes[0] if len(key_nodes) == 1 else None  # two keys -> ambiguous
    submit = None
    for n in _walk(scope):
        kind = n.attrs.get("type", "submit" if n.tag == "button" else "").lower()
        if (n.tag == "button" or n.tag == "input") and kind == "submit" and n.attrs.get("name"):
            submit = (n.attrs["name"], n.attrs.get("value", ""))
            break
    answer = next((n.attrs["name"] for n in inputs if n.attrs["name"] == ANSWER_FIELD), None)
    return LoginForm(
        action=scope.attrs.get("action", "") if scope.tag == "form" else "",
        hidden_fields=hidden,
        input_names=frozenset(n.attrs["name"] for n in inputs),
        has_password_input=any(n.attrs.get("type", "").lower() == "password" for n in inputs),
        secureq_key=(key_node.attrs.get("value", "") or None) if key_node else None,
        question=_question_for(key_node, scope) if key_node else None,
        answer_field=answer,
        submit=submit,
    )


def build_login_payload(form: LoginForm, *, username_field: str, username: str,
                        password_field: str, password: str, solution=None) -> dict[str, str]:
    """Browser-equivalent POST body for *this* form.

    ``solution`` must have been derived from ``form`` itself; the key and the
    question are compared so a stale answer can never be sent with a new key.
    """
    payload = dict(form.hidden_fields)  # CSRF/nonce/referer exactly as served
    if form.submit:
        payload[form.submit[0]] = form.submit[1]
    payload[username_field], payload[password_field] = username, password
    if solution is not None:
        if solution.key != form.secureq_key or solution.question != form.question \
                or solution.answer_field != form.answer_field:
            raise ChallengeStaleError("solution does not belong to this form")
        payload[KEY_FIELD] = form.secureq_key  # always the key served with the question
        payload[solution.answer_field] = solution.answer
    return payload
