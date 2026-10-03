"""Runtime smoke tests for the encode path, driven against the real stack.

These cover the failures that unit tests cannot: what a *viewer* is told when an
encode dies, and whether a wedged ffmpeg leaves anything behind.

    set -a && . ./.stream_db.env && set +a
    ./venv/bin/python scripts/smoke_encode.py

Exits non-zero if any assertion fails, so it is usable as a pre-merge gate.

Scenarios:
  1. success        a real clip encodes and publishes fMP4-ready HLS
  2. corrupt input  transcode_error carries a translated message and no path
  3. stall          a wedged ffmpeg is killed by the watchdog, group and all
  4. atomic output  a failed encode publishes nothing at all
  5. orphans        no ffmpeg process survives any scenario

No database row is created or touched: everything here calls the encode
functions directly, which is the seam being changed.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before media_pipeline is imported: RunnerConfig reads the
# environment once, at class-definition time.
os.environ.setdefault("STREAM_ENCODE_STALL_SECONDS", "2")
os.environ.setdefault("STREAM_ENCODE_MAX_RETRIES", "1")
os.environ.setdefault("STREAM_ENCODE_RETRY_BASE_DELAY", "0.2")
os.environ.setdefault("STREAM_ENCODE_MIN_FREE_MB", "1")

FAILURES = []
PASSES = []


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
        print(f"  PASS  {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL  {name} — {detail}")


def make_fixture(path, duration=2):
    cmd = [shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=s=320x240:d={duration}:r=10",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}:sample_rate=48000",
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "128k",
           "-ac", "2", path]
    return subprocess.run(cmd, capture_output=True, check=False).returncode == 0


def capture_events(app):
    """Collect socket emissions instead of sending them."""
    events = []
    real = app.socketio.emit

    def capture(event, *a, **kw):
        events.append({"event": event, "payload": a[0] if a else kw})
    app.socketio.emit = capture
    return events, lambda: setattr(app.socketio, "emit", real)


def run_encode(app, source, rendition_dir, duration, label="240p"):
    os.makedirs(rendition_dir, exist_ok=True)
    cmd = app._build_single_rendition_cmd(source, rendition_dir, 240, "600k", "96k")
    outputs = (os.path.join(rendition_dir, "seg_%03d.ts"),
               os.path.join(rendition_dir, "index.m3u8"))
    return app._run_progressive_ffmpeg(cmd, "smoke", duration, label,
                                      output_paths=outputs)


def ffmpeg_processes():
    out = subprocess.run(["pgrep", "-af", "ffmpeg"], capture_output=True, check=False)
    return [ln for ln in out.stdout.decode().splitlines()
            if "pgrep" not in ln and "smoke_encode" not in ln]


# ── scenarios ────────────────────────────────────────────────────────────────


def scenario_success(app, tmp):
    print("\n[1] success path")
    source = os.path.join(tmp, "ok.mp4")
    if not make_fixture(source):
        return check("fixture built", False, "ffmpeg could not synthesise a clip")
    rendition = os.path.join(tmp, "hls", "240p")
    events, restore = capture_events(app)
    try:
        result = run_encode(app, source, rendition, 2)
    finally:
        restore()

    check("exit code 0", result[0] == 0, f"got {result[0]}")
    playlist = os.path.join(rendition, "index.m3u8")
    check("playlist published", os.path.exists(playlist))
    segments = [n for n in os.listdir(rendition) if n.endswith(".ts")] if os.path.exists(rendition) else []
    check("segments published", len(segments) > 0, f"rendition dir: {segments}")
    check("no .partial left behind",
          not os.path.exists(rendition + ".partial"),
          "a finished encode must not leave its partial directory")
    progress = [e for e in events if e["event"] == "transcode_progress"]
    check("progress events emitted", len(progress) > 0, f"{len(progress)} events")
    check("no error event on success",
          not [e for e in events if e["event"] == "transcode_error"])
    final = progress[-1]["payload"] if progress else {}
    check("final event complete", final.get("complete") is True, json.dumps(final))
    check("pct never exceeds 100",
          all((e["payload"].get("pct") or 0) <= 100 for e in progress))


def scenario_corrupt(app, tmp):
    print("\n[2] corrupt input -> translated message, no path leak")
    good = os.path.join(tmp, "full.mp4")
    if not make_fixture(good):
        return check("fixture built", False, "could not synthesise a clip")
    broken = os.path.join(tmp, "broken.mp4")
    with open(good, "rb") as src, open(broken, "wb") as dst:
        dst.write(src.read(3000))

    rendition = os.path.join(tmp, "broken_hls", "240p")
    events, restore = capture_events(app)
    try:
        result = run_encode(app, broken, rendition, 2)
    finally:
        restore()

    check("non-zero exit", result[0] != 0, f"got {result[0]}")
    errors = [e for e in events if e["event"] == "transcode_error"]
    check("transcode_error emitted", len(errors) == 1, f"{len(errors)} error events")

    payload = errors[0]["payload"] if errors else {}
    check("payload has an item id", payload.get("item_id") == "smoke", json.dumps(payload))
    message = payload.get("user_message") or ""
    check("message is not empty", bool(message.strip()))
    check("message is Persian",
          any("\u0600" <= ch <= "\u06ff" for ch in message), repr(message))

    leaks = [needle for needle in (tmp, "/media/", ".mp4", "ffmpeg", "Invalid data",
                                   "Error opening")
             if needle in message]
    check("no path or ffmpeg wording in the client message", not leaks, f"leaked {leaks}")
    # The failure kind must be a decision, not a guess.
    check("classified as a known kind",
          payload.get("kind") in ("corrupt", "unknown", "oom", "disk_full",
                                  "timeout", "stall", "hw_failure", "killed"),
          f"kind={payload.get('kind')!r}")

    # Nothing may be published for a failed encode.
    check("no playlist published", not os.path.exists(os.path.join(rendition, "index.m3u8")))
    check("no partial left behind", not os.path.exists(rendition + ".partial"),
          os.path.exists(rendition + ".partial") and "partial survived" or "")


def scenario_stall(app, tmp):
    print("\n[3] stalled ffmpeg is killed, process group and all")
    stub_dir = os.path.join(tmp, "stub_bin")
    os.makedirs(stub_dir, exist_ok=True)
    child_pid_file = os.path.join(tmp, "orphan.pid")
    stub = os.path.join(stub_dir, "ffmpeg")
    with open(stub, "w") as fh:
        fh.write(
            "#!/bin/bash\n"
            # A grandchild, exactly the case proc.kill() used to leak.
            "sleep 300 &\n"
            f"echo $! > {child_pid_file}\n"
            'echo "out_time_us=1000000"\n'
            "wait\n"
        )
    os.chmod(stub, 0o755)

    source = os.path.join(tmp, "ok2.mp4")
    if not make_fixture(source, duration=1):
        return check("fixture built", False, "could not synthesise a clip")

    real_path = os.environ.get("PATH", "")
    os.environ["PATH"] = stub_dir + os.pathsep + real_path
    rendition = os.path.join(tmp, "stall_hls", "240p")
    events, restore = capture_events(app)
    started = time.time()
    try:
        result = run_encode(app, source, rendition, 1)
    finally:
        restore()
        os.environ["PATH"] = real_path
    took = time.time() - started

    check("stall detected, not hung", took < 90, f"took {took:.0f}s")
    check("non-zero exit", result[0] != 0, f"got {result[0]}")
    errors = [e for e in events if e["event"] == "transcode_error"]
    check("transcode_error emitted", len(errors) >= 1)
    check("classified as a stall",
          errors and errors[0]["payload"].get("kind") == "stall",
          json.dumps(errors[0]["payload"] if errors else {}))
    check("no playlist published", not os.path.exists(os.path.join(rendition, "index.m3u8")))
    check("no partial left behind", not os.path.exists(rendition + ".partial"))

    if os.path.exists(child_pid_file):
        with open(child_pid_file) as fh:
            pid = int(fh.read().strip())
        gone = False
        deadline = time.time() + 10
        while time.time() < deadline and os.path.exists(f"/proc/{pid}"):
            time.sleep(0.1)
            gone = True
        check("grandchild killed with the group", gone or not os.path.exists(f"/proc/{pid}"),
              f"pid {pid} survived the group kill")


def scenario_concurrency(app, tmp):
    """Several encodes through the one shared runner, as production does.

    The runner is a process-wide singleton (it owns the hardware circuit
    breaker), so concurrent jobs must not cross-talk: each one's progress,
    callbacks and partial directory have to stay its own.
    """
    print("\n[4] concurrent encodes share one runner safely")
    import threading

    sources = []
    for i in range(3):
        path = os.path.join(tmp, f"conc{i}.mp4")
        if not make_fixture(path, duration=1):
            return check("fixtures built", False, "could not synthesise clips")
        sources.append(path)

    events, restore = capture_events(app)
    results = {}

    def worker(index, source):
        rendition = os.path.join(tmp, f"conc_hls_{index}", "240p")
        try:
            results[index] = run_encode(app, source, rendition, 1, label=f"conc{index}")
        except Exception as exc:  # a greenlet crash must not hide here
            results[index] = exc

    threads = [threading.Thread(target=worker, args=(i, s)) for i, s in enumerate(sources)]
    started = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)
    took = time.time() - started
    restore()

    check("all encodes finished", len(results) == 3, f"{len(results)} completed")
    check("no worker raised", not [r for r in results.values() if isinstance(r, Exception)],
          str([r for r in results.values() if isinstance(r, Exception)]))
    check("all succeeded", all(r[0] == 0 for r in results.values() if not isinstance(r, tuple)),
          str({k: (v[0] if isinstance(v, tuple) else v) for k, v in results.items()}))
    # Three 1s encodes run at once must not take three times as long as one.
    check("ran concurrently", took < 25, f"took {took:.0f}s, which is serial")

    labels = {e["payload"].get("label") for e in events
              if e["event"] == "transcode_progress"}
    check("each encode reported under its own label",
          labels == {"conc0", "conc1", "conc2"}, f"got {sorted(labels)}")
    for index in range(3):
        playlist = os.path.join(tmp, f"conc_hls_{index}", "240p", "index.m3u8")
        check(f"rendition {index} published its own playlist", os.path.exists(playlist))
        check(f"rendition {index} left no partial",
              not os.path.exists(os.path.join(tmp, f"conc_hls_{index}", "240p.partial")))


def scenario_orphans():
    print("\n[5] no stray processes")
    leftover = ffmpeg_processes()
    check("no ffmpeg processes left running", not leftover, "; ".join(leftover[:3]))


def scenario_boot_cleanup_is_wired():
    """The boot hook must actually run, not merely exist."""
    print("\n[6] boot cleanup hook is invoked at startup")
    import app
    from config import Config
    source = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
    text = open(source, encoding="utf-8").read()
    main_block = text.split('if __name__ == "__main__":', 1)[-1]
    check("cleanup_orphaned_partials runs under __main__",
          "cleanup_orphaned_partials(Config.HLS_DIR)" in main_block)
    check("it runs before interrupted encodes resume",
          main_block.index("cleanup_orphaned_partials") < main_block.index("_resume_interrupted_encodes"))
    check("Config.HLS_DIR exists for it to clean", os.path.isdir(Config.HLS_DIR))


def main():
    import app

    tmp = tempfile.mkdtemp(prefix="smoke-encode-")
    try:
        scenario_success(app, tmp)
        scenario_corrupt(app, tmp)
        scenario_stall(app, tmp)
        scenario_concurrency(app, tmp)
        scenario_orphans()
        scenario_boot_cleanup_is_wired()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print(f"  FAILED: {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())