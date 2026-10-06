"""Filesystem adapter: map a remote URL onto a local path, when one exists.

The only reason this exists: ffmpeg should read the file from disk instead of
over HTTP, so a flaky or overloaded HTTP fetch can never silently truncate a
long encode.
"""
from __future__ import annotations

import os
import re
import urllib.parse

# Root of the verGoBoy file store on the server.
DEFAULT_LOCAL_ROOT = "/opt/files/data"

_FILES_DATA_RE = re.compile(r"^https?://[^/]+/files/data/(.*)$")


class UnsafePathError(ValueError):
    """A path handed to ffprobe/ffmpeg that leaves the allowed roots (audit S2)."""


def safe_local_path(path: str, roots) -> str:
    """Return the `realpath` of `path` if (and only if) it lands inside one of
    `roots`, else raise.

    This is the local-path half of the gate every source passes through before
    it becomes an ffprobe/ffmpeg argument: a stored source is data from the DB,
    and `..`, an absolute path or a symlink can all walk out of the media
    store. Containment is decided after normalization — checking the literal
    string first is exactly the mistake that made the old argv buildable.
    """
    if not isinstance(path, str) or not path or "\x00" in path:
        raise UnsafePathError("empty or nul path")
    if "://" in path:
        raise UnsafePathError(f"not a local path: {path!r}")
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError) as e:
        raise UnsafePathError(f"unresolvable path: {e}") from e
    for root in roots:
        if not isinstance(root, str) or not root:
            continue
        real_root = os.path.realpath(root)
        if real == real_root or real.startswith(real_root + os.sep):
            return real
    raise UnsafePathError(f"path outside allowed roots: {path!r}")


def local_mirror(url: str, root: str = DEFAULT_LOCAL_ROOT) -> str:
    """If `url` is a verGoBoy file URL that also exists under `root`, return the
    local path; otherwise return `url` unchanged.

    Containment is decided on `realpath`, never on the literal string. The URL's
    remainder is attacker-controlled and percent-decoded before it is joined, so
    `..`, an absolute segment and a symlink are all ways out of the root — and
    the resulting path becomes the ffmpeg/ffprobe encode source (audit S1).
    Checking after normalization is what makes those refusals meaningful.
    """
    if not isinstance(url, str) or not url.startswith("http"):
        return url
    m = _FILES_DATA_RE.match(url)
    if not m:
        return url
    rel = urllib.parse.unquote(m.group(1))
    if "\x00" in rel or os.path.isabs(rel):
        return url
    try:
        real_root = os.path.realpath(root)
        real_path = os.path.realpath(os.path.join(root, rel))
    except (OSError, ValueError):
        return url
    if real_path != real_root and not real_path.startswith(real_root + os.sep):
        return url
    if os.path.isfile(real_path):
        return real_path
    return url