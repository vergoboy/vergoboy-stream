"""Auth tokens must travel in headers/body/auth-dicts — never in the query
string, where they land in logs, history and Referer headers (audit S2,
"access token accepted via query string"). Failing-before/passing-after.

verify-email/reset tokens are *single-purpose email tokens* and stay exactly
where they are; this file is only about the long-lived access token.
"""
from __future__ import annotations

import pytest

import app


def _query_context(token: str):
    return app.app.test_request_context(
        "/stream/api/something", query_string={"token": token, "room": "X"},
    )


def test_bearer_token_is_not_read_from_the_query_string():
    """Failing-before: `?token=abc` was accepted."""
    with _query_context("abc"):
        assert app._bearer_token() == ""


def test_bearer_token_still_read_from_the_authorization_header():
    with app.app.test_request_context("/", headers={"Authorization": "Bearer s3cr3t"}):
        assert app._bearer_token() == "s3cr3t"


def test_bearer_token_still_read_from_form_and_json_body():
    with app.app.test_request_context("/", data={"token": "formtok"}):
        assert app._bearer_token() == "formtok"
    with app.app.test_request_context("/", json={"token": "jsontok"}):
        assert app._bearer_token() == "jsontok"


def test_bearer_token_ignores_query_when_other_channels_are_empty():
    """A query token must not ride along with a form/header request."""
    with app.app.test_request_context(
        "/", query_string={"token": "malice"}, data={"token": "formtok"},
    ):
        assert app._bearer_token() == "formtok"


def test_on_connect_rejects_a_query_string_token(monkeypatch):
    """Failing-before: the token was pulled from the query and handed to the
    decoder (and, being a valid-looking payload, into the DB layer). Passing-
    after it is "" so the decoder — and DB — are never reached."""
    decoded: list = []

    def _fake_decode(token):
        decoded.append(token)
        return {"sub": "u", "type": "access"}

    monkeypatch.setattr(app.dbmod, "decode_token", _fake_decode)
    monkeypatch.setattr(
        app.dbmod, "SessionLocal",
        lambda: pytest.fail("DB was reached — the query string token was accepted"),
    )
    with app.app.test_request_context("/socket.io/", query_string={"token": "abc"}):
        from flask import request

        request._get_current_object().sid = "sock1"
        assert app.on_connect(auth=None) is False
    assert decoded == []