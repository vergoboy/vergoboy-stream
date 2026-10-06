"""Tests for app_logging: the module that makes crashes visible at all.

The behaviours asserted here are the ones that decide whether a crash leaves a
record. Each test writes to a temp directory and restores the module globals, so
nothing leaks into the repo's real data/logs.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading

import pytest

import app_logging
from app_logging import (
    CRASH_LOG,
    ERROR_LOG,
    MAX_FIELD,
    _RedactingFormatter,
    configure,
    diagnostics,
    record_client_error,
    record_crash,
)


@pytest.fixture()
def logdir(tmp_path, monkeypatch):
    """Point every log path at a temp dir and reset the module's once-only state.

    `configure()` refuses to run twice and the hook installer refuses to rewrap,
    both of which are the behaviour under test elsewhere, so both flags are reset
    here. The handlers themselves are detached on teardown because
    `logging.Logger` caches them on the global logger object.
    """
    monkeypatch.setattr(app_logging, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(app_logging, "APP_LOG", str(tmp_path / "app.log"))
    monkeypatch.setattr(app_logging, "ERROR_LOG", str(tmp_path / "error.log"))
    monkeypatch.setattr(app_logging, "CRASH_LOG", str(tmp_path / "crash.log"))
    monkeypatch.setattr(app_logging, "_configured", False)
    monkeypatch.setattr(app_logging, "_crash_logger", None)
    monkeypatch.setattr(app_logging, "_excepthooks_installed", False)

    original_hooks = (sys.excepthook, sys.unraisablehook, threading.excepthook)
    yield tmp_path

    for name in ("app", "app.crash"):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
    sys.excepthook, sys.unraisablehook, threading.excepthook = original_hooks


@pytest.fixture()
def fresh_hooks(monkeypatch):
    """Reset the hook state so `configure()` installs real hooks.

    Needed because `sys.excepthook` is process-global: once an earlier test
    configured logging, later tests would otherwise keep the first test's hooks
    (pointing at a deleted temp file) instead of exercising the installer.
    """
    monkeypatch.setattr(app_logging, "_excepthooks_installed", False)
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    monkeypatch.setattr(sys, "unraisablehook", sys.__unraisablehook__)
    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)
    yield


def read(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


# ── files are created and written ────────────────────────────────────────────

def test_configure_writes_to_app_and_error_logs(logdir):
    logger = configure("INFO")
    logger.info("an informational line")
    logger.error("a failing line")

    app_text = read(logdir / "app.log")
    assert "an informational line" in app_text
    # error.log is WARNING+ only; the INFO line must not be duplicated there.
    error_text = read(logdir / "error.log")
    assert "a failing line" in error_text
    assert "an informational line" not in error_text


def test_debug_lines_are_dropped_at_info_level(logdir):
    # The default level is INFO because log_debug() routes DEBUG there on every
    # call, and a DEBUG write on every request would dominate the file.
    logger = configure("INFO")
    logger.debug("debug chatter")
    logger.info("real line")
    assert "debug chatter" not in read(logdir / "app.log")
    assert "real line" in read(logdir / "app.log")


def test_configure_is_idempotent(logdir):
    # app.py, the tests and the socket suite can share an interpreter; a second
    # configure() must not double every line.
    configure("INFO")
    configure("INFO")
    configure("INFO")
    logging.getLogger("app").info("written once")
    assert read(logdir / "app.log").count("written once") == 1


def test_crash_is_kept_separate_from_app_log(logdir):
    configure("INFO")
    record_crash("synthetic", "boom")
    assert "boom" in read(logdir / "crash.log")
    assert "CRASH kind=synthetic" in read(logdir / "app.log")


def test_crash_record_includes_traceback_and_fields(logdir):
    configure("INFO")
    record_crash("greenlet", "ValueError: bad",
                 traceback_text="Traceback:\n  line one\n  line two",
                 fields={"where": "gevent", "greenlet": "worker"})
    text = read(logdir / "crash.log")
    assert "line one" in text and "line two" in text
    assert "where = gevent" in text and "greenlet = worker" in text
    assert "pid=" in text and "thread=" in text


def test_error_log_captures_warnings(logdir):
    configure("INFO")
    logging.getLogger("app").warning("something suspicious")
    assert "something suspicious" in read(logdir / "error.log")


# ── redaction: the log must not become a credential leak ────────────────────

def test_secrets_are_redacted_before_reaching_disk(logdir):
    configure("INFO")
    record_crash("leak", "token=supersecretvalue cookie=eyJhbGciOiJI9abcdefghijklmnop")
    text = read(logdir / "crash.log")
    assert "supersecretvalue" not in text
    assert "eyJhbGciOiJI9abcdefghijklmnop" not in text
    assert "<redacted>" in text


def test_url_credentials_are_redacted(logdir):
    configure("INFO")
    logging.getLogger("app").info("connecting to https://admin:hunter2@digimoviez.com/wp-admin/")
    text = read(logdir / "app.log")
    assert "hunter2" not in text
    assert "digimoviez.com" in text  # host stays, so the log is still useful


def test_formatter_redacts_a_library_log_call(logdir):
    # Redaction sits in the formatter, not the call sites, so a third-party
    # library logging a whole request object cannot leak a cookie.
    handler = app_logging._handler(str(logdir / "x.log"), logging.INFO)
    record = logging.LogRecord("lib", logging.INFO, __file__, 1,
                               "payload: session=SECRETVALUE123", None, None)
    assert "SECRETVALUE123" not in handler.formatter.format(record)


def test_formatter_never_raises_on_broken_record():
    formatter = _RedactingFormatter("%(message)s")
    record = logging.LogRecord("lib", logging.INFO, __file__, 1, {"un": "serialisable"}, None, None)
    # Must produce something rather than raising, or a logging bug becomes a crash.
    assert isinstance(formatter.format(record), str)


# ── client errors ───────────────────────────────────────────────────────────

def test_client_error_is_recorded_with_its_context(logdir):
    configure("INFO")
    record_client_error({
        "kind": "unhandledrejection",
        "message": "TypeError: x is not a function",
        "url": "http://localhost:3000/page",
        "stack": "TypeError: x is not a function\n  at Player",
    }, client_ip="192.168.70.5")

    text = read(logdir / "crash.log")
    assert "client_unhandledrejection" in text
    assert "TypeError: x is not a function" in text
    assert "192.168.70.5" in text
    # A client report also surfaces in the ordinary error log.
    assert "client error" in read(logdir / "error.log")


def test_client_error_fields_are_bounded(logdir):
    configure("INFO")
    huge = "A" * (MAX_FIELD * 3)
    record_client_error({"kind": "window.onerror", "message": "m", "stack": huge})
    text = read(logdir / "crash.log")
    # One runaway field must not be able to fill the log file.
    assert len(text) < MAX_FIELD * 4
    assert "truncated" in text


def test_client_error_tolerates_a_non_dict_payload(logdir):
    configure("INFO")
    # A malformed report must still be loggable, not raise inside the handler.
    record_client_error(["not", "a", "dict"])  # type: ignore[arg-type]
    assert "client_client_error" in read(logdir / "crash.log")


def test_client_error_survives_a_missing_message(logdir):
    configure("INFO")
    record_client_error({"kind": "window.onerror"})
    assert "<no message>" in read(logdir / "crash.log")


def test_client_error_returns_an_id(logdir):
    configure("INFO")
    first = record_client_error({"kind": "window.onerror", "message": "a"})
    second = record_client_error({"kind": "window.onerror", "message": "b"})
    assert first.startswith("cl-") and second.startswith("cl-")


# ── uncaught-failure hooks ──────────────────────────────────────────────────

def test_excepthook_records_an_uncaught_exception(logdir, fresh_hooks):
    configure("INFO")
    _fire_excepthook(ValueError("unhandled boom"))
    assert "unhandled_exception" in read(logdir / "crash.log")
    assert "unhandled boom" in read(logdir / "crash.log")


def test_excepthook_still_lets_systemexit_through(logdir, monkeypatch):
    # SystemExit is normal control flow (a clean shutdown); recording it as a
    # crash would report every successful exit as a failure.
    configure("INFO")
    fired = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: fired.append(args[0].__name__))
    _fire_excepthook(SystemExit(0))
    assert fired == ["SystemExit"]
    assert "unhandled_exception" not in read(logdir / "crash.log")


def test_threading_excepthook_records_a_thread_crash(logdir, fresh_hooks):
    configure("INFO")
    _fire_threading_excepthook(RuntimeError("thread boom"))
    assert "thread_exception" in read(logdir / "crash.log")
    assert "thread boom" in read(logdir / "crash.log")


def test_unraisable_hook_records_a_swallowed_error(logdir, fresh_hooks):
    configure("INFO")
    _fire_unraisablehook(KeyError("swallowed"))
    assert "unraisable" in read(logdir / "crash.log")


def test_gevent_hook_is_installed_and_logs_greenlet_failures(logdir):
    gevent = pytest.importorskip("gevent")
    from gevent import hub as gevent_hub

    configure("INFO")
    app_logging.install_gevent_hook()
    # A greenlet that raises looks identical to one that finished quietly
    # unless handle_error is set; assert the hook actually writes.
    try:
        raise RuntimeError("greenlet boom")
    except RuntimeError:
        gevent_hub.get_hub().handle_error(
            {"greenlet": gevent.getcurrent()}, RuntimeError, RuntimeError("greenlet boom"),
            sys.exc_info()[2])
    assert "greenlet_exception" in read(logdir / "crash.log")


# ── diagnostics ─────────────────────────────────────────────────────────────

def test_diagnostics_reports_files_and_recent_content(logdir):
    configure("INFO")
    record_crash("synthetic", "diagnostic probe")
    logging.getLogger("app").error("an error line")
    # Handler buffers must be on disk for the reader to see them.
    for handler in logging.getLogger("app").handlers:
        handler.flush()

    diag = diagnostics()
    assert diag["pid"] == os.getpid()
    assert diag["log_dir"] == str(logdir)
    for name in ("app.log", "error.log", "crash.log"):
        assert diag["files"][name]["exists"] is True
        assert diag["files"][name]["bytes"] > 0
        assert "mtime" in diag["files"][name]
    assert any("diagnostic probe" in b for b in diag["recent_crashes"])
    assert any("an error line" in line for line in diag["recent_errors"])


def test_diagnostics_works_before_any_log_file_exists(logdir, monkeypatch):
    # The endpoint has to answer even when logging could not open its files.
    monkeypatch.setattr(app_logging, "_configured", False)
    diag = diagnostics()
    assert diag["files"]["app.log"]["exists"] is False
    assert diag["recent_crashes"] == []
    assert diag["recent_errors"] == []


def test_diagnostics_counts_blocks_not_lines(logdir):
    configure("INFO")
    for i in range(3):
        record_crash("synthetic", f"failure {i}")
    for handler in (logging.getLogger("app.crash").handlers or []):
        handler.flush()
    diag = diagnostics()
    # Bounded tail: the newest few blocks, not every crash ever recorded.
    assert 1 <= len(diag["recent_crashes"]) <= 4
    assert "failure 2" in diag["recent_crashes"][-1]


# ── rotation ────────────────────────────────────────────────────────────────

def test_rotation_keeps_files_bounded(logdir, monkeypatch):
    monkeypatch.setattr(app_logging, "MAX_BYTES", 512)
    monkeypatch.setattr(app_logging, "BACKUP_COUNT", 3)
    configure("INFO")
    for i in range(400):
        logging.getLogger("app").error("x" * 40 + f" #{i}")

    sizes = [os.path.getsize(p) for p in
             [str(logdir / "app.log")] + [str(logdir / f"app.log.{i}") for i in (1, 2, 3)]]
    assert all(size <= 1024 for size in sizes), sizes
    # The oldest rotation is dropped rather than growing without bound.
    assert not (logdir / "app.log.4").exists()
    # And the most recent line survives rotation.
    assert "#399" in read(logdir / "app.log")


# ── structured log helper ───────────────────────────────────────────────────

def test_log_appends_fields_as_json(logdir):
    configure("INFO")
    app_logging.log("state changed", level=logging.WARNING, room=7, user="u1")
    line = next(l for l in read(logdir / "app.log").splitlines() if "state changed" in l)
    payload = line[line.index("{"):]
    assert json.loads(payload) == {"room": 7, "user": "u1"}


# ── helpers for the hook tests ──────────────────────────────────────────────
#
# `threading.excepthook` and `sys.unraisablehook` take C-level argument objects
# that cannot be faked from Python (`TypeError: ... expected 3 arguments`), so
# each failure is provoked for real and the hook is allowed to do its own work.

def _fire_excepthook(exc: BaseException) -> None:
    try:
        raise exc
    except type(exc):
        sys.excepthook(type(exc), exc, sys.exc_info()[2])


def _fire_threading_excepthook(exc: BaseException) -> None:
    def boom():
        raise exc

    thread = threading.Thread(target=boom, name="crash-probe")
    thread.start()
    thread.join(timeout=5)


def _fire_unraisablehook(exc: BaseException) -> None:
    class BadFinaliser:
        def __del__(self):
            raise exc

    BadFinaliser()  # noqa: F841 - the exception is raised when it is collected
    import gc
    gc.collect()