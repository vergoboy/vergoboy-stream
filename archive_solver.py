"""Client for the local archive-solver sidecar (Cloudflare clearance minting).

The sidecar runs a *headed* browser on a virtual display -- the only client
class Cloudflare's managed challenge currently lets through from the archive
proxy's exit address (live evidence 2026-10-17: native headless variants spin
forever; a headed browser clears in seconds and its ``cf_clearance`` replays
from plain ``requests`` when presented with the same User-Agent).

This module is deliberately dumb: one POST, strict shape checking, and every
failure mode collapses to ``None`` so the caller keeps its ``site_challenge``
classification and recovers on its own once the sidecar cooperates.  The
sidecar is reached directly on loopback: no process proxy setting may reroute
it, exactly like the media session in ``archive_network``.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

LOG = logging.getLogger("archive.solver")

# A solve is a browser launch plus a Turnstile run; the sidecar's own deadline
# is shorter than this, the margin covers queueing and slow proxies.
SOLVE_TIMEOUT_S = 90.0


def create_solver_session() -> requests.Session:
    """A loopback-only session that cannot inherit any process proxy settings."""
    session = requests.Session()
    session.trust_env = False
    session.proxies = {}
    return session


def fetch_clearance(solver_url: str, target_url: str,
                    session: requests.Session | None = None) -> tuple[dict[str, str], str] | None:
    """Mint a clearance for ``target_url``; returns (cookies, user_agent) or None."""
    if not solver_url:
        return None
    http = session or create_solver_session()
    try:
        response = http.post(f"{solver_url.rstrip('/')}/solve", json={"url": target_url},
                             timeout=SOLVE_TIMEOUT_S)
    except requests.RequestException as exc:
        LOG.warning("clearance solver unreachable error=%s", type(exc).__name__)
        return None
    if response.status_code != 200:
        LOG.warning("clearance solver declined status=%s", response.status_code)
        return None
    try:
        data: Any = response.json()
    except ValueError:
        LOG.warning("clearance solver returned non-JSON body")
        return None
    if not isinstance(data, dict) or data.get("ok") is not True:
        LOG.warning("clearance solver reported failure error=%s",
                    data.get("error") if isinstance(data, dict) else "malformed")
        return None
    cookies, user_agent = data.get("cookies"), data.get("user_agent")
    if not isinstance(cookies, dict) or not cookies \
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in cookies.items()) \
            or not isinstance(user_agent, str) or not user_agent:
        LOG.warning("clearance solver response missed cookies or user_agent")
        return None
    return cookies, user_agent
