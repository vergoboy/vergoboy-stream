"""Characterization tests for the media URL-intake domain.

Originally written against app.py *before* the domain moved into `media_domain/`
(commit 931d924), then retargeted at the new home with every assertion left
byte-identical. That is the whole point: the suite going green afterwards is
the evidence that the move was behaviour-preserving rather than merely
plausible.

Quirks are pinned deliberately, including url_ext("http://a.b/c") == "b/c":
it splits on the last dot of the whole URL, so a dotted hostname yields
garbage. Harmless today because every call site compares against a known
extension set, but it must not change silently during the move.

The S1 path traversal and S2 SSRF holes are intentionally NOT characterized.
Those are not "current behaviour worth keeping" — they get their own
failing-before/passing-after tests in the commits that fix them. A
characterization test that pinned `../../../etc/passwd` resolving successfully
would have to be deleted rather than inverted, and would hide the fix.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import requests

import app  # still owns the ffprobe wrapper; not part of this slice
from media_domain.adapters import filesystem
from media_domain.adapters.http import download_to_file
from media_domain.services import metadata, url_intake


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, *, status_code=200, headers=None, content=b"",
                 chunks=None, json_data=None, raise_on_status=False):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self._chunks = chunks if chunks is not None else [content]
        self._json = json_data
        self._raise_on_status = raise_on_status
        self.closed = False

    def raise_for_status(self):
        if self._raise_on_status or self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def iter_content(self, size=1):
        for chunk in self._chunks:
            yield chunk

    def json(self):
        return self._json

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeClient:
    """Routes URL -> response, recording every call for assertions."""

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


def fake_exists(paths, files):
    """Patch os.path.exists/isfile so local_mirror's hardcoded
    /opt/files/data root needs no privileged real directory."""
    real_exists, real_isfile = os.path.exists, os.path.isfile

    def exists(p):
        return p in paths or real_exists(p)

    def isfile(p):
        return p in files or (p in paths and real_isfile(p))

    return exists, isfile


# ── url_ext ──────────────────────────────────────────────────────────────────

def test_url_ext_returns_extension_lowercased():
    assert url_intake.url_ext("http://x/movie.mkv") == "mkv"
    assert url_intake.url_ext("http://x/movie.MKV") == "mkv"


def test_url_ext_strips_query_and_fragment():
    assert url_intake.url_ext("http://x/movie.mkv?token=abc") == "mkv"
    assert url_intake.url_ext("http://x/movie.mkv#t=10") == "mkv"
    assert url_intake.url_ext("http://x/movie.mkv?t=1#f") == "mkv"


def test_url_ext_returns_empty_for_empty_string():
    assert url_intake.url_ext("") == ""


def test_url_ext_quirk_returns_whole_tail_when_host_contains_a_dot():
    # Pinned quirk, not an endorsement: url_ext splits on the LAST dot of the
    # whole URL, so a dotted hostname yields garbage like "b/c" for
    # "http://a.b/c". Harmless today because every call site compares against
    # a known-extension set, but it must not change silently during the move.
    assert url_intake.url_ext("http://a.b/c") == "b/c"


# ── is_direct_media_url ──────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://x/a.mkv", "http://x/a.mp4", "http://x/a.m4v", "http://x/a.webm",
    "http://x/a.avi", "http://x/a.mov", "http://x/a.mp3", "http://x/a.aac",
    "http://x/a.mka", "http://x/a.ogg", "http://x/a.flac", "http://x/a.wav",
])
def test_is_direct_media_url_accepts_known_extensions(url):
    assert url_intake.is_direct_media_url(url) is True


def test_is_direct_media_url_rejects_unknown_extension():
    assert url_intake.is_direct_media_url("http://x/a.txt") is False
    assert url_intake.is_direct_media_url("http://x/page") is False
    assert url_intake.is_direct_media_url("") is False


def test_is_direct_media_url_ignores_query_string():
    assert url_intake.is_direct_media_url("http://x/a.mp4?token=1") is True


# ── download_to_file ─────────────────────────────────────────────────────────

def test_download_to_file_writes_body_to_disk(tmp_path):
    dest = tmp_path / "out.bin"
    client = FakeClient({"http://x/f": FakeResponse(chunks=[b"abc", b"def"])})
    download_to_file(client, "http://x/f", str(dest))
    assert dest.read_bytes() == b"abcdef"
    assert client.calls[0][0] == "http://x/f"


def test_download_to_file_sends_browser_user_agent(tmp_path):
    client = FakeClient({"http://x/f": FakeResponse(content=b"x")})
    download_to_file(client, "http://x/f", str(tmp_path / "o.bin"))
    assert client.calls[0][1]["headers"]["User-Agent"] == "Mozilla/5.0"


def test_download_to_file_propagates_http_error(tmp_path):
    client = FakeClient({"http://x/f": FakeResponse(status_code=404)})
    with pytest.raises(requests.HTTPError):
        download_to_file(client, "http://x/f", str(tmp_path / "o.bin"))


# ── check_link_ok ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    (200, True), (204, True), (301, True), (302, True), (399, True),
    (400, False), (403, False), (404, False), (500, False),
])
def test_check_link_ok_maps_status_to_bool(status, expected):
    resp = FakeResponse(status_code=status, chunks=[b"x"])
    client = FakeClient({"http://x/f": resp})
    assert url_intake.check_link_ok(client, "http://x/f") is expected


def test_check_link_ok_closes_the_connection():
    resp = FakeResponse(status_code=200, chunks=[b"x"])
    url_intake.check_link_ok(FakeClient({"http://x/f": resp}), "http://x/f")
    assert resp.closed is True


def test_check_link_ok_reads_a_single_byte_only():
    """It must not transfer the whole file just to validate a link."""
    resp = FakeResponse(status_code=200, chunks=[b"x", b"y", b"z"])
    url_intake.check_link_ok(FakeClient({"http://x/f": resp}), "http://x/f")
    assert next(resp.iter_content(1)) == b"x"


def test_check_link_ok_returns_false_on_transport_error():
    client = FakeClient(exc=requests.ConnectionError("dns"))
    assert url_intake.check_link_ok(client, "http://x/f") is False


def test_check_link_ok_returns_false_on_timeout():
    client = FakeClient(exc=requests.Timeout("slow"))
    assert url_intake.check_link_ok(client, "http://x/f") is False


# ── resolve_media_url_remote ─────────────────────────────────────────────────

def test_resolve_returns_direct_media_url_without_any_request():
    client = FakeClient()
    assert url_intake.resolve_media_url_remote(client, "http://x/a.mkv") == "http://x/a.mkv"
    assert client.calls == []


@pytest.mark.parametrize("ctype", ["video/mp4", "audio/mpeg", "video/x-matroska"])
def test_resolve_passes_through_media_content_types(ctype):
    client = FakeClient({"http://x/p": FakeResponse(headers={"Content-Type": ctype})})
    assert url_intake.resolve_media_url_remote(client, "http://x/p") == "http://x/p"


def test_resolve_passes_through_non_html_content_types():
    client = FakeClient({"http://x/p": FakeResponse(headers={"Content-Type": "application/json"})})
    assert url_intake.resolve_media_url_remote(client, "http://x/p") == "http://x/p"


def test_resolve_extracts_first_direct_media_link_from_html():
    html = b'<a href="http://x/one.mkv">1</a><a href="http://x/two.mp4">2</a>'
    client = FakeClient({"http://x/p": FakeResponse(
        headers={"Content-Type": "text/html"}, content=html)})
    assert url_intake.resolve_media_url_remote(client, "http://x/p") == "http://x/one.mkv"


def test_resolve_ignores_non_video_links_in_html():
    html = b'<a href="http://x/readme.txt">r</a><a href="http://x/clip.webm">c</a>'
    client = FakeClient({"http://x/p": FakeResponse(
        headers={"Content-Type": "text/html"}, content=html)})
    assert url_intake.resolve_media_url_remote(client, "http://x/p") == "http://x/clip.webm"


def test_resolve_returns_original_when_html_has_no_media():
    html = b'<a href="http://x/readme.txt">r</a>'
    client = FakeClient({"http://x/p": FakeResponse(
        headers={"Content-Type": "text/html"}, content=html)})
    assert url_intake.resolve_media_url_remote(client, "http://x/p") == "http://x/p"


def test_resolve_returns_original_on_network_error():
    client = FakeClient(exc=requests.ConnectionError("boom"))
    assert url_intake.resolve_media_url_remote(client, "http://x/p") == "http://x/p"


def test_resolve_uses_file_manager_api_for_files_data_folder():
    listing = FakeResponse(json_data={"items": [
        {"type": "dir", "name": "sub"},
        {"type": "file", "name": "clip.mp4"},
    ]})
    client = FakeClient({
        "http://x/files/api/list": listing,
        "http://x/files/data": FakeResponse(headers={"Content-Type": "text/html"}, content=b"<html/>"),
    })
    result = url_intake.resolve_media_url_remote(client, "http://x/files/data/Movies")
    assert result == "http://x/files/data/Movies/clip.mp4"
    assert any(c[0].startswith("http://x/files/api/list?p=Movies") for c in client.calls)


def test_resolve_percent_encodes_each_path_segment():
    listing = FakeResponse(json_data={"items": [
        {"type": "file", "name": "my movie.mp4"},
    ]})
    client = FakeClient({
        "http://x/files/api/list": listing,
        "http://x/files/data": FakeResponse(headers={"Content-Type": "text/html"}, content=b"<html/>"),
    })
    result = url_intake.resolve_media_url_remote(client, "http://x/files/data/Folder%20Name")
    assert result == "http://x/files/data/Folder%20Name/my%20movie.mp4"


def test_resolve_returns_original_when_list_api_fails():
    client = FakeClient({
        "http://x/files/api/list": FakeResponse(status_code=500),
        "http://x/files/data": FakeResponse(headers={"Content-Type": "text/html"}, content=b"<html/>"),
    })
    assert url_intake.resolve_media_url_remote(client, "http://x/files/data/Movies") == \
        "http://x/files/data/Movies"


# ── local_mirror ─────────────────────────────────────────────────────────────

def test_local_mirror_passes_through_non_string():
    assert filesystem.local_mirror(None) is None
    assert filesystem.local_mirror(123) == 123


def test_local_mirror_passes_through_non_http():
    assert filesystem.local_mirror("/files/data/a.mkv") == "/files/data/a.mkv"
    assert filesystem.local_mirror("file:///opt/files/data/a.mkv") == "file:///opt/files/data/a.mkv"


def test_local_mirror_passes_through_urls_outside_files_data():
    url = "http://x/other/a.mkv"
    assert filesystem.local_mirror(url) == url


def test_local_mirror_maps_existing_file_to_local_path(monkeypatch):
    exists, isfile = fake_exists({"/opt/files/data/a.mkv"}, {"/opt/files/data/a.mkv"})
    monkeypatch.setattr(os.path, "exists", exists)
    monkeypatch.setattr(os.path, "isfile", isfile)
    assert filesystem.local_mirror("http://x/files/data/a.mkv") == "/opt/files/data/a.mkv"


def test_local_mirror_percent_decodes_the_relative_path(monkeypatch):
    exists, isfile = fake_exists({"/opt/files/data/my movie.mkv"}, {"/opt/files/data/my movie.mkv"})
    monkeypatch.setattr(os.path, "exists", exists)
    monkeypatch.setattr(os.path, "isfile", isfile)
    assert filesystem.local_mirror("http://x/files/data/my%20movie.mkv") == \
        "/opt/files/data/my movie.mkv"


def test_local_mirror_passes_through_when_local_file_absent(monkeypatch):
    exists, isfile = fake_exists(set(), set())
    monkeypatch.setattr(os.path, "exists", exists)
    monkeypatch.setattr(os.path, "isfile", isfile)
    url = "http://x/files/data/gone.mkv"
    assert filesystem.local_mirror(url) == url


def test_local_mirror_passes_through_when_target_is_a_directory(monkeypatch):
    exists, isfile = fake_exists({"/opt/files/data/Movies"}, set())
    monkeypatch.setattr(os.path, "exists", exists)
    monkeypatch.setattr(os.path, "isfile", isfile)
    url = "http://x/files/data/Movies"
    assert filesystem.local_mirror(url) == url


# ── resolve_media_url (composition) ─────────────────────────────────────────

def test_resolve_media_url_composes_remote_then_mirror(monkeypatch):
    client = FakeClient({
        "http://x/files/api/list": FakeResponse(json_data={
            "items": [{"type": "file", "name": "a.mkv"}]}),
        "http://x/files/data": FakeResponse(headers={"Content-Type": "text/html"}, content=b"<html/>"),
    })
    local = "/opt/files/data/Movies/a.mkv"
    exists, isfile = fake_exists({local}, {local})
    monkeypatch.setattr(os.path, "exists", exists)
    monkeypatch.setattr(os.path, "isfile", isfile)
    resolved = url_intake.resolve_media_url(client, "http://x/files/data/Movies")
    assert resolved == local
    assert client.calls, "the remote half must still run first"


def test_resolve_media_url_stays_remote_when_no_local_copy(monkeypatch):
    """The composition must not lose the resolved URL when the file is not
    mirrored locally — that is the normal case for third-party links."""
    client = FakeClient({
        "http://x/files/api/list": FakeResponse(json_data={
            "items": [{"type": "file", "name": "a.mkv"}]}),
        "http://x/files/data": FakeResponse(headers={"Content-Type": "text/html"}, content=b"<html/>"),
    })
    exists, isfile = fake_exists(set(), set())
    monkeypatch.setattr(os.path, "exists", exists)
    monkeypatch.setattr(os.path, "isfile", isfile)
    assert url_intake.resolve_media_url(client, "http://x/files/data/Movies") == \
        "http://x/files/data/Movies/a.mkv"


def test_resolve_media_url_is_a_noop_for_direct_urls():
    client = FakeClient()
    assert url_intake.resolve_media_url(client, "http://x/a.mkv") == "http://x/a.mkv"
    assert client.calls == []


# ── probe metadata accessors ─────────────────────────────────────────────────

def test_get_duration_prefers_format_duration():
    data = {"format": {"duration": "12.5"}, "streams": [{"duration": "99"}]}
    assert metadata.get_duration_s(data) == 12.5


def test_get_duration_falls_back_to_stream_duration():
    assert metadata.get_duration_s({"format": {}, "streams": [{"duration": "8"}]}) == 8.0


def test_get_duration_is_zero_when_absent_or_unparsable():
    assert metadata.get_duration_s({}) == 0.0
    assert metadata.get_duration_s({"format": {"duration": "abc"}}) == 0.0
    assert metadata.get_duration_s({"format": {"duration": "0"}}) == 0.0
    assert metadata.get_duration_s({"streams": [{"duration": None}]}) == 0.0


def test_get_video_dimensions():
    data = {"streams": [
        {"codec_type": "audio"},
        {"codec_type": "video", "width": 1920, "height": 1080},
    ]}
    assert metadata.get_video_width(data) == 1920
    assert metadata.get_video_height(data) == 1080


def test_get_video_dimensions_are_zero_without_video_stream():
    assert metadata.get_video_width({"streams": [{"codec_type": "audio"}]}) == 0
    assert metadata.get_video_height({}) == 0
    assert metadata.get_video_height({"streams": [{"codec_type": "video", "height": "x"}]}) == 0


def test_find_text_subtitle_streams_filters_and_shapes():
    data = {"streams": [
        {"index": 0, "codec_type": "subtitle", "codec_name": "ass",
         "tags": {"language": "eng", "title": "Full"}},
        {"index": 1, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"},
        {"index": 2, "codec_type": "video", "codec_name": "hevc"},
    ]}
    assert metadata.find_text_subtitle_streams(data) == [
        {"index": 0, "lang": "eng", "title": "Full"}]


def test_find_text_subtitle_streams_defaults_missing_tags():
    data = {"streams": [{"index": 3, "codec_type": "subtitle", "codec_name": "subrip"}]}
    assert metadata.find_text_subtitle_streams(data) == [{"index": 3, "lang": "", "title": ""}]


def test_find_text_subtitle_streams_truncates_long_language():
    data = {"streams": [{"index": 0, "codec_type": "subtitle", "codec_name": "subrip",
                         "tags": {"language": "abcdefghijklmnop"}}]}
    assert metadata.find_text_subtitle_streams(data)[0]["lang"] == "abcdefgh"


def test_text_subtitle_codecs_moved_with_the_accessor():
    """Guard against the definition being reintroduced in app.py."""
    assert "ass" in metadata.TEXT_SUBTITLE_CODECS
    assert "TEXT_SUBTITLE_CODECS = " not in Path(app.__file__).read_text()


# ── ffprobe wrapper: still app.py's, not part of this slice ──────────────────

def test_ffprobe_source_returns_empty_dict_on_nonzero_returncode(monkeypatch):
    class Proc:
        returncode = 1
        stdout = b"{}"
        stderr = b"boom"
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: Proc())
    assert app._ffprobe_source("http://x/a.mkv") == {}


def test_ffprobe_source_returns_empty_dict_on_exception(monkeypatch):
    def boom(*a, **k):
        raise OSError("no ffprobe")
    monkeypatch.setattr(app.subprocess, "run", boom)
    assert app._ffprobe_source("http://x/a.mkv") == {}


def test_ffprobe_source_parses_json_stdout(monkeypatch):
    class Proc:
        returncode = 0
        stdout = b'{"format": {"duration": "5.0"}}'
        stderr = b""
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: Proc())
    assert app._ffprobe_source("http://x/a.mkv") == {"format": {"duration": "5.0"}}


# ── app.py rewiring ──────────────────────────────────────────────────────────

def test_app_no_longer_defines_the_moved_helpers():
    """Rule 2': the move is only real once the old definitions are gone.

    Asserted against app.py's *source*, not hasattr(): a moved helper that is
    still called from app.py (url_ext is, by _is_hls) stays importable as an
    attribute, so hasattr would pass on a duplicate `def` shadowing the import —
    which is exactly the mistake this test exists to catch.
    """
    source = Path(app.__file__).read_text()
    for name in ("_download_to_file", "_check_link_ok", "_resolve_media_url",
                 "_resolve_media_url_remote", "_local_mirror", "_is_direct_media_url",
                 "_get_duration_s", "_get_video_height", "_get_video_width",
                 "_find_text_subtitle_streams", "url_ext"):
        assert f"def {name}(" not in source, f"app.py still defines {name}"
    assert "TEXT_SUBTITLE_CODECS = " not in source


def test_app_reexports_the_moved_helpers_for_its_call_sites():
    assert app.check_link_ok is url_intake.check_link_ok
    assert app.resolve_media_url is url_intake.resolve_media_url
    assert app.url_ext is url_intake.url_ext
    assert app.get_duration_s is metadata.get_duration_s