"""Network adapter: streaming a remote body to disk."""
from __future__ import annotations

_BROWSER_UA = "Mozilla/5.0"

_CHUNK_BYTES = 1024 * 1024


def download_to_file(client, url: str, dest_path: str, timeout: int = 25) -> None:
    """GET `url` into `dest_path`.

    `client` is an injected ``requests.Session``-alike owned by the caller, so
    proxy and TLS policy stay the caller's decision. Raises whatever the
    client's ``raise_for_status`` raises; the caller decides whether that is
    fatal.
    """
    with client.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=timeout,
                    stream=True) as resp, open(dest_path, "wb") as out:
        resp.raise_for_status()
        for chunk in resp.iter_content(_CHUNK_BYTES):
            if chunk:
                out.write(chunk)