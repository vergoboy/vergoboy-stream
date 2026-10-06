"""S2 ffprobe/ffmpeg input policy — failing-before/passing-after.

The audit's "SSRF in ffprobe/ffmpeg (+ -protocol_whitelist)" item, split into
what the server must refuse before an argv is ever built (non-http schemes,
loopback/private hosts, paths outside the allowed roots) and what the argv
must carry when the source really is remote (`-protocol_whitelist`, so a
hostile playlist cannot pivot the demuxer into `file:` or `subfile:` URIs).

Literal IP addresses are used wherever DNS would otherwise be involved: the
policy resolves hostnames, but 127.0.0.1 needs no resolver and keeps these
tests offline.
"""
from __future__ import annotations

import os

import pytest

import app
from config import Config
from media_domain.adapters.filesystem import UnsafePathError, safe_local_path
from media_domain.services.source_policy import (
    MEDIA_PROTO_WHITELIST,
    UnsafeUrlError,
    validate_media_source,
)

PUBLIC_IP = "93.184.216.34"
PRIVATE_IP = "10.0.0.9"
WHITELIST = MEDIA_PROTO_WHITELIST


class _Proc:
    def __init__(self, returncode=0, stdout=b'{"format": {"duration": "5.0"}}',
                 stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def ffprobe_spy(monkeypatch):
    """Never run ffprobe; record argv instead."""
    calls: list = []

    def _fake_run(args, **kwargs):
        calls.append(list(args))
        return _Proc()

    monkeypatch.setattr(app.subprocess, "run", _fake_run)
    return calls


# ── safe_local_path (the one local-path gate) ───────────────────────────────

def test_safe_local_path_accepts_a_path_inside_the_root(tmp_path):
    root = tmp_path / "media"
    (root / "hls").mkdir(parents=True)
    target = root / "hls" / "index.m3u8"
    assert safe_local_path(str(target), [str(root)]) == str(target)


def test_safe_local_path_refuses_dotdot_escape(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    with pytest.raises(UnsafePathError):
        safe_local_path(str(root / ".." / ".." / "etc" / "passwd"), [str(root)])


def test_safe_local_path_refuses_an_absolute_path_outside(tmp_path):
    with pytest.raises(UnsafePathError):
        safe_local_path("/etc/passwd", [str(tmp_path / "media")])


def test_safe_local_path_refuses_symlink_escape(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    link = root / "escape"
    link.symlink_to("/etc")
    with pytest.raises(UnsafePathError):
        safe_local_path(str(link / "passwd"), [str(root)])


def test_safe_local_path_refuses_urls_and_empty_values(tmp_path):
    with pytest.raises(UnsafePathError):
        safe_local_path("file:///etc/passwd", [str(tmp_path)])
    with pytest.raises(UnsafePathError):
        safe_local_path("", [str(tmp_path)])
    with pytest.raises(UnsafePathError):
        safe_local_path("a\x00b", [str(tmp_path)])


# ── the app-level source gate ────────────────────────────────────────────────

def test_validated_source_refuses_private_http():
    with pytest.raises(UnsafeUrlError):
        validate_media_source(f"http://{PRIVATE_IP}/a.mkv", [])


def test_validated_source_refuses_metadata_endpoint():
    with pytest.raises(UnsafeUrlError):
        validate_media_source("http://169.254.169.254/latest/meta-data/", [])


def test_validated_source_refuses_non_http_schemes():
    for url in ("file:///etc/passwd", "ftp://x/a.mkv", "gopher://x/"):
        with pytest.raises(UnsafeUrlError):
            validate_media_source(url, [])


def test_validated_source_refuses_paths_outside_the_media_roots():
    with pytest.raises(UnsafePathError):
        validate_media_source("/etc/passwd", [])


def test_validated_source_passes_public_http_and_inside_paths(tmp_path):
    assert validate_media_source(f"http://{PUBLIC_IP}/a.mkv", []) == f"http://{PUBLIC_IP}/a.mkv"
    inside = os.path.join(str(tmp_path), "item", "720p", "index.m3u8")
    assert validate_media_source(inside, [str(tmp_path)]) == os.path.realpath(inside)


# ── ffprobe argv ─────────────────────────────────────────────────────────────

def test_ffprobe_refuses_private_http_source_without_running(ffprobe_spy):
    """Failing-before: the old code shelled out to ffprobe on it."""
    assert app._ffprobe_source(f"http://{PRIVATE_IP}/a.mkv") == {}
    assert ffprobe_spy == []


def test_ffprobe_refuses_file_scheme_without_running(ffprobe_spy):
    assert app._ffprobe_source("file:///etc/passwd") == {}
    assert ffprobe_spy == []


def test_ffprobe_refuses_local_path_outside_roots_without_running(ffprobe_spy):
    assert app._ffprobe_source("/etc/passwd") == {}
    assert ffprobe_spy == []


def test_ffprobe_accepts_a_path_inside_the_hls_root(ffprobe_spy, tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "HLS_DIR", str(tmp_path))
    src = os.path.join(str(tmp_path), "item", "index.m3u8")
    assert app._ffprobe_source(src) == {"format": {"duration": "5.0"}}
    assert ffprobe_spy[0][-1] == os.path.realpath(src)


def test_ffprobe_whitelists_protocols_for_a_remote_source(ffprobe_spy):
    """Failing-before: no -protocol_whitelist anywhere in the argv."""
    src = f"http://{PUBLIC_IP}/a.mkv"
    app._ffprobe_source(src)
    argv = ffprobe_spy[0]
    i = argv.index("-protocol_whitelist")
    assert argv[i + 1] == WHITELIST
    assert argv[-1] == src
    assert i < len(argv) - 1  # input option precedes the input


# ── the live rendition argv ──────────────────────────────────────────────────

def test_rendition_cmd_whitelists_protocols_for_a_remote_source():
    """Failing-before: _build_single_rendition_cmd had no whitelist."""
    src = f"http://{PUBLIC_IP}/a.mkv"
    cmd = app._build_single_rendition_cmd(src, "/tmp/out", 720, "2500k", "128k")
    i = cmd.index("-protocol_whitelist")
    assert cmd[i + 1] == WHITELIST
    assert cmd.index("-i") > i  # the option binds to this input
    assert cmd[cmd.index("-i") + 1] == src


def test_rendition_cmd_has_no_whitelist_for_a_local_source(tmp_path):
    src = str(tmp_path / "a.mkv")
    cmd = app._build_single_rendition_cmd(src, str(tmp_path), 720, "2500k", "128k")
    assert "-protocol_whitelist" not in cmd


# ── the encode funnel refuses before touching the pipeline ──────────────────

def test_encode_rendition_refuses_a_private_source_before_it_starts(
        monkeypatch, tmp_path):
    """Failing-before: no refusal is logged — the encode simply starts."""
    monkeypatch.setattr(Config, "HLS_DIR", str(tmp_path))
    monkeypatch.setattr(app, "free_space_mb", lambda p: 1 << 40)
    logs: list = []
    monkeypatch.setattr(app, "log_debug", lambda msg, *a, **k: logs.append(str(msg)))

    app._encode_rendition("safetyrefuse", f"http://{PRIVATE_IP}/a.mkv",
                          "720p", 720, "2500k", "128k", "tester", True)

    assert any("Rendition encode failed" in m for m in logs), logs
    assert not list(tmp_path.iterdir())  # nothing was ever created
