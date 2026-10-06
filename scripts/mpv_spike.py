#!/usr/bin/env python3
"""Phase 1A spike — can we drive mpv as a *separate process* over IPC?

Answers, with evidence, the questions ADR 0002 has to answer:

  1. Can we spawn mpv with --input-ipc-server (unix socket) and talk JSON to it?
  2. Do we get `time-pos`, `pause`, `paused-for-cache`, `core-idle` and seeking?
  3. Can we *send* seek / pause / speed and see the effect?
  4. Does the process survive our protocol (clean shutdown, no zombies)?

Default run is headless (`--vo=null --ao=null`) so it works in CI. Pass
`--window` once to confirm a real window can be opened on this machine (the
phase-1 gate wants a real mpv window at least once).

Usage:
    python scripts/mpv_spike.py            # headless observations
    python scripts/mpv_spike.py --window   # also open a real window briefly

Writes `docs/spike/mpv-ipc-observations.json` (and prints it).
"""
from __future__ import annotations

import argparse
import http.server
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


# ── tiny IPC client (JSON lines over a unix socket) ──────────────────────────
class MpvIPC:
    def __init__(self, path: str, timeout: float = 10.0):
        self.path = path
        self.timeout = timeout
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        deadline = time.time() + timeout
        last = None
        while True:
            try:
                self.sock.connect(path)
                break
            except (FileNotFoundError, ConnectionRefusedError) as e:
                last = e
                if time.time() > deadline:
                    raise TimeoutError(f"mpv IPC socket {path} not up: {last}") from e
                time.sleep(0.05)
        self.buf = b""
        self.next_id = 1
        self.events: list[dict] = []

    def send(self, command: list) -> int:
        rid = self.next_id
        self.next_id += 1
        payload = json.dumps({"command": command, "request_id": rid}).encode() + b"\n"
        self.sock.sendall(payload)
        return rid

    def request(self, command: list, want_id: int | None = None) -> dict:
        """Send a command and read replies until its response arrives.

        Property-change events that arrive in between are collected, not
        dropped — they are the point of the spike.
        """
        want = self.send(command) if want_id is None else want_id
        while True:
            msg = self._read()
            if msg.get("request_id") == want and ("error" in msg or "data" in msg):
                return msg
            if msg.get("event"):
                self.events.append(msg)

    def observe(self, name: str) -> None:
        self.request(["observe_property", self.next_id, name])

    def wait_for_event(self, name: str, timeout: float = 10.0) -> dict | None:
        return self.wait_for(lambda ev: ev.get("event") == name, timeout)

    def wait_for(self, predicate, timeout: float = 10.0) -> dict | None:
        """Return the first already-buffered or newly-arrived event matching
        `predicate`, else None. mpv reports observed properties as
        `property-change` events carrying `name`/`data`, not as bare events."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for i, ev in enumerate(self.events):
                if predicate(ev):
                    return self.events.pop(i)
            try:
                self.sock.settimeout(max(0.05, deadline - time.time()))
                msg = self._read()
                if predicate(msg):
                    return msg
                if msg.get("event"):
                    self.events.append(msg)
            except (socket.timeout, TimeoutError):
                break
        return None

    def _read(self) -> dict:
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("mpv closed the IPC socket")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def make_test_file(dest: Path) -> Path:
    """A real container with video + audio, built by ffmpeg if needed."""
    if dest.exists():
        return dest
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found; cannot build the spike fixture")
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=20:size=320x240:rate=24",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         # moov at the front: without it mpv cannot demux until the throttled
         # server has shipped the whole file, and playback never starts.
         "-movflags", "+faststart",
         "-c:a", "aac", "-shortest", str(dest)],
        check=True,
    )
    return dest


def spawn_mpv(socket_path: str, media: str, window: bool) -> subprocess.Popen:
    cmd = [
        "mpv",
        f"--input-ipc-server={socket_path}",
        "--idle=yes",
        "--osc=no",
        "--no-terminal",
        "--force-window=no" if not window else "--force-window=yes",
        "--keep-open=yes",
    ]
    if not window:
        cmd += ["--vo=null", "--ao=null"]
    cmd.append(str(media))
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _ThrottledHandler(http.server.SimpleHTTPRequestHandler):
    """Serves one file at a fixed rate so a local mpv *has* to buffer."""

    # Below the fixture's video bitrate on purpose: playback must catch up
    # with the download and starve, otherwise paused-for-cache never fires.
    rate = 12 * 1024          # bytes per second
    chunk = 4 * 1024
    directory = "."

    def log_message(self, *args):  # silence
        pass

    def copyfile(self, source, outputfile):
        while True:
            buf = source.read(self.chunk)
            if not buf:
                break
            try:
                outputfile.write(buf)
            except (ConnectionResetError, BrokenPipeError):
                return          # mpv dropped the connection; nothing to report
            time.sleep(self.chunk / self.rate)


def start_throttled_server(path: Path):
    handler = lambda *a, **kw: _ThrottledHandler(*a, directory=str(path.parent), **kw)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/{path.name}"


def buffering_check(media: Path) -> dict:
    """Play the file over a deliberately slow local HTTP server and catch the
    paused-for-cache property-change edge (Buffering(true) in PlayerPort)."""
    tmp = Path(tempfile.mkdtemp(prefix="mpv-spike-buf-"))
    sock = str(tmp / "mpv.sock")
    srv, url = start_throttled_server(media)
    proc = spawn_mpv(sock, url, window=False)
    result: dict = {"source": "throttled-local-http", "url": url}
    try:
        ipc = MpvIPC(sock)
        ipc.observe("paused-for-cache")
        ipc.observe("core-idle")
        ipc.wait_for_event("file-loaded", timeout=20)
        # 60s of media at 64 KB/s cannot be read in one go: mpv must stall.
        ev = ipc.wait_for(
            lambda e: e.get("event") == "property-change"
            and e.get("name") == "paused-for-cache" and e.get("data") is True,
            timeout=25,
        )
        result["paused_for_cache_event"] = ev
        result["got_buffering_edge"] = ev is not None
        # …and the edge back to False when the cache refills. Only meaningful
        # if we actually saw True first: observe_property emits the current
        # value on subscription, which would otherwise fake a "cleared".
        ev2 = ipc.wait_for(
            lambda e: e.get("event") == "property-change"
            and e.get("name") == "paused-for-cache" and e.get("data") is False,
            timeout=25,
        ) if ev is not None else None
        result["buffering_cleared"] = ev2 is not None
        ipc.request(["quit", 0])
        ipc.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", action="store_true",
                    help="open a real mpv window instead of --vo=null")
    ap.add_argument("--out", default=str(REPO / "docs" / "spike" / "mpv-ipc-observations.json"))
    args = ap.parse_args()

    obs: dict = {"mpv_version": subprocess.run(["mpv", "--version"], capture_output=True,
                                               text=True).stdout.splitlines()[0],
                 "mode": "window" if args.window else "headless",
                 "checks": {}}

    tmp = Path(tempfile.mkdtemp(prefix="mpv-spike-"))
    media = make_test_file(tmp / "spike.mp4")
    sock = str(tmp / "mpv.sock")
    proc = spawn_mpv(sock, str(media), window=args.window)
    try:
        ipc = MpvIPC(sock)
        obs["checks"]["spawn_and_connect"] = True

        # Properties we need for the player port.
        for prop in ("time-pos", "pause", "paused-for-cache", "core-idle",
                     "duration", "speed", "filename", "eof-reached"):
            ipc.observe(prop)
        # 1) file loaded (we spawned with a file, so it arrives without a command)
        loaded = ipc.wait_for_event("file-loaded", timeout=15)
        obs["checks"]["file_loaded_event"] = loaded is not None

        # 2) time-pos ticks
        t0 = ipc.request(["get_property", "time-pos"])
        time.sleep(1.0)
        t1 = ipc.request(["get_property", "time-pos"])
        obs["checks"]["time_pos_ticks"] = (
            t0.get("data") is not None and t1.get("data") is not None
            and t1["data"] > t0["data"]
        )
        obs["time_pos_sample"] = {"first": t0.get("data"), "after_1s": t1.get("data")}

        # 3) pause / unpause round-trip
        ipc.request(["set_property", "pause", True])
        time.sleep(0.3)
        paused = ipc.request(["get_property", "pause"])["data"]
        ipc.request(["set_property", "pause", False])
        time.sleep(0.3)
        resumed = ipc.request(["get_property", "pause"])["data"]
        obs["checks"]["pause_roundtrip"] = paused is True and resumed is False
        obs["pause_observed_event"] = any(e.get("event") == "property-change"
                                          and e.get("name") == "pause" and e.get("data") is True
                                          for e in ipc.events)

        # 4) seek + UserSeek-equivalent (seek event + time-pos change)
        ipc.events.clear()
        ipc.request(["seek", 12.0, "absolute+exact"])
        time.sleep(0.5)
        pos = ipc.request(["get_property", "time-pos"])["data"]
        seek_ev = ipc.wait_for_event("seek", timeout=3)
        obs["checks"]["seek_absolute"] = abs(pos - 12.0) < 1.5
        obs["checks"]["seek_event"] = seek_ev is not None
        obs["seek_result_pos"] = pos

        # 5) speed
        ipc.request(["set_property", "speed", 2.0])
        spd = ipc.request(["get_property", "speed"])["data"]
        obs["checks"]["speed_set"] = spd == 2.0
        ipc.request(["set_property", "speed", 1.0])

        # 6) paused-for-cache / core-idle are observable properties (values may
        #    be False on a healthy local file — the point is we can read them).
        pfc = ipc.request(["get_property", "paused-for-cache"])
        idle = ipc.request(["get_property", "core-idle"])
        obs["checks"]["paused_for_cache_readable"] = pfc.get("error") == "success"
        obs["checks"]["core_idle_readable"] = idle.get("error") == "success"
        obs["paused_for_cache_value"] = pfc.get("data")
        obs["core_idle_value"] = idle.get("data")

        # buffering while playing: `core-idle` goes True when the demuxer has
        # nothing to give the decoder, which is what a Buffering(bool) port
        # event has to be built from.
        ipc.events.clear()
        ipc.request(["set_property", "pause", True])
        time.sleep(0.2)
        idle_after_pause = ipc.request(["get_property", "core-idle"])
        ipc.request(["set_property", "pause", False])
        obs["core_idle_when_paused"] = idle_after_pause.get("data")
        obs["checks"]["core_idle_reflects_pause"] = idle_after_pause.get("data") is True

        # 7) buffering: starve the demuxer cache and watch paused-for-cache
        #    flip to True as a property-change event — that is exactly the
        #    Buffering(true) edge the PlayerPort needs.
        ipc.request(["set_property", "demuxer-max-bytes", 150 * 1024 * 1024])
        ipc.request(["set_property", "demuxer-readahead-secs", 1.0])

        # 8) shutdown: quit and reap, no zombie
        ipc.request(["quit", 0])
        ipc.close()
        proc.wait(timeout=10)
        obs["checks"]["clean_quit"] = proc.returncode is not None
        obs["checks"]["no_zombie"] = proc.poll() is not None
        obs["mpv_exit_code"] = proc.returncode
        obs["events_seen"] = sorted({e.get("event") for e in ipc.events if e.get("event")})

        # 7) buffering edge, on a source that is actually slow: a throttled
        #    local HTTP server. This is the Buffering(true)/Buffering(false)
        #    pair the PlayerPort has to expose.
        buf = buffering_check(media)
        obs["buffering"] = buf
        obs["checks"]["paused_for_cache_event_possible"] = bool(buf.get("got_buffering_edge"))
        obs["checks"]["buffering_edge_clears"] = bool(buf.get("buffering_cleared"))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        shutil.rmtree(tmp, ignore_errors=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(obs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(obs, indent=2, ensure_ascii=False))
    failed = [k for k, v in obs["checks"].items() if not v]
    if failed:
        print(f"\nFAILED CHECKS: {failed}", file=sys.stderr)
        return 1
    print(f"\nOK — wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
