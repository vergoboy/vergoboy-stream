"""A fake Digimoviez mounted as a ``requests`` transport adapter.

It runs behind a *real* ``requests.Session`` (so cookie jar, proxy settings and
header preparation are the production code paths) and enforces what the real
site does: one valid challenge at a time, key/question/answer/nonce/referer
must match, only a logged-in cookie opens /account/.
"""
from __future__ import annotations

import datetime
import json
from urllib.parse import parse_qs

import requests
from requests.adapters import BaseAdapter

BASE = "https://digimoviez.test"
LOGIN_URL = f"{BASE}/account/login/"
ACCOUNT_URL = f"{BASE}/account/"
AJAX_URL = f"{BASE}/wp-admin/admin-ajax.php"
COOKIE = "wordpress_logged_in"

# WordPress emits this link *only* for an authenticated session.  It is the one
# structural difference between the logged-in and logged-out rendering of the
# same page, because the theme renders the login widget either way.
LOGOUT_LINK = f'<a href="{BASE}/wp-login.php?action=logout&amp;_wpnonce=deadbeef">خروج</a>'

# Dormant popup the theme renders into every page, with a DIFFERENT key and a
# decoy question.  Correct code must never pair with or answer it.
POPUP = (
    '<div class="popup_box"><div class="inner_popup_box"><div class="body_popup">'
    '<form class="login_box_form"><input type="hidden" name="login_security_str" value="POPUPNONCE">'
    '<input type="hidden" name="popup_only_field" value="decoy">'
    '<input type="text" name="username"><input type="password" name="password">'
    '<input type="hidden" name="secureq_key" value="999"><span>سوال امنیتی: ۱ بعلاوه ۱</span>'
    '<input type="number" name="secureq_ans"></form></div></div></div>'
)


def account_page() -> str:
    """An authenticated page: account heading plus the logout link."""
    return "<html><body>" + POPUP + "<h1>حساب کاربری</h1>" + LOGOUT_LINK + "</body></html>"


class FakeDigimoviez(BaseAdapter):
    def __init__(self, questions=(("پانصد بعلاوۀ چهل", "540"),), *, username="user", password="pw!Secret-7",
                 reject_first_posts=0, always_reject=False, challenge=True,
                 account_shows_login_inline=False):
        super().__init__()
        self.questions = list(questions)
        self.username, self.password = username, password
        self.reject_first_posts, self.always_reject, self.challenge = reject_first_posts, always_reject, challenge
        self.account_shows_login_inline = account_shows_login_inline
        self.post_keys: list[str | None] = []
        self.session: requests.Session | None = None
        self.sessions: set[str] = set()
        self.requests: list[dict] = []       # every request seen, incl. proxies actually used
        self.rejections: list[str] = []      # why each POST was rejected
        self.issued: list[dict] = []         # every challenge served, in order
        self.current: dict | None = None
        self.posts = self.login_gets = self.account_gets = self.ajax_searches = 0
        self._sid = 0

    # --- wiring -------------------------------------------------------
    def mount_on(self, session: requests.Session) -> requests.Session:
        self.session = session
        session.mount("https://", self)
        session.mount("http://", self)
        return session

    def expire_sessions(self) -> None:
        self.sessions.clear()

    def close(self) -> None:  # BaseAdapter API
        pass

    @staticmethod
    def item_small_loop(url: str, title: str) -> str:
        """The theme's AJAX search card shape."""
        return (f'<div class="item_small_loop" data-ID="4370">'
                f'<a title="دانلود فیلم {title}" href="{url}">'
                f'<div class="cover"><img src="{BASE}/poster.jpg"></div>'
                f'<h2>{title}</h2></a></div>')

    # --- site behaviour -----------------------------------------------
    def _issue(self) -> dict:
        n = len(self.issued) + 1
        question, answer = self.questions[(n - 1) % len(self.questions)]
        self.current = {"key": f"k{n}", "nonce": f"nonce{n}", "question": question, "answer": answer}
        self.issued.append(self.current)
        return self.current

    def _login_page(self) -> str:
        c = self._issue()
        challenge = (f'<div><input type="hidden" name="secureq_key" value="{c["key"]}">'
                     f'<span>سوال امنیتی: {c["question"]}</span></div>'
                     '<input type="number" name="secureq_ans" id="secureq_ans">') if self.challenge else ""
        return ("<!doctype html><html><body>" + POPUP + '<div class="user_dashboard"><form class="dashboard_form" method="post">'
                f'<input type="hidden" id="login_security_str" name="login_security_str" value="{c["nonce"]}">'
                '<input type="hidden" name="_wp_http_referer" value="/account/login/">'
                '<input type="hidden" name="extra_csrf" value="abc123">'
                '<input type="text" name="username"><input type="password" name="password">'
                + challenge + '<button type="submit" name="loginkon">ورود</button>'
                '<button type="button" id="digiQrLoginBtn">QR</button></form></div></body></html>')

    def _cookie_sid(self, request) -> str | None:
        for part in (request.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return value
        return None

    def _handle_login_post(self, request) -> tuple[str, str]:
        raw = request.body.decode() if isinstance(request.body, bytes) else (request.body or "")
        data = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        self.last_post = data
        self.post_keys.append(data.get("secureq_key"))
        cur = self.current or {}
        problems = []
        if request.headers.get("Referer") != LOGIN_URL:
            problems.append("bad_referer")
        if data.get("_wp_http_referer") != "/account/login/" or data.get("extra_csrf") != "abc123":
            problems.append("hidden_fields_missing")
        if "popup_only_field" in data:
            problems.append("popup_field_leaked")
        if data.get("login_security_str") != cur.get("nonce"):
            problems.append("stale_nonce")
        if self.challenge:
            if data.get("secureq_key") != cur.get("key"):
                problems.append("stale_key")
            elif data.get("secureq_ans") != cur.get("answer"):
                problems.append("wrong_answer")
        if data.get("username") != self.username or data.get("password") != self.password:
            problems.append("bad_credentials")
        if self.always_reject or self.posts <= self.reject_first_posts:
            problems.append("server_rotated")
        if problems:
            self.rejections.append(",".join(problems))
            return self._login_page(), LOGIN_URL  # rejected: a fresh challenge, no cookie
        self._sid += 1
        sid = f"SID-{self._sid}-secretvalue"
        self.sessions.add(sid)
        self.session.cookies.set(COOKIE, sid)
        return account_page(), ACCOUNT_URL

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        self.requests.append({"method": request.method, "url": request.url, "proxies": dict(proxies or {}),
                              "headers": dict(request.headers)})
        url = request.url.split("?")[0]
        status = 200
        if url == LOGIN_URL and request.method == "GET":
            self.login_gets += 1
            body, final = self._login_page(), LOGIN_URL
        elif url == LOGIN_URL and request.method == "POST":
            self.posts += 1
            body, final = self._handle_login_post(request)
        elif url == ACCOUNT_URL:
            self.account_gets += 1
            if self._cookie_sid(request) in self.sessions:
                body, final = account_page(), ACCOUNT_URL
            else:  # logged out: WordPress redirects to the login page
                body, final = self._login_page(), (ACCOUNT_URL if self.account_shows_login_inline else LOGIN_URL)
        elif url == AJAX_URL and request.method == "POST":
            # Mirrors the theme's own header search: only a free-text term is
            # honoured, and the answer comes back as a JSON string of HTML.
            self.ajax_searches += 1
            data = {k: v[0] for k, v in parse_qs(
                request.body.decode() if isinstance(request.body, bytes) else (request.body or ""),
                keep_blank_values=True).items()}
            term = data.get("s", "").strip()
            cards = [f'{self.item_small_loop("https://digimoviez.test/tt-0/", f"Result {n} 2014")}'
                     for n, match in enumerate([term], 1) if match]
            body, final = json.dumps("".join(cards)), AJAX_URL
        else:
            body, final, status = "not found", url, 404
        response = requests.Response()
        response.status_code, response.url, response.request = status, final, request
        response._content, response._content_consumed = body.encode("utf-8"), True
        response.encoding, response.headers = "utf-8", requests.structures.CaseInsensitiveDict({"Content-Type": "text/html"})
        response.elapsed = datetime.timedelta(0)
        return response
