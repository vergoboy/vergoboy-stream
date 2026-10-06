"""Crash diagnostics: durable log files plus uncaught-failure capture.

Why this exists. The app has three processes that can die independently, and
before this module each reported it differently or not at all:

  * the Python backend — gevent greenlets that raise are swallowed silently by
    the hub, and ``sys.excepthook`` only fires for the main greenlet, so a
    background task dying left no trace anywhere;
  * the Rust/Tauri host — a panic aborts the process and the message went to
    stderr, which in a GUI launch is nowhere the user looks;
  * the webview — a JS exception only ever appeared in a devtools console that
    a packaged app does not open.

So the failure that matters is frequently the one with no record. This module
writes three files under the shared log directory (``$XDG_STATE_HOME/
vergoboy-stream/logs``, or ``$STREAM_LOG_DIR`` when set):

    app.log       rolling INFO+ log of everything, via ``logging``
    error.log     WARNING+ only, the file to read first
    crash.log     uncaught exceptions, unhandled rejections and Rust panics,
                  one self-contained block per failure with a full traceback

Rotation is size-based (``RotatingFileHandler``) so a crash loop cannot fill the
disk, and every message is passed through :func:`redact` first so a traceback
carrying a cookie or token does not end up on disk in the clear.

Client-side errors are accepted over ``POST /stream/api/log/client``; see
:func:`record_client_error`.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
from typing import Any

from archive_logging import redact

def _default_log_dir() -> str:
    """Resolve the log directory. Must match `log_dir()` in webapp/src-tauri/src/lib.rs.

    ``$STREAM_LOG_DIR`` wins, then the XDG state directory. Not the repo's
    ``data/`` dir: the packaged Arch binary runs from ``/usr/bin`` with no repo
    present, so a repo-relative path would have the two processes writing to
    different places — or nowhere at all.
    """
    override = os.environ.get("STREAM_LOG_DIR", "").strip()
    if override:
        return override
    state_home = os.environ.get("XDG_STATE_HOME", "").strip()
    if not state_home:
        state_home = os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(state_home, "vergoboy-stream", "logs")


LOG_DIR = _default_log_dir()
APP_LOG = os.path.join(LOG_DIR, "app.log")
ERROR_LOG = os.path.join(LOG_DIR, "error.log")
CRASH_LOG = os.path.join(LOG_DIR, "crash.log")

# 2 MiB per file, 5 rotations: ~12 MiB total worst case, which is small enough
# to never be the reason a machine runs out of space and large enough that a
# crash loop cannot evict the evidence of its first few failures.
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5

LOGGER_NAME = "app"

_lock = threading.Lock()
_configured = False
_crash_logger: logging.Logger | None = None
# Module-level rather than an attribute on `sys`, so it is inspectable and
# resettable in tests; `sys` is shared with every other library in the process.
_excepthooks_installed = False

# Anything longer than this in a client error is a runaway string or a blob, not
# a useful message; truncation keeps one bad report from filling the log.
MAX_FIELD = 4000


class _RedactingFormatter(logging.Formatter):
    """Formatter that scrubs secrets on the way to disk.

    Redaction happens here rather than at each call site so a third-party
    library logging a full request object cannot leak a cookie by accident.
    """

    def format(self, record: logging.LogRecord) -> str:
        try:
            return redact(super().format(record))
        except Exception:
            # A formatter must never raise: a broken log call site would
            # otherwise turn a warning into the very crash it was reporting.
            return f"<log formatting failed: {type(record).__name__}: {record.msg!r}>"


def _handler(path: str, level: int) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(_RedactingFormatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    return handler


def configure(level: str = "INFO", *, stream: Any = None) -> logging.Logger:
    """Attach file (and optional stderr) handlers exactly once.

    Idempotent, because ``app.py`` is imported by the WSGI app, by tests and by
    the socket suite in the same interpreter; adding handlers twice would double
    every line. Returns the shared ``app`` logger.
    """
    global _configured, _crash_logger

    logger = logging.getLogger(LOGGER_NAME)
    with _lock:
        if _configured:
            return logger

        os.makedirs(LOG_DIR, exist_ok=True)
        resolved = getattr(logging, str(level).upper(), logging.INFO)
        logger.setLevel(min(resolved, logging.INFO))
        logger.propagate = False

        try:
            logger.addHandler(_handler(APP_LOG, logging.INFO))
            logger.addHandler(_handler(ERROR_LOG, logging.WARNING))
        except OSError as exc:
            # An unwritable log directory must not stop the app from starting;
            # stderr still carries everything.
            print(f"[app_logging] cannot open log files in {LOG_DIR}: {exc}", flush=True)

        if stream is not None:
            stderr = logging.StreamHandler(stream)
            stderr.setLevel(resolved)
            stderr.setFormatter(_RedactingFormatter("%(levelname)-7s [%(name)s] %(message)s"))
            logger.addHandler(stderr)

        _crash_logger = logging.getLogger("app.crash")
        _crash_logger.setLevel(logging.ERROR)
        # Keep propagation off and attach the crash handlers explicitly, so
        # `crash.log` gets the full block while `app.log`/`error.log` get a
        # single summary line. Propagating would write the whole multi-line
        # traceback into app.log on every crash.
        _crash_logger.propagate = False
        try:
            _crash_logger.addHandler(_handler(CRASH_LOG, logging.ERROR))
        except OSError as exc:
            print(f"[app_logging] cannot open {CRASH_LOG}: {exc}", flush=True)

        _configured = True

    _install_excepthooks()
    return logger


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name if name.startswith(LOGGER_NAME) else f"{LOGGER_NAME}.{name}")


def log(message: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one structured line. ``fields`` are appended as JSON when present."""
    if fields:
        message = f"{message} {json.dumps(fields, ensure_ascii=False, default=str, sort_keys=True)}"
    logging.getLogger(LOGGER_NAME).log(level, "%s", message)


# ── uncaught failures ────────────────────────────────────────────────────────

def _fmt_fields(fields: dict[str, Any]) -> str:
    return "\n".join(f"  {key} = {value}" for key, value in fields.items())


def record_crash(kind: str, message: str, *, traceback_text: str | None = None,
                 fields: dict[str, Any] | None = None) -> None:
    """Write one self-contained block to crash.log.

    Deliberately not routed through ``log``: the crash record must survive even
    if the normal handlers are what failed.
    """
    logger = _crash_logger or logging.getLogger("app.crash")
    block = [
        "=" * 78,
        f"CRASH kind={kind} at {time.strftime('%Y-%m-%d %H:%M:%S%z')}",
        f"pid={os.getpid()} thread={threading.current_thread().name}",
        f"message: {message}",
    ]
    if fields:
        block.append(_fmt_fields(fields))
    if traceback_text:
        block.append(traceback_text.rstrip())
    try:
        # The full block goes to crash.log; a one-line summary also lands in
        # app.log and error.log so `tail app.log` shows that something failed
        # without needing to know crash.log exists.
        logger.error("%s", redact("\n".join(block)))
        logging.getLogger(LOGGER_NAME).error(
            "CRASH kind=%s pid=%s message=%s", kind, os.getpid(), redact(str(message)))
    except Exception:
        # Last resort: the crash log itself is unusable. Never raise from here.
        print(f"[app_logging] CRASH {kind}: {redact(message)}", flush=True)


def _install_excepthooks() -> None:
    """Catch failures that would otherwise vanish.

    Three separate escape hatches, because they catch different things:

    ``sys.excepthook``
        uncaught exception on the main greenlet.
    ``threading.excepthook``
        an exception in any non-gevent background thread.
    ``sys.unraisablehook``
        an exception in a ``__del__`` or a swallowed-except path, which
        otherwise prints nothing at all.
    """
    global _excepthooks_installed
    if _excepthooks_installed:
        return
    # Wrap whatever was there before, so pytest's own reporting and any other
    # library's hook keep working.
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        if exc_type is SystemExit:
            previous(exc_type, exc_value, exc_tb)
            return
        record_crash("unhandled_exception", f"{exc_type.__name__}: {exc_value}",
                     traceback_text="".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
                     fields={"where": "sys.excepthook"})
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
    previous_thread = threading.excepthook

    def _thread_hook(args):
        record_crash("thread_exception", f"{args.exc_type.__name__}: {args.exc_value}",
                     traceback_text="".join(traceback.format_exception(
                         args.exc_type, args.exc_value, args.exc_traceback)),
                     fields={"where": "threading.excepthook", "thread": args.thread.name})
        previous_thread(args)

    threading.excepthook = _thread_hook

    def _unraisable(args):
        record_crash("unraisable", f"{args.exc_type.__name__}: {args.exc_value}",
                     traceback_text="".join(traceback.format_exception(
                         args.exc_type, args.exc_value, args.exc_traceback)),
                     fields={"where": "sys.unraisablehook"})

    sys.unraisablehook = _unraisable
    _excepthooks_installed = True


def install_gevent_hook() -> None:
    """Log greenlet failures instead of letting the hub swallow them.

    gevent prints nothing for a greenlet that raises unless ``hub.handle_error``
    is set, so a background task dying looks exactly like a task that finished
    quietly. This is the hook that makes background crashes visible.
    """
    try:
        from gevent import hub as gevent_hub
    except ImportError:
        return

    def _handler(context, exc_type, exc_value, tb):
        record_crash("greenlet_exception", f"{exc_type.__name__}: {exc_value}",
                     traceback_text="".join(traceback.format_exception(exc_type, exc_value, tb)),
                     fields={"where": "gevent hub.handle_error",
                             "greenlet": getattr(context, "greenlet", None)})

    gevent_hub.get_hub().handle_error = _handler


# ── client-side errors ───────────────────────────────────────────────────────

def _clean(value: Any) -> Any:
    """Coerce and bound a value arriving from the browser."""
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= MAX_FIELD else text[:MAX_FIELD] + f"...<truncated {len(text) - MAX_FIELD}B>"


def record_client_error(payload: dict[str, Any], *, client_ip: str = "") -> str:
    """Log an error the frontend caught. Returns a short id for the reply.

    The browser cannot see stderr, so the webview reports here. A JS exception
    during render is one of the most common real failures in this app and used
    to leave no trace whatsoever outside of devtools.
    """
    payload = payload if isinstance(payload, dict) else {}
    kind = _clean(payload.get("kind") or "client_error")
    message = _clean(payload.get("message") or "<no message>")
    fields = {
        "kind": kind,
        "source": _clean(payload.get("source")),
        "line": _clean(payload.get("line")),
        "column": _clean(payload.get("column")),
        "url": _clean(payload.get("url")),
        "stack": _clean(payload.get("stack")),
        "client_ip": client_ip or _clean(payload.get("client_ip")),
    }
    record_crash(f"client_{kind}", message, traceback_text=fields.get("stack") or None,
                 fields={k: v for k, v in fields.items() if k not in ("kind", "stack")})
    log(f"client error: {message}", level=logging.ERROR, **{
        k: v for k, v in fields.items() if k in ("kind", "source", "line", "url")})
    return f"cl-{int(time.time() * 1000) % 100000000:08d}"


def diagnostics() -> dict[str, Any]:
    """Small snapshot for ``GET /stream/api/log/diagnostics``.

    Deliberately shallow: sizes, mtimes, tail of the crash log and the log path.
    It must be safe to hit while the app is broken, so it never parses more than
    the last few kilobytes and never touches the database.
    """
    out: dict[str, Any] = {"log_dir": LOG_DIR, "files": {}}
    for path in (APP_LOG, ERROR_LOG, CRASH_LOG):
        entry: dict[str, Any] = {"path": path, "exists": os.path.exists(path)}
        if entry["exists"]:
            try:
                stat = os.stat(path)
                entry["bytes"] = stat.st_size
                entry["mtime"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
                # Oldest rotation first, so the newest tail is the last line.
                rotated = sorted(
                    f"{path}.{i}" for i in range(1, BACKUP_COUNT + 1) if os.path.exists(f"{path}.{i}"))
                entry["rotations"] = rotated
            except OSError as exc:
                entry["error"] = str(exc)
        out["files"][os.path.basename(path)] = entry

    try:
        if os.path.exists(ERROR_LOG):
            with open(ERROR_LOG, "r", encoding="utf-8", errors="replace") as handle:
                out["recent_errors"] = handle.readlines()[-40:]
        else:
            out["recent_errors"] = []
    except OSError as exc:
        out["recent_errors"] = [f"<unreadable: {exc}>"]

    try:
        if os.path.exists(CRASH_LOG):
            with open(CRASH_LOG, "r", encoding="utf-8", errors="replace") as handle:
                blocks = handle.read().split("=" * 78)
            out["recent_crashes"] = [b.strip() for b in blocks[-4:] if b.strip()]
        else:
            out["recent_crashes"] = []
    except OSError as exc:
        out["recent_crashes"] = [f"<unreadable: {exc}>"]

    out["pid"] = os.getpid()
    return out
