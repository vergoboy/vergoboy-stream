"""Relay between the Flask room service and the w2g-server timing authority.

The Rust sync server (w2g-server) owns playback TIMING — play/pause/seek/rate
and the item a room is on — for every room. Clients follow it directly over
its WebSocket (the web app and mpv both speak the same protocol); this module
is Flask's window into that authority, over loopback only:

  * forward_cmd() — POST /cmd: legacy `control` socket events and playlist
    selection are relayed here, so every client path converges on one timing
    implementation instead of Flask keeping a second one. Flask still applies
    its own per-action permission checks and the encoded-frontier seek clamp
    BEFORE forwarding; the sync server's `role` is never consulted on this
    surface (it is the account service, not a room client).

  * fetch_state() — GET /state/<room>: the mirror. Flask's RoomState
    position/playing/rate are refreshed from the authority so `state_sync`
    (the webapp's bootstrap phase, legacy consumers, /api/state) never
    invents a second timeline.

The wire format of `cmd` must stay in step with w2g-server's parse_cmd:
    {"c": "play" | "pause" | "seek" | "pause_at" | "rate" | "item", ...}
play/pause deliberately carry NO position: the server computes it itself,
which closes the old "client-supplied at wins" trust hole.

No Flask imports on purpose: this stays a thin, unit-testable I/O layer.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from config import Config

_log = logging.getLogger("sync_relay")

# All calls are loopback; a short timeout keeps a wedged sync server from
# stalling the socket handler that triggered the relay.
_TIMEOUT = 2.0


def configured() -> bool:
    return bool(Config.SYNC_CONFIGURED and Config.SYNC_INTERNAL_URL)


def _request(method: str, path: str, body: dict | None = None) -> dict | None:
    if not configured():
        return None
    url = Config.SYNC_INTERNAL_URL.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
            return parsed if isinstance(parsed, dict) else None
    except urllib.error.HTTPError as e:
        # 404 = the authority has never seen this room (nobody has played yet):
        # a normal state, not a fault — stay quiet so the mirror loop is clean.
        if e.code != 404:
            _log.warning("sync relay %s %s failed: %s", method, path, e)
        return None
    except (urllib.error.URLError, OSError, ValueError) as e:
        _log.warning("sync relay %s %s failed: %s", method, path, e)
        return None


def forward_cmd(room_code: str, cmd: dict) -> dict | None:
    """Applies one controller command in the authority. Returns the server's
    response ({"ok": True, "state": {...}, "expected": f64}) or None."""
    return _request("POST", "/cmd", {"room": room_code, "cmd": cmd})


def fetch_state(room_code: str) -> dict | None:
    """Snapshot of a room's authoritative state + expected position, or None."""
    return _request("GET", "/state/" + urllib.parse.quote(room_code))


def mirror_room(rs, payload: dict) -> bool:
    """Applies a server payload onto the local RoomState (the mirror).

    `expected` is the authority's own answer for "where is this room right
    now" — stamped by the server, so no client clock is trusted. Returns True
    when the mirror actually moved (worth persisting/broadcasting).
    """
    state = (payload or {}).get("state")
    if not isinstance(state, dict):
        return False
    try:
        expected = float(payload.get("expected", state.get("anchor_pos", 0.0)) or 0.0)
    except (TypeError, ValueError):
        expected = 0.0
    playing = bool(state.get("playing", rs.playing))
    try:
        rate = float(state.get("rate", rs.rate) or 1.0)
    except (TypeError, ValueError):
        rate = 1.0
    rate = min(4.0, max(0.25, rate))
    changed = (
        rs.playing != playing
        or rs.rate != rate
        or abs(rs.current_position() - expected) > 0.05
    )
    rs.playing = playing
    rs.rate = rate
    rs.position = max(0.0, expected)
    rs.updated_at = time.time()
    return changed
