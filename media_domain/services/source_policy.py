"""Source policy: the one gate between a stored source and ffprobe/ffmpeg.

A source is data from the DB (a URL a viewer pasted, a path the uploader
chose), and it ends up verbatim in a subprocess argv, so the two ways that
goes wrong are remote (SSRF: loopback/private/metadata hosts, non-http
schemes — audit S2) and local (`..`, absolute paths, symlinks out of the
media store — audit S1's sibling for ffprobe/ffmpeg inputs). One function
refuses both, so no call site can forget half of it.
"""
from __future__ import annotations

from media_domain.adapters.filesystem import UnsafePathError, safe_local_path
from media_domain.adapters.http import UnsafeUrlError, assert_safe_url

# Protocols an http(s) input may pivot into once the demuxer starts following
# the container. crypto/subfile/file are deliberately absent: a hostile remote
# playlist must not be able to pull local files or the VOD store through
# ffprobe/ffmpeg (audit S2, -protocol_whitelist).
MEDIA_PROTO_WHITELIST = "http,https,tcp,tls,crypto"


def validate_media_source(source: str, roots) -> str:
    """Return `source` fit to become an ffprobe/ffmpeg argument, or raise
    UnsafeUrlError / UnsafePathError.

    http(s) → host/IP policy; anything else with a scheme is refused outright;
    the rest must be a local path inside `roots` (computed by the caller, so
    runtime Config overrides are honoured).
    """
    if not isinstance(source, str) or not source:
        raise UnsafePathError("empty source")
    if source.startswith(("http://", "https://")):
        assert_safe_url(source)
        return source
    if "://" in source:
        raise UnsafeUrlError(f"unsupported scheme: {source!r}")
    return safe_local_path(source, roots)
