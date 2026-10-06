"""Constructing playlist items from a source URL.

The decisions ``add-url`` makes before anything touches the disk:

* does this URL need transcoding at all, or is it already playable;
* what should the item be called when the caller supplied no title;
* what a freshly added row looks like, including the ``src``/``status`` pair that
  follows from the answer to the first question.

All of it is pure. The caller keeps everything with a side effect: minting the
id, writing the room, bumping the quota and starting the encoder.
"""
from __future__ import annotations

from media_domain.services.url_intake import url_ext


def is_hls_source(url: str) -> bool:
    """True when ``url`` is already an HLS playlist and needs no encoder.

    Either the extension says so, or ``m3u8`` appears in the path. The extension
    test alone would miss a CDN URL that serves a manifest from a query string or
    from an extensionless path, which is exactly how the archive hands some of
    these over.

    The ``split("?")[0]`` is deliberate and not equivalent to ``url_ext``: a
    signed URL whose *query* contains "m3u8" is still an MP4.
    """
    return url_ext(url) == "m3u8" or "m3u8" in url.split("?")[0].lower()


def derive_title(url: str) -> str:
    """Last path segment of ``url``, without any query string.

    Falls back to ``"ویدیو"`` when there is nothing usable, which is the same
    fallback the route has always used, so a URL ending in ``/`` still lands in
    the playlist under a name.
    """
    return url.rsplit("/", 1)[-1].split("?")[0] or "ویدیو"


def build_url_item(*, item_id: str, url: str, title: str, added_by: str,
                   room_id: str, added_by_user_id: str | None,
                   added_at: float, need_encode: bool | None = None) -> dict:
    """A new playlist item for ``url``.

    ``need_encode`` exists so the caller can override the HLS probe once it has
    already resolved the URL and decided what to do with it; by default the
    decision is made here from :func:`is_hls_source`.

    The ``src``/``status`` pairing is the contract the rest of the system reads.
    An item that will be encoded has no ``src`` yet and is ``queued``; one that
    is already playable keeps its own URL and is ``ready`` immediately. Nothing
    ever stores a ``src`` alongside a ``queued`` status, because a queued item
    would then look playable to every client that checks only ``src``.
    """
    if need_encode is None:
        need_encode = not is_hls_source(url)
    return {
        "id": item_id,
        "type": "url",
        "title": title or derive_title(url),
        "src": None if need_encode else url,
        "subtitles": [],
        "audio_tracks": [],
        "added_by": added_by,
        "added_at": added_at,
        "status": "queued" if need_encode else "ready",
        "_room_id": room_id,
        "added_by_user_id": added_by_user_id,
    }