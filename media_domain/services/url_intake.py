"""Turning a user-pasted link into something ffmpeg can actually read.

Three steps, each independently useful:

* :func:`check_link_ok` — is this URL even reachable, the way ffmpeg will see it?
* :func:`resolve_media_url_remote` — the link points at a page/folder, not a file.
* :func:`resolve_media_url` — both of the above, then prefer a local copy.
"""
from __future__ import annotations

import logging
import re
import urllib.parse

from archive_logging import event as archive_event, safe_target, timer as ArchiveTimer

from media_domain.adapters.filesystem import local_mirror

DIRECT_MEDIA_EXT = {
    ".mkv", ".mp4", ".m4v", ".webm", ".avi", ".mov",
    ".mp3", ".aac", ".mka", ".ogg", ".flac", ".wav",
}
RESOLVE_VIDEO_EXT = (".mkv", ".mp4", ".m4v", ".webm", ".avi", ".mov")

_UA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}

_LINK_CHECK_HEADERS = {
    "User-Agent": _UA_HEADERS["User-Agent"],
    "Accept": "video/*,*/*;q=0.8",
}

# Mirrors ffmpeg's own behaviour rather than the browser's: a plain GET that
# follows redirects and verifies TLS.
_MAX_HTML_BYTES = 1_000_000


def url_ext(url: str) -> str:
    """Lowercased file extension, or "" when there isn't one.

    Quirk, preserved from app.py and pinned by
    tests/unit/test_media_url_intake.py: this splits on the last dot of the
    whole URL, so a dotted hostname yields a tail like "b/c" for
    "http://a.b/c". Harmless while every caller compares against a known
    extension set, which is exactly why it must not change silently.
    """
    clean = url.split("?", 1)[0].split("#", 1)[0]
    return clean.rsplit(".", 1)[-1].lower() if "." in clean else ""


def is_direct_media_url(url: str) -> bool:
    ext = url_ext(url)
    return "." + ext in DIRECT_MEDIA_EXT if ext else False


def check_link_ok(client, url: str, timeout: int = 15) -> bool:
    """Cheap reachability check for a direct media URL, mirroring what ffmpeg
    will do at encode time: browser User-Agent, follow redirects, TLS
    verification, plain GET. Reads a single byte then drops the connection, so
    even a server that ignores Range/HEAD never transfers the whole file.

    Returns False for HTTP >= 400, TLS/certificate failures, DNS/connection
    errors and timeouts — i.e. any URL that could never be encoded.

    Note: `client` is followed without any host policy. The S2 SSRF fix lands
    in its own commit, with its own tests.
    """
    try:
        started = ArchiveTimer()
        archive_event("media", "probe_start", proxy_enabled=False, direct=True, **safe_target(url))
        response = client.get(url, headers=_LINK_CHECK_HEADERS, timeout=timeout, stream=True)
        ok = 200 <= response.status_code < 400
        next(response.iter_content(1), b"")
        response.close()
        archive_event("media", "probe_result", status=response.status_code, elapsed_ms=started.ms,
                      proxy_enabled=False, direct=True, **safe_target(url))
        return ok
    except Exception as exc:
        archive_event("media", "probe_error", level=logging.WARNING, error_class=type(exc).__name__,
                      error=str(exc)[:200], proxy_enabled=False, direct=True, **safe_target(url))
        return False


def resolve_media_url_remote(client, url: str, timeout: int = 15) -> str:
    """Turns a user-pasted link into a URL ffmpeg can actually read.

    Links pasted into "add video" sometimes point at a folder or a directory
    listing page instead of the media file itself — e.g. the verGoBoy file
    manager serves its /files/data/… folder URLs as a redirect to the UI. Such
    URLs can never be encoded, so before queueing an item we inspect the
    target and, when it turns out to be an HTML/directory page, pick the first
    playable media file's direct URL from inside it. If nothing resolves, the
    original URL is returned so the normal "link is broken" flow still applies.
    """
    if is_direct_media_url(url):
        return url

    html = None
    try:
        with client.get(url, headers=_UA_HEADERS, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if ctype.startswith(("video/", "audio/")) or "matroska" in ctype:
                return url
            if not ctype.startswith(("text/html", "application/xhtml")):
                return url
            html = resp.content.decode("utf-8", errors="ignore")[:_MAX_HTML_BYTES]
    except Exception:
        return url

    # 1) plain HTML directory page → first direct media link
    if html:
        for u in re.findall(r'href="(https?://[^"]+)"', html, re.I):
            if u.lower().split("?")[0].endswith(RESOLVE_VIDEO_EXT):
                return u

    # 2) verGoBoy-style file manager: /files/data/<folder> is a directory that
    #    redirects to the UI, so list the folder via its API and take the video.
    m = re.match(r"^(https?://[^/]+)/files/data/(.*)$", url)
    if m:
        base, folder = m.group(1), urllib.parse.unquote(m.group(2))
        list_url = f"{base}/files/api/list?p=" + urllib.parse.quote(folder, safe="/")
        try:
            with client.get(list_url, headers=_UA_HEADERS, timeout=timeout) as resp:
                resp.raise_for_status()
                data = resp.json()
            for it in (data or {}).get("items", []) or []:
                if it.get("type") != "file":
                    continue
                name = it.get("name") or ""
                if not name.lower().endswith(RESOLVE_VIDEO_EXT):
                    continue
                segs = [urllib.parse.quote(s, safe="")
                        for s in (folder.strip("/") + "/" + name).split("/")]
                return base + "/files/data/" + "/".join(segs)
        except Exception:
            pass

    return url


def resolve_media_url(client, url: str, timeout: int = 15) -> str:
    return local_mirror(resolve_media_url_remote(client, url, timeout=timeout))