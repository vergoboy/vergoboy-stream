"""Network adapter: fetching a remote URL safely, streaming a body to disk.

Every fetch of a URL that a user could have influenced goes through
:func:`safe_fetch_url` — one place that enforces the audit's S2 policy:
http/https only, no credentials in the URL, every DNS answer must be a
global unicast address, redirects are re-validated hop by hop (so a public
link cannot bounce the server into its own metadata endpoint), buffered
reads are size-capped, and the caller always supplies the timeout.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.parse

_BROWSER_UA = "Mozilla/5.0"

_CHUNK_BYTES = 1024 * 1024

#: Default cap for a buffered (non-streaming) read: nobody legitimately needs
#: more than this of an HTML page or an API listing, and without a cap the
#: "how big may this get?" answer would be "whatever the peer feels like".
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

#: Hard ceiling for a streamed download, regardless of what the server claims.
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024 * 1024  # 64 GiB

_REDIRECT_CODES = {301, 302, 303, 307, 308}


class UnsafeUrlError(ValueError):
    """The URL (or a redirect hop toward it) violates the fetch policy."""


def _resolve(host: str) -> list[str]:
    """All IP addresses `host` answers with. Lookup failure raises OSError."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def _assert_public_ip(ip: str) -> None:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise UnsafeUrlError(f"unparseable address {ip!r}") from exc
    if not (addr.is_global and not addr.is_multicast):
        raise UnsafeUrlError(f"non-public address {ip}")


def assert_safe_url(url: str) -> None:
    """Raise :class:`UnsafeUrlError` unless `url` is fetchable under policy.

    Exposed separately so call sites that hand a URL to ffmpeg/ffprobe (which
    :func:`safe_fetch_url` cannot intercept) can apply the same rule.
    """
    if not isinstance(url, str) or not url:
        raise UnsafeUrlError("empty url")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"scheme {parts.scheme!r} not allowed")
    if parts.username is not None:
        raise UnsafeUrlError("credentials in url")
    if not parts.hostname:
        raise UnsafeUrlError("no host")
    try:
        ips = _resolve(parts.hostname)
    except OSError as exc:
        raise UnsafeUrlError(f"cannot resolve {parts.hostname}") from exc
    if not ips:
        raise UnsafeUrlError(f"no address for {parts.hostname}")
    for ip in ips:
        _assert_public_ip(ip)


def safe_fetch_url(client, url: str, *, headers=None, timeout: int = 15,
                   max_bytes: int | None = None, stream: bool = False,
                   max_redirects: int = 5):
    """GET `url` under the S2 policy; return the final response.

    `client` stays injected (the caller owns proxy/TLS policy); redirects are
    followed manually with ``allow_redirects=False`` so every hop passes
    :func:`assert_safe_url` first. Buffered mode (``stream=False``) consumes at
    most `max_bytes` (default :data:`DEFAULT_MAX_BYTES`) and returns a
    response whose ``.content`` is that prefix; streaming mode hands back the
    raw response for the caller to read and close.

    DNS rebinding between the check and the connection is out of scope here
    (documented in the audit): the policy closes the practical SSRF paths —
    literal internal addresses, internal hostnames, and redirect pivots.
    """
    current = url
    for _ in range(max_redirects + 1):
        assert_safe_url(current)
        resp = client.get(current, headers=headers, timeout=timeout,
                          allow_redirects=False, stream=True)
        if resp.status_code in _REDIRECT_CODES and resp.headers.get("Location"):
            location = urllib.parse.urljoin(current, resp.headers["Location"])
            resp.close()
            current = location
            continue
        break
    else:
        resp.close()
        raise UnsafeUrlError(f"more than {max_redirects} redirects")

    if stream:
        return resp

    cap = DEFAULT_MAX_BYTES if max_bytes is None else max_bytes
    body = bytearray()
    for chunk in resp.iter_content(_CHUNK_BYTES):
        body.extend(chunk)
        if len(body) >= cap:
            break
    resp.close()
    return _BufferedResponse(resp, bytes(body[:cap]))


class _BufferedResponse:
    """The policy-capped view of a response: body already consumed."""

    def __init__(self, raw, content: bytes):
        self._raw = raw
        self.status_code = raw.status_code
        self.headers = raw.headers
        self.content = content

    def raise_for_status(self):
        self._raw.raise_for_status()

    def json(self):
        return json.loads(self.content)

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def download_to_file(client, url: str, dest_path: str, timeout: int = 25,
                     max_bytes: int = MAX_DOWNLOAD_BYTES) -> None:
    """GET `url` into `dest_path` under the S2 policy.

    `client` is an injected ``requests.Session``-alike owned by the caller, so
    proxy and TLS policy stay the caller's decision. Raises whatever the
    client's ``raise_for_status`` raises; the caller decides whether that is
    fatal. Aborts once `max_bytes` bytes have been written: a size cap on the
    server side cannot be trusted, so the client enforces one too.
    """
    resp = safe_fetch_url(client, url, headers={"User-Agent": _BROWSER_UA},
                          timeout=timeout, stream=True)
    written = 0
    with resp, open(dest_path, "wb") as out:
        resp.raise_for_status()
        for chunk in resp.iter_content(_CHUNK_BYTES):
            if not chunk:
                continue
            written += len(chunk)
            if written > max_bytes:
                raise UnsafeUrlError(
                    f"download exceeded {max_bytes} bytes")
            out.write(chunk)
