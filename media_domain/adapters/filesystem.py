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


def local_mirror(url: str, root: str = DEFAULT_LOCAL_ROOT) -> str:
    """If `url` is a verGoBoy file URL that also exists under `root`, return the
    local path; otherwise return `url` unchanged.

    Note: this performs no containment check on `root` — the S1 path-traversal
    fix lands in its own commit, with its own tests, rather than riding along
    inside the move.
    """
    if not isinstance(url, str) or not url.startswith("http"):
        return url
    m = _FILES_DATA_RE.match(url)
    if not m:
        return url
    rel = urllib.parse.unquote(m.group(1))
    local = os.path.join(root, rel)
    if os.path.exists(local) and os.path.isfile(local):
        return local
    return url