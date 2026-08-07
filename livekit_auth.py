"""LiveKit access-token generation (no external deps).

Implements the same HS256 JWT format the official livekit-server-sdk produces:
header {alg:HS256, typ:JWT} + claims {iss=api_key, sub=identity, name, video grant,
nbf/iat/exp}. Works with livekit-server >= 1.5 (the old "sha256" claim is not
required there).
"""
import base64
import hashlib
import hmac
import json
import time


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_json(obj: dict) -> str:
    return _b64url(json.dumps(obj, separators=(",", ":")).encode("utf-8"))


def generate_livekit_token(api_key, api_secret, room, identity, name,
                           metadata=None, ttl=6 * 3600, grant=None):
    now = int(time.time())
    video = {
        "room": room,
        "roomJoin": True,
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": True,
        "canUpdateOwnMetadata": True,
    }
    if grant:
        video.update(grant)

    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": api_key,
        "sub": identity,
        "name": name,
        "metadata": metadata or "",
        "video": video,
        "nbf": now - 5,
        "iat": now,
        "exp": now + ttl,
    }
    signing_input = _b64url_json(header) + "." + _b64url_json(payload)
    sig = hmac.new(api_secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + _b64url(sig)
