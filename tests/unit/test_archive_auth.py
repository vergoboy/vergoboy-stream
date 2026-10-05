from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import requests

from archive_auth import ArchiveAuthManager, AuthSettings, AuthState


class Response:
    def __init__(self, url="https://example.test/account/", status=200, text="ok"):
        self.url, self.status_code, self.text, self.ok = url, status, text, status < 400
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class Session:
    def __init__(self, get_results=(), post_results=()):
        self.get_results, self.post_results = list(get_results), list(post_results)
        self.cookies = requests.cookies.RequestsCookieJar()
        self.posts = 0
        self.post_data = []
    def get(self, *args, **kwargs):
        result = self.get_results.pop(0)
        if isinstance(result, BaseException): raise result
        return result
    def post(self, *args, **kwargs):
        self.posts += 1
        self.post_data.append(kwargs.get("data"))
        result = self.post_results.pop(0)
        if isinstance(result, BaseException): raise result
        return result


def login_page(question="۶ در ۸", key="k", extra=""):
    """Realistic login markup: password input, hidden CSRF fields, question + key."""
    return ('<form class="dashboard_form"><input type="hidden" name="login_security_str" value="n">'
            '<input type="hidden" name="_wp_http_referer" value="/login/">' + extra +
            '<input type="text" name="username"><input type="password" name="password">'
            + (f'<div><input type="hidden" name="secureq_key" value="{key}"><span>سوال امنیتی: {question}</span></div>'
               '<input type="number" name="secureq_ans" id="secureq_ans">' if question else "") +
            '<button type="submit" name="loginkon">ورود</button></form>')


def account_page() -> str:
    """A verified session.

    WordPress renders the login widget whether or not anyone is signed in, so
    the logout link is the only positive signal on a page.
    """
    return '<html><body><h1>حساب کاربری</h1><a href="https://example.test/wp-login.php?action=logout">خروج</a></body></html>'


def session_ok(**kwargs) -> Response:
    return Response(text=account_page(), **kwargs)


def settings(tmp_path: Path) -> AuthSettings:
    return AuthSettings(True, "https://example.test/login/", "https://example.test/account/",
        "user", "password", tmp_path / "cookies.json", "http://127.0.0.1:10808", "username", "password", retry_count=2)


def test_valid_session_is_untouched(tmp_path):
    session = Session([session_ok()])
    manager = ArchiveAuthManager(settings(tmp_path), session=session)
    assert manager.ensure_authenticated() is True
    assert session.posts == 0 and manager.state is AuthState.AUTHENTICATED


def test_expired_session_reauthenticates_and_persists(tmp_path):
    session = Session([Response("https://example.test/login/"), Response(text=login_page(question=None)), session_ok()], [Response()])
    session.cookies.set("session", "opaque")
    manager = ArchiveAuthManager(settings(tmp_path), session=session, sleep=lambda _: None)
    assert manager.ensure_authenticated() is True
    assert session.posts == 1 and manager.state is AuthState.AUTHENTICATED
    assert (tmp_path / "cookies.json").exists()


def test_timeout_is_not_treated_as_logout(tmp_path):
    session = Session([requests.Timeout()])
    manager = ArchiveAuthManager(settings(tmp_path), session=session)
    assert manager.ensure_authenticated() is False
    assert session.posts == 0


def test_security_challenge_requires_manual_intervention(tmp_path):
    # Supported security question is auto-solved
    session = Session([Response("https://example.test/login/"), Response(text=login_page()), session_ok()], [Response()])
    session.cookies.set("session", "opaque")
    manager = ArchiveAuthManager(settings(tmp_path), session=session, sleep=lambda _: None)
    assert manager.ensure_authenticated() is True
    assert manager.state is AuthState.AUTHENTICATED


def test_manual_challenge_state_does_not_restart_login_on_each_archive_request(tmp_path):
    # Auto-solve: first call solves and submits; second call verifies session
    session = Session([Response("https://example.test/login/"), Response(text=login_page()), session_ok(), session_ok()], [Response()])
    session.cookies.set("session", "opaque")
    manager = ArchiveAuthManager(settings(tmp_path), session=session, sleep=lambda _: None)
    assert manager.ensure_authenticated() is True
    assert manager.ensure_authenticated() is True
    assert manager.state is AuthState.AUTHENTICATED


def test_explicit_manual_session_check_keeps_challenge_state_without_login(tmp_path):
    session = Session([Response(text="<input name='secureq_ans'> سوال امنیتی")])
    manager = ArchiveAuthManager(settings(tmp_path), session=session, sleep=lambda _: None)
    manager.state = AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED
    assert manager.verify_existing_session() is False
    assert manager.state is AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED
    assert session.posts == 0


def test_explicit_session_check_verifies_and_persists_backend_session(tmp_path):
    session = Session([session_ok()])
    session.cookies.set("session", "opaque")
    manager = ArchiveAuthManager(settings(tmp_path), session=session, sleep=lambda _: None)
    manager.state = AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED
    assert manager.verify_existing_session() is True
    assert manager.state is AuthState.AUTHENTICATED
    assert (tmp_path / "cookies.json").exists()


def test_missing_credentials_never_posts(tmp_path):
    cfg = AuthSettings(True, "https://example.test/login/", "https://example.test/account/", "", "", tmp_path / "x")
    session = Session([Response("https://example.test/login/")])
    assert not ArchiveAuthManager(cfg, session=session).ensure_authenticated()
    assert session.posts == 0


def test_login_submits_configured_fields_and_hidden_csrf_data(tmp_path):
    html = ('<form><input type="hidden" name="login_security_str" value="nonce"><input type="hidden" name="_wp_http_referer" value="/account/login/">'
            '<input name="username"><input type="password" name="password"></form>')
    session = Session([Response("https://example.test/login/"), Response(text=html), session_ok()], [Response()])
    session.cookies.set("session", "opaque")
    manager = ArchiveAuthManager(settings(tmp_path), session=session, sleep=lambda _: None)
    assert manager.ensure_authenticated() is True
    assert session.post_data == [{"login_security_str": "nonce", "_wp_http_referer": "/account/login/", "username": "user", "password": "password"}]
    assert session.posts == 1


def test_challenge_is_not_submitted_and_session_is_preserved(tmp_path):
    # Solvable challenge is submitted with computed answer
    session = Session([Response("https://example.test/login/"), Response(text=login_page()), session_ok()], [Response()])
    session.cookies.set("existing", "cookie")
    manager = ArchiveAuthManager(settings(tmp_path), session=session, sleep=lambda _: None)
    assert manager.ensure_authenticated() is True
    assert manager.state is AuthState.AUTHENTICATED
    assert session.posts == 1 and session.cookies.get("existing") == "cookie"


def test_zero_cookie_challenge_does_not_create_or_overwrite_session_file(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text('{"valid_session":"opaque"}', encoding="utf-8")
    session = Session([Response("https://example.test/login/"), Response(text='<input name="secureq_ans"> سوال امنیتی')])
    session.cookies.clear()  # Simulate an intermediate jar that has no session.
    manager = ArchiveAuthManager(settings(tmp_path), session=session, sleep=lambda _: None)
    manager.session.cookies.clear()
    assert manager.ensure_authenticated() is False
    assert path.read_text(encoding="utf-8") == '{"valid_session":"opaque"}'


def test_empty_session_file_is_removed_and_not_authenticated(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text("{}", encoding="utf-8")
    manager = ArchiveAuthManager(settings(tmp_path), session=Session())
    assert not path.exists()
    assert len(manager.session.cookies) == 0
    assert manager.state is AuthState.AUTH_FAILED


def test_valid_authenticated_session_persists_and_reloads(tmp_path):
    session = Session([session_ok()])
    session.cookies.set("session", "opaque")
    first = ArchiveAuthManager(settings(tmp_path), session=session)
    assert first.ensure_authenticated() is True
    assert first._save_cookies() is True
    reloaded = ArchiveAuthManager(settings(tmp_path), session=Session())
    assert len(reloaded.session.cookies) == 1


# --- lag detection and the in-code watchdog ----------------------------
def slow_settings(tmp_path: Path, **overrides) -> AuthSettings:
    """AuthSettings is frozen, so build it through replace() instead."""
    return replace(AuthSettings(True, "https://example.test/login/", "https://example.test/account/",
                                "user", "password", tmp_path / "cookies.json", "http://127.0.0.1:10808",
                                "username", "password", retry_count=2, check_interval=30.0),
                   **overrides)


def test_slow_check_is_reported_as_lagging(tmp_path):
    manager = ArchiveAuthManager(slow_settings(tmp_path, lag_threshold=0.05), session=Session())
    manager._note_latency(200.0)
    assert manager.lagging is True and manager.lag_streak == 1
    assert manager.status()["lagging"] is True
    assert manager.status()["last_check_ms"] == 200.0


def test_fast_check_clears_the_lag_flag(tmp_path):
    manager = ArchiveAuthManager(slow_settings(tmp_path, lag_threshold=0.05), session=Session())
    manager._note_latency(200.0)
    manager._note_latency(5.0)
    assert manager.lagging is False and manager.lag_streak == 0


def test_watchdog_polls_at_the_base_interval_while_healthy(tmp_path):
    manager = ArchiveAuthManager(slow_settings(tmp_path), session=Session())
    assert manager._next_interval() == 30.0


def test_watchdog_backs_off_while_lagging_and_returns_to_normal(tmp_path):
    manager = ArchiveAuthManager(slow_settings(tmp_path, lag_threshold=0.05,
                                               max_lag_streak=2, lag_backoff_max=4.0),
                                 session=Session())
    assert manager._next_interval() == 30.0          # streak 0
    manager._note_latency(200.0)
    assert manager._next_interval() == 30.0          # streak 1, under the limit
    manager._note_latency(200.0)
    assert manager._next_interval() == 30.0          # streak 2, still at the limit
    manager._note_latency(200.0)
    assert manager._next_interval() == 45.0          # streak 3 -> factor 1.5
    manager._note_latency(200.0)
    assert manager._next_interval() == 60.0          # streak 4 -> factor 2.0
    for _ in range(20):                              # saturate the streak
        manager._note_latency(200.0)
    assert manager._next_interval() == 120.0         # capped by lag_backoff_max=4
    manager._note_latency(1.0)
    assert manager._next_interval() == 30.0          # recovered
