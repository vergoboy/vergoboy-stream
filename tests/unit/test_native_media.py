"""native_media: URL signing for nginx `secure_link` (original files, no transcode)."""
from __future__ import annotations

import pytest

import native_media as nm

SECRET = "0123456789abcdef0123456789abcdef"
PATH = "/w2g/native/uploads/it1_src.mkv"


def test_signature_matches_the_nginx_recipe():
    # Computed independently with:
    #   echo -n "4102444800/w2g/native/uploads/it1_src.mkv" "u-1 <secret>" | openssl md5 -binary | base64 | tr +/ -_ | tr -d =
    assert nm.signature(SECRET, 4102444800, PATH, "u-1") == "u6dc5Lb3nGcnuo_BWcVzwQ"


def test_signed_path_shape():
    p = nm.signed_path(SECRET, "/w2g/native/uploads/", "it1_src.mkv", "u-1", 4102444800)
    assert p == f"{PATH}?exp=4102444800&u=u-1&md5=u6dc5Lb3nGcnuo_BWcVzwQ"


@pytest.mark.parametrize("a,b", [
    ((4102444800, PATH, "u-1"), (4102444801, PATH, "u-1")),                       # expiry
    ((4102444800, PATH, "u-1"), (4102444800, PATH.replace("it1", "it2"), "u-1")),  # file
    ((4102444800, PATH, "u-1"), (4102444800, PATH, "u-2")),                       # user
])
def test_signature_binds_expiry_file_and_user(a, b):
    assert nm.signature(SECRET, *a) != nm.signature(SECRET, *b)


@pytest.mark.parametrize("name", ["", "..", ".", "a/b.mkv", "../x", "a b.mkv", "a?x=1.mkv", "a\n.mkv", "ü.mkv"])
def test_unsafe_file_names_are_refused(name):
    with pytest.raises(ValueError):
        nm.signed_path(SECRET, "/w2g/native/uploads", name, "u-1", 1)


@pytest.mark.parametrize("uid", ["", "u 1", "u&x=1", "u/../x", "علی"])
def test_unsafe_user_ids_are_refused(uid):
    with pytest.raises(ValueError):
        nm.signed_path(SECRET, "/w2g/native/uploads", "a.mkv", uid, 1)


def test_short_secret_is_refused():
    with pytest.raises(ValueError):
        nm.signature("short", 1, PATH, "u-1")


@pytest.mark.parametrize("dur,want", [
    (None, 6 * 3600), (0, 6 * 3600), (7200, 6 * 3600),           # normal film: 6 h floor
    (4 * 3600, 8 * 3600), (13 * 3600, 24 * 3600), (10**9, 24 * 3600),  # long: 2x film, capped at 24 h
    ("bad", 6 * 3600),
])
def test_ttl_is_max_6h_or_twice_the_film_capped(dur, want):
    assert nm.ttl_seconds(dur) == want


# ---------------------------------------------------------------- source selection
import os  # noqa: E402

UP = "/srv/media/uploads"
KW = dict(user_id="u-1", upload_dir=UP, base_path="/w2g/native/uploads", secret=SECRET, now=1_000_000)


def pick(item, **over):
    kw = dict(KW, remote_ok=False, isfile=lambda p: True)
    kw.update(over)
    return nm.pick_source(item, **kw)


def test_uploaded_file_still_on_disk_is_served_as_original_with_a_signed_url():
    got = pick({"_raw_path": f"{UP}/it1_src.mkv", "duration": 7200, "src": "/w2g/media/hls/it1/master.m3u8"})
    assert got["kind"] == "original"
    assert got["expires_at"] == 1_000_000 + 6 * 3600
    assert got["url"].startswith("/w2g/native/uploads/it1_src.mkv?exp=")
    assert f"&md5={nm.signature(SECRET, got['expires_at'], '/w2g/native/uploads/it1_src.mkv', 'u-1')}" in got["url"]


def test_long_film_gets_a_longer_token():
    got = pick({"_raw_path": f"{UP}/a.mkv", "duration": 5 * 3600})
    assert got["expires_at"] == 1_000_000 + 10 * 3600


def test_only_files_that_exist_in_the_uploads_dir_are_signed():
    seen = []
    pick({"_raw_path": "/somewhere/else/it1_src.mkv", "src": "/h"}, isfile=lambda p: seen.append(p) or False)
    assert seen == [f"{UP}/it1_src.mkv"]          # looks only inside the uploads dir (by basename)
    assert pick({"_raw_path": "/x/it1_src.mkv", "src": "/h"}, isfile=lambda p: False)["kind"] == "hls"


def test_without_a_secret_there_is_no_original_but_hls_still_works():
    assert pick({"_raw_path": f"{UP}/a.mkv", "src": "/h"}, secret="")["kind"] == "hls"


def test_url_imports_play_directly_only_when_the_owner_opted_in():
    item = {"_source": "https://cdn.example.org/film.mkv", "src": "/w2g/media/hls/x/master.m3u8"}
    assert pick(item)["kind"] == "hls"                                   # default: not exposed
    got = pick(item, remote_ok=True)
    assert got == {"kind": "remote", "url": "https://cdn.example.org/film.mkv", "expires_at": None}


@pytest.mark.parametrize("url", [
    "http://localhost/a.mkv", "http://127.0.0.1/a.mkv", "http://10.0.0.5/a.mkv", "http://192.168.1.2/a.mkv",
    "http://169.254.169.254/latest/meta-data", "http://[::1]/a", "http://0.0.0.0/a", "ftp://example.org/a.mkv",
    "file:///etc/passwd", "/srv/library/film.mkv", "", None, "https:///nohost", "http://foo.localhost/a",
])
def test_internal_or_non_http_sources_are_never_handed_out(url):
    assert nm.is_public_http_url(url) is False
    assert pick({"_source": url, "src": "/h"}, remote_ok=True)["kind"] == "hls"


@pytest.mark.parametrize("url", ["https://example.org/a.mkv", "http://dl.example.ir:8080/x.mp4?t=1", "https://8.8.8.8/a"])
def test_public_urls_are_accepted(url):
    assert nm.is_public_http_url(url) is True


def test_nothing_playable_returns_none():
    assert pick({"title": "x"}) is None
