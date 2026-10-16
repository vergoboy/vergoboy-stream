"""A Cloudflare edge challenge is not an expired Digimoviez session.

Live evidence (2026-10-16, ~15:30 UTC): digimoviez.com began stamping every
response -- from the archive proxy exit *and* from residential networks --
with ``cf-mitigated: challenge`` and a "Just a moment..." page in front of
every endpoint, including the login form itself.  The old classification read
that 403 as "session expired; log in again", which is both wrong and a dead
end: relogging cannot succeed when the login page is walled too.  These tests
pin the corrected behaviour: the wall is a temporary transport-level failure
that is reported as its own kind, never triggers a login attempt, and clears
on its own once the edge lets the client back in.
"""
from __future__ import annotations

import pytest
import requests

import app
import archive_scraper
from archive_auth import AuthState, _is_cloudflare_challenge
from archive_scraper import DigimoviezMovieCollector
from tests.unit.fake_digimoviez import BASE, CF_CHALLENGE_PAGE, FakeDigimoviez
from tests.unit.test_archive_auth_flow import make


def walled(tmp_path):
    """An auth manager whose every request meets the Cloudflare wall."""
    site = FakeDigimoviez(cloudflare_wall=True)
    manager, _ = make(tmp_path, site)
    return manager, site


def test_walled_site_is_not_an_expired_session(tmp_path):
    manager, site = walled(tmp_path)
    assert manager.ensure_authenticated() is False
    assert manager.state is AuthState.AUTH_FAILED       # not AUTH_EXPIRED
    assert manager.last_check_error == "site_challenge"
    assert site.login_gets == 0 and site.posts == 0     # the walled login form is never opened


def test_search_reports_the_challenge_kind_not_session_expired(tmp_path):
    manager, _ = walled(tmp_path)
    collector = DigimoviezMovieCollector(BASE, manager, connect_timeout=1, read_timeout=1,
                                         total_timeout=5, retries=0)
    with pytest.raises(archive_scraper.ArchiveRequestError) as exc:
        collector._search_ajax("dune", "movie")
    assert exc.value.kind == archive_scraper.KIND_SITE_CHALLENGE


def test_api_message_is_503_retry_later_and_offers_no_manual_import():
    """Importing a browser session cannot help: the edge blocks before WordPress."""
    status, _message = app._ARCHIVE_ERRORS[archive_scraper.KIND_SITE_CHALLENGE]
    assert status == 503
    assert archive_scraper.KIND_SITE_CHALLENGE not in app.ARCHIVE_MANUAL_SESSION_KINDS


def _response(*, header: str | None = None, body: str = "") -> requests.Response:
    response = requests.Response()
    response.status_code = 403
    response._content, response._content_consumed = body.encode("utf-8"), True
    if header is not None:
        response.headers["cf-mitigated"] = header
    return response


def test_detector_matches_the_header_marker_and_the_body_marker():
    # The header alone identifies the wall even if Cloudflare trims the page.
    assert _is_cloudflare_challenge(_response(header="challenge", body="<html></html>"))
    # The body marker is the fallback for intermediaries that strip headers.
    assert _is_cloudflare_challenge(_response(body=CF_CHALLENGE_PAGE))
    assert not _is_cloudflare_challenge(_response(body="<html><body>ورود</body></html>"))


def test_genuine_expiry_still_relogs_in_automatically(tmp_path):
    """The correction must not reclassify a real expiry as an edge problem."""
    site = FakeDigimoviez()
    manager, _ = make(tmp_path, site)
    assert manager.ensure_authenticated() is True
    site.expire_sessions()                                 # server-side expiry
    assert manager.ensure_authenticated() is True          # the old path still recovers
    assert site.posts >= 2                                 # it really did log in again
    assert manager.last_check_error is None


def test_wall_coming_down_recovers_without_any_manual_import(tmp_path):
    manager, site = walled(tmp_path)
    assert manager.ensure_authenticated() is False
    site.cloudflare_wall = False                           # the edge lifts the challenge
    assert manager.ensure_authenticated() is True
    assert manager.last_check_error is None
