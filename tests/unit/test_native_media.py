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
