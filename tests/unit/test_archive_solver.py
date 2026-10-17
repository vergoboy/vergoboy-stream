"""archive_solver speaks FlareSolverr's loopback API and fails closed.

A real loopback HTTP server stands in for the sidecar so the adapter's shape
checking, error collapsing and proxy immunity are exercised against actual
sockets, not mocks.  The pinned contract is FlareSolverr's ``POST /v1``:
``{"status": "ok", "solution": {"userAgent", "cookies": [...]}}`` on success,
and a ``cf_clearance`` cookie is required before anything is adopted.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from archive_solver import SOLVE_MAX_TIMEOUT_MS, fetch_clearance

BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/152.0.0.0 Safari/537.36")
TARGET = "https://digimoviez.test/"
PROXY = "http://127.0.0.1:10808"


def _solution(**overrides):
    payload = {
        "status": 200,
        "userAgent": BROWSER_UA,
        "cookies": [{"name": "cf_clearance", "value": "tok-1",
                     "domain": ".digimoviez.test", "path": "/", "expiry": 4_000_000_000}],
    }
    payload.update(overrides)
    return payload


def _solved(**overrides):
    return {"status": "ok", "message": "Challenge solved!", "solution": _solution(**overrides)}


class _SidecarStub(BaseHTTPRequestHandler):
    mode = "ok"
    received: list[dict] = []

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length") or 0)
        try:
            _SidecarStub.received.append(json.loads(self.rfile.read(length)))
        except ValueError:
            _SidecarStub.received.append({})
        responses = {
            "ok": (200, _solved()),
            "unsolved": (200, {"status": "error", "message": "Challenge not solved",
                               "solution": _solution(status=403)}),
            "no_clearance": (200, _solved(cookies=[{"name": "__cf_bm", "value": "x"}])),
            "empty_cookies": (200, _solved(cookies=[])),
            "no_user_agent": (200, _solved(userAgent="")),
            "unnamed_cookie": (200, _solved(cookies=[{"value": "x"}])),
            "timeout": (504, {"status": "error", "message": "Challenge solve timed out"}),
            "server_error": (500, {"error": "boom"}),
        }
        if self.mode == "garbage":
            body, status = b"definitely not json", 200
        elif self.mode == "list_body":
            body, status = b"[1, 2, 3]", 200
        else:
            status, payload = responses[self.mode]
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # keep pytest output clean
        pass


@pytest.fixture()
def sidecar():
    _SidecarStub.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SidecarStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)


def test_successful_solve_returns_cookies_and_user_agent(sidecar):
    assert fetch_clearance(sidecar, "https://digimoviez.test/") == (
        {"cf_clearance": "tok-1"}, BROWSER_UA)


def test_request_carries_the_target_and_flaresolverr_ceiling(sidecar):
    fetch_clearance(sidecar, "https://digimoviez.test/")
    assert _SidecarStub.received == [{"cmd": "request.get", "url": "https://digimoviez.test/",
                                      "maxTimeout": SOLVE_MAX_TIMEOUT_MS}]


def test_target_proxy_is_forwarded_to_the_sidecar(sidecar):
    """The sidecar's Chrome -- not this process -- reaches the walled site."""
    fetch_clearance(sidecar, "https://digimoviez.test/", PROXY)
    assert _SidecarStub.received[0]["proxy"] == {"url": PROXY}


def test_no_proxy_key_when_none_is_configured(sidecar):
    fetch_clearance(sidecar, "https://digimoviez.test/", "")
    assert "proxy" not in _SidecarStub.received[0]


@pytest.mark.parametrize("mode", ["unsolved", "no_clearance", "empty_cookies", "no_user_agent",
                                  "unnamed_cookie", "timeout", "server_error", "garbage",
                                  "list_body"])
def test_every_failure_mode_collapses_to_none(sidecar, monkeypatch, mode):
    monkeypatch.setattr(_SidecarStub, "mode", mode)
    assert fetch_clearance(sidecar, "https://digimoviez.test/") is None


def test_empty_gate_returns_none_without_any_socket():
    assert fetch_clearance("", "https://digimoviez.test/") is None


def test_unreachable_sidecar_returns_none():
    assert fetch_clearance("http://127.0.0.1:9", "https://digimoviez.test/") is None


def test_env_proxy_cannot_reroute_the_sidecar_call(sidecar, monkeypatch):
    """The sidecar is loopback-only: a stray HTTPS_PROXY must not reroute it."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    assert fetch_clearance(sidecar, "https://digimoviez.test/") == (
        {"cf_clearance": "tok-1"}, BROWSER_UA)
