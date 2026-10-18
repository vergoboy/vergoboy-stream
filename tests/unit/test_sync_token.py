"""sync_token: tokens for the Rust room sync service.

FIXTURE is asserted byte-for-byte here AND in w2g-server's tests/auth.rs
(`python_minted_token_verifies`); if either side changes the format, one of the two fails.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

import sync_token

SECRET = b"0123456789abcdef0123456789abcdef"
FIXTURE = (
    "v1.eyJleHAiOjQxMDI0NDQ4MDAsInJvbGUiOiJjb250cm9sbGVyIiwicm9vbSI6IlIxIiwic3ViIjoiYWxpY2UifQ."
    "0v1oyyKGYHuqUWYEGS--xLVnOnyw3AfrSyCzaupyuRY"
)


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_fixture_is_stable():
    assert sync_token.mint(SECRET, "alice", "R1", "controller", 4102444800) == FIXTURE


def test_payload_and_signature_roundtrip():
    version, p, sig = FIXTURE.split(".")
    assert version == "v1"
    assert json.loads(_unb64(p)) == {"sub": "alice", "room": "R1", "role": "controller", "exp": 4102444800}
    want = hmac.new(SECRET, f"v1.{p}".encode(), hashlib.sha256).digest()
    assert _unb64(sig) == want
    assert "=" not in FIXTURE  # unpadded base64url, as the Rust side expects


def test_non_ascii_subject_is_kept_utf8():
    t = sync_token.mint(SECRET, "علی", "R1", "watcher", 4102444800)
    assert json.loads(_unb64(t.split(".")[1]))["sub"] == "علی"


@pytest.mark.parametrize("secret,role", [(b"short", "watcher"), (SECRET, "owner")])
def test_rejects_weak_secret_and_unknown_role(secret, role):
    with pytest.raises(ValueError):
        sync_token.mint(secret, "a", "R1", role, 1)


def test_role_needs_play_pause_and_seek():
    assert sync_token.role_for(True, True, True) == "controller"
    for flags in [(False, True, True), (True, False, True), (True, True, False), (False, False, False)]:
        assert sync_token.role_for(*flags) == "watcher"
