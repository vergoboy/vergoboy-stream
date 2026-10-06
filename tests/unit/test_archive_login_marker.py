"""Regressions for the two defects that broke Digimoviez search and login.

Both were found against the live site, not by inspection:

* The session check trusted the login widget and the security-question
  challenge as proof of being logged out.  Digimoviez renders the widget into
  ordinary pages, so every healthy session was reported expired and the
  collector relogged in on every single request.  Only the WordPress logout
  link is positive proof.
* A text search was issued to the ``?s=`` page, which renders the same
  "dubbed" listing whatever the query is.  The site's own header search posts
  ``action=ajaxsearch`` to ``admin-ajax.php`` and gets a JSON string of HTML.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

from archive_auth import ArchiveAuthManager, AuthSettings, AuthState
from archive_login_form import is_authenticated_page
from archive_scraper import _unwrap_ajax_payload, _parse_search_card, _iter_blocks
from tests.unit.fake_digimoviez import (ACCOUNT_URL, COOKIE, LOGIN_URL, LOGOUT_LINK,
                                        POPUP, FakeDigimoviez, account_page)

LOGIN_WIDGET = ('<form class="dashboard_form"><input name="username">'
                '<input name="password"><input name="secureq_ans">'
                '<span>سوال امنیتی: ۶ در ۸</span></form>')


# --- the session marker -------------------------------------------------
def test_logout_link_alone_marks_the_page_authenticated():
    assert is_authenticated_page(account_page()) is True


def test_login_widget_alone_is_not_an_authenticated_page():
    assert is_authenticated_page(LOGIN_WIDGET) is False


def test_logged_out_homepage_is_not_authenticated():
    assert is_authenticated_page(f"<html><body>{POPUP}<h1>خانه</h1></body></html>") is False


def test_widget_rendered_next_to_a_logout_link_still_counts_as_a_session():
    # The exact live shape: the theme draws the login widget even for a
    # signed-in visitor.  Marker precedence is what stops the login loop.
    assert is_authenticated_page(account_page() + LOGIN_WIDGET) is True


def test_check_reports_a_live_session_instead_of_relogging_in():
    """A healthy session must cost one GET and zero POSTs."""
    session = requests.Session()
    fake = FakeDigimoviez()
    fake.mount_on(session)
    session.cookies.set(COOKIE, "SID-1-secretvalue")
    fake.sessions.add("SID-1-secretvalue")

    settings = AuthSettings(True, LOGIN_URL, ACCOUNT_URL, "user", "pw!Secret-7",
                            Path("/tmp/does-not-matter.json"), "http://127.0.0.1:10808",
                            "username", "password")
    manager = ArchiveAuthManager(settings, session=session, sleep=lambda _: None)

    assert manager.ensure_authenticated() is True
    assert manager.ensure_authenticated() is True
    assert fake.posts == 0, "a valid session was re-authenticated on every request"


def test_server_side_lagout_triggers_exactly_one_relogin():
    session = requests.Session()
    fake = FakeDigimoviez()
    fake.mount_on(session)
    settings = AuthSettings(True, LOGIN_URL, ACCOUNT_URL, "user", "pw!Secret-7",
                            Path("/tmp/does-not-matter-2.json"), "http://127.0.0.1:10808",
                            "username", "password")
    manager = ArchiveAuthManager(settings, session=session, sleep=lambda _: None)

    assert manager.ensure_authenticated() is True
    assert fake.posts == 1
    fake.expire_sessions()               # cookie survives, server forgot us
    assert manager.ensure_authenticated() is True
    assert fake.posts == 2, "a lagout must be retried, not reported as a failure"


def test_transport_failure_is_not_mistaken_for_a_lagout():
    session = requests.Session()
    fake = FakeDigimoviez()
    fake.mount_on(session)
    session.cookies.set(COOKIE, "SID-1-secretvalue")
    fake.sessions.add("SID-1-secretvalue")

    class TimingOut(FakeDigimoviez):
        def send(self, request, **kwargs):
            raise requests.Timeout("proxy stalled")

    settings = AuthSettings(True, LOGIN_URL, ACCOUNT_URL, "user", "pw!Secret-7",
                            Path("/tmp/does-not-matter-3.json"), "http://127.0.0.1:10808",
                            "username", "password")
    manager = ArchiveAuthManager(settings, session=session, sleep=lambda _: None)
    manager.session.mount("https://", TimingOut())
    manager.session.mount("http://", TimingOut())

    assert manager.ensure_authenticated() is False
    assert manager.state is AuthState.AUTH_FAILED
    assert fake.posts == 0, "a timeout must not spend credentials on a login"


# --- the AJAX search payload -------------------------------------------
def test_ajax_payload_is_json_escaped_html():
    body = account_page()
    assert json.dumps(body).startswith('"<div') or json.dumps(body).startswith('"')
    assert _unwrap_ajax_payload(json.dumps(body)) == body


def test_ajax_payload_of_no_results_is_left_alone():
    # WordPress answers an empty search with the bare strings "0"/"-1".
    assert _unwrap_ajax_payload("0") == "0"
    assert _unwrap_ajax_payload("-1") == "-1"
    assert _unwrap_ajax_payload("") == ""


def test_unparsable_body_is_returned_unchanged():
    raw = '<div class="item_small_loop">plain</div>'
    assert _unwrap_ajax_payload(raw) == raw


def test_ajax_search_card_is_parsed_after_unwrapping():
    # item_small_loop carries no IMDb block on the live site, so a rating must
    # come back as None rather than being scraped out of unrelated text.
    card_html = ('<div class="item_small_loop" data-ID="4370">'
                 '<a title="دانلود فیلم Interstellar 2014" href="https://digimoviez.com/interstellar-2014/">'
                 '<div class="cover"><img src="https://digimoviez.com/poster.jpg"></div>'
                 '<h2>Interstellar 2014</h2></a></div>')
    blocks = list(_iter_blocks(_unwrap_ajax_payload(json.dumps(card_html)), 'class="item_small_loop'))
    assert len(blocks) == 1
    card = _parse_search_card(blocks[0], "https://digimoviez.com")
    assert card is not None
    assert card["url"] == "https://digimoviez.com/interstellar-2014/"
    assert card["year"] == "2014"
    assert card["rating"] is None


def test_dubbled_page_card_still_reports_its_imdb_rating():
    # The ?s= page layout does carry a rating; keep that path covered.
    card_html = ('<div class="item_dubbled"><a href="https://digimoviez.com/inception-2010/">'
                 '<h2 class="title">Inception 2010</h2>'
                 '<div class="rate_num"><strong>8.8</strong></div></a></div>')
    blocks = list(_iter_blocks(card_html, 'class="item_dubbled'))
    card = _parse_search_card(blocks[0], "https://digimoviez.com")
    assert card["rating"] == "8.8"
    assert card["year"] == "2010"


def test_ajax_search_hits_the_endpoint_the_theme_uses():
    """A text query must not be sent to the non-filtering ?s= page."""
    from archive_filters import SearchFilters
    from archive_scraper import DigimoviezMovieCollector

    sent: list[tuple[str, dict | None]] = []

    # Let a real login happen against the fake so the collector's own
    # ensure_authenticated() path is exercised rather than stubbed out.
    class Recorder(FakeDigimoviez):
        def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
            sent.append((request.url.split("?")[0], request.body))
            return super().send(request, stream, timeout, verify, cert, proxies)

    session = requests.Session()
    fake = Recorder()
    fake.mount_on(session)
    settings = AuthSettings(True, LOGIN_URL, ACCOUNT_URL, "user", "pw!Secret-7",
                            Path("/tmp/does-not-matter-4.json"), "http://127.0.0.1:10808",
                            "username", "password")
    auth = ArchiveAuthManager(settings, session=session, sleep=lambda _: None)
    auth.base_url = "https://digimoviez.test"
    collector = DigimoviezMovieCollector("https://digimoviez.test", auth,
                                         connect_timeout=1, read_timeout=1,
                                         total_timeout=1, retries=0)
    results = collector.search(SearchFilters.from_mapping({"query": "interstellar"}), max_pages=1)

    assert auth.state is AuthState.AUTHENTICATED
    posted = [(url, body) for url, body in sent if url.endswith("admin-ajax.php")]
    assert len(posted) == 1, f"expected one AJAX search, got {sent}"
    body = posted[0][1]
    body = body.decode() if isinstance(body, bytes) else body
    assert "ajaxsearch" in body and "interstellar" in body
    # And the payload actually became a result rather than a silent empty list.
    assert [card["url"] for card in results] == ["https://digimoviez.test/tt-0/"]
    assert results[0]["year"] == "2014"


def test_logout_link_survives_as_html_escaped_nonce():
    # The live link carries &amp;, so the marker must not require a bare "&".
    assert "&amp;_wpnonce" in LOGOUT_LINK
    assert is_authenticated_page(LOGOUT_LINK) is True