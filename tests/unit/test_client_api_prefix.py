"""Client API URLs must carry the instance's /api prefix exactly once.

The client bundles one exported constant, ``API_PREFIX``
(``${API_BASE_PATH}/api``, see webapp/lib/config.ts), and every request appends
the path *after* ``/api``: ``API_PREFIX + "/upload"``. When the mount point was
parametrized, 46 call sites in api.ts/auth.ts were instead written
``API_PREFIX + "/api/…"``, which would have sent every request to
``/stream/api/api/…`` — a 404 on every endpoint. Nothing else catches it: the
URL is composed at runtime from a ``basePath`` that Next inlines at build time,
so it is not a literal string in the bundle and neither ``tsc`` nor the backend
suite sees it.

The check is source-level on purpose — it needs no browser, no build and no
new dependency — and pins three things: the shape of ``API_PREFIX``, the shape
of the config value it derives from, and the shape of its call sites.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WEBAPP = Path(__file__).resolve().parents[2] / "webapp"

#: Every file that composes a request URL from API_PREFIX.
CALL_SITES = (
    "lib/api.ts",
    "lib/auth.ts",
    "components/ClientErrorReporter.tsx",
)

_config = WEBAPP / "lib" / "config.ts"


def _bodies() -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in (*CALL_SITES, "lib/config.ts", "next.config.ts"):
        path = WEBAPP / rel
        if path.is_file():
            out[rel] = path.read_text(encoding="utf-8")
    return out


def test_config_defines_api_prefix_as_the_api_base_path_plus_api():
    """API_PREFIX is the API mount point plus one /api — not the mount point alone."""
    body = _config.read_text(encoding="utf-8")
    collapsed = re.sub(r"\s+", "", body)
    assert "exportconstAPI_PREFIX=`${API_BASE_PATH}/api`;" in collapsed, (
        "webapp/lib/config.ts must define API_PREFIX as `${API_BASE_PATH}/api`; "
        "call sites append the path after /api"
    )


def test_api_base_path_defaults_to_the_asset_mount_point():
    """API_BASE_PATH may be overridden for a sub-path instance.

    NEXT_PUBLIC_API_BASE_PATH is what a desktop build sets (e.g. "/w2g"): its
    assets have no mount point of their own, but the instance it talks to does.
    Unset — every browser build — it must fall back to BASE_PATH, so nothing
    changes for the original instance.
    """
    body = _config.read_text(encoding="utf-8")
    collapsed = re.sub(r"\s+", "", body)
    assert "exportconstAPI_BASE_PATH=process.env.NEXT_PUBLIC_API_BASE_PATH??BASE_PATH;" in collapsed, (
        "webapp/lib/config.ts must define API_BASE_PATH as "
        "process.env.NEXT_PUBLIC_API_BASE_PATH ?? BASE_PATH"
    )


def test_socket_path_follows_the_api_base_path():
    """Socket.IO is served next to the API, so it must not drift from it."""
    body = _config.read_text(encoding="utf-8")
    collapsed = re.sub(r"\s+", "", body)
    assert "exportconstSOCKET_PATH=`${API_BASE_PATH}/socket.io`;" in collapsed, (
        "webapp/lib/config.ts must derive SOCKET_PATH from API_BASE_PATH"
    )


def test_next_config_reuses_the_one_base_path_constant():
    """The build cannot disagree with the runtime about the mount point."""
    body = (WEBAPP / "next.config.ts").read_text(encoding="utf-8")
    assert re.search(r'import\s*\{[^}]*\bBASE_PATH\b[^}]*\}\s*from\s*"\./lib/config"', body), (
        "next.config.ts must import BASE_PATH from ./lib/config rather than "
        "recomputing it"
    )
    assert re.search(r"basePath:\s*BASE_PATH\b", body), "next.config.ts must set basePath: BASE_PATH"


@pytest.mark.parametrize("rel", CALL_SITES)
def test_call_sites_do_not_re_append_the_api_segment(rel: str):
    """`API_PREFIX + "/api/…"` (or the template-literal form) is the bug."""
    body = _bodies()[rel]
    offenders = [
        (body.count("\n", 0, m.start()) + 1, m.group(0).strip())
        for m in re.finditer(r"API_PREFIX\s*\+\s*[`\"']/api/", body)
    ]
    assert not offenders, (
        f"{rel} appends /api to API_PREFIX (which already ends in /api); "
        f"that yields <mount>/api/api/… — offending lines: {offenders}"
    )


@pytest.mark.parametrize("rel", CALL_SITES)
def test_call_sites_use_the_prefix_and_not_a_hardcoded_mount(rel: str):
    """No call site may hardcode a mount point: that is what the prefix is for."""
    body = _bodies()[rel]
    offenders = [
        (body.count("\n", 0, m.start()) + 1, m.group(0))
        for m in re.finditer(r"[`\"']/(?:stream|w2g)/", body)
    ]
    assert not offenders, f"{rel} hardcodes a mount point instead of API_PREFIX: {offenders}"


def test_client_uses_api_prefix_rather_than_absolute_paths():
    """Sanity: the guard above is only meaningful if the file actually uses it."""
    for rel in CALL_SITES:
        assert "API_PREFIX" in _bodies()[rel], f"{rel} does not reference API_PREFIX at all"


def test_invite_urls_are_built_from_the_api_base_path():
    """An invite link is an absolute URL a human opens in a browser.

    It must carry the instance's public path (API_BASE_PATH), not the asset
    mount point (BASE_PATH). The desktop target has an empty BASE_PATH — its
    own assets load from tauri://localhost — so building the link from it would
    hand out `https://vergoboy.ir/?join=…` for *either* instance, pointing the
    recipient at the wrong one.
    """
    found = 0
    for rel in ("app/page.tsx", "components/RoomBar.tsx"):
        for lineno, line in enumerate((WEBAPP / rel).read_text(encoding="utf-8").splitlines(), 1):
            if "?join=${roomCode}" not in line:
                continue
            found += 1
            assert "API_BASE_PATH" in line and "basePath" not in line, (
                f"{rel}:{lineno} builds an invite link from BASE_PATH instead of "
                f"API_BASE_PATH: {line.strip()}"
            )
    assert found == 2, f"expected 2 invite-link builders, found {found} — the guard above went stale"
