"""archive_solver speaks to the sidecar over loopback and fails closed.

A real loopback HTTP server stands in for the sidecar so the adapter's shape
checking, error collapsing and proxy immunity are exercised against actual
sockets, not mocks.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from archive_solver import fetch_clearance

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:156.0) Gecko/20100101 Firefox/156.0"


class _SidecarStub(BaseHTTPRequestHandler):
    mode = "ok"

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        responses = {
            "ok": (200, {"ok": True, "cookies": {"cf_clearance": "tok-1"}, "user_agent": BROWSER_UA}),
            "busy": (503, {"ok": False, "error": "busy"}),
            "timeout": (504, {"ok": False, "error": "solve_timeout"}),
            "refused_token": (200, {"ok": False, "error": "solve_timeout"}),
            "empty_cookies": (200, {"ok": True, "cookies": {}, "user_agent": BROWSER_UA}),
            "no_user_agent": (200, {"ok": True, "cookies": {"cf_clearance": "tok-1"}, "user_agent": ""}),
        }
        if self.mode == "garbage":
            body, status = b"definitely not json", 200
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
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SidecarStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)


def test_successful_solve_returns_cookies_and_user_agent(sidecar):
    assert fetch_clearance(sidecar, "https://digimoviez.test/") == (
        {"cf_clearance": "tok-1"}, BROWSER_UA)


@pytest.mark.parametrize("mode", ["busy", "timeout", "refused_token", "empty_cookies",
                                  "no_user_agent", "garbage"])
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
