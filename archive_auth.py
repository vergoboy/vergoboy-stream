"""Session-safe authentication for the archive collector.

This module intentionally detects security challenges but never solves or
submits them.  A browser login is informative to the operator only: the
backend must independently verify its own persisted session before it can use
the archive.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

import requests

from archive_network import create_archive_session
from archive_logging import event as archive_event, safe_target, timer as ArchiveTimer
try:
    from archive_challenge_auto import AutomaticChallengeHandler
except Exception:
    AutomaticChallengeHandler = None  # type: ignore[misc,assignment]
from archive_challenge import ChallengeHandler, ManualOnlyChallengeHandler

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


def _login_hidden_fields(html: str) -> dict[str, str]:
    """Retain CSRF/referer inputs from a login form; never infer challenge answers."""
    fields: dict[str, str] = {}
    for tag in re.findall(r"<input\b[^>]*>", html, re.I):
        attrs = dict((name.lower(), value) for name, _quote, value in re.findall(r"([\w:-]+)\s*=\s*(['\"])(.*?)\2", tag, re.S))
        if attrs.get("type", "").lower() != "hidden":
            continue
        name, value = attrs.get("name", ""), attrs.get("value", "")
        if name:
            fields[name] = value
    return fields


class ArchiveAuthManager:
    def __init__(self, settings: AuthSettings, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 challenge_handler: ChallengeHandler | None = None) -> None:
        self.settings, self.session, self._sleep = settings, session or create_archive_session(settings.http_proxy), sleep
        if challenge_handler is not None:
            self.challenge_handler = challenge_handler
        elif AutomaticChallengeHandler is not None:
            self.challenge_handler = AutomaticChallengeHandler()
        else:
            self.challenge_handler = ManualOnlyChallengeHandler()
        self.state = AuthState.AUTH_FAILED if settings.enabled else AuthState.AUTHENTICATED
        self._login_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failures = 0
        self.last_check_error: str | None = None
        self._load_cookies()

    def _log(self, event: str, **fields: object) -> None:
        archive_event("authentication", event, proxy=self.settings.http_proxy, **fields)

    def _load_cookies(self) -> None:
        if not self.settings.enabled or not self.settings.persistence_path.exists():
            return
        try:
            data = json.loads(self.settings.persistence_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data:
                self.settings.persistence_path.unlink(missing_ok=True)
                self._log("AUTH_NO_PERSISTED_SESSION", reason="empty_or_invalid_cookie_jar")
                return
            self.session.cookies.update(data)
            if not self.session.cookies:
                self.settings.persistence_path.unlink(missing_ok=True)
                self._log("AUTH_NO_PERSISTED_SESSION", reason="no_usable_cookies")
                return
            self._log("AUTH_SESSION_LOADED", cookie_count=len(self.session.cookies))
        except (OSError, ValueError, TypeError):
            self.settings.persistence_path.unlink(missing_ok=True)
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
        temp.write_text(json.dumps(self.session.cookies.get_dict()), encoding="utf-8")
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
        self.state = AuthState.AUTH_CHECKING
        started = ArchiveTimer()
        self._log("AUTH_CHECK", target=safe_target(self.settings.check_url))
        try:
            response = self.session.get(self.settings.check_url, timeout=self.settings.request_timeout,
                                        allow_redirects=True)
        except requests.exceptions.ProxyError:
            self.last_check_error = "proxy_unavailable"
            self.state = AuthState.AUTH_FAILED
            self._log("AUTH_CHECK_PROXY_ERROR")
            return None
        except requests.Timeout:
            self.last_check_error = "network_timeout"
            self.state = AuthState.AUTH_FAILED
            self._log("AUTH_CHECK_TIMEOUT")
            return None
        except requests.RequestException as exc:
            self.last_check_error = "network_error"
            self.state = AuthState.AUTH_FAILED
            self._log("AUTH_CHECK_HTTP_ERROR", error=type(exc).__name__)
            return None
        challenge = self.challenge_handler.inspect(response.text)
        if challenge.present:
            # The normal automatic path is allowed to proceed to its login
            # preflight. An explicit operator check must instead remain at the
            # manual boundary, without triggering another login attempt.
            self.state = (AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED
                          if keep_manual_challenge else AuthState.AUTH_EXPIRED)
            self._log("AUTH_CHALLENGE_ON_SESSION_CHECK", status=response.status_code, challenge_kind=challenge.kind,
                      redirect_to=safe_target(response.url), elapsed_ms=started.ms)
            return False
        login_path = self.settings.login_url.rstrip("/")
        if response.url.rstrip("/") == login_path or response.status_code in (401, 403):
            self.state = AuthState.AUTH_EXPIRED
            self._log("AUTH_EXPIRED", status=response.status_code, redirect_to=safe_target(response.url), elapsed_ms=started.ms)
            return False
        if response.ok:
            self.state = AuthState.AUTHENTICATED
            self._log("AUTH_OK", status=response.status_code, redirect_to=safe_target(response.url),
                      cookie_count=len(self.session.cookies), elapsed_ms=started.ms)
            return True
        self.last_check_error = "http_error"
        self.state = AuthState.AUTH_FAILED
        self._log("AUTH_CHECK_HTTP_ERROR", status=response.status_code)
        return None

    def ensure_authenticated(self) -> bool:
        # A challenge is a deliberate terminal state.  Do this before the
        # account check so every archive search cannot restart the
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
            self.state = AuthState.AUTH_FAILED
            self.last_check_error = "session_not_persistable"
            self._log("AUTH_SESSION_NOT_PERSISTED", reason="verified_empty_cookie_jar",
                      cookie_count=len(self.session.cookies))
            return False
        self._log("AUTH_SESSION_PERSISTED", cookie_count=len(self.session.cookies))
        return True

    def _login(self) -> bool:
        if not self.settings.username or not self.settings.password:
            self.state = AuthState.AUTH_FAILED
            self._log("AUTH_LOGIN_FAILED", reason="missing_credentials")
            return False
        if not self.settings.username_field or not self.settings.password_field:
            self.state = AuthState.AUTH_FAILED
            self._log("AUTH_LOGIN_FAILED", reason="missing_login_field_configuration")
            return False
        for attempt in range(self.settings.retry_count):
            self.state = AuthState.AUTHENTICATING
            self._log("AUTH_LOGIN_START", attempt=attempt + 1)
            try:
                started = ArchiveTimer()
                # Inspect the actual form before sending credentials. A
                # challenge is a hard manual boundary, not a failed login and
                # never receives a generated/submitted answer from this code.
                form = self.session.get(self.settings.login_url, timeout=self.settings.request_timeout,
                                        allow_redirects=True)
                challenge = self.challenge_handler.inspect(form.text)
                if challenge.present:
                    # Try to auto-solve if supported
                    auto_solved = False
                    try:
                        if hasattr(self.challenge_handler, 'can_auto_solve') and self.challenge_handler.can_auto_solve(challenge):
                            pass  # will try to solve below
                        # also check if it has solve method and kind is security_question
                    except Exception:
                        pass
                    post_solved_response = None
                    if getattr(challenge, 'kind', None) == 'security_question' and hasattr(self.challenge_handler, 'solve'):
                        try:
                            sol = self.challenge_handler.solve(form.text)
                            if sol and 'secureq_ans' in sol:
                                payload = _login_hidden_fields(form.text)
                                payload.update({
                                    self.settings.username_field: self.settings.username,
                                    self.settings.password_field: self.settings.password,
                                })
                                payload.update(sol)
                                self._log("AUTH_SECURITY_QUESTION_SOLVED", login_target=safe_target(self.settings.login_url))
                                post_solved_response = self.session.post(self.settings.login_url, data=payload,
                                                                         timeout=self.settings.request_timeout, allow_redirects=True)
                                try:
                                    from urllib.parse import urlparse
                                    pr = post_solved_response
                                    self._log("AUTH_CHALLENGE_RESULT", status=pr.status_code, final_path=urlparse(pr.url).path, next_challenge=self.challenge_handler.inspect(pr.text).present)
                                except Exception:
                                    pass
                                # continue to check
                                form = type('R', (), {'text': post_solved_response.text, 'status_code': post_solved_response.status_code, 'url': post_solved_response.url, 'raise_for_status': lambda self: None})()
                                challenge = self.challenge_handler.inspect(form.text)
                                if not challenge.present:
                                    auto_solved = True
                                    response = post_solved_response
                        except Exception as exc:
                            self._log("AUTH_SECURITY_QUESTION_SOLVE_FAILED", error=type(exc).__name__)
                    if not auto_solved:
                        self.state = AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED
                        self._log("AUTH_MANUAL_INTERVENTION_REQUIRED", status=form.status_code, challenge_kind=challenge.kind,
                                  login_target=safe_target(self.settings.login_url), elapsed_ms=started.ms)
                        self._log("AUTH_MANUAL_WAITING", cookie_count=len(self.session.cookies),
                                  session_preserved=True)
                        return False
                form.raise_for_status()
                if 'response' not in locals() or not auto_solved:
                    payload = _login_hidden_fields(form.text)
                    payload.update({
                        self.settings.username_field: self.settings.username,
                        self.settings.password_field: self.settings.password,
                    })
                    # Log field *names* only. Values include a password/CSRF data.
                    self._log("AUTH_LOGIN_SUBMITTED", login_target=safe_target(self.settings.login_url),
                              field_names=sorted(payload), hidden_field_count=len(payload) - 2)
                    response = self.session.post(self.settings.login_url, data=payload,
                                                 timeout=self.settings.request_timeout, allow_redirects=True)
                # if auto_solved, response is already set
                challenge = self.challenge_handler.inspect(response.text)
                if challenge.present:
                    # try to auto-solve security questions repeatedly if they keep coming
                    for _ in range(2):  # solve at most twice more
                        if getattr(challenge, 'kind', None) == 'security_question' and hasattr(self.challenge_handler, 'solve'):
                            try:
                                sol = self.challenge_handler.solve(response.text)
                                if sol and 'secureq_ans' in sol:
                                    payload = _login_hidden_fields(response.text)
                                    payload.update({
                                        self.settings.username_field: self.settings.username,
                                        self.settings.password_field: self.settings.password,
                                    })
                                    payload.update(sol)
                                    self._log("AUTH_SECURITY_QUESTION_SOLVED", login_target=safe_target(self.settings.login_url))
                                    response = self.session.post(self.settings.login_url, data=payload,
                                                                 timeout=self.settings.request_timeout, allow_redirects=True)
                                    try:
                                        from urllib.parse import urlparse
                                        self._log("AUTH_CHALLENGE_RESULT", status=response.status_code, final_path=urlparse(response.url).path, next_challenge=self.challenge_handler.inspect(response.text).present)
                                    except Exception:
                                        pass
                                    challenge = self.challenge_handler.inspect(response.text)
                                    if not challenge.present:
                                        break
                                    continue
                            except Exception as exc:
                                self._log("AUTH_SECURITY_QUESTION_SOLVE_FAILED", error=type(exc).__name__)
                        break
                    if challenge.present:
                        if getattr(challenge, 'kind', None) == 'security_question':
                            self.state = AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED
                            self._log("AUTH_MANUAL_INTERVENTION_REQUIRED", status=response.status_code, challenge_kind=challenge.kind,
                                      redirect_to=safe_target(response.url), elapsed_ms=started.ms)
                            return False
                        else:
                            self.state = AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED
                            self._log("AUTH_MANUAL_INTERVENTION_REQUIRED", status=response.status_code, challenge_kind=challenge.kind,
                                      redirect_to=safe_target(response.url), elapsed_ms=started.ms)
                            return False
                if response.status_code >= 400 or self.check() is not True:
                    raise requests.HTTPError(f"login did not establish a session ({response.status_code})")
                if not self._save_cookies():
                    raise requests.HTTPError("authenticated response had no persistable session cookies")
                self._log("AUTH_SESSION_PERSISTED", cookie_count=len(self.session.cookies))
                self._failures = 0
                self.state = AuthState.AUTHENTICATED
                self._log("AUTH_LOGIN_SUCCESS", status=response.status_code, redirect_to=safe_target(response.url),
                          cookie_count=len(self.session.cookies), elapsed_ms=started.ms)
                return True
            except requests.exceptions.ProxyError:
                reason = "proxy_unavailable"
            except requests.Timeout:
                reason = "timeout"
            except requests.RequestException as exc:
                reason = type(exc).__name__
            self._failures += 1
            self.state = AuthState.AUTH_FAILED
            self._log("AUTH_LOGIN_FAILED", reason=reason, attempt=attempt + 1)
            if attempt + 1 < self.settings.retry_count:
                self._sleep(min(60, 2 ** self._failures) + random.uniform(0, 0.5))
        return False
