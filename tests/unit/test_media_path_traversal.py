"""S1: path traversal in the local-mirror mapping.

`local_mirror` turns a remote verGoBoy URL into a local path under
Config's media root. Before the fix it accepted *any* host, percent-decoded
the remainder, and joined it onto the root with no normalization or
containment check — so `/files/data/../../../etc/passwd` escaped the root.
That local path then became the ffmpeg/ffprobe encode source (audit S1).

These tests are the failing half: they were written and run against the
vulnerable implementation before it was fixed, and are kept as the permanent
regression net afterwards.
"""
from __future__ import annotations

import os

import pytest

from media_domain.adapters.filesystem import local_mirror

ROOT = "/opt/files/data"


def _allow(real_fs_paths, monkeypatch):
    """Pretend these absolute paths exist as regular files.

    Probes are normalized first, the way a real filesystem resolves `..` and
    symlinks. Without that, `os.path.join` hands the unnormalized
    "/opt/files/data/../../etc/passwd" to the check, the fake reports it
    missing, and the traversal tests pass for the wrong reason.
    """
    real_exists, real_isfile = os.path.exists, os.path.isfile

    def _norm(p):
        try:
            return os.path.normpath(os.path.realpath(p))
        except (OSError, ValueError):
            return os.path.normpath(p)

    def exists(p):
        return _norm(p) in real_fs_paths or real_exists(p)

    def isfile(p):
        return _norm(p) in real_fs_paths or real_isfile(p)

    monkeypatch.setattr(os.path, "exists", exists)
    monkeypatch.setattr(os.path, "isfile", isfile)


# ── the vulnerability ────────────────────────────────────────────────────────

@pytest.mark.parametrize("host", [
    "127.0.0.1",
    "localhost",
    "169.254.169.254",          # cloud metadata service
    "10.0.0.5",                 # RFC1918
    "192.168.70.109",
    "evil.example.com",
])
def test_traversal_out_of_the_root_is_refused(host, monkeypatch):
    _allow({os.path.normpath("/etc/passwd")}, monkeypatch)
    url = f"http://{host}/files/data/../../../../etc/passwd"
    assert local_mirror(url, root=ROOT) == url, \
        "must not escape the media root, whatever the host"


def test_percent_encoded_traversal_is_refused(monkeypatch):
    """Encoded so the traversal survives the unquote step."""
    _allow({os.path.normpath("/etc/passwd")}, monkeypatch)
    url = "http://x/files/data/%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd"
    assert local_mirror(url, root=ROOT) == url


def test_traversal_that_stays_inside_is_allowed(monkeypatch):
    """A relative segment that does not escape is legitimate and must keep
    working — the fix must not over-block ordinary nested paths."""
    inside = os.path.join(ROOT, "Movies", "2024", "a.mkv")
    _allow({inside}, monkeypatch)
    url = "http://x/files/data/Movies/2024/a.mkv"
    assert local_mirror(url, root=ROOT) == inside


def test_traversal_that_escapes_then_returns_is_still_refused(monkeypatch):
    """`a/../../b` normalizes to outside the root even though it ends up
    looking shallow; containment is checked after normalization, not on the
    literal string."""
    outside = "/opt/other/b.mkv"
    _allow({outside}, monkeypatch)
    url = "http://x/files/data/Movies/../../other/b.mkv"
    assert local_mirror(url, root=ROOT) == url


def test_symlink_escape_is_refused(monkeypatch, tmp_path):
    """realpath is what matters: a symlink inside the root pointing outside it
    must not be followed."""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.mkv").write_bytes(b"x")
    (root / "link").symlink_to(outside / "secret.mkv")

    url = "http://x/files/data/link"
    # The literal candidate is inside the root; only realpath() reveals the
    # escape, so the mapping must be refused outright rather than returned.
    assert local_mirror(url, root=str(root)) == url


def test_null_byte_is_refused(monkeypatch):
    _allow(set(), monkeypatch)
    url = "http://x/files/data/a.mkv%00.txt"
    result = local_mirror(url, root=ROOT)
    assert "\x00" not in result


def test_absolute_path_in_the_relative_part_cannot_escape(monkeypatch):
    """os.path.join(root, "/etc/passwd") yields "/etc/passwd" — the classic
    join-absolute-path trap."""
    _allow({os.path.normpath("/etc/passwd")}, monkeypatch)
    url = "http://x/files/data//etc/passwd"
    assert local_mirror(url, root=ROOT) == url