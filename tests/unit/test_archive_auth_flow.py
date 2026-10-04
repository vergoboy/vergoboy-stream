"""End-to-end ArchiveAuthManager login against a fake Digimoviez.

The session is a real ``requests.Session`` built by ``create_archive_session``
(real cookie jar + proxy config); only the transport is faked.
"""
from __future__ import annotations

import json
import logging
import stat

import pytest

from archive_auth import ArchiveAuthManager, AuthSettings, AuthState
from archive_network import create_archive_session, create_direct_media_session
from tests.unit.fake_digimoviez import ACCOUNT_URL, BASE, LOGIN_URL, FakeDigimoviez

PROXY = "http://127.0.0.1:10808"
PASSWORD = "pw!Secret-7"


def make(tmp_path, site=None, **overrides):
    site = site or FakeDigimoviez()
    clock = overrides.pop("clock", None)
    cfg = dict(enabled=True, login_url=LOGIN_URL, check_url=ACCOUNT_URL, username="user", password=PASSWORD,
               persistence_path=tmp_path / "session.json", http_proxy=PROXY, username_field="username",
               password_field="password", retry_count=3)
    cfg.update(overrides)
    session = site.mount_on(create_archive_session(PROXY))
    manager = ArchiveAuthManager(AuthSettings(**cfg), session=session, sleep=lambda _: None,
                                 **({"clock": clock} if clock else {}))
    return manager, site


def restart(tmp_path, site, **overrides):
    """A new process: fresh Session/manager, same on-disk file, same remote site."""
    return make(tmp_path, site, **overrides)[0]


# --- successful login ---------------------------------------------------------
def test_successful_login_verifies_account_and_persists(tmp_path):
    manager, site = make(tmp_path)
    assert manager.ensure_authenticated() is True
    assert manager.state is AuthState.AUTHENTICATED
    assert site.posts == 1 and site.rejections == []
    assert site.account_gets >= 2                      # one before login, one verifying it
    saved = json.loads((tmp_path / "session.json").read_text())
    assert list(saved) == ["wordpress_logged_in"]


def test_post_carries_form_fields_current_key_and_referer(tmp_path):
    manager, site = make(tmp_path)
    assert manager.ensure_authenticated()
    data, current = site.last_post, site.issued[-1]     # the last page served before the POST
    assert data["secureq_key"] == current["key"] and data["secureq_ans"] == current["answer"] == "540"
    assert data["login_security_str"] == current["nonce"] and data["_wp_http_referer"] == "/account/login/"
    assert data["extra_csrf"] == "abc123" and data["loginkon"] == ""        # hidden + submit preserved
    assert "popup_only_field" not in data                                    # popup decoy not leaked
    post = [r for r in site.requests if r["method"] == "POST"][0]
    assert post["headers"]["Referer"] == LOGIN_URL and post["headers"]["Origin"] == BASE


def test_login_without_challenge_still_works(tmp_path):
    manager, site = make(tmp_path, FakeDigimoviez(challenge=False))
    assert manager.ensure_authenticated() is True
    assert "secureq_ans" not in site.last_post and "secureq_key" not in site.last_post


# --- failed login --------------------------------------------------------------
def test_wrong_password_fails_without_retry_storm(tmp_path):
    manager, site = make(tmp_path, FakeDigimoviez(password="other"))
    assert manager.ensure_authenticated() is False
    assert manager.state is AuthState.AUTH_FAILED
    assert site.posts <= 3                              # max_login_posts
    assert not (tmp_path / "session.json").exists()    # nothing persisted for a failed login
    assert all("bad_credentials" in r for r in site.rejections)


def test_failure_sets_cooldown_so_next_call_does_not_post(tmp_path):
    now = [1000.0]
    manager, site = make(tmp_path, FakeDigimoviez(password="other"), clock=lambda: now[0])
    assert manager.ensure_authenticated() is False
    posts = site.posts
    assert manager.ensure_authenticated() is False
    assert site.posts == posts                          # cooldown: no new credential POST
    now[0] += 301
    manager.ensure_authenticated()
    assert site.posts > posts                           # allowed again after the cooldown


# --- key rotation / stale key ----------------------------------------------------
def test_rejected_post_refetches_form_and_never_reuses_the_old_key(tmp_path):
    site = FakeDigimoviez(reject_first_posts=1)         # server rotates the challenge once
    manager, _ = make(tmp_path, site)
    assert manager.ensure_authenticated() is True
    assert site.posts == 2
    assert len(site.post_keys) == 2 and site.post_keys[0] != site.post_keys[1]   # never the same key twice
    assert site.post_keys[1] == site.issued[-1]["key"]                           # last POST used the newest key
    assert site.rejections == ["server_rotated"]        # and no stale_key/stale_nonce rejection
    assert site.login_gets >= 2                         # a fresh GET between the POSTs


def test_stale_key_is_rejected_by_the_server_model(tmp_path):
    """The fake site really enforces freshness: replaying a previous key fails."""
    site = FakeDigimoviez()
    manager, _ = make(tmp_path, site)
    site.session.get(LOGIN_URL)                         # key k1 issued
    site.session.get(LOGIN_URL)                         # k1 is now stale, k2 current
    r = site.session.post(LOGIN_URL, data={"secureq_key": "k1", "secureq_ans": "540", "login_security_str": "nonce1",
                                           "username": "user", "password": PASSWORD},
                          headers={"Referer": LOGIN_URL})
    assert "stale_key" in site.rejections[-1] and "stale_nonce" in site.rejections[-1]


def test_question_changes_between_posts_each_answer_matches_its_own_question(tmp_path):
    site = FakeDigimoviez(questions=[("پانصد بعلاوۀ چهل", "540"), ("۶ در ۸", "48"), ("ده", None)][:2],
                          reject_first_posts=1)
    manager, _ = make(tmp_path, site)
    assert manager.ensure_authenticated() is True
    assert [i["answer"] for i in site.issued[:2]] == ["540", "48"]
    assert site.rejections == ["server_rotated"] and "wrong_answer" not in site.rejections[0]


# --- retry limits ---------------------------------------------------------------
def test_endless_rejection_stops_at_the_post_cap(tmp_path):
    site = FakeDigimoviez(always_reject=True)
    manager, _ = make(tmp_path, site, max_login_posts=3, retry_count=5)
    assert manager.ensure_authenticated() is False
    assert site.posts == 3                              # hard cap across ALL retries
    assert manager.state is AuthState.AUTH_FAILED


def test_unsupported_question_never_posts_and_refetch_is_bounded(tmp_path):
    site = FakeDigimoviez(questions=[("رنگ آسمان چیست", "x")])
    manager, _ = make(tmp_path, site, max_unsupported_refetch=2)
    assert manager.ensure_authenticated() is False
    assert site.posts == 0                              # a guess is never submitted
    assert site.login_gets == 3                         # 1 + 2 refetches, then stop
    assert manager.state is AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED


def test_unsupported_then_supported_question_succeeds(tmp_path):
    site = FakeDigimoviez(questions=[("رنگ آسمان چیست", "x"), ("۶ در ۸", "48")])
    manager, _ = make(tmp_path, site)
    assert manager.ensure_authenticated() is True
    assert site.posts == 1 and site.last_post["secureq_ans"] == "48" and site.last_post["secureq_key"] == "k2"


def test_manual_state_is_terminal_until_explicit_recheck(tmp_path):
    site = FakeDigimoviez(questions=[("رنگ آسمان چیست", "x")])
    manager, _ = make(tmp_path, site)
    assert manager.ensure_authenticated() is False
    gets = site.login_gets
    for _ in range(5):
        assert manager.ensure_authenticated() is False
    assert site.login_gets == gets and site.posts == 0   # no loop re-entering login


def test_captcha_is_never_answered(tmp_path):
    site = FakeDigimoviez()
    original = site._login_page
    site._login_page = lambda: original().replace("</form>", '<div class="g-recaptcha" data-sitekey="k"></div></form>')
    manager, _ = make(tmp_path, site)
    assert manager.ensure_authenticated() is False
    assert site.posts == 0 and manager.state is AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED


# --- verification is not weakened -------------------------------------------------
def test_account_page_that_renders_login_form_is_not_authenticated(tmp_path):
    """200 on /account/ (no redirect) but showing the login form must not count."""
    site = FakeDigimoviez(always_reject=True, account_shows_login_inline=True)
    manager, _ = make(tmp_path, site)
    assert manager.check() is False
    assert manager.state is AuthState.AUTH_EXPIRED


def test_post_that_sets_no_valid_session_is_not_success(tmp_path):
    site = FakeDigimoviez()
    manager, _ = make(tmp_path, site)
    original = site._handle_login_post

    def fake_success_page_without_session(request):
        original(request)
        site.sessions.clear()                            # page says "ok" but the cookie is worthless
        site.session.cookies.set("wordpress_logged_in", "bogus")
        return "<html><body><h1>حساب</h1></body></html>", ACCOUNT_URL
    site._handle_login_post = fake_success_page_without_session
    assert manager.ensure_authenticated() is False
    assert not (tmp_path / "session.json").exists()


# --- persistence / restart / expiry ----------------------------------------------
def test_session_file_is_private(tmp_path):
    manager, _ = make(tmp_path)
    assert manager.ensure_authenticated()
    assert stat.S_IMODE((tmp_path / "session.json").stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))


def test_restart_reuses_validated_session_without_logging_in(tmp_path):
    first, site = make(tmp_path)
    assert first.ensure_authenticated()
    posts, gets = site.posts, site.login_gets
    second = restart(tmp_path, site)
    assert len(second.session.cookies) == 1             # reloaded from disk
    assert second.ensure_authenticated() is True
    assert second.state is AuthState.AUTHENTICATED
    assert site.posts == posts and site.login_gets == gets   # validated via /account/, no new login


def test_expired_persisted_session_triggers_automatic_relogin(tmp_path):
    first, site = make(tmp_path)
    assert first.ensure_authenticated()
    old_cookie = json.loads((tmp_path / "session.json").read_text())["wordpress_logged_in"]
    site.expire_sessions()                              # server forgot the session (e.g. across a restart)
    second = restart(tmp_path, site)
    assert second.ensure_authenticated() is True
    assert site.posts == 2
    new_cookie = json.loads((tmp_path / "session.json").read_text())["wordpress_logged_in"]
    assert new_cookie != old_cookie and second.state is AuthState.AUTHENTICATED


def test_session_expiring_while_running_reauthenticates(tmp_path):
    manager, site = make(tmp_path)
    assert manager.ensure_authenticated()
    site.expire_sessions()
    assert manager.ensure_authenticated() is True
    assert site.posts == 2


def test_corrupt_session_file_is_discarded_then_login_runs(tmp_path):
    (tmp_path / "session.json").write_text("{not json")
    manager, site = make(tmp_path)
    assert manager.ensure_authenticated() is True and site.posts == 1


# --- proxy routing / media isolation ----------------------------------------------
def test_every_archive_request_uses_the_archive_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://wrong-proxy:9999")
    manager, site = make(tmp_path)
    assert manager.ensure_authenticated()
    assert site.requests and all(r["proxies"] == {"http": PROXY, "https": PROXY} for r in site.requests)
    assert {r["method"] for r in site.requests} == {"GET", "POST"}


def test_media_session_is_direct_and_never_touches_the_archive_site(tmp_path):
    site = FakeDigimoviez()
    media = create_direct_media_session()
    media.mount("https://", site)
    media.get("https://cdn.example.test/video.mp4", timeout=5)
    assert site.requests[-1]["proxies"] == {}           # direct: no archive proxy
    assert media.cookies.get_dict() == {}               # media client has no auth cookies


def test_manager_session_is_not_the_media_session(tmp_path):
    manager, _ = make(tmp_path)
    media = create_direct_media_session()
    assert manager.session is not media and manager.session.proxies and not media.proxies


# --- logging never leaks secrets ---------------------------------------------------
def test_logs_never_contain_secrets(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    logging.getLogger("archive").propagate = True
    site = FakeDigimoviez(reject_first_posts=1)
    manager, _ = make(tmp_path, site, http_proxy="http://proxyuser:proxypass@127.0.0.1:10808")
    assert manager.ensure_authenticated()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "AUTH_LOGIN_SUCCESS" in text and "AUTH_STATE_TRANSITION" in text    # structured events exist
    for secret in (PASSWORD, "proxypass", "proxyuser", "SID-", "secretvalue", "540", "nonce1"):
        assert secret not in text, secret
