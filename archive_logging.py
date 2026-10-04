"""Safe, correlation-aware structured logging for the archive integration."""
from __future__ import annotations

import contextvars
import json
import logging
import re
import time
from urllib.parse import urlsplit

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("archive_request_id", default="-")
LOG = logging.getLogger("archive")


def configure(level: str) -> None:
    LOG.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Flask does not configure a root INFO handler in every launch mode. Keep
    # archive traces observable without depending on global logging setup.
    if not LOG.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        LOG.addHandler(handler)
    LOG.propagate = False


def set_request_id(value: str) -> contextvars.Token[str]:
    return _request_id.set(value)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id.reset(token)


def safe_target(url: str) -> dict[str, str]:
    """Keep scheme/host/path; discard query strings, credentials and tokens."""
    try:
        parsed = urlsplit(url)
        return {"scheme": parsed.scheme, "host": parsed.hostname or "", "path": parsed.path or "/"}
    except ValueError:
        return {"scheme": "", "host": "<invalid>", "path": ""}


# Credentials embedded in a URL, plus long opaque secrets that would otherwise
# be pasted verbatim into a log line by an exception message.
_REDACTIONS = (
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*)://[^/\s:@]+:[^/\s@]*@"), r"\1://<redacted>@"),
    (re.compile(r"(?i)\b(authorization|cookie|set-cookie|password|passwd|token|session|api[_-]?key)\b\s*[:=]\s*\S+"),
     r"\1=<redacted>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}"), "<redacted-jwt>"),
    (re.compile(r"(?i)\b(md5|expires|signature|sig)=[^&\s]+"), r"\1=<redacted>"),
)


def redact(text: str) -> str:
    """Strip credentials and secrets from free-form text bound for a log."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def event(stage: str, name: str, *, level: int = logging.INFO, **fields: object) -> None:
    payload = {"archive_request_id": _request_id.get(), "stage": stage, "event": name, **fields}
    LOG.log(level, "ARCHIVE %s", json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True))


class timer:
    def __init__(self) -> None:
        self.started = time.monotonic()
    @property
    def ms(self) -> int:
        return round((time.monotonic() - self.started) * 1000)
