"""Session-safe authentication for the archive collector.

The backend logs in on its own: it opens the login page through the archive
proxy, parses the *current* form (``archive_login_form``), answers a supported
security question (``archive_challenge_auto``) and posts it together with the
form's own hidden fields and the key served with that question.  Success is
only ever concluded from a fresh ``/account/`` check, never from the POST.
Challenge parsing lives in the helper modules; this class owns HTTP, session
persistence and the bounded retry policy.
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import requests

from archive_network import create_archive_session
from archive_logging import event as archive_event, safe_target, timer as ArchiveTimer
from archive_challenge import ChallengeHandler, UnsolvableChallenge
from archive_challenge_auto import AutomaticChallengeHandler
from archive_login_form import LoginForm, LoginFormError, build_login_payload, parse_login_form

LOG = logging.getLogger("archive.auth")


class AuthState(str, Enum):
    AUTHENTICATED = "AUTHENTICATED"
    AUTH_CHECKING = "AUTH_CHECKING"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    AUTHENTICATING = "AUTHENTICATING"
    AUTH_FAILED = "AUTH_FAILED"
    AUTH_MANUAL_INTERVENTION_REQUIRED = "AUTH_MANUAL_INTERVENTION_REQUIRED"


class SecurityChallengeRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool
    login_url: str
    check_url: str
    username: str
    password: str
    persistence_path: Path
    http_proxy: str = "http://127.0.0.1:10808"
    username_field: str = ""
    password_field: str = ""
    check_interval: float = 120
    request_timeout: float = 30
    retry_count: int = 3
    # Hard ceiling on credential POSTs per login run (all retries included).
    max_login_posts: int = 3
    # Fresh GETs allowed when the served question is unsupported (nothing is
    # posted for those, so they cannot lock the account).
    max_unsupported_refetch: int = 2
    # After a rejected login no new login is attempted for this long.
    failed_login_cooldown: float = 300.0


class _Outcome(Enum):
    SUCCESS = "success"
    MANUAL = "manual"
    REJECTED = "rejected"


def _proxy_label(proxy: str) -> str:
    """scheme://host:port without any userinfo."""
    try:
        parsed = urlsplit(proxy)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname or ''}{port}"
    except ValueError:
        return "<invalid>"


class ArchiveAuthManager:
    def __init__(self, settings: AuthSettings, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 challenge_handler: ChallengeHandler | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.settings, self.session, self._sleep = settings, session or create_archive_session(settings.http_proxy), sleep
        self.challenge_handler = challenge_handler if challenge_handler is not None else AutomaticChallengeHandler()
        self._clock = clock
        self.state = AuthState.AUTH_FAILED if settings.enabled else AuthState.AUTHENTICATED
        self._login_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failures = 0
        self._login_posts = 0
        self._login_blocked_until = 0.0
        self.last_check_error: str | None = None
        self._load_cookies()

    def _log(self, event: str, **fields: object) -> None:
        archive_event("authentication", event, proxy=_proxy_label(self.settings.http_proxy), **fields)

    def _transition(self, new: AuthState, reason: str | None = None) -> None:
        """Single place that changes ``state`` so every transition is logged."""
        old, self.state = self.state, new
        if old is not new:
            self._log("AUTH_STATE_TRANSITION", from_state=old.value, to_state=new.value, reason=reason)

    def _load_cookies(self) -> None:
        if not self.settings.enabled or not self.settings.persistence_path.exists():
            return
        path = self.settings.persistence_path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data \
                    or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
                path.unlink(missing_ok=True)
                self._log("AUTH_NO_PERSISTED_SESSION", reason="empty_or_invalid_cookie_jar")
                return
            self.session.cookies.update(data)
            if not self.session.cookies:
                path.unlink(missing_ok=True)
                self._log("AUTH_NO_PERSISTED_SESSION", reason="no_usable_cookies")
                return
            # Loaded, not yet trusted: ensure_authenticated() validates it via
            # /account/ before any login is attempted.
            self._log("AUTH_SESSION_LOADED", cookie_count=len(self.session.cookies))
        except (OSError, ValueError, TypeError):
            path.unlink(missing_ok=True)
            self._log("AUTH_NO_PERSISTED_SESSION", reason="invalid_cookie_file")

    def _save_cookies(self) -> bool:
        """Persist only a session proven authenticated by ``check()``.

        A login form/challenge may set no cookies or transient cookies.  Writing
        either would replace a previously valid persisted session with a file
        that falsely looks like one after restart.
        """
        if self.state is not AuthState.AUTHENTICATED or not self.session.cookies:
            self._log("AUTH_SESSION_NOT_PERSISTED", reason=("not_authenticated" if self.state is not AuthState.AUTHENTICATED else "empty_cookie_jar"),
                      cookie_count=len(self.session.cookies))
            return False
        path = self.settings.persistence_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        # Created 0600 from the start: the secret is never briefly world-readable.
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.session.cookies.get_dict()))
        os.chmod(temp, 0o600)
        temp.replace(path)
        return True

    def start(self) -> None:
        if not self.settings.enabled or self._thread:
            return
        self._thread = threading.Thread(target=self._watchdog, name="archive-auth-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _watchdog(self) -> None:
        while not self._stop.wait(self.settings.check_interval):
            self.ensure_authenticated()

    def check(self, *, keep_manual_challenge: bool = False) -> bool | None:
        """True=valid, False=expired, None=temporary transport failure."""
        if not self.settings.enabled:
            return True
        self.last_check_error = None
        self._transition(AuthState.AUTH_CHECKING)
        started = ArchiveTimer()
        self._log("AUTH_CHECK", target=safe_target(self.settings.check_url))
        try:
            response = self.session.get(self.settings.check_url, timeout=self.settings.request_timeout,
                                        allow_redirects=True)
        except requests.exceptions.ProxyError:
            self.last_check_error = "proxy_unavailable"
            self._transition(AuthState.AUTH_FAILED, "proxy_unavailable")
            self._log("AUTH_CHECK_PROXY_ERROR")
            return None
        except requests.Timeout:
            self.last_check_error = "network_timeout"
            self._transition(AuthState.AUTH_FAILED, "network_timeout")
            self._log("AUTH_CHECK_TIMEOUT")
            return None
        except requests.RequestException as exc:
            self.last_check_error = "network_error"
            self._transition(AuthState.AUTH_FAILED, "network_error")
            self._log("AUTH_CHECK_HTTP_ERROR", error=type(exc).__name__)
            return None
        challenge = self.challenge_handler.inspect(response.text)
        if challenge.present:
            # The normal automatic path proceeds to its login; an explicit
            # operator check stays at the manual boundary without logging in.
            self._transition(AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED if keep_manual_challenge
                             else AuthState.AUTH_EXPIRED, "challenge_on_session_check")
            self._log("AUTH_CHALLENGE_ON_SESSION_CHECK", status=response.status_code, challenge_kind=challenge.kind,
                      redirect_to=safe_target(response.url), elapsed_ms=started.ms)
            return False
        login_path = self.settings.login_url.rstrip("/")
        if response.url.rstrip("/") == login_path or response.status_code in (401, 403):
            self._transition(AuthState.AUTH_EXPIRED, "redirected_to_login")
            self._log("AUTH_EXPIRED", status=response.status_code, redirect_to=safe_target(response.url), elapsed_ms=started.ms)
            return False
        form = parse_login_form(response.text)
        if form is not None and form.is_login_form(self.settings.username_field, self.settings.password_field):
            # A 200 that renders the login form is not an account page.
            self._transition(AuthState.AUTH_EXPIRED, "login_form_on_account_page")
            self._log("AUTH_EXPIRED", status=response.status_code, redirect_to=safe_target(response.url),
                      reason="login_form_rendered", elapsed_ms=started.ms)
            return False
        if response.ok:
            self._transition(AuthState.AUTHENTICATED, "account_verified")
            self._log("AUTH_OK", status=response.status_code, redirect_to=safe_target(response.url),
                      cookie_count=len(self.session.cookies), elapsed_ms=started.ms)
            return True
        self.last_check_error = "http_error"
        self._transition(AuthState.AUTH_FAILED, "http_error")
        self._log("AUTH_CHECK_HTTP_ERROR", status=response.status_code)
        return None

    def ensure_authenticated(self) -> bool:
        # An unsolvable challenge is a deliberate terminal state.  Do this
        # before the account check so every archive search cannot restart the
        # check -> login -> challenge cycle.  Only verify_existing_session()
        # explicitly rechecks it.
        if self.state is AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED:
            self._log("AUTH_MANUAL_WAITING", cookie_count=len(self.session.cookies), session_preserved=True)
            return False
        status = self.check()
        if status is True or status is None:
            return status is True
        if not self._login_lock.acquire(blocking=False):
            return False
        try:
            return self._login()
        finally:
            self._login_lock.release()

    def verify_existing_session(self) -> bool:
        """Explicit UI-triggered check of this backend's session only.

        This never opens/posts the login form and therefore cannot treat a
        user's browser session as the archive service's session.
        """
        verified = self.check(keep_manual_challenge=True)
        if verified is not True:
            return False
        if not self._save_cookies():
            self._transition(AuthState.AUTH_FAILED, "session_not_persistable")
            self.last_check_error = "session_not_persistable"
            self._log("AUTH_SESSION_NOT_PERSISTED", reason="verified_empty_cookie_jar",
                      cookie_count=len(self.session.cookies))
            return False
        self._log("AUTH_SESSION_PERSISTED", cookie_count=len(self.session.cookies))
        return True

    # ------------------------------------------------------------------ login

    def _login(self) -> bool:
        if not self.settings.username or not self.settings.password:
            self._transition(AuthState.AUTH_FAILED, "missing_credentials")
            self._log("AUTH_LOGIN_FAILED", reason="missing_credentials")
            return False
        if not self.settings.username_field or not self.settings.password_field:
            self._transition(AuthState.AUTH_FAILED, "missing_login_field_configuration")
            self._log("AUTH_LOGIN_FAILED", reason="missing_login_field_configuration")
            return False
        remaining = self._login_blocked_until - self._clock()
        if remaining > 0:
            # A previous run was rejected; hammering a wrong password/answer
            # would risk locking the account.
            self._transition(AuthState.AUTH_FAILED, "login_cooldown")
            self._log("AUTH_LOGIN_COOLDOWN", retry_in_s=round(remaining))
            return False
        self._login_posts = 0  # budget for this whole run, shared by all retries
        for attempt in range(self.settings.retry_count):
            self._transition(AuthState.AUTHENTICATING, "login_start")
            self._log("AUTH_LOGIN_START", attempt=attempt + 1)
            started = ArchiveTimer()
            try:
                outcome = self._login_attempt()
            except LoginFormError as exc:  # permanent: never retried, nothing posted
                self._transition(AuthState.AUTH_FAILED, str(exc))
                self._log("AUTH_LOGIN_FAILED", reason=str(exc), attempt=attempt + 1)
                return False
            except requests.exceptions.ProxyError:
                reason = "proxy_unavailable"
            except requests.Timeout:
                reason = "timeout"
            except requests.RequestException as exc:
                reason = type(exc).__name__
            else:
                if outcome is _Outcome.SUCCESS:
                    self._failures = 0
                    self._log("AUTH_LOGIN_SUCCESS", cookie_count=len(self.session.cookies),
                              posts=self._login_posts, elapsed_ms=started.ms)
                    return True
                if outcome is _Outcome.MANUAL:
                    self._transition(AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED, "challenge_not_solvable")
                    self._log("AUTH_MANUAL_WAITING", cookie_count=len(self.session.cookies), session_preserved=True)
                    return False
                self._transition(AuthState.AUTH_FAILED, "login_rejected")
                self.last_check_error = "login_rejected"
                self._login_blocked_until = self._clock() + self.settings.failed_login_cooldown
                self._log("AUTH_LOGIN_FAILED", reason="login_rejected", posts=self._login_posts,
                          cooldown_s=self.settings.failed_login_cooldown)
                return False
            self._failures += 1
            self._transition(AuthState.AUTH_FAILED, reason)
            self._log("AUTH_LOGIN_FAILED", reason=reason, attempt=attempt + 1)
            if attempt + 1 < self.settings.retry_count:
                self._sleep(min(60, 2 ** self._failures) + random.uniform(0, 0.5))
        return False

    def _login_attempt(self) -> _Outcome:
        """One login run: fetch form -> (solve) -> POST -> verify, bounded.

        Every iteration starts with a *fresh* GET, so the ``secureq_key``,
        question and CSRF fields of a POST always come from the response
        immediately before it.  Nothing from an earlier page load is reused.
        """
        s = self.settings
        # POSTs are capped by max_login_posts; unsupported-question refetches
        # post nothing and are capped separately, so the loop always ends.
        fetch_cap = s.max_login_posts + s.max_unsupported_refetch + 1
        unsupported = 0
        for fetch in range(1, fetch_cap + 1):
            page = self.session.get(s.login_url, timeout=s.request_timeout, allow_redirects=True)
            page.raise_for_status()
            challenge = self.challenge_handler.inspect(page.text)
            if challenge.present and challenge.kind != "security_question":
                self._log("AUTH_CHALLENGE_UNSUPPORTED", challenge_kind=challenge.kind, round=fetch)
                return _Outcome.MANUAL
            form = parse_login_form(page.text)
            if form is None or not form.is_login_form(s.username_field, s.password_field):
                if challenge.present:  # a challenge without a usable login form is never guessed at
                    self._log("AUTH_CHALLENGE_UNSOLVABLE", reason="no_login_form", round=fetch)
                    return _Outcome.MANUAL
                # No login form: an already-valid session may have redirected us.
                if self.check() is True:
                    return self._finish_authenticated()
                raise requests.HTTPError("login form not found")
            self._log("AUTH_LOGIN_FORM_PARSED", round=fetch, challenge=challenge.present,
                      has_key=form.secureq_key is not None, hidden_field_names=sorted(form.hidden_fields))
            solution = None
            if challenge.present:
                try:
                    solution = self.challenge_handler.solve(form)
                except UnsolvableChallenge as exc:
                    self._log("AUTH_CHALLENGE_UNSOLVABLE", reason=exc.reason, round=fetch)
                    unsupported += 1
                    if exc.reason == "unsupported_question" and unsupported <= s.max_unsupported_refetch:
                        continue  # the site may serve a supported question next time
                    return _Outcome.MANUAL
                self._log("AUTH_CHALLENGE_SOLVED", question_kind=solution.kind, round=fetch)
            if self._login_posts >= s.max_login_posts:
                self._log("AUTH_LOGIN_RETRY_LIMIT", posts=self._login_posts)
                return _Outcome.REJECTED
            payload = build_login_payload(form, username_field=s.username_field, username=s.username,
                                          password_field=s.password_field, password=s.password, solution=solution)
            target = form.resolve_action(page.url, s.login_url)
            origin = urlsplit(target)
            headers = {"Referer": page.url, "Origin": f"{origin.scheme}://{origin.netloc}"}
            self._login_posts += 1
            # Field *names* only; values include the password, key and answer.
            self._log("AUTH_LOGIN_SUBMITTED", login_target=safe_target(target), round=fetch,
                      post_number=self._login_posts, field_names=sorted(payload), answered=solution is not None)
            response = self.session.post(target, data=payload, headers=headers,
                                         timeout=s.request_timeout, allow_redirects=True)
            if response.status_code >= 400:
                raise requests.HTTPError(f"login POST failed ({response.status_code})")
            after = self.challenge_handler.inspect(response.text)
            if after.present:
                if after.kind != "security_question":
                    self._log("AUTH_CHALLENGE_UNSUPPORTED", challenge_kind=after.kind, round=fetch)
                    return _Outcome.MANUAL
                # Rejected or rotated: loop to a fresh GET (never reuse this key).
                self._log("AUTH_CHALLENGE_REISSUED", round=fetch, redirect_to=safe_target(response.url))
                continue
            verified = self.check()
            if verified is True:
                return self._finish_authenticated()
            if verified is None:
                raise requests.HTTPError("could not verify the login")
            return _Outcome.REJECTED
        self._log("AUTH_LOGIN_RETRY_LIMIT", posts=self._login_posts, fetches=fetch_cap)
        return _Outcome.REJECTED

    def _finish_authenticated(self) -> _Outcome:
        """Called only after ``check()`` returned True on /account/."""
        if not self._save_cookies():
            raise requests.HTTPError("authenticated response had no persistable session cookies")
        self._log("AUTH_SESSION_PERSISTED", cookie_count=len(self.session.cookies))
        return _Outcome.SUCCESS
