"""Unit pins for playlist_domain.services.items.

These are the decisions that were lifted out of ``app.py``. The HTTP-level
behaviour they produce is pinned separately in
``tests/api/test_playlist_endpoints.py``, against a real server; this file is
for the edges that are awkward to reach over HTTP.

Deliberately no Flask, no database, no server: the module under test imports
none of them, and ``tests/unit/test_import_boundaries.py`` enforces that.
"""
from __future__ import annotations

from playlist_domain.services.items import (
    build_url_item,
    derive_title,
    is_hls_source,
)

MP4 = "https://example.com/movie.mp4"
M3U8 = "https://example.com/stream/master.m3u8"


def _item(url=M3U8, **kw):
    args = dict(item_id="i1", url=url, title="", added_by="someone",
                room_id="ROOM", added_by_user_id="u1", added_at=1000.0)
    args.update(kw)
    return build_url_item(**args)


# ── is_hls_source ───────────────────────────────────────────────────────────

def test_an_m3u8_needs_no_encoding():
    assert is_hls_source(M3U8) is True


def test_an_extensionless_path_that_says_m3u8_counts():
    """CDNs hand out manifests from paths that do not end in .m3u8."""
    assert is_hls_source("https://cdn.example.com/live/stream.m3u8/index") is True


def test_m3u8_in_the_query_string_does_not_count():
    """A signed URL whose *query* mentions m3u8 is still a plain file, and
    treating it as playable would hand a client something HLS cannot load."""
    assert is_hls_source("https://example.com/clip.mp4?src=master.m3u8") is False


def test_m3u8_matching_ignores_case():
    assert is_hls_source("https://example.com/MASTER.M3U8") is True
    assert is_hls_source("https://example.com/a/Master.M3u8") is True


def test_an_mp4_needs_encoding():
    assert is_hls_source(MP4) is False


def test_a_dotted_hostname_is_not_mistaken_for_an_m3u8():
    assert is_hls_source("http://a.b.c/d") is False


def test_an_empty_url_is_not_hls():
    assert is_hls_source("") is False


# ── derive_title ────────────────────────────────────────────────────────────

def test_title_comes_from_the_last_path_segment():
    assert derive_title(M3U8) == "master.m3u8"


def test_derived_title_drops_the_query_string():
    assert derive_title("https://example.com/a/b/clip.m3u8?t=9&k=x") == "clip.m3u8"


def test_a_bare_host_yields_the_hostname_as_the_title():
    """Not the fallback: splitting "https://example.com" on "/" leaves
    "example.com" behind, and that is the name this item has always had."""
    assert derive_title("https://example.com") == "example.com"


def test_derived_title_of_a_trailing_slash_falls_back():
    """A trailing slash is the case that actually reaches the fallback."""
    assert derive_title("https://example.com/videos/") == "ویدیو"


# ── build_url_item ──────────────────────────────────────────────────────────

def test_a_playable_item_keeps_its_src_and_is_ready_at_once():
    item = _item(M3U8)
    assert item["src"] == M3U8
    assert item["status"] == "ready"


def test_an_item_to_encode_has_no_src_and_is_queued():
    """Storing a src beside a queued status would make the item look playable
    to every client that only checks src."""
    item = _item(MP4)
    assert item["src"] is None
    assert item["status"] == "queued"


def test_the_callers_title_wins_over_the_derived_one():
    assert _item(M3U8, title="my channel")["title"] == "my channel"


def test_the_derived_title_is_used_when_the_caller_gives_none():
    assert _item(M3U8)["title"] == "master.m3u8"


def test_the_item_carries_the_identity_fields_the_room_relies_on():
    item = _item(M3U8)
    assert item["id"] == "i1"
    assert item["type"] == "url"
    assert item["_room_id"] == "ROOM"
    assert item["added_by_user_id"] == "u1"
    assert item["added_at"] == 1000.0
    assert item["added_by"] == "someone"


def test_a_new_item_starts_with_no_subtitles_or_audio_tracks():
    item = _item(M3U8)
    assert item["subtitles"] == []
    assert item["audio_tracks"] == []


def test_each_call_returns_independent_containers():
    """Two items must not share one list, or subtitles added to the first would
    appear on the second."""
    first, second = _item(M3U8), _item(M3U8)
    first["subtitles"].append("en")
    assert second["subtitles"] == []


def test_need_encode_can_be_overridden_by_the_caller():
    """The caller resolves the URL first and may already know the answer, e.g.
    because an archive URL turned out to point at an mp4 behind an m3u8 name."""
    assert _item(M3U8, need_encode=True)["status"] == "queued"
    assert _item(MP4, need_encode=False)["status"] == "ready"


def test_need_encode_false_on_an_mp4_keeps_the_url():
    assert _item(MP4, need_encode=False)["src"] == MP4