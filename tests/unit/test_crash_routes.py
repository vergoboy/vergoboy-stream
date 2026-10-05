"""Route tests for the crash-logging endpoints.

`app_logging` itself is covered in test_app_logging.py. What is checked here is
the HTTP contract: the browser can only report through these two routes, so if
one of them 404s or 500s the diagnostics are worthless exactly when they are
needed.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

import app
import app_logging


@pytest.fixture()
def client(logdir):
    """Flask test client with logging redirected to a temp dir."""
    app.app.config["TESTING"] = True
    with app.app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def logdir(tmp_path, monkeypatch):
    """Re-point the *live* log handlers at a temp dir, then restore them.

    Patching `app_logging.LOG_DIR` alone is not enough: `configure()` ran when
    `app.py` was imported and each `FileHandler` already holds the real path, so
    a record would still be written into the repo's data/logs. The handlers are
    therefore torn down and rebuilt against the temp paths.
    """
    monkeypatch.setattr(app_logging, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(app_logging, "APP_LOG", str(tmp_path / "app.log"))
    monkeypatch.setattr(app_logging, "ERROR_LOG", str(tmp_path / "error.log"))
    monkeypatch.setattr(app_logging, "CRASH_LOG", str(tmp_path / "crash.log"))

    original_handlers = {name: list(logging.getLogger(name).handlers)
                         for name in ("app", "app.crash")}
    _detach_handlers()
    monkeypatch.setattr(app_logging, "_configured", False)
    monkeypatch.setattr(app_logging, "_crash_logger", None)
    app_logging.configure("INFO")

    yield tmp_path

    _detach_handlers()
    for name, handlers in original_handlers.items():
        for handler in handlers:
            logging.getLogger(name).addHandler(handler)
    monkeypatch.setattr(app_logging, "_configured", True)


def _detach_handlers() -> None:
    for name in ("app", "app.crash"):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def test_client_error_route_is_registered_and_reports(client):
    response = client.post("/stream/api/log/client", json={
        "kind": "unhandledrejection",
        "message": "TypeError: player is null",
        "stack": "TypeError: player is null\n    at Player.tsx:88",
        "url": "http://localhost:3000/room/1",
    })
    assert response.status_code == 201
    assert response.get_json()["ok"] is True
    assert response.get_json()["id"].startswith("cl-")

    body = read_log("crash.log")
    assert "client_unhandledrejection" in body
    assert "player is null" in body


def test_client_error_route_rejects_a_non_object_body(client):
    response = client.post("/stream/api/log/client", json=["nope"])
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_client_error_route_accepts_an_empty_body(client):
    # The reporter must never be the thing that throws; a malformed report
    # should still be accepted and logged as an empty one.
    response = client.post("/stream/api/log/client", json={})
    assert response.status_code == 201


def test_diagnostics_route_answers(client):
    response = client.get("/stream/api/log/diagnostics")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["log_dir"]
    assert set(payload["files"]) == {"app.log", "error.log", "crash.log"}
    assert isinstance(payload["recent_crashes"], list)


def test_diagnostics_route_reflects_a_recorded_crash(client):
    client.post("/stream/api/log/client", json={"kind": "window.onerror", "message": "visible now"})
    payload = client.get("/stream/api/log/diagnostics").get_json()
    assert any("visible now" in block for block in payload["recent_crashes"])


def test_diagnostics_route_survives_the_log_directory_being_missing(client, monkeypatch):
    # The endpoint has to answer even when logging could not open its files —
    # that is precisely the situation where someone needs to read diagnostics.
    monkeypatch.setattr(app_logging, "CRASH_LOG", str(logdir_path() / "gone" / "crash.log"))
    monkeypatch.setattr(app_logging, "ERROR_LOG", str(logdir_path() / "gone" / "error.log"))
    response = client.get("/stream/api/log/diagnostics")
    assert response.status_code == 200
    assert response.get_json()["recent_errors"] == []


def test_client_error_route_records_the_remote_address(client):
    client.post("/stream/api/log/client", json={"kind": "window.onerror", "message": "who am i"})
    body = read_log("crash.log")
    assert "client_ip = 127.0.0.1" in body


# ── request-level crashes ───────────────────────────────────────────────────
#
# These drive a route that raises on the real app rather than calling the error
# handler directly: what matters is whether an exception raised *inside a
# request* reaches the crash log, and invoking the handler by hand would prove
# only that the handler logs.

@pytest.fixture()
def crash_route():
    """Names of the throw routes registered at import time below."""
    yield ("/_test/raise", "/_test/raise-value")


def test_request_exception_is_logged_with_its_traceback(client, crash_route):
    response = client.get("/_test/raise-value")
    assert response.status_code == 500
    body = read_log("crash.log")
    assert "request_exception" in body
    assert "kaboom from a request" in body
    # A traceback is the point: the message alone rarely says where it came from.
    # It points at the raising line in the view, not at the errorhandler — the
    # handler runs after the traceback is already built, so its own frame is
    # never in it.
    assert "Traceback (most recent call last)" in body
    assert "_raise_value" in body
    assert "raise ValueError" in body


def test_request_exception_response_does_not_leak_internals(client, crash_route):
    # The traceback goes to the log, not to the caller.
    assert client.get("/_test/raise").get_json() == {"error": "internal error"}


def test_request_exception_records_method_and_path(client, crash_route):
    client.get("/_test/raise")
    body = read_log("crash.log")
    assert "method = GET" in body
    assert "path = /_test/raise" in body


def test_request_exception_appears_in_the_error_log(client, crash_route):
    client.get("/_test/raise")
    assert "request_exception" in read_log("error.log")


def test_throw_routes_belong_to_this_module_only():
    # Guards the module-level registration below: if these routes were ever
    # declared in app.py they would 500 in production on every call.
    registered_here = {
        r.endpoint for r in app.app.url_map.iter_rules() if r.rule.startswith("/_test/")
    }
    assert registered_here == {"test_raise", "test_raise_value"}
    source = Path(__file__).read_text(encoding="utf-8")
    assert "app.add_url_rule" in source


def test_client_error_route_is_not_guarded_by_auth(client):
    # The reporter runs before any login exists and must never be rejected:
    # an auth failure on this route would silently discard the crash report
    # that explains the auth failure.
    response = client.post("/stream/api/log/client", json={"kind": "window.onerror", "message": "x"})
    assert response.status_code == 201


@pytest.fixture(autouse=True)
def isolate_default_log_dir(tmp_path, monkeypatch):
    """Keep the shared XDG log dir out of the repo during the test run.

    `app.py` configures logging at import time using the real path, so without
    this every import would create files under ~/.local/state.
    """
    monkeypatch.setenv("STREAM_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(app_logging, "_default_log_dir", lambda: str(tmp_path / "logs"))
    yield


# Registered at import time: Flask refuses `add_url_rule` after the first
# request is handled, and a fixture is too late by then. Scoped to /_test/ and
# asserted to be absent from app.py by
# test_throw_routes_belong_to_this_module_only.
def _raise_division():
    raise ZeroDivisionError("division by zero")


def _raise_value():
    raise ValueError("kaboom from a request")


app.app.add_url_rule("/_test/raise", endpoint="test_raise",
                     view_func=_raise_division, methods=["GET"])
app.app.add_url_rule("/_test/raise-value", endpoint="test_raise_value",
                     view_func=_raise_value, methods=["GET"])


def logdir_path():
    return Path(app_logging.LOG_DIR)


def read_log(name: str) -> str:
    """Read a log file, flushing first.

    `RotatingFileHandler` writes on emit, but a record emitted from a request
    thread can still be sitting in a buffer when the assertion runs.
    """
    for handler in logging.getLogger("app.crash").handlers + logging.getLogger("app").handlers:
        handler.flush()
    path = logdir_path() / name
    return path.read_text(encoding="utf-8") if path.exists() else ""