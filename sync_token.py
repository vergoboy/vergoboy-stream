"""Room-sync access tokens for the w2g-server (the Rust room sync service).

Format (must stay byte-compatible with w2g-server's verifier):
    v1.<b64url(payload json)>.<b64url(HMAC-SHA256(secret, "v1." + payload_b64))>
payload = {"sub", "room", "role": "controller"|"watcher", "exp": unix seconds}, keys sorted (canonical;
the Rust minter emits the same bytes)

The role is decided HERE from the user's resolved room permissions; the sync server
trusts only the signature, never what a client claims.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

ROLE_CONTROLLER = "controller"
ROLE_WATCHER = "watcher"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint(secret: bytes, sub: str, room: str, role: str, exp: int) -> str:
    if role not in (ROLE_CONTROLLER, ROLE_WATCHER):
        raise ValueError(f"bad role: {role!r}")
    if len(secret) < 32:
        raise ValueError("sync secret must be at least 32 bytes")
    payload = json.dumps(
        {"sub": sub, "room": room, "role": role, "exp": int(exp)},
        separators=(",", ":"), ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")
    p = _b64url(payload)
    sig = hmac.new(secret, f"v1.{p}".encode("ascii"), hashlib.sha256).digest()
    return f"v1.{p}.{_b64url(sig)}"


def role_for(can_play: bool, can_pause: bool, can_seek: bool) -> str:
    """Controller only if the user may do everything the sync server accepts.
    (Per-command capabilities are a planned refinement; until then this is all-or-nothing.)"""
    return ROLE_CONTROLLER if (can_play and can_pause and can_seek) else ROLE_WATCHER
