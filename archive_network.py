"""Strictly separated HTTP clients for archive pages and media URLs."""
from __future__ import annotations

from urllib.parse import urlsplit
import os

import requests


class ArchiveProxyConfigurationError(ValueError):
    pass


def create_archive_session(proxy_url: str) -> requests.Session:
    """A Digimoviez-only session that always uses the configured local proxy."""
    parsed = urlsplit(proxy_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ArchiveProxyConfigurationError("STREAM_ARCHIVE_HTTP_PROXY must be an HTTP(S) proxy URL")
    session = requests.Session()
    # Prevent HTTP_PROXY/HTTPS_PROXY/ALL_PROXY from changing this route.
    session.trust_env = False
    session.proxies = {"http": proxy_url, "https": proxy_url}
    return session


def create_direct_media_session() -> requests.Session:
    """A media-only session that cannot inherit any process proxy settings."""
    session = requests.Session()
    session.trust_env = False
    session.proxies = {}
    return session


def direct_media_environment() -> dict[str, str]:
    """Copy process env without any proxy variables for ffmpeg/ffprobe."""
    blocked = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
    return {key: value for key, value in os.environ.items() if key.lower() not in blocked}
