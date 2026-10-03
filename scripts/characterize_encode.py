"""Characterization harness for the encode path.

Runs one short clip through whatever ``app._run_progressive_ffmpeg`` currently
is — the old hand-rolled implementation before the swap, the EncodeRunner-backed
one after — and records everything observable about the result. Run it before
and after the change and diff the JSON:

    ./venv/bin/python scripts/characterize_encode.py --out /tmp/before.json
    # ... swap the implementation ...
    ./venv/bin/python scripts/characterize_encode.py --out /tmp/after.json
    ./venv/bin/python scripts/characterize_encode.py --compare /tmp/before.json /tmp/after.json

The point is that "the runner swap did not change what gets produced" is a
measurement, not a claim. Socket emissions are captured rather than sent, no
database row is touched, and the fixture is synthesised so both runs see byte-
identical input.

Importing ``app`` is safe here: everything with side effects (server start,
orphan cleanup, resume-interrupted) lives behind ``if __name__ == "__main__"``.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

W, H, FPS, DURATION = 320, 240, 10, 2
HEIGHT = 240
VBR, ABR = "600k", "96k"


def make_fixture(path):
    """A deterministic clip: same bytes for every run, so outputs are comparable."""
    cmd = [
        shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:d={DURATION}:r={FPS}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={DURATION}:sample_rate=48000",
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-preset", "ultrafast", "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart", path,
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0 or not os.path.exists(path):
        raise SystemExit(f"could not build fixture: {proc.stderr.decode(errors='ignore')[-800:]}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe(playlist):
    """What ffprobe says about the finished presentation."""
    out = subprocess.run(
        [shutil.which("ffprobe"), "-v", "error", "-show_entries",
         "format=duration,format_name:stream=codec_name,codec_type,width,height,"
         "profile,pix_fmt,sample_rate,channels",
         "-of", "json", playlist],
        capture_output=True, check=False,
    )
    try:
        return json.loads(out.stdout.decode() or "{}")
    except ValueError:
        return {"error": out.stderr.decode(errors="ignore")[-400:]}


def normalize_playlist(text):
    """Playlist text minus anything legitimately run-to-run variable."""
    keep = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # EXTINF durations are stable for a fixed input, but the sequence
        # numbers and any date stamps are not meaningful to compare.
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME"):
            continue
        keep.append(line)
    return keep


def snapshot_output(rendition_dir):
    """Everything on disk that a viewer could receive."""
    snap = {"files": [], "playlist_lines": [], "total_bytes": 0}
    playlist = os.path.join(rendition_dir, "index.m3u8")
    if not os.path.exists(playlist):
        snap["missing_playlist"] = True
        return snap
    with open(playlist) as fh:
        snap["playlist_lines"] = normalize_playlist(fh.read())
    snap["probe"] = probe(playlist)
    for name in sorted(os.listdir(rendition_dir)):
        path = os.path.join(rendition_dir, name)
        if not os.path.isfile(path):
            continue
        size = os.path.getsize(path)
        snap["files"].append({"name": name, "bytes": size, "sha256": sha256(path)})
        snap["total_bytes"] += size
    return snap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="write the observation JSON here")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                    help="diff two observation files instead of encoding")
    args = ap.parse_args()

    if args.compare:
        return compare(*args.compare)

    import app

    tmp = tempfile.mkdtemp(prefix="characterize-")
    try:
        source = os.path.join(tmp, "src.mp4")
        make_fixture(source)

        rendition_dir = os.path.join(tmp, "hls", "240p")
        os.makedirs(rendition_dir, exist_ok=True)

        # Capture what would have gone out over the socket. The old code emits
        # straight from inside the read loop; the new one does it from callbacks,
        # so this is the seam whose behaviour has to be preserved.
        events = []
        real_emit = app.socketio.emit

        def capture(event, *a, **kw):
            events.append({"event": event, "payload": a[0] if a else kw})
        app.socketio.emit = capture
        try:
            cmd = app._build_single_rendition_cmd(source, rendition_dir, HEIGHT, VBR, ABR)
            # Signature: (cmd, item_id, duration_s, label, on_ready=None, timeout=...,
            #             output_paths=None)
            output_paths = (
                os.path.join(rendition_dir, "seg_%03d.ts"),
                os.path.join(rendition_dir, "index.m3u8"),
            )
            try:
                result = app._run_progressive_ffmpeg(
                    cmd, "characterize", DURATION, "240p", output_paths=output_paths,
                )
            except TypeError as exc:
                # The pre-swap implementation has no output_paths parameter.
                # That is the whole point of this harness: it must run against
                # both, so fall back rather than pretend the swap happened.
                if "output_paths" not in str(exc):
                    raise
                result = app._run_progressive_ffmpeg(cmd, "characterize", DURATION, "240p")
        finally:
            app.socketio.emit = real_emit

        returncode = result[0] if isinstance(result, tuple) else result
        stderr = result[1] if isinstance(result, tuple) else b""
        error = result[2] if isinstance(result, tuple) and len(result) > 2 else None

        progress = [e for e in events if e["event"] == "transcode_progress"]
        observation = {
            "returncode": returncode,
            "stderr_bytes": len(stderr or b""),
            "error_kind": getattr(getattr(error, "kind", None), "value", None),
            "error_user_message": getattr(error, "user_message", None),
            "progress_event_count": len(progress),
            "progress_monotonic": all(
                (progress[i]["payload"].get("encoded_seconds") or 0)
                <= (progress[i + 1]["payload"].get("encoded_seconds") or 0)
                for i in range(len(progress) - 1)
            ),
            "final_event": progress[-1]["payload"] if progress else None,
            "any_pct_over_100": any(
                (e["payload"].get("pct") or 0) > 100 for e in progress
            ),
            "other_events": sorted({e["event"] for e in events} - {"transcode_progress"}),
            "output": snapshot_output(rendition_dir),
            # Anything left behind is a bug: a failed or partial encode must not
            # publish anything.
            "leftover_partial_dirs": sorted(
                n for n in os.listdir(os.path.join(tmp, "hls")) if n.endswith(".partial")
            ),
        }

        text = json.dumps(observation, indent=2, sort_keys=True, ensure_ascii=False)
        if args.out:
            with open(args.out, "w") as fh:
                fh.write(text + "\n")
            print(f"wrote {args.out}")
        print(text)
        return 0 if returncode == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


#: Things that may legitimately differ between two runs of the same code.
VOLATILE = {"stderr_bytes"}

#: Quantities that may legitimately move, with the rule that keeps them honest.
#: More progress events is an improvement (finer updates), fewer would be a
#: regression; nothing else is allowed to change.
def _check_progress_count(before, after, problems):
    b = (before.get("output") and before.get("progress_event_count")) or 0
    a = (after.get("output") and after.get("progress_event_count")) or 0
    if a < b:
        problems.append(f".progress_event_count regressed: {b} -> {a}")


def compare(before_path, after_path):
    with open(before_path) as fh:
        before = json.load(fh)
    with open(after_path) as fh:
        after = json.load(fh)

    problems = []

    skip = {"progress_event_count"}

    def walk(a, b, path="", skip=()):
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key in VOLATILE or key in skip:
                    continue
                walk(a.get(key), b.get(key), f"{path}.{key}")
            return
        if a != b:
            problems.append(f"{path}: {a!r} -> {b!r}")

    walk(before, after, skip=("progress_event_count",))
    _check_progress_count(before, after, problems)
    if problems:
        print("DIFFERENCES (the swap changed observable behaviour):")
        for p in problems:
            print("  " + p)
        return 1
    print("IDENTICAL: the swap did not change the produced output or events")
    return 0


if __name__ == "__main__":
    sys.exit(main())