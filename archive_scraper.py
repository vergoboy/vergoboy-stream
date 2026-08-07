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
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
TIMEOUT = 20

DS_BASE = "https://donyayeserial.com"
AX_BASE = "https://animex.click"
CDN_BASE = "https://csdl1.hollowofthealley.space"

SOURCES = ("donyayeserial", "animex")

_MEDIA_EXT = (".mkv", ".mp4", ".m4v", ".webm", ".avi", ".mov", ".mp3", ".mka", ".aac")
_VIDEO_EXT = (".mkv", ".mp4", ".m4v", ".webm", ".avi", ".mov")


def _clean(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _fetch(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "fa,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


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

def search_donyayeserial(q):
    """Searches both movies (post) and series archives."""
    results = []
    for post_type in ("post", "series"):
        url = DS_BASE + "/?" + urllib.parse.urlencode({
            "s": q, "search_type": "advanced", "post_type": post_type,
        })
        try:
            page = _fetch(url)
        except Exception:
            continue
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
    page = _fetch(url)
    meta = _ds_title_meta(page, url)
    seg = page
    i = page.find("content-downloads")
    if i >= 0:
        seg = page[i:]
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
    """Parses csdl1.hollowofthealley.space's ?dir= HTML into direct file URLs."""
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
        files.append({
            "name": name,
            "url": urllib.parse.urljoin(CDN_BASE, h),
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
    """Runs the enabled source searches and merges the results."""
    if sources is None:
        sources = list(SOURCES)
    sources = [s for s in sources if s in SOURCES]
    out = []
    if "donyayeserial" in sources:
        out.extend(search_donyayeserial(q))
    if "animex" in sources:
        out.extend(search_animex(q))
    return out


def title(source, url):
    if source == "donyayeserial":
        return parse_donyayeserial_title(url)
    if source == "animex":
        return parse_animex_title(url)
    raise ValueError("منبع ناشناخته")
