"""S2 SSRF: every fetch of a user-supplied URL goes through safe_fetch_url.

These are the failing-before/passing-after tests the audit asked for. Before
the fix, `check_link_ok` forwarded any URL straight to the session ("The S2
SSRF fix lands in its own commit" said its docstring) — so a pasted
http://127.0.0.1/… or http://169.254.169.254/… was fetched by the server, and
a redirect could turn a public-looking link into an internal one. The tests
below pin the policy: http/https only, no credentials in the URL, every DNS
answer must be a global unicast address, redirects are re-validated hop by
hop, buffered reads are capped.

The resolver is monkeypatched rather than mocked at the socket layer: the
policy code is what is under test, not getaddrinfo.
"""
from __future__ import annotations

import pytest
import requests

from media_domain.adapters.http import (
    UnsafeUrlError,
    safe_fetch_url,
)
from media_domain.services import url_intake

PUBLIC = "93.184.216.34"  # example.com; global unicast, never actually contacted


class FakeResponse:
    def __init__(self, *, status_code=200, headers=None, content=b"",
                 chunks=None, json_data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self._chunks = chunks if chunks is not None else [content]
        self._json = json_data
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def iter_content(self, size=1):
        yield from self._chunks

    def json(self):
        return self._json

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeClient:
    def __init__(self, routes=None, exc=None):
        self.routes = routes or {}
        self.exc = exc
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc is not None:
            raise self.exc
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                return response
        raise AssertionError(f"unexpected GET {url!r}")


@pytest.fixture
def resolve(monkeypatch):
    """Replace DNS: tests say which IP each hostname answers with."""
    mapping: dict[str, list[str]] = {}

    def _resolve(host):
        if host in mapping:
            return mapping[host]
        import socket as _socket
        raise _socket.gaierror(f"no test mapping for {host!r}")

    monkeypatch.setattr("media_domain.adapters.http._resolve", _resolve)
    return mapping


# ── scheme / credential / DNS policy ─────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com/x",
    "httpx://example.com/x",
    "",
    "not a url",
])
def test_refuses_non_http_schemes(url, resolve):
    client = FakeClient()
    resolve["example.com"] = [PUBLIC]
    with pytest.raises(UnsafeUrlError):
        safe_fetch_url(client, url)
    assert client.calls == []


def test_refuses_credentials_in_the_url(resolve):
    client = FakeClient()
    resolve["example.com"] = [PUBLIC]
    with pytest.raises(UnsafeUrlError):
        safe_fetch_url(client, "http://user:pass@example.com/x")
    assert client.calls == []


@pytest.mark.parametrize("ip", [
    "127.0.0.1",      # loopback
    "10.1.2.3",       # RFC1918
    "172.16.9.9",     # RFC1918
    "192.168.0.5",    # RFC1918
    "169.254.169.254",  # link-local / cloud metadata
    "0.0.0.0",        # unspecified
    "::1",            # IPv6 loopback
    "fe80::1",        # IPv6 link-local
    "::",             # IPv6 unspecified
    "::ffff:127.0.0.1",  # IPv4-mapped loopback
])
def test_refuses_non_public_ips(ip, resolve):
    client = FakeClient()
    resolve["internal.test"] = [ip]
    with pytest.raises(UnsafeUrlError):
        safe_fetch_url(client, "http://internal.test/x")
    assert client.calls == []


def test_refuses_host_with_no_usable_dns_answer(resolve):
    client = FakeClient()
    with pytest.raises(UnsafeUrlError):
        safe_fetch_url(client, "http://ghost.test/x")
    assert client.calls == []


def test_refuses_a_host_that_resolves_to_nothing(resolve):
    client = FakeClient()
    resolve["empty.test"] = []
    with pytest.raises(UnsafeUrlError):
        safe_fetch_url(client, "http://empty.test/x")
    assert client.calls == []


# ── redirects are re-validated hop by hop ────────────────────────────────────

def test_follows_redirect_to_another_public_host(resolve):
    resolve["example.com"] = [PUBLIC]
    resolve["cdn.example.net"] = [PUBLIC]
    client = FakeClient({
        "http://example.com/a": FakeResponse(
            status_code=302, headers={"Location": "http://cdn.example.net/b"}),
        "http://cdn.example.net/b": FakeResponse(content=b"body"),
    })
    resp = safe_fetch_url(client, "http://example.com/a")
    assert resp.content == b"body"
    assert len(client.calls) == 2


def test_refuses_redirect_to_a_private_host(resolve):
    resolve["example.com"] = [PUBLIC]
    resolve["internal.test"] = ["10.0.0.1"]
    client = FakeClient({
        "http://example.com/a": FakeResponse(
            status_code=302, headers={"Location": "http://internal.test/b"}),
    })
    with pytest.raises(UnsafeUrlError):
        safe_fetch_url(client, "http://example.com/a")
    assert len(client.calls) == 1  # the internal hop was never fetched


def test_refuses_redirect_to_a_non_http_scheme(resolve):
    resolve["example.com"] = [PUBLIC]
    client = FakeClient({
        "http://example.com/a": FakeResponse(
            status_code=302, headers={"Location": "file:///etc/passwd"}),
    })
    with pytest.raises(UnsafeUrlError):
        safe_fetch_url(client, "http://example.com/a")
    assert len(client.calls) == 1


def test_refuses_endless_redirect_loops(resolve):
    resolve["example.com"] = [PUBLIC]
    client = FakeClient({
        "http://example.com/a": FakeResponse(
            status_code=302, headers={"Location": "/a"}),
    })
    with pytest.raises(UnsafeUrlError):
        safe_fetch_url(client, "http://example.com/a", max_redirects=3)


# ── size caps ────────────────────────────────────────────────────────────────

def test_buffered_read_is_capped_at_max_bytes(resolve):
    resolve["example.com"] = [PUBLIC]
    body = b"0123456789"
    client = FakeClient({"http://example.com/x": FakeResponse(
        content=body, chunks=[body[:4], body[4:]])})
    resp = safe_fetch_url(client, "http://example.com/x", max_bytes=4)
    assert resp.content == b"0123"
    assert resp.status_code == 200


def test_stream_mode_returns_the_response_unread(resolve):
    resolve["example.com"] = [PUBLIC]
    resp = FakeResponse(chunks=[b"a", b"b"])
    client = FakeClient({"http://example.com/x": resp})
    out = safe_fetch_url(client, "http://example.com/x", stream=True)
    assert out is resp
    assert b"".join(out.iter_content(1)) == b"ab"


# ── the rewired intake functions inherit the policy ──────────────────────────

def test_check_link_ok_refuses_private_ip_without_touching_the_network(resolve):
    """Failing-before: the old code fetched it and reported True."""
    resolve["internal.test"] = ["127.0.0.1"]
    client = FakeClient({"http://internal.test/x": FakeResponse(status_code=200)})
    assert url_intake.check_link_ok(client, "http://internal.test/x") is False
    assert client.calls == []


def test_check_link_ok_still_accepts_a_public_reachable_url(resolve):
    resolve["example.com"] = [PUBLIC]
    client = FakeClient({"http://example.com/f": FakeResponse(status_code=200)})
    assert url_intake.check_link_ok(client, "http://example.com/f") is True


def test_resolve_refuses_to_describe_a_private_page(resolve):
    resolve["internal.test"] = ["192.168.1.5"]
    client = FakeClient({"http://internal.test/page": FakeResponse(
        status_code=200, headers={"Content-Type": "text/html"},
        content=b'<a href="http://internal.test/secret.mkv">x</a>')})
    # Failing-before: returned a resolved media URL inside the private network.
    assert url_intake.resolve_media_url_remote(client, "http://internal.test/page") == \
        "http://internal.test/page"
    assert client.calls == []
