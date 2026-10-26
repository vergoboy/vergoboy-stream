"""The clearance sidecar lifts a Cloudflare wall without changing the rest.

Live evidence (2026-10-17): from the archive proxy's exit address the edge
serves a managed challenge to every client that is not a real browser --
headless Camoufox, its Xvfb variants and raw curl all stalled or were walled --
and the clearance it finally issues is bound to the User-Agent that minted it.
The deployed answer is the FlareSolverr sidecar.  These tests pin the app-side
contract: a wall triggers exactly one solver round-trip, the minted cookies and
their bound User-Agent are adopted atomically, the adopted UA survives into
scrapes, the retry is bounded, and every solver failure degrades to the honest
site_challenge behaviour that predates this feature.
"""
from __future__ import annotations

import archive_auth
from archive_auth import AuthState
from archive_scraper import DigimoviezMovieCollector
from tests.unit.fake_digimoviez import ACCOUNT_URL, AJAX_URL, BASE, FakeDigimoviez
from tests.unit.test_archive_auth_flow import make

SOLVER_URL = "http://127.0.0.1:8895"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:156.0) Gecko/20100101 Firefox/156.0"


def walled_with_clearance(tmp_path, *, valid_clearance="tok-1"):
    site = FakeDigimoviez(cloudflare_wall=True, valid_clearance=valid_clearance)
    manager, _ = make(tmp_path, site, solver_url=SOLVER_URL)
    return manager, site


def test_wall_with_working_sidecar_logs_in_end_to_end(tmp_path, monkeypatch):
    manager, site = walled_with_clearance(tmp_path)
    calls: list[tuple[str, str, str]] = []

    def fake_fetch(solver_url: str, target_url: str, proxy: str):
        calls.append((solver_url, target_url, proxy))
        return {"cf_clearance": "tok-1"}, BROWSER_UA

    monkeypatch.setattr(archive_auth, "fetch_clearance", fake_fetch)
    assert manager.ensure_authenticated() is True
    assert manager.state is AuthState.AUTHENTICATED
    assert manager.last_check_error is None
    assert site.posts == 1                                  # it really logged in behind the wall
    # One round-trip, against the check URL, carrying the archive proxy the
    # sidecar's own browser must use.
    assert calls == [(SOLVER_URL, ACCOUNT_URL, "http://127.0.0.1:10808")]
    # The login POST carried the adopted clearance together with its bound UA.
    posts = [seen for seen in site.requests if seen["method"] == "POST"]
    assert posts
    for seen in posts:
        assert seen["headers"]["User-Agent"] == BROWSER_UA
        assert "cf_clearance=tok-1" in (seen["headers"].get("Cookie") or "")


def test_solver_failure_degrades_to_site_challenge(tmp_path, monkeypatch):
    manager, site = walled_with_clearance(tmp_path)
    monkeypatch.setattr(archive_auth, "fetch_clearance", lambda *_args: None)
    assert manager.ensure_authenticated() is False
    assert manager.state is AuthState.AUTH_FAILED           # not AUTH_EXPIRED
    assert manager.last_check_error == "site_challenge"
    assert site.login_gets == 0 and site.posts == 0


def test_gate_off_never_calls_the_sidecar(tmp_path, monkeypatch):
    site = FakeDigimoviez(cloudflare_wall=True)
    manager, _ = make(tmp_path, site)                       # solver_url unset = disabled
    calls: list[tuple[str, str, str]] = []

    def fake_fetch(solver_url: str, target_url: str, proxy: str):
        calls.append((solver_url, target_url, proxy))
        return {"cf_clearance": "tok"}, BROWSER_UA

    monkeypatch.setattr(archive_auth, "fetch_clearance", fake_fetch)
    assert manager.ensure_authenticated() is False
    assert manager.last_check_error == "site_challenge"
    assert calls == []


def test_provisional_clearance_gets_one_retry_then_the_honest_classification(tmp_path, monkeypatch):
    """The sidecar answers but the edge refuses the token: exactly one retry."""
    manager, site = walled_with_clearance(tmp_path, valid_clearance=None)
    calls: list[int] = []

    def fake_fetch(*_args):
        calls.append(1)
        return {"cf_clearance": "provisional"}, BROWSER_UA

    monkeypatch.setattr(archive_auth, "fetch_clearance", fake_fetch)
    assert manager.ensure_authenticated() is False
    assert manager.last_check_error == "site_challenge"
    assert len(calls) == 1                                  # never a solver loop
    assert site.posts == 0


def test_existing_wall_tests_still_hold_with_the_gate_configured_but_failing(tmp_path, monkeypatch):
    """The 2026-10-16 classification slice is behaviourally unchanged."""
    manager, site = walled_with_clearance(tmp_path)
    monkeypatch.setattr(archive_auth, "fetch_clearance", lambda *_args: None)
    collector_checks = manager.ensure_authenticated()
    assert collector_checks is False
    assert manager.state is AuthState.AUTH_FAILED
    site.cloudflare_wall = False                            # the edge lifts on its own
    assert manager.ensure_authenticated() is True
    assert manager.last_check_error is None


def test_guest_mode_adopts_a_clearance_and_searches(tmp_path, monkeypatch):
    """Auth disabled (the /stream instance): the sidecar alone clears the wall.

    Nothing in the guest path runs a session check, so the scrape is the only
    place that can notice the wall.  It must spend exactly one solve and then
    search with the cookie pair that solve minted.
    """
    site = FakeDigimoviez(cloudflare_wall=True, valid_clearance="tok-1")
    manager, _ = make(tmp_path, site, enabled=False, solver_url=SOLVER_URL)
    calls: list[tuple[str, str, str]] = []

    def fake_fetch(solver_url: str, target_url: str, proxy: str):
        calls.append((solver_url, target_url, proxy))
        return {"cf_clearance": "tok-1"}, BROWSER_UA

    monkeypatch.setattr(archive_auth, "fetch_clearance", fake_fetch)
    collector = DigimoviezMovieCollector(BASE, manager, connect_timeout=1, read_timeout=1,
                                         total_timeout=5, retries=0)
    results = collector._search_ajax("dune", "movie")
    assert results, "the search returned no cards behind the wall"
    assert len(calls) == 1                                  # one solve, never a loop
    scrapes = [seen for seen in site.requests if seen["url"].startswith(AJAX_URL)]
    assert "cf_clearance=tok-1" in (scrapes[-1]["headers"].get("Cookie") or "")
    assert scrapes[-1]["headers"]["User-Agent"] == BROWSER_UA


def test_search_sends_the_user_agent_the_clearance_is_bound_to(tmp_path, monkeypatch):
    """The adopter's UA must survive into scrapes, not just into the session check.

    A cf_clearance is bound to the User-Agent that minted it; the collector used
    to stamp its own hardcoded UA on every scrape, which the edge then walled
    even with a valid clearance (live evidence 2026-10-17: adopted UA -> 200,
    hardcoded UA -> 403).  This pins that the scrape carries the adopted UA.
    """
    manager, site = walled_with_clearance(tmp_path)
    monkeypatch.setattr(archive_auth, "fetch_clearance",
                        lambda *_args: ({"cf_clearance": "tok-1"}, BROWSER_UA))
    collector = DigimoviezMovieCollector(BASE, manager, connect_timeout=1, read_timeout=1,
                                         total_timeout=5, retries=0)
    assert manager.ensure_authenticated() is True
    collector._search_ajax("dune", "movie")
    scrapes = [seen for seen in site.requests if seen["url"].startswith(AJAX_URL)]
    assert scrapes, "the search never reached the site"
    assert scrapes[-1]["headers"]["User-Agent"] == BROWSER_UA
    assert "cf_clearance=tok-1" in (scrapes[-1]["headers"].get("Cookie") or "")
