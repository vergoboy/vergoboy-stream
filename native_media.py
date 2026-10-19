"""Token-protected URLs for ORIGINAL media files (no transcode), verified by nginx `secure_link`.

nginx recipe (deploy/nginx-native-media.conf):
    secure_link     $arg_md5,$arg_exp;
    secure_link_md5 "$secure_link_expires$uri$arg_u <secret>";
so the signature covers: expiry, the exact file path, and the user id.  The player is handed
    <path>?exp=<unix>&u=<user id>&md5=<base64url(md5(f"{exp}{path}{user} {secret}"))>
Bytes (including Range requests) are served by nginx itself; Python is never in the data path.

MD5 is what nginx's secure_link supports. Here it is only a keyed short-lived MAC over a unique,
non-secret path, not a password hash; the secret never leaves the server.
"""
from __future__ import annotations

import base64
import hashlib
import re

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_USER = re.compile(r"^[A-Za-z0-9-]+$")

MIN_TTL = 6 * 3600
MAX_TTL = 24 * 3600


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def ttl_seconds(duration_s: float | None) -> int:
    """Whole session: max(6 h, 2x the film), capped at 24 h."""
    try:
        d = float(duration_s or 0)
    except (TypeError, ValueError):
        d = 0.0
    return int(min(MAX_TTL, max(MIN_TTL, 2 * d)))


def signature(secret: str, exp: int, path: str, user_id: str) -> str:
    if len(secret) < 32:
        raise ValueError("native media secret must be at least 32 characters")
    msg = f"{int(exp)}{path}{user_id} {secret}".encode("utf-8")
    return _b64url(hashlib.md5(msg, usedforsecurity=False).digest())


def signed_path(secret: str, base_path: str, filename: str, user_id: str, exp: int) -> str:
    """`/w2g/native/uploads/<file>?exp=..&u=..&md5=..` (a path; the client adds the origin)."""
    if not _SAFE_NAME.match(filename or "") or filename in (".", ".."):
        raise ValueError(f"unsafe file name: {filename!r}")
    if not _SAFE_USER.match(user_id or ""):
        raise ValueError(f"unsafe user id: {user_id!r}")
    path = f"{base_path.rstrip('/')}/{filename}"
    return f"{path}?exp={int(exp)}&u={user_id}&md5={signature(secret, exp, path, user_id)}"
