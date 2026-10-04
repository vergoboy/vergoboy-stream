"""Scrapers for the site's search archive.

Pulls title lists and download links from a couple of Persian movie/series
sites that don't offer a public API, plus the CDN directory listing that
animex stores its raw episode files on.

Everything is read-only HTML parsing (no auth, no JS execution); each fetch
is a plain GET with a browser User-Agent. Every network call has a hard
timeout so the archive endpoints can never hang the Flask process.
"""

import base64
import html
import json
import re
import time
import urllib.parse
import urllib.request

import logging
import random
from dataclasses import dataclass
from typing import Any

import requests

from archive_auth import ArchiveAuthManager, AuthSettings
from archive_filters import SearchFilters, build_search_request
from archive_network import ArchiveProxyConfigurationError
from archive_logging import event as archive_event, redact, safe_target, timer as ArchiveTimer

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
TIMEOUT = 12
RETRIES = 1

DS_BASE = "https://donyayeserial.com"
DS_ALT_BASE = "https://donyayeserial-new.top"
# donyayeserial is frequently unreachable from datacenter IPs (TLS drops /
# intermittent HTTP 500s) — retry a couple of times since failures return
# fast, but keep each attempt short so searches don't hang waiting on it.
DS_TIMEOUT = 4
DS_RETRIES = 2
AX_BASE = "https://animex.click"
CDN_BASE = "https://csdl1.hollowofthealley.space"

# The old sources remain implemented below for backwards-compatible parsing,
# but are deliberately not active collectors.  A single movie collector keeps
# archive jobs, requests and writes in scope.
SOURCES = ("digimoviez",)
DIGIMOVIEZ_BASE = "https://digimoviez.com"
LOG = logging.getLogger("archive.collector")

# Error kinds.  These cross the API boundary verbatim so the UI can tell an
# expired session from a challenge it must not attempt to solve, from a dead
# proxy, from Digimoviez rejecting the request.
KIND_AUTH_REQUIRED = "auth_required"
KIND_MANUAL_CHALLENGE = "manual_challenge_required"
KIND_SESSION_EXPIRED = "session_expired"
KIND_AUTH_UNAVAILABLE = "auth_unavailable"
KIND_HTTP_ERROR = "http_error"
KIND_PROXY_UNAVAILABLE = "proxy_unavailable"
KIND_NETWORK_TIMEOUT = "network_timeout"
KIND_NETWORK_ERROR = "network_error"
KIND_PROXY_CONFIG = "proxy_configuration"
KIND_PARSE_ERROR = "parse_error"
KIND_VALIDATION = "validation"

# Verified against the live theme: one result card per ``div.item_def_loop``
# and page links inside ``div.alphapageNavi`` as ``a.page-numbers``.
CARD_CLASS = "item_def_loop"
PAGINATION_CLASS = "alphapageNavi"
MAX_SEARCH_PAGES = 3
_TITLE_PREFIX = re.compile(r"^دانلود\s+(?:فیلم|سریال)\s+")

_MEDIA_EXT = (".mkv", ".mp4", ".m4v", ".webm", ".avi", ".mov", ".mp3", ".mka", ".aac")
_VIDEO_EXT = (".mkv", ".mp4", ".m4v", ".webm", ".avi", ".mov")


def _clean(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _fetch(url, timeout=TIMEOUT, _retries=RETRIES):
    last = None
    for attempt in range(_retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Language": "fa,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:  # HTTPError / URLError / socket timeout / ...
            last = e
            if attempt < _retries:
                time.sleep(0.8)
    raise last


def _is_media_url(u):
    return u.lower().endswith(_MEDIA_EXT) or re.search(
        r"\.(mkv|mp4|m4v|webm|avi|mov)(?:[?#]|$)", u.lower()) is not None


def _media_links(blk):
    """Extract (href, label) pairs for direct video links inside an HTML chunk."""
    out = []
    for u, lbl in re.findall(
        r'<a[^>]+href="(https?://[^"]+\.(?:mkv|mp4|m4v|webm|avi|mov)[^"]*)"[^>]*>(.*?)</a>',
        blk, re.S,
    ):
        if _is_media_url(u):
            out.append((u, _clean(lbl)))
    return out


# ────────────────────────────────────────────────────────────────────────────
# DonyayeSerial
# ────────────────────────────────────────────────────────────────────────────

def _ds_search_one(q, post_type):
    """Searches a single donyayeserial archive (movies=post, series=series).

    The site serves on two hostnames (donyayeserial.com redirects to the
    newer -new.top) and is frequently flaky — one host 500s while the other
    is fine — so try both in turn instead of giving up after the first."""
    for base in (DS_BASE, DS_ALT_BASE):
        url = base + "/?" + urllib.parse.urlencode({
            "s": q, "search_type": "advanced", "post_type": post_type,
        })
        try:
            page = _fetch(url, timeout=DS_TIMEOUT, _retries=DS_RETRIES)
            break
        except Exception:
            page = None
    if not page:
        return []
    results = []
    for m in re.finditer(r'<article class="[^"]*postItems', page):
        block = page[m.end():]
        t = re.search(r'<h2>\s*<a href="([^"]+)"[^>]*title="([^"]*)"', block)
        if not t:
            continue
        href, title = t.group(1), _clean(t.group(2))
        if not title or not href:
            continue
        img = re.search(r'<img[^>]+src="([^"]+)"', block)
        rat = re.search(
            r'<div class="imdb-rating[^>]*>.*?<span class="text-warning">([\d.]+)</span>',
            block, re.S)
        kind = "series" if "/series/" in href else "movie"
        results.append({
            "source": "donyayeserial",
            "kind": kind,
            "title": title,
            "url": href,
            "poster": img.group(1) if img else None,
            "rating": rat.group(1) if rat else None,
            "year": None,
        })
    return results


def search_donyayeserial(q):
    """Searches both movies (post) and series archives in parallel."""
    try:
        from gevent.pool import Group
        pages = Group().map(lambda pt: _ds_search_one(q, pt), ("post", "series"))
    except Exception:
        pages = [_ds_search_one(q, pt) for pt in ("post", "series")]
    out = []
    for r in pages:
        out.extend(r)
    return out


def _ds_swap_domain(url):
    """Returns the same donyayeserial page on the other hostname."""
    if url.startswith(DS_BASE):
        return DS_ALT_BASE + url[len(DS_BASE):]
    if url.startswith(DS_ALT_BASE):
        return DS_BASE + url[len(DS_ALT_BASE):]
    return None


def _ds_title_meta(page, url):
    title = None
    m = re.search(r'<meta property="og:title" content="([^"]+)"', page)
    if m:
        title = _clean(m.group(1))
    if not title:
        m = re.search(r"<title>([^<]*)</title>", page)
        if m:
            title = _clean(m.group(1)).split(" - دنیای")[0].split(" – دنیای")[0]
    poster = None
    m = re.search(r'<meta property="og:image" content="([^"]+)"', page)
    if m:
        poster = m.group(1)
    kind = "series" if "/series/" in url else "movie"
    return {"source": "donyayeserial", "title": title or url, "poster": poster, "kind": kind}


def parse_donyayeserial_title(url):
    page = None
    for u in (url, _ds_swap_domain(url)):
        if not u or u == url and page is not None:
            continue
        try:
            page = _fetch(u, timeout=DS_TIMEOUT, _retries=DS_RETRIES)
            break
        except Exception:
            page = None
    if not page:
        raise TimeoutError("site timed out")
    meta = _ds_title_meta(page, url)
    seg = page
    i = page.find("content-downloads")
    if i >= 0:        seg = page[i:]
    groups = []

    # Series layout: one `div.item` per season × quality block, each with a
    # summary line (فصل / کیفیت / میانگین حجم / ورژن / قسمت ها) followed by
    # per-episode links and a hidden textarea with every URL.
    for blk in re.split(r'<div style="text-align: center;" class="item">', seg)[1:]:
        eps = [{"num": _ep_num(lbl), "url": u} for u, lbl in _media_links(blk)]
        eps = [e for e in eps if e["url"]]
        if not eps:
            continue
        season = quality = version = size = None
        pm = re.search(r'<p style="text-align: center;color: #7400b8;">(.*?)</p>', blk, re.S)
        if pm:
            txt = _clean(pm.group(1))
            sm = re.search(
                r"فصل:\s*([^/]+?)\s*/\s*کیفیت:\s*([^/]+?)\s*/\s*میانگین حجم:\s*([^/]+?)\s*/\s*ورژن:\s*([^/]+?)\s*/\s*قسمت ها:\s*([\d]+)",
                txt)
            if sm:
                season, quality, size, version, _ = [x.strip() for x in sm.groups()]
        label = f"فصل {season} — {quality}" if season else (quality or f"{len(eps)} قسمت")
        groups.append({
            "label": label,
            "season": season,
            "quality": quality,
            "version": version,
            "size": size,
            "episodes": eps,
        })

    # Movie layout: no season blocks — every direct link inside the download
    # box is one quality variant of the same movie.
    if not groups:
        box = seg
        m = re.search(r'<div class="alert[^"]*dl-box-alert[^"]*"[^>]*>(.*?)(?=<div class="alert[^"]*dl-box-alert|$)', seg, re.S)
        if m:
            box = m.group(1)
        for u, lbl in _media_links(box):
            if not lbl:
                lbl = u.rsplit("/", 1)[-1]
            groups.append({
                "label": lbl,
                "season": None,
                "quality": lbl,
                "version": None,
                "size": None,
                "episodes": [{"num": 1, "url": u}],
            })

    meta["groups"] = groups
    return meta


# ────────────────────────────────────────────────────────────────────────────
# Animex
# ────────────────────────────────────────────────────────────────────────────

def search_animex(q):
    params = {
        "s": q,
        "asp_active": "1",
        "customset[]": "movie",
        "customset[]": "serial",
        "customset[]": "anime",
        "customset[]": "korean",
        "customset[]": "turkey",
    }
    url = AX_BASE + "/?" + urllib.parse.urlencode(params)
    try:
        page = _fetch(url)
    except Exception:
        return []
    results = []
    for m in re.finditer(r'<article id="post-(\d+)" class="([^"]*)"', page):
        cls = m.group(2)
        block = page[m.end():]
        a = re.search(r'<a href="([^"]+)"[^>]*class="relative', block)
        if not a:
            continue
        img = re.search(r'<img[^>]+src="([^"]+)"[^>]*alt="([^"]*)"', block)
        rat = re.search(r'<div class="sjmrate">.*?([\d.]+)\s*</div>', block, re.S)
        km = re.search(r"type-(\w+)", cls)
        ym = re.search(r"releasea-(\d{4})", cls)
        results.append({
            "source": "animex",
            "kind": km.group(1) if km else "anime",
            "title": _clean(img.group(2)) if img and img.group(2) else "بدون عنوان",
            "url": a.group(1),
            "poster": img.group(1) if img else None,
            "rating": rat.group(1) if rat else None,
            "year": ym.group(1) if ym else None,
        })
    return results


def _decode_animex_go(token):
    """animex signs its download/stream links as base64url(json).signature —
    we only read the JSON half (target/action), we never forge the link."""
    try:
        b64 = token.split(".", 1)[0]
        b64 += "=" * (-len(b64) % 4)
        return json.loads(base64.urlsafe_b64decode(b64))
    except Exception:
        return None


def parse_animex_title(url):
    page = _fetch(url)
    title = None
    m = re.search(r'<meta property="og:title" content="([^"]+)"', page)
    if m:
        title = _clean(m.group(1))
    if not title:
        m = re.search(r"<title>([^<]*)</title>", page)
        if m:
            title = _clean(m.group(1)).split(" – انیمکس")[0].split(" - انیمکس")[0]
    poster = None
    m = re.search(r'<meta property="og:image" content="([^"]+)"', page)
    if m:
        poster = m.group(1)
    if not poster:
        # animex title pages carry no og:image — fall back to the post's
        # featured thumbnail so the banner shows in the detail view.
        imgm = re.search(
            r'<img[^>]*class="[^"]*attachment-post-thumbnail[^"]*"[^>]*>',
            page)
        if imgm:
            sm = re.search(r'src="([^"]+)"', imgm.group(0))
            if sm:
                poster = sm.group(1)
    km = re.search(r'<article[^>]+class="[^"]*type-(\w+)', page)
    kind = km.group(1).lower() if km else "anime"

    groups = []
    i = page.find('id="dlbox"')
    if i >= 0:
        seg = page[i:]
        token_re = re.compile(
            r'<li class="flex items-center justify-between py-2 mb-2 rounded dliteminfo">(.*?)</li>'
            r'|<li class="flex items-center justify-between px-4 py-2 mb-2 rounded dlitems">(.*?)</li>',
            re.S,
        )
        for m in token_re.finditer(seg):
            if m.group(1) is not None:
                gt = re.search(r'rightinfodl">([^<]+)</span>', m.group(1))
                gv = re.search(r'leftinfodl">([^<]*)</span>', m.group(1))
                groups.append({
                    "label": _clean(gt.group(1)) if gt else "دانلود",
                    "version": _clean(gv.group(1)) if gv else "",
                    "items": [],
                })
            else:
                row = m.group(2)
                ql = re.search(r'<span class="font-bold">([^<]+)</span>', row)
                quality = _clean(ql.group(1)) if ql else ""
                for hm in re.finditer(r'href="https://animex\.click/\?animex_go=([^"]+)"', row):
                    payload = _decode_animex_go(hm.group(1))
                    if payload and payload.get("action") == "download" and payload.get("target"):
                        target = payload["target"]
                        # keep only the CDN directory listings (skip non-csdl hosts)
                        if "hollowofthealley" not in target:
                            continue
                        item = {"quality": quality, "dir_url": target}
                        if groups:
                            groups[-1]["items"].append(item)
                        else:
                            groups.append({"label": "دانلود", "version": "", "items": [item]})

    # each group carries no episodes here — the per-quality CDN dirs have to be
    # listed via /archive/files to know the episode file URLs.
    return {
        "source": "animex",
        "title": title or url,
        "poster": poster,
        "kind": kind,
        "groups": [{"label": g["label"], "version": g["version"], "items": g["items"]} for g in groups],
    }


# ────────────────────────────────────────────────────────────────────────────
# CDN directory listing (animex raw files)
# ────────────────────────────────────────────────────────────────────────────

def list_dir(url):
    """Parses csdl1.hollowofthealley.space's ?dir= HTML into direct file URLs.

    Some titles (specials, movies) link straight to the media file instead of
    a directory — those are returned as a single-episode listing."""
    if _is_media_url(url):
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1].split("?")[0])
        return [{"name": name, "url": url}]
    page = _fetch(url)
    files = []
    for h, t in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
        name = _clean(t)
        h = html.unescape(h).strip()
        if not h or not name:
            continue
        if h.startswith("?dir=") or h.startswith("http") or h.startswith("tg://"):
            continue
        if not h.lower().endswith(_VIDEO_EXT):
            continue
        # Resolve against the host that actually served the listing — the
        # subdomains (ndl5/csdl1/...) are per-file shards and csdl1 404s on
        # files that ndl5 serves.
        files.append({
            "name": name,
            "url": urllib.parse.urljoin(url, h),
        })
    # natural sort by embedded episode number so قسمت 2 doesn't land after 10
    files.sort(key=lambda f: _sort_key(f["name"]))
    return files


def _sort_key(name):
    nums = re.findall(r"(\d+)", name)
    return int(nums[0]) if nums else 0


def _ep_num(label):
    m = re.search(r"قسمت\s*(\d+)", label)
    return int(m.group(1)) if m else None


def search(q, sources=None):
    """Compatibility entry point for the movie-only collector."""
    return get_collector().search(SearchFilters.from_mapping({"query": q}))


def title(source, url):
    if source == "digimoviez":
        return get_collector().title(url)
    raise ValueError("منبع ناشناخته")


class ArchiveRequestError(RuntimeError):
    """A classified, non-secret error suitable for the API layer."""
    def __init__(self, kind: str, message: str = "archive request failed") -> None:
        super().__init__(message)
        self.kind = kind


def _absolute(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, html.unescape(href))


def _iter_blocks(page: str, marker: str):
    """Yield the markup of each element that opens with ``marker``.

    Slicing between consecutive occurrences of the marker avoids trying to
    balance ``</div>`` tags by hand, which real WordPress markup frequently
    makes impossible.
    """
    starts = [match.start() for match in re.finditer(re.escape(marker), page)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(page)
        yield page[start:end]


def _field(pattern: str, block: str, group: int = 1) -> str | None:
    match = re.search(pattern, block, re.I | re.S)
    return _clean(match.group(group)) if match else None


def _parse_search_card(block: str, base_url: str) -> dict[str, Any] | None:
    """Read one real ``item_def_loop`` result card."""
    anchor = re.search(
        r'<div[^>]+class="[^"]*title_h[^"]*"[^>]*>\s*<h2[^>]*>\s*'
        r'<a[^>]+title="([^"]*)"[^>]+href="([^"]+)"',
        block, re.I | re.S)
    if not anchor:
        anchor = re.search(
            r'<div[^>]+class="[^"]*inner_cover[^"]*"[^>]*>\s*'
            r'<a[^>]+title="([^"]*)"[^>]+href="([^"]+)"',
            block, re.I | re.S)
    if not anchor:
        return None
    title = _TITLE_PREFIX.sub("", _clean(anchor.group(1)))
    url = _absolute(base_url, anchor.group(2))
    if not title or not url.startswith(base_url + "/"):
        return None
    poster = _field(r'<div[^>]+class="[^"]*inner_cover[^"]*"[^>]*>.*?<img[^>]+src="([^"]+)"', block)
    return {
        "source": "digimoviez", "kind": "movie", "title": title, "url": url,
        "poster": _absolute(base_url, poster) if poster else None,
        "rating": _field(r'<div[^>]+class="[^"]*rate_num[^"]*"[^>]*>\s*<strong>([\d.]+)</strong>', block),
        "year": _field(r'\b((?:19|20)\d{2})\b', title),
    }


def _next_page_url(page: str, base_url: str) -> str | None:
    """Return the numbered ``/page/N/`` successor, or None at the last page.

    ``rel="next"`` is absent on this theme, so the successor is resolved from
    the ``alphapageNavi`` block.  The current page is marked
    ``aria-current="page"``; only that page's successor is followed, which also
    avoids the theme's links to the far end of the archive.
    """
    for block in _iter_blocks(page, f'class="{PAGINATION_CLASS}"'):
        current = re.search(r'<span[^>]*aria-current="page"[^>]*>(\d+)</span>', block, re.I)
        if not current:
            continue
        wanted = str(int(current.group(1)) + 1)
        link = re.search(rf'<a[^>]+class="[^"]*page-numbers[^"]*"[^>]+href="([^"]+)"[^>]*>\s*{wanted}\s*</a>',
                         block, re.I)
        if link:
            candidate = _absolute(base_url, html.unescape(link.group(1))).split("#", 1)[0]
            return candidate if candidate.startswith(base_url) else None
    return None


def _auth_failure(auth: ArchiveAuthManager) -> tuple[str, str]:
    """Map an unusable authentication state onto a distinct, actionable kind.

    Keeping these apart is what lets the UI show the manual authentication
    panel only for a real challenge instead of for every failed login.
    """
    from archive_auth import AuthState

    state = auth.state
    if state is AuthState.AUTH_MANUAL_INTERVENTION_REQUIRED:
        return KIND_MANUAL_CHALLENGE, "Digimoviez requires a manual security challenge"
    if state is AuthState.AUTH_EXPIRED:
        return KIND_SESSION_EXPIRED, "the Digimoviez session has expired"
    return KIND_AUTH_UNAVAILABLE, "archive authentication is unavailable"


class DigimoviezMovieCollector:
    """The only enabled collector; it performs no background crawl jobs."""
    name = "digimoviez"

    def __init__(self, base_url: str, auth: ArchiveAuthManager, *, connect_timeout: float,
                 read_timeout: float, total_timeout: float, retries: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.connect_timeout, self.read_timeout = connect_timeout, read_timeout
        self.total_timeout, self.retries = total_timeout, retries
        self.session = auth.session

    def _request(self, request: dict[str, Any]) -> str:
        deadline = time.monotonic() + self.total_timeout
        for attempt in range(self.retries + 1):
            started = ArchiveTimer()
            error_class = ""
            error_message = ""
            archive_event("proxy", "request_start", method=request["method"], attempt=attempt + 1,
                          proxy=self.auth.settings.http_proxy, **safe_target(request["url"]))
            if self.auth.settings.enabled and not self.auth.ensure_authenticated():
                raise ArchiveRequestError(*_auth_failure(self.auth))
            try:
                response = self.session.request(request["method"], request["url"],
                    timeout=(self.connect_timeout, self.read_timeout), headers={
                        "User-Agent": UA, "Accept-Language": "fa,en;q=0.8",
                    })
                if response.status_code >= 500:
                    raise requests.HTTPError(response.status_code)
                if response.status_code >= 400:
                    raise ArchiveRequestError(KIND_HTTP_ERROR, f"archive returned HTTP {response.status_code}")
                archive_event("proxy", "request_complete", status=response.status_code, elapsed_ms=started.ms,
                              content_type=response.headers.get("Content-Type", ""), response_bytes=len(response.content),
                              redirects=[safe_target(h.url) for h in response.history], **safe_target(response.url))
                return response.text
            except ArchiveRequestError:
                raise
            except requests.exceptions.ProxyError:
                kind = KIND_PROXY_UNAVAILABLE
                error_class, error_message = "ProxyError", "configured archive proxy is unavailable"
            except requests.Timeout:
                kind = KIND_NETWORK_TIMEOUT
                error_class, error_message = "Timeout", "archive request timed out"
            except requests.RequestException as exc:
                kind = KIND_NETWORK_ERROR
                # Transport messages can embed the proxy URL, so they are
                # redacted before they reach a log line.
                error_class, error_message = type(exc).__name__, redact(str(exc))[:200]
                LOG.info("REQUEST_FAILED kind=%s error=%s", kind, type(exc).__name)
            archive_event("proxy", "request_error", level=logging.WARNING, classification=kind,
                          error_class=error_class, error=error_message, elapsed_ms=started.ms,
                          retry=attempt + 1, **safe_target(request["url"]))
            if attempt >= self.retries or time.monotonic() >= deadline:
                raise ArchiveRequestError(kind)
            delay = min(8, 2 ** attempt) + random.uniform(0, 0.25)
            LOG.info("REQUEST_RETRY kind=%s attempt=%s", kind, attempt + 1)
            time.sleep(min(delay, max(0, deadline - time.monotonic())))
        raise ArchiveRequestError(KIND_NETWORK_ERROR)

    def search(self, filters: SearchFilters, max_pages: int = MAX_SEARCH_PAGES) -> list[dict[str, Any]]:
        request = build_search_request(filters, self.base_url)
        archive_event("filters", "search_request", filters=request["params"], **safe_target(request["url"]))
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        pages = 0
        url = request["url"]
        while url and pages < max(1, max_pages):
            page = self._request({"method": "GET", "url": url})
            pages += 1
            for block in _iter_blocks(page, CARD_CLASS):
                card = _parse_search_card(block, self.base_url)
                if card and card["url"] not in seen:
                    seen.add(card["url"])
                    results.append(card)
            url = _next_page_url(page, self.base_url)
        archive_event("collector", "search_parsed", result_count=len(results), pages_fetched=pages,
                      titles=[result["title"][:80] for result in results[:3]],
                      pagination="bounded" if pages > 1 else "single_page")
        return results

    def title(self, url: str) -> dict[str, Any]:
        if not url.startswith(self.base_url + "/"):
            raise ValueError("invalid archive URL")
        archive_event("collector", "detail_start", **safe_target(url))
        page = self._request({"method": "GET", "url": url})
        title = _field(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', page) or url
        poster = _field(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page)
        groups, gated = self._parse_download_groups(page, url)
        if gated:
            # Digimoviez replaces the download rows with a "sign in" notice for
            # guests.  Reporting that as "no episodes" would silently look like
            # a broken parser, so it is surfaced as its own condition.
            archive_event("collector", "detail_gated", classification=KIND_AUTH_REQUIRED, **safe_target(url))
            raise ArchiveRequestError(KIND_AUTH_REQUIRED, "Digimoviez requires an authenticated session for download links")
        if not groups:
            archive_event("collector", "detail_parsed", parsed="og:title" in page, group_count=0,
                          media_url_count=0, level=logging.WARNING,
                          note="no download rows matched; the theme layout may have changed")
        media_count = sum(len(group["episodes"]) for group in groups)
        archive_event("collector", "detail_parsed", parsed=bool(poster is not None), group_count=len(groups),
                      media_url_count=media_count)
        return {"source": self.name, "title": title, "poster": poster, "kind": "movie", "groups": groups}

    def _parse_download_groups(self, page: str, page_url: str) -> tuple[list[dict[str, Any]], bool]:
        """Read the real ``dllink_holder_ham`` quality groups.

        Returns the groups plus a flag that is set when the page shows the
        guest "sign in to download" notice instead of download rows.
        """
        groups: list[dict[str, Any]] = []
        gated = False
        for block in _iter_blocks(page, 'class="dllink_holder_ham'):
            body_match = re.search(r'<div[^>]+class="[^"]*body_dllink_movies[^"]*"[^>]*>(.*)', block, re.I | re.S)
            if not body_match:
                continue
            body = body_match.group(1)
            if re.search(r'class="[^"]*guest_line_comments[^"]*"|data-popup="login_box"', body, re.I):
                gated = True
                continue
            label = _field(r'<div[^>]+class="[^"]*title_dllink[^"]*"[^>]*>.*?'
                           r'<div[^>]+class="[^"]*right_title[^"]*"[^>]*>(.*?)</div>', block) or "دانلود"
            episodes: list[dict[str, Any]] = []
            for index, anchor in enumerate(
                    re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.I | re.S), 1):
                href = _absolute(page_url, anchor.group(1))
                text = _clean(anchor.group(2))
                episodes.append({"num": _ep_num(text) or index, "url": href, "label": text})
            for direct, text in _media_links(body):
                episodes.append({"num": _ep_num(text) or len(episodes) + 1, "url": direct, "label": text})
            if episodes:
                groups.append({"label": label, "season": None, "quality": label, "version": None,
                               "size": None, "episodes": episodes})
        return groups, gated


_collector: DigimoviezMovieCollector | None = None


def enabled_collectors(config: Any) -> tuple[str, ...]:
    """Explicit registry: disabled categories never instantiate a worker."""
    return ("digimoviez",) if config.ARCHIVE_MOVIES_ENABLED else ()


def get_collector() -> DigimoviezMovieCollector:
    global _collector
    if _collector is None:
        from config import Config
        if not enabled_collectors(Config):
            raise RuntimeError("COLLECTOR_DISABLED name=digimoviez")
        settings = AuthSettings(Config.ARCHIVE_AUTH_ENABLED, Config.ARCHIVE_LOGIN_URL,
            Config.ARCHIVE_AUTH_CHECK_URL, Config.ARCHIVE_USERNAME, Config.ARCHIVE_PASSWORD,
            __import__("pathlib").Path(Config.ARCHIVE_SESSION_FILE),
            Config.ARCHIVE_HTTP_PROXY, Config.ARCHIVE_LOGIN_USERNAME_FIELD, Config.ARCHIVE_LOGIN_PASSWORD_FIELD,
            Config.ARCHIVE_AUTH_CHECK_INTERVAL, Config.ARCHIVE_REQUEST_TIMEOUT, Config.ARCHIVE_LOGIN_RETRY_COUNT)
        try:
            auth = ArchiveAuthManager(settings)
        except ArchiveProxyConfigurationError as exc:
            raise ArchiveRequestError("proxy_configuration", str(exc)) from exc
        _collector = DigimoviezMovieCollector(Config.ARCHIVE_BASE_URL, auth,
            connect_timeout=Config.ARCHIVE_CONNECT_TIMEOUT, read_timeout=Config.ARCHIVE_READ_TIMEOUT,
            total_timeout=Config.ARCHIVE_TOTAL_TIMEOUT, retries=Config.ARCHIVE_REQUEST_RETRY_COUNT)
    return _collector
