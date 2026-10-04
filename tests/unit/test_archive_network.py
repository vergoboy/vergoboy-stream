from __future__ import annotations

import os
from pathlib import Path

import pytest
import requests

from archive_auth import ArchiveAuthManager, AuthSettings
from archive_filters import SearchFilters
from archive_network import ArchiveProxyConfigurationError, create_archive_session, create_direct_media_session, direct_media_environment
from archive_scraper import ArchiveRequestError, DigimoviezMovieCollector


PROXY = "http://127.0.0.1:10808"


def test_archive_session_routes_both_schemes_through_configured_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://wrong-proxy:9999")
    session = create_archive_session(PROXY)
    assert session.trust_env is False
    assert session.proxies == {"http": PROXY, "https": PROXY}


def test_media_session_is_direct_despite_all_proxy_environment(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:10808")
    session = create_direct_media_session()
    assert session.trust_env is False
    assert session.proxies == {}
    env = direct_media_environment()
    assert not {name for name in env if name.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}


def test_playlist_media_validation_uses_the_direct_media_client(monkeypatch):
    # ``_check_link_ok`` is the validation immediately before api_add_many
    # hands a URL to _add_url_item; importing the app lets this assertion cover
    # the real playlist handoff instead of a duplicate helper.
    import app
    assert app.MEDIA_CLIENT.trust_env is False
    assert app.MEDIA_CLIENT.proxies == {}


def test_invalid_archive_proxy_is_rejected_not_bypassed():
    with pytest.raises(ArchiveProxyConfigurationError):
        create_archive_session("not-a-proxy")


class Response:
    status_code = 200
    ok = True
    url = "https://digimoviez.com/account/"
    text = "<article><a href='/movie/x'>Movie</a></article>"
    content = text.encode()
    headers = {"Content-Type": "text/html"}
    history = []


class ArchiveSession:
    def __init__(self, fail=False):
        self.cookies = requests.cookies.RequestsCookieJar()
        self.requests: list[tuple[str, str]] = []
        self.fail = fail
    def request(self, method, url, **kwargs):
        self.requests.append((method, url))
        if self.fail:
            raise requests.exceptions.ProxyError("proxy down")
        return Response()


def _collector(tmp_path: Path, session: ArchiveSession) -> DigimoviezMovieCollector:
    settings = AuthSettings(False, "https://digimoviez.com/account/login/", "https://digimoviez.com/account/",
        "", "", tmp_path / "cookies.json", PROXY)
    auth = ArchiveAuthManager(settings, session=session)
    return DigimoviezMovieCollector("https://digimoviez.com", auth, connect_timeout=1, read_timeout=1, total_timeout=1, retries=0)


def test_login_search_and_detail_share_only_the_proxied_archive_client(tmp_path):
    manager = ArchiveAuthManager(AuthSettings(True, "https://digimoviez.com/account/login/", "https://digimoviez.com/account/", "u", "p", tmp_path / "c", PROXY, "u", "p"))
    assert manager.session.proxies["https"] == PROXY
    session = ArchiveSession()
    collector = _collector(tmp_path, session)
    collector.search(SearchFilters.from_mapping({"query": "x"}))
    collector.title("https://digimoviez.com/movie/x")
    assert collector.session is collector.auth.session
    assert [url for _, url in session.requests] == [
        "https://digimoviez.com?advanced_search=on&min_release=1888&max_release=2026&min_rate=0.0&max_rate=10.0&s=x",
        "https://digimoviez.com/movie/x",
    ]


def test_proxy_failure_is_classified_without_direct_retry(tmp_path):
    collector = _collector(tmp_path, ArchiveSession(fail=True))
    with pytest.raises(ArchiveRequestError, match="archive request failed") as exc:
        collector.search(SearchFilters.from_mapping({"query": "x"}))
    assert exc.value.kind == "proxy_unavailable"
