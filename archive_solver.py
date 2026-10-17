"""Client for the local archive-solver sidecar (Cloudflare clearance minting).

The sidecar is FlareSolverr: a maintained service that drives its own Chrome
build through the managed challenge and hands back the ``cf_clearance`` cookie
together with the User-Agent that cookie is bound to.  Live evidence
(2026-10-17): FlareSolverr clears the archive's wall in roughly 80s from the
archive proxy's exit address, while every browser we drove ourselves (headless
Camoufox and its Xvfb variants) stalled at the interactive step; the minted
``cf_clearance`` then replays from plain ``requests`` when presented with the
same User-Agent (403 without it).

This module is deliberately dumb: one POST, strict shape checking, and every
failure mode collapses to ``None`` so the caller keeps its ``site_challenge``
classification and recovers on its own once the sidecar cooperates.  The
sidecar is reached directly on loopback: no process proxy setting may reroute
it, exactly like the media session in ``archive_network``.  The *target* proxy
travels per request because it is the sidecar's Chrome -- not this process --
that has to reach the archive.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

LOG = logging.getLogger("archive.solver")

# A solve is a browser launch plus a Turnstile run.  This socket timeout sits
# above the sidecar's own ceiling so a slow solve is reported by the sidecar
# rather than by a torn connection.
SOLVE_TIMEOUT_S = 200.0
# The sidecar's own per-request ceiling (FlareSolverr's documented maximum).
SOLVE_MAX_TIMEOUT_MS = 180_000


def create_solver_session() -> requests.Session:
    """A loopback-only session that cannot inherit any process proxy settings."""
    session = requests.Session()
    session.trust_env = False
    session.proxies = {}
    return session


def fetch_clearance(solver_url: str, target_url: str,
                    proxy: str = "") -> tuple[dict[str, str], str] | None:
    """Mint a clearance for ``target_url``; returns ``(cookies, user_agent)``.

    ``proxy`` is the archive proxy the *sidecar's* browser must use -- this
    process talks to the sidecar over loopback and needs no proxy of its own.
    Every failure (unreachable sidecar, a declined solve, a body we cannot
    read, or a solved page that carried no ``cf_clearance``) collapses to
    ``None`` so the caller keeps its honest ``site_challenge`` classification.
    """
    if not solver_url:
        return None
    payload: dict[str, Any] = {"cmd": "request.get", "url": target_url,
                               "maxTimeout": SOLVE_MAX_TIMEOUT_MS}
    if proxy:
        payload["proxy"] = {"url": proxy}
    http = create_solver_session()
    try:
        response = http.post(f"{solver_url.rstrip('/')}/v1", json=payload,
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
    if not isinstance(data, dict):
        LOG.warning("clearance solver returned a non-object body")
        return None
    solution = data.get("solution")
    if data.get("status") != "ok" or not isinstance(solution, dict):
        LOG.warning("clearance solver reported failure message=%s", data.get("message"))
        return None
    cookies = _extract_cookies(solution.get("cookies"))
    user_agent = solution.get("userAgent")
    # A "solved" page with no cf_clearance is not a clearance: adopting it would
    # only burn the caller's single retry (live evidence 2026-10-17), so it is
    # reported as a failure and the wall stays honestly classified.
    if not isinstance(user_agent, str) or not user_agent or "cf_clearance" not in cookies:
        LOG.warning("clearance solver returned no cf_clearance solution_status=%s", solution.get("status"))
        return None
    return cookies, user_agent


def _extract_cookies(raw: Any) -> dict[str, str]:
    """FlareSolverr's cookie objects (name/value/domain/expiry) as a flat jar."""
    if not isinstance(raw, list):
        return {}
    cookies: dict[str, str] = {}
    for cookie in raw:
        if isinstance(cookie, dict) and isinstance(cookie.get("name"), str) \
                and isinstance(cookie.get("value"), str):
            cookies[cookie["name"]] = cookie["value"]
    return cookies
