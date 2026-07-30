from gevent import monkey
monkey.patch_all()

import gevent
import json
import os
import re as _re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit

from config import Config
from srt_to_vtt import convert_srt_to_vtt


def log_debug(msg):
    print(f"[DEBUG_STREAM] {msg}", file=sys.stderr, flush=True)


def safe_save(room_obj):
    try:
        room_obj.save()
    except Exception as e:
        log_debug(f"[CRITICAL] State save error: {e}")


try:
    from state import LOCK, CHAT_LOCK, room, chat
    log_debug("Successfully imported state module (room + chat).")
except ImportError as e:
    log_debug(f"Failed to import state elements: {e}")
    LOCK = gevent.lock.Semaphore()
    CHAT_LOCK = gevent.lock.Semaphore()
    room = None
    chat = None


app = Flask(__name__, static_url_path="/stream/static", template_folder="templates")
app.config.from_object(Config)

socketio = SocketIO(
    app,
    path="stream/socket.io",
    cors_allowed_origins="*",
    async_mode="gevent",
)

# Limits how many ffmpeg rendition-encode jobs can run at once so a burst of
# adds/quality-requests can't starve the server's CPU. Extra jobs sit at
# status "queued" until a slot frees up.
ENCODE_SEMAPHORE = gevent.lock.BoundedSemaphore(Config.MAX_CONCURRENT_ENCODES)


def allowed(filename: str, exts: set) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in exts


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def broadcast_state():
    if room:
        socketio.emit("state_sync", room.to_public_dict())


def broadcast_notify(ntype: str, name: str, **extra):
    payload = {"type": ntype, "name": name, "ts": time.time()}
    payload.update(extra)
    socketio.emit("notify", payload)


def broadcast_presence():
    if room:
        socketio.emit("presence", {
            "online": len(room.users),
            "users": room.users_public_list(),
        })


def url_ext(url: str) -> str:
    clean = url.split("?", 1)[0].split("#", 1)[0]
    return clean.rsplit(".", 1)[-1].lower() if "." in clean else ""


def free_space_mb(path: str) -> float:
    return shutil.disk_usage(path).free / (1024 * 1024)


def _download_to_file(url: str, dest_path: str, timeout: int = 25) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest_path, "wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 1024)


MIN_FREE_MB_FOR_TRANSCODE = 500
TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}
LANG_LABELS = {
    "fa": "فارسی", "per": "فارسی", "fas": "فارسی",
    "en": "انگلیسی", "eng": "انگلیسی",
    "ar": "عربی", "ara": "عربی",
}

_FFMPEG_DURATION_RE = _re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")


# ────────────────────────────────────────────────────────────────────────────
# ffprobe / stream inspection
# ────────────────────────────────────────────────────────────────────────────

def _ffprobe_source(source: str, timeout: int = 20) -> dict:
    log_debug(f"Starting ffprobe on: {source}")
    start_time = time.time()
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-print_format", "json",
                "-show_streams", "-show_format",
                "-rw_timeout", str(timeout * 1_000_000),
                source,
            ],
            capture_output=True, timeout=timeout + 10,
        )
        log_debug(f"ffprobe done in {time.time() - start_time:.2f}s, returncode={proc.returncode}")
        if proc.returncode != 0:
            log_debug(f"ffprobe stderr: {proc.stderr.decode(errors='ignore')[:300]}")
            return {}
        return json.loads(proc.stdout or "{}")
    except Exception as e:
        log_debug(f"ffprobe exception: {e}")
        return {}


def _find_text_subtitle_streams(probe_data: dict) -> list:
    subs = []
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "subtitle" and s.get("codec_name") in TEXT_SUBTITLE_CODECS:
            tags = s.get("tags") or {}
            subs.append({
                "index": s["index"],
                "lang": (tags.get("language") or "")[:8],
                "title": tags.get("title") or "",
            })
    return subs


def _get_duration_s(probe_data: dict) -> float:
    try:
        d = float(probe_data.get("format", {}).get("duration") or 0)
        if d > 0:
            return d
    except (TypeError, ValueError):
        pass
    for s in probe_data.get("streams", []):
        try:
            d = float(s.get("duration") or 0)
            if d > 0:
                return d
        except (TypeError, ValueError):
            pass
    return 0.0


def _get_video_height(probe_data: dict) -> int:
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "video" and s.get("height"):
            try:
                return int(s["height"])
            except (TypeError, ValueError):
                continue
    return 0


def _get_video_width(probe_data: dict) -> int:
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "video" and s.get("width"):
            try:
                return int(s["width"])
            except (TypeError, ValueError):
                continue
    return 0


def _pick_ladder(source_height: int):
    """Up to Config.HLS_MAX_RENDITIONS renditions from Config.HLS_LADDER that
    don't exceed the source's own height (never upscale)."""
    if not source_height or source_height <= 0:
        source_height = 720
    candidates = [t for t in Config.HLS_LADDER if t[1] <= source_height]
    if not candidates:
        _, _, vbr, abr = Config.HLS_LADDER[-1]
        return [(f"{source_height}p", source_height, vbr, abr)]
    return candidates[: Config.HLS_MAX_RENDITIONS]


def _pick_default_rendition(ladder: list):
    """720p is the sane default — good balance of quality vs. how fast it
    encodes. Everything else in the ladder is only encoded on-demand, the
    moment a viewer actually asks for it (see _encode_rendition / the
    /api/request-quality endpoint), which is what makes first playback so
    much faster than encoding every rendition upfront."""
    for t in ladder:
        if t[1] == 720:
            return t
    return ladder[0]


def _bandwidth_estimate(vbr: str, abr: str) -> int:
    def to_bits(s):
        s = (s or "0").strip().lower()
        if s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        if s.endswith("m"):
            return int(float(s[:-1]) * 1_000_000)
        return int(float(s))
    return to_bits(vbr) + to_bits(abr)


# ────────────────────────────────────────────────────────────────────────────
# Single-rendition progressive HLS encoding
#
# Each ffmpeg job encodes exactly ONE rendition (unlike an earlier design
# that split+encoded all renditions in one ffmpeg call — that tied first-
# playback speed to however long the *slowest* rendition took). Only the
# default (720p, or the closest available) rendition is encoded right away;
# every other rendition in the ladder starts out "pending" and is only
# encoded when a viewer actually picks it from the quality menu. We
# ourselves write master.m3u8 (ffmpeg's hls muxer isn't used for that here,
# since it only knows about a single invocation's outputs) and rewrite it
# every time a rendition becomes ready, so hls.js always sees the current
# full set of ready qualities.
#
# scale=-2:H keeps width/height even, which is the actual fix for the old
# black-video bug (odd/mismatched dims from a -c:v copy passthrough on
# yuv444p sources). hls_playlist_type=event means the per-rendition .m3u8
# keeps flushing as segments are produced, so playback can start well
# before the whole file is encoded.
# ────────────────────────────────────────────────────────────────────────────

def _build_single_rendition_cmd(source: str, rendition_dir: str, height: int, vbr: str, abr: str) -> list:
    return [
        "ffmpeg", "-y", "-threads", "0", "-i", source,
        "-vf", f"scale=-2:{height}",
        "-c:v", "libx264", "-b:v", vbr,
        "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
        "-g", "60", "-sc_threshold", "0",
        "-force_key_frames", "expr:gte(t,n_forced*4)",
        "-c:a", "aac", "-b:a", abr,
        "-f", "hls",
        "-hls_time", str(Config.HLS_SEGMENT_SECONDS),
        "-hls_list_size", "0",
        "-hls_playlist_type", "event",
        "-hls_flags", "independent_segments+temp_file",
        "-hls_segment_filename", os.path.join(rendition_dir, "seg_%03d.ts"),
        os.path.join(rendition_dir, "index.m3u8"),
    ]


def _rewrite_master_playlist(out_dir: str, ready_renditions: list, aspect: float):
    """(Re)writes master.m3u8 to list every rendition that is at least
    partially ready. Written atomically (temp file + os.replace) so a
    concurrent reader (Nginx/Flask serving it, or hls.js polling it) never
    sees a half-written file."""
    lines = ["#EXTM3U", "#EXT-X-VERSION:6"]
    for r in sorted(ready_renditions, key=lambda x: x["height"], reverse=True):
        bw = _bandwidth_estimate(r["vbr"], r["abr"])
        width = int(round(r["height"] * (aspect or 16 / 9) / 2) * 2)
        lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={width}x{r["height"]},CODECS="avc1.64001f,mp4a.40.2"')
        lines.append(f'{r["label"]}/index.m3u8')
    content = "\n".join(lines) + "\n"
    os.makedirs(out_dir, exist_ok=True)
    tmp_path = os.path.join(out_dir, "master.m3u8.tmp")
    final_path = os.path.join(out_dir, "master.m3u8")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, final_path)


def _run_progressive_ffmpeg(cmd: list, item_id: str, duration_s: float, label: str, on_ready=None, timeout: int = 10800):
    """Runs one rendition's ffmpeg command with -progress pipe:1, emitting
    real transcode_progress events (real % from ffprobe duration — this is
    the fix for the old "tick every 5MB" heuristic that only applied to
    sources without a Content-Length header). Fires `on_ready` exactly once,
    the moment enough of the timeline is encoded for safe early playback."""
    progress_cmd = [cmd[0]] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    log_debug(f"FFmpeg [{item_id}/{label}] start: {' '.join(progress_cmd[:4])} ...")

    proc = subprocess.Popen(progress_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    stderr_chunks = []
    last_pct = -1
    ready_fired = [False]
    _duration = [duration_s]

    def _read_stderr():
        for chunk in proc.stderr:
            stderr_chunks.append(chunk)
            if _duration[0] == 0:
                text = chunk.decode(errors="ignore")
                m = _FFMPEG_DURATION_RE.search(text)
                if m:
                    h, mi, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
                    _duration[0] = h * 3600 + mi * 60 + s
                    log_debug(f"Duration extracted from ffmpeg stderr: {_duration[0]:.1f}s")

    gevent.spawn(_read_stderr)

    deadline = time.time() + timeout
    start_time = time.time()
    for raw_line in proc.stdout:
        if time.time() > deadline:
            log_debug(f"FFmpeg [{item_id}/{label}] hit timeout deadline — killing.")
            proc.kill()
            break
        line = raw_line.decode(errors="ignore").strip()
        if line.startswith("out_time_ms="):
            try:
                val = line.split("=", 1)[1]
                if val == "N/A":
                    continue
                elapsed_s = int(val) / 1_000_000
                dur = _duration[0]
                pct = min(99, int(elapsed_s / dur * 100)) if dur > 0 else min(90, int(elapsed_s / 3))
                if not ready_fired[0] and elapsed_s >= Config.HLS_READY_AFTER_SECONDS:
                    ready_fired[0] = True
                    if on_ready:
                        try:
                            on_ready(elapsed_s)
                        except Exception as e:
                            log_debug(f"on_ready callback error: {e}")
                if pct != last_pct or ready_fired[0]:
                    last_pct = pct
                    socketio.emit("transcode_progress", {
                        "id": item_id, "label": label, "pct": pct,
                        "encoded_seconds": round(elapsed_s, 1),
                        "duration": round(dur, 1) if dur > 0 else None,
                        "ready": ready_fired[0],
                    })
            except (ValueError, ZeroDivisionError):
                pass

    proc.wait()
    log_debug(f"FFmpeg [{item_id}/{label}] exited code={proc.returncode} in {time.time()-start_time:.1f}s")
    if proc.returncode == 0:
        socketio.emit("transcode_progress", {
            "id": item_id, "label": label, "pct": 100,
            "encoded_seconds": round(_duration[0], 1) if _duration[0] else None,
            "duration": round(_duration[0], 1) if _duration[0] else None,
            "ready": True, "complete": True,
        })
    return proc.returncode, b"".join(stderr_chunks)


def _extract_subtitles_async(item_id: str, source: str, sub_streams: list, requester_name: str):
    """Separate, lightweight ffmpeg pass per subtitle stream (text streams
    are tiny/fast) so a subtitle problem can never affect video
    playability."""
    new_subs = []
    for i, s in enumerate(sub_streams[:4]):
        srt_path = os.path.join(Config.SUBS_DIR, f"{item_id}_{i}_tmp.srt")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", source, "-map", f"0:{s['index']}", "-c:s", "srt", srt_path],
                capture_output=True, timeout=120, check=True,
            )
            if os.path.exists(srt_path) and os.path.getsize(srt_path) > 10:
                sub_id = new_id()
                vtt_name = f"{sub_id}.vtt"
                convert_srt_to_vtt(srt_path, os.path.join(Config.SUBS_DIR, vtt_name))
                label = (s["title"] or LANG_LABELS.get(s["lang"], s["lang"])
                         or f"زیرنویس {len(new_subs) + 1}")
                new_subs.append({
                    "id": sub_id, "label": label, "lang": s["lang"] or "und",
                    "url": f"/stream/media/subs/{vtt_name}",
                })
        except Exception as e:
            log_debug(f"Auto subtitle extraction failed for stream {s['index']}: {e}")
        finally:
            if os.path.exists(srt_path):
                try:
                    os.remove(srt_path)
                except OSError:
                    pass

    if not new_subs:
        return
    with LOCK:
        item = room.find_item(item_id) if room else None
        if item:
            item.setdefault("subtitles", []).extend(new_subs)
            safe_save(room)
            title = item["title"]
        else:
            title = ""
    broadcast_state()
    broadcast_notify("subtitle_auto_extracted", requester_name, title=title, count=len(new_subs))


def _encode_rendition(item_id: str, source: str, label: str, height: int, vbr: str, abr: str,
                       requester_name: str, is_default: bool, duration_s: float = None):
    """Encodes exactly one rendition. Used both for the default rendition
    kicked off right after an item is added, and for any other rendition a
    viewer later requests via /api/request-quality."""
    out_dir = os.path.join(Config.HLS_DIR, item_id)
    rendition_dir = os.path.join(out_dir, label)

    def set_rendition_status(status, error=None):
        with LOCK:
            item = room.find_item(item_id) if room else None
            if not item:
                return None
            for r in item.get("renditions", []):
                if r["label"] == label:
                    r["status"] = status
                    if error:
                        r["error"] = error[:200]
                    else:
                        r.pop("error", None)
            safe_save(room)
            return item

    def collect_ready(item):
        return [r for r in item.get("renditions", []) if r["status"] in ("ready", "complete")]

    def fail(msg: str):
        log_debug(f"Rendition encode failed [{item_id}/{label}]: {msg}")
        shutil.rmtree(rendition_dir, ignore_errors=True)
        item = set_rendition_status("error", msg)
        if item and is_default:
            with LOCK:
                item["status"] = "error"
                item["error"] = msg[:300]
                safe_save(room)
        broadcast_state()
        title = item["title"] if item else ""
        if is_default:
            broadcast_notify("media_error", requester_name, id=item_id, title=title)
        else:
            broadcast_notify("quality_error", requester_name, id=item_id, title=title, label=label)

    free_mb = free_space_mb(Config.MEDIA_DIR)
    if free_mb < MIN_FREE_MB_FOR_TRANSCODE:
        fail(f"فضای آزاد دیسک سرور ناکافی است (فقط {free_mb:.0f}MB آزاد).")
        return

    set_rendition_status("queued")
    if is_default:
        with LOCK:
            item = room.find_item(item_id) if room else None
            if item:
                item["status"] = "queued"
                safe_save(room)
    broadcast_state()

    acquired = ENCODE_SEMAPHORE.acquire()  # blocks this greenlet only
    try:
        with LOCK:
            item = room.find_item(item_id) if room else None
        if not item:
            log_debug(f"Item {item_id} removed while queued — aborting rendition encode.")
            return

        set_rendition_status("encoding")
        if is_default:
            with LOCK:
                item["status"] = "encoding"
                safe_save(room)
        broadcast_state()

        dur = duration_s
        if dur is None:
            dur = _get_duration_s(_ffprobe_source(source))

        os.makedirs(rendition_dir, exist_ok=True)

        def on_ready(elapsed_s):
            item = set_rendition_status("ready")
            if not item:
                return
            with LOCK:
                aspect = item.get("_aspect") or 16 / 9
                _rewrite_master_playlist(out_dir, collect_ready(item), aspect)
                if is_default:
                    item["status"] = "ready"
                    item["src"] = f"/stream/media/hls/{item_id}/master.m3u8"
                    safe_save(room)
                title = item["title"]
            broadcast_state()
            if is_default:
                broadcast_notify("media_partial_ready", requester_name, id=item_id, title=title)
            else:
                broadcast_notify("quality_partial_ready", requester_name, id=item_id, title=title, label=label)

        cmd = _build_single_rendition_cmd(source, rendition_dir, height, vbr, abr)
        returncode, stderr_bytes = _run_progressive_ffmpeg(cmd, item_id, dur, label, on_ready=on_ready)

        if returncode != 0:
            stderr_txt = stderr_bytes.decode(errors="ignore")
            if "No space left" in stderr_txt:
                detail = "فضای دیسک سرور در حین تبدیل تمام شد"
            else:
                lines = stderr_txt.strip().splitlines()
                detail = lines[-1] if lines else f"ffmpeg exit code {returncode}"
            fail(f"تبدیل ({label}) ناموفق بود: {detail}")
            return

        item = set_rendition_status("complete")
        if not item:
            return
        with LOCK:
            aspect = item.get("_aspect") or 16 / 9
            _rewrite_master_playlist(out_dir, collect_ready(item), aspect)
            if is_default:
                item["status"] = "complete"
                item["src"] = f"/stream/media/hls/{item_id}/master.m3u8"
                item.pop("error", None)
            safe_save(room)
            title = item["title"]
        broadcast_state()
        if is_default:
            broadcast_notify("media_ready", requester_name, id=item_id, title=title)
        else:
            broadcast_notify("quality_ready", requester_name, id=item_id, title=title, label=label)

    except subprocess.TimeoutExpired:
        fail("زمان تبدیل ویدیو بیش از حد طول کشید (timeout)")
    except Exception as e:
        fail(str(e))
    finally:
        if acquired:
            ENCODE_SEMAPHORE.release()


def start_item_encoding(item_id: str, source: str, requester_name: str, local_raw_path: str = None):
    """
    Entry point used for every added video (file upload, MKV, direct link,
    or a resolved YouTube download). Probes the source once, builds the
    rendition ladder, records it on the item (so the quality menu can show
    every option immediately, even the ones not encoded yet), and kicks off
    only the default rendition's encode. `source` can be a local path or a
    remote URL — ffmpeg reads both the same way.
    """
    log_debug(f"start_item_encoding: {item_id} <- {source}")

    def fail_all(msg: str):
        with LOCK:
            item = room.find_item(item_id) if room else None
            if item:
                item["status"] = "error"
                item["error"] = msg[:300]
                safe_save(room)
                title = item["title"]
            else:
                title = ""
        broadcast_state()
        broadcast_notify("media_error", requester_name, id=item_id, title=title)

    free_mb = free_space_mb(Config.MEDIA_DIR)
    if free_mb < MIN_FREE_MB_FOR_TRANSCODE:
        fail_all(f"فضای آزاد دیسک سرور ناکافی است (فقط {free_mb:.0f}MB آزاد). لطفاً ابتدا فضا آزاد کن.")
        return

    probe = _ffprobe_source(source)
    duration_s = _get_duration_s(probe)
    height = _get_video_height(probe)
    width = _get_video_width(probe)
    aspect = (width / height) if (width and height) else (16 / 9)
    sub_streams = _find_text_subtitle_streams(probe)
    ladder = _pick_ladder(height)
    default_label, default_height, default_vbr, default_abr = _pick_default_rendition(ladder)
    log_debug(f"Item {item_id}: duration={duration_s}s height={height} ladder={[l[0] for l in ladder]} default={default_label}")

    renditions = [
        {"label": l, "height": h, "vbr": v, "abr": a,
         "status": "queued" if l == default_label else "pending",
         "is_default": (l == default_label)}
        for (l, h, v, a) in ladder
    ]

    with LOCK:
        item = room.find_item(item_id) if room else None
        if not item:
            log_debug(f"Item {item_id} vanished before encoding could start.")
            return
        item["renditions"] = renditions
        item["status"] = "queued"
        item["_source"] = source
        item["_aspect"] = aspect
        if local_raw_path:
            item["_raw_path"] = local_raw_path
        safe_save(room)
    broadcast_state()

    os.makedirs(os.path.join(Config.HLS_DIR, item_id), exist_ok=True)
    gevent.spawn(_extract_subtitles_async, item_id, source, sub_streams, requester_name)
    gevent.spawn(_encode_rendition, item_id, source, default_label, default_height,
                 default_vbr, default_abr, requester_name, True, duration_s)


def request_quality(item_id: str, label: str, requester_name: str):
    """Kicks off an on-demand rendition encode when a viewer picks a
    not-yet-ready quality from the menu. Idempotent: a second request for a
    rendition that's already queued/encoding/ready is a no-op."""
    with LOCK:
        item = room.find_item(item_id) if room else None
        if not item:
            return False, "آیتم پیدا نشد"
        source = item.get("_source")
        if not source:
            return False, "این آیتم هنوز آماده نیست"
        target = next((r for r in item.get("renditions", []) if r["label"] == label), None)
        if not target:
            return False, "این کیفیت برای این ویدیو وجود ندارد"
        if target["status"] in ("queued", "encoding", "ready", "complete"):
            return True, "already in progress"
        target["status"] = "queued"
        height, vbr, abr = target["height"], target["vbr"], target["abr"]
        safe_save(room)
    broadcast_state()
    gevent.spawn(_encode_rendition, item_id, source, label, height, vbr, abr, requester_name, False)
    return True, "started"


# ────────────────────────────────────────────────────────────────────────────
# Orphan-file cleanup helpers
# ────────────────────────────────────────────────────────────────────────────

def _collect_referenced_files() -> tuple:
    upload_refs, sub_refs, hls_ids = set(), set(), set()
    for item in (room.playlist if room else []):
        raw_path = item.get("_raw_path")
        if raw_path:
            upload_refs.add(os.path.basename(raw_path))
        if item.get("renditions") or "/media/hls/" in (item.get("src") or ""):
            hls_ids.add(item["id"])
        for s in item.get("subtitles") or []:
            url = s.get("url") or ""
            if "/media/subs/" in url:
                sub_refs.add(url.rsplit("/", 1)[-1])
    return upload_refs, sub_refs, hls_ids


def _cleanup_orphans() -> dict:
    with LOCK:
        upload_refs, sub_refs, hls_ids = _collect_referenced_files()

    removed = {"uploads": [], "subs": [], "hls": []}
    skip = {".gitkeep"}

    for fname in os.listdir(Config.UPLOAD_DIR):
        if fname in skip or fname.startswith("."):
            continue
        if fname not in upload_refs:
            try:
                os.remove(os.path.join(Config.UPLOAD_DIR, fname))
                removed["uploads"].append(fname)
            except OSError as e:
                log_debug(f"Failed to remove orphan upload {fname}: {e}")

    for fname in os.listdir(Config.SUBS_DIR):
        if fname in skip or fname.startswith("."):
            continue
        if fname not in sub_refs:
            try:
                os.remove(os.path.join(Config.SUBS_DIR, fname))
                removed["subs"].append(fname)
            except OSError as e:
                log_debug(f"Failed to remove orphan sub {fname}: {e}")

    if os.path.isdir(Config.HLS_DIR):
        for dirname in os.listdir(Config.HLS_DIR):
            if dirname not in hls_ids:
                try:
                    shutil.rmtree(os.path.join(Config.HLS_DIR, dirname), ignore_errors=True)
                    removed["hls"].append(dirname)
                except OSError as e:
                    log_debug(f"Failed to remove orphan hls dir {dirname}: {e}")

    log_debug(f"Cleanup done: {len(removed['uploads'])} uploads, {len(removed['subs'])} subs, "
              f"{len(removed['hls'])} hls dirs removed.")
    return removed


def _chat_purge_loop():
    while True:
        gevent.sleep(Config.CHAT_PURGE_INTERVAL_SECONDS)
        if not chat:
            continue
        with CHAT_LOCK:
            removed = chat.purge_older_than(Config.CHAT_RETENTION_SECONDS)
        if removed:
            _delete_chat_images(removed)
            socketio.emit("chat_purged", {"removed": len(removed)})
            log_debug(f"Chat auto-purge removed {len(removed)} message(s).")


def _delete_chat_images(messages: list):
    for m in messages:
        url = m.get("image_url")
        if url and "/media/chat/" in url:
            fname = url.rsplit("/", 1)[-1]
            path = os.path.join(Config.CHAT_IMG_DIR, fname)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


# ────────────────────────────────────────────────────────────────────────────
# Pages
# ────────────────────────────────────────────────────────────────────────────

@app.route("/stream/")
@app.route("/stream")
def index():
    return render_template("index.html")


@app.route("/stream/media/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(Config.UPLOAD_DIR, filename, conditional=True)


@app.route("/stream/media/subs/<path:filename>")
def serve_sub(filename):
    return send_from_directory(Config.SUBS_DIR, filename, conditional=True)


@app.route("/stream/media/hls/<item_id>/<path:filename>")
def serve_hls(item_id, filename):
    # NOTE: in production this route is normally bypassed — Nginx should
    # serve /stream/media/hls/ directly from disk (see deployment notes)
    # for far better throughput on segment requests. This Flask route is a
    # functional fallback (e.g. local/dev testing without Nginx in front).
    d = os.path.join(Config.HLS_DIR, item_id)
    resp = send_from_directory(d, filename, conditional=True)
    if filename.endswith(".m3u8"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/stream/media/chat/<path:filename>")
def serve_chat_image(filename):
    return send_from_directory(Config.CHAT_IMG_DIR, filename, conditional=True)


@app.route("/stream/media/avatars/<path:filename>")
def serve_avatar(filename):
    return send_from_directory(Config.AVATAR_DIR, filename, conditional=True)


# ────────────────────────────────────────────────────────────────────────────
# REST API — playlist / media
# ────────────────────────────────────────────────────────────────────────────

@app.route("/stream/api/state")
def api_state():
    return jsonify(room.to_public_dict() if room else {})


@app.route("/stream/api/upload", methods=["POST"])
def api_upload():
    f = request.files.get("file")
    title = (request.form.get("title") or "").strip()
    name = (request.form.get("name") or "ناشناس").strip()

    if not f or f.filename == "":
        return jsonify({"error": "فایلی انتخاب نشده"}), 400
    if not allowed(f.filename, Config.ALLOWED_VIDEO_EXT):
        return jsonify({"error": "فرمت ویدیو پشتیبانی نمی‌شود"}), 400

    item_id = new_id()
    ext = f.filename.rsplit(".", 1)[-1].lower()
    raw_path = os.path.join(Config.UPLOAD_DIR, f"{item_id}_src.{ext}")
    log_debug(f"Upload: {f.filename} → {raw_path}")
    f.save(raw_path)

    item = {
        "id": item_id, "type": "file", "title": title or f.filename, "src": None,
        "subtitles": [], "audio_tracks": [], "added_by": name,
        "added_at": time.time(), "status": "queued",
    }
    with LOCK:
        if room:
            room.add_item(item)
            safe_save(room)
    broadcast_state()
    broadcast_notify("playlist_add_processing", name, title=item["title"])
    gevent.spawn(start_item_encoding, item_id, raw_path, name, raw_path)
    return jsonify(item)


@app.route("/stream/api/youtube-formats", methods=["POST"])
def api_youtube_formats():
    data = request.get_json(force=True, silent=True) or {}
    yt_url = (data.get("url") or "").strip()
    if not yt_url:
        return jsonify({"error": "لینک خالی است"}), 400

    cmd = [
        "yt-dlp", "--force-ipv4",
        "--extractor-args", "youtube:player_client=web,android,ios",
        "--no-playlist", "-J",
    ]
    if Config.YTDLP_PROXY:
        cmd += ["--proxy", Config.YTDLP_PROXY]
    if Config.YTDLP_COOKIES and os.path.exists(Config.YTDLP_COOKIES):
        cmd += ["--cookies", Config.YTDLP_COOKIES]
    cmd.append(yt_url)

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=45)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "yt-dlp timeout — ممکنه پروکسی در دسترس نباشه"}), 504
    except FileNotFoundError:
        return jsonify({"error": "yt-dlp روی سرور نصب نیست"}), 500

    if r.returncode != 0:
        err = r.stderr.decode(errors="ignore").strip().splitlines()
        return jsonify({"error": err[-1] if err else "خطای نامشخص"}), 502

    try:
        info = json.loads(r.stdout)
    except Exception:
        return jsonify({"error": "خروجی yt-dlp قابل پارس نیست"}), 502

    title = info.get("title", "")
    thumbnail = info.get("thumbnail", "")

    seen_heights = set()
    formats = []
    for f in info.get("formats", []):
        vcodec = f.get("vcodec") or "none"
        acodec = f.get("acodec") or "none"
        ext = f.get("ext", "")
        height = f.get("height")
        if not (vcodec != "none" and acodec != "none" and ext in ("mp4", "webm", "m4v")):
            continue
        if height is None or height in seen_heights:
            continue
        seen_heights.add(height)
        tbr = f.get("tbr") or 0
        formats.append({
            "format_id": f["format_id"], "height": height, "ext": ext, "tbr": round(tbr),
            "label": f"{height}p  ({ext}  ~{round(tbr)}kbps)" if tbr else f"{height}p  ({ext})",
        })

    formats.sort(key=lambda x: x["height"], reverse=True)
    formats.insert(0, {
        "format_id": "__best__", "height": 9999, "ext": "mp4", "tbr": 0,
        "label": "⭐ بهترین کیفیت موجود (منبع برای تولید چند کیفیت داخلی)",
    })
    return jsonify({"title": title, "thumbnail": thumbnail, "formats": formats})


def _fetch_and_encode_youtube(item_id: str, yt_url: str, fmt_selector: str, requester_name: str):
    """Downloads the chosen source quality locally first (rather than
    handing ffmpeg the ephemeral googlevideo URL directly), which avoids
    URL-expiry / missing-header failures, then feeds it into the normal
    per-item encoding pipeline."""
    raw_path = os.path.join(Config.UPLOAD_DIR, f"{item_id}_src.mp4")
    cmd = [
        "yt-dlp", "--force-ipv4", "-f", fmt_selector,
        "--extractor-args", "youtube:player_client=web,android,ios",
        "--no-playlist", "--merge-output-format", "mp4", "-o", raw_path,
    ]
    if Config.YTDLP_PROXY:
        cmd += ["--proxy", Config.YTDLP_PROXY]
    if Config.YTDLP_COOKIES and os.path.exists(Config.YTDLP_COOKIES):
        cmd += ["--cookies", Config.YTDLP_COOKIES]
    cmd.append(yt_url)

    def fail(msg):
        with LOCK:
            item = room.find_item(item_id) if room else None
            if item:
                item["status"] = "error"
                item["error"] = msg[:300]
                safe_save(room)
                title = item["title"]
            else:
                title = ""
        broadcast_state()
        broadcast_notify("media_error", requester_name, id=item_id, title=title)

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=1800)
    except subprocess.TimeoutExpired:
        fail("دانلود از یوتیوب بیش از حد طول کشید")
        return
    except FileNotFoundError:
        fail("yt-dlp روی سرور نصب نیست")
        return

    if r.returncode != 0 or not os.path.exists(raw_path):
        err = r.stderr.decode(errors="ignore").strip().splitlines()
        fail(f"دانلود از یوتیوب ناموفق بود: {err[-1] if err else 'خطای نامشخص'}")
        return

    start_item_encoding(item_id, raw_path, requester_name, raw_path)


@app.route("/stream/api/add-youtube", methods=["POST"])
def api_add_youtube():
    data = request.get_json(force=True, silent=True) or {}
    yt_url = (data.get("url") or "").strip()
    title = (data.get("title") or "").strip()
    name = (data.get("name") or "ناشناس").strip()
    format_id = (data.get("format_id") or "").strip()

    if not yt_url:
        return jsonify({"error": "لینک خالی است"}), 400

    fmt_selector = (format_id if format_id and format_id != "__best__" else Config.YTDLP_FORMAT)
    title = title or yt_url

    item_id = new_id()
    item = {
        "id": item_id, "type": "youtube", "title": title, "src": None,
        "subtitles": [], "audio_tracks": [], "added_by": name,
        "added_at": time.time(), "status": "queued", "yt_url": yt_url,
    }
    with LOCK:
        if room:
            room.add_item(item)
            safe_save(room)
    broadcast_state()
    broadcast_notify("playlist_add_processing", name, title=title)
    gevent.spawn(_fetch_and_encode_youtube, item_id, yt_url, fmt_selector, name)
    return jsonify(item)


VIDEO_EXTS = {"mp4", "webm", "ogg", "ogv", "mov", "m4v", "mkv", "avi", "ts"}


def _is_hls(url: str) -> bool:
    return url_ext(url) == "m3u8" or "m3u8" in url.split("?")[0].lower()


@app.route("/stream/api/add-url", methods=["POST"])
def api_add_url():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    title = (data.get("title") or "").strip()
    name = (data.get("name") or "ناشناس").strip()

    if not url:
        return jsonify({"error": "لینک خالی است"}), 400

    item_id = new_id()
    is_hls = _is_hls(url)
    needs_encode = not is_hls

    item = {
        "id": item_id, "type": "url",
        "title": title or url.rsplit("/", 1)[-1].split("?")[0] or "ویدیو",
        "src": None if needs_encode else url,
        "subtitles": [], "audio_tracks": [], "added_by": name,
        "added_at": time.time(),
        "status": "queued" if needs_encode else "ready",
    }
    with LOCK:
        if room:
            room.add_item(item)
            safe_save(room)
    broadcast_state()
    if needs_encode:
        broadcast_notify("playlist_add_processing", name, title=item["title"])
        gevent.spawn(start_item_encoding, item_id, url, name)
    else:
        broadcast_notify("playlist_add", name, title=item["title"])
    return jsonify(item)


@app.route("/stream/api/request-quality", methods=["POST"])
def api_request_quality():
    data = request.get_json(force=True, silent=True) or {}
    item_id = data.get("item_id")
    label = (data.get("label") or "").strip()
    name = (data.get("name") or "ناشناس").strip()
    if not item_id or not label:
        return jsonify({"error": "ورودی ناقص است"}), 400
    ok, msg = request_quality(item_id, label, name)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"ok": True})


@app.route("/stream/api/live/new-key", methods=["POST"])
def api_new_key():
    key = uuid.uuid4().hex[:10]
    return jsonify({
        "key": key,
        "push_url": Config.RTMP_PUSH_URL_TEMPLATE.format(key=key),
        "playback_url": Config.HLS_PLAYBACK_URL_TEMPLATE.format(key=key),
    })


@app.route("/stream/api/add-live", methods=["POST"])
def api_add_live():
    data = request.get_json(force=True, silent=True) or {}
    playback_url = (data.get("playback_url") or "").strip()
    title = (data.get("title") or "پخش زنده").strip()
    name = (data.get("name") or "ناشناس").strip()
    key = (data.get("key") or "").strip()

    if not playback_url:
        return jsonify({"error": "آدرس پخش (HLS) خالی است"}), 400

    item_id = new_id()
    item = {
        "id": item_id, "type": "live", "title": title, "src": playback_url,
        "key": key, "subtitles": [], "audio_tracks": [], "added_by": name,
        "added_at": time.time(), "status": "ready",
    }
    with LOCK:
        if room:
            room.add_item(item)
            safe_save(room)
    broadcast_state()
    broadcast_notify("playlist_add_live", name, title=title)
    return jsonify(item)


@app.route("/stream/api/playlist/<item_id>", methods=["DELETE"])
def api_remove_item(item_id):
    name = request.args.get("name", "ناشناس")
    do_cleanup = request.args.get("cleanup", "1") != "0"
    with LOCK:
        item = room.find_item(item_id) if room else None
        title = item["title"] if item else "ویدیو"
        raw_path = item.get("_raw_path") if item else None
        if room:
            room.remove_item(item_id)
            safe_save(room)
    broadcast_state()
    broadcast_notify("playlist_remove", name, title=title)
    shutil.rmtree(os.path.join(Config.HLS_DIR, item_id), ignore_errors=True)
    if raw_path and os.path.exists(raw_path):
        try:
            os.remove(raw_path)
        except OSError:
            pass
    if do_cleanup:
        gevent.spawn(_cleanup_orphans)
    return jsonify({"ok": True})


@app.route("/stream/api/cleanup", methods=["POST"])
def api_cleanup():
    removed = _cleanup_orphans()
    total = len(removed["uploads"]) + len(removed["subs"]) + len(removed["hls"])
    return jsonify({"removed": removed, "total": total})


@app.route("/stream/api/subtitle-upload", methods=["POST"])
def api_subtitle_upload():
    f = request.files.get("file")
    item_id = request.form.get("item_id")
    label = (request.form.get("label") or "زیرنویس").strip()
    lang = (request.form.get("lang") or "fa").strip()
    name = (request.form.get("name") or "ناشناس").strip()

    if not f or not item_id:
        return jsonify({"error": "ورودی ناقص است"}), 400
    if not allowed(f.filename, Config.ALLOWED_SUB_EXT):
        return jsonify({"error": "فقط فایل vtt یا srt پذیرفته می‌شود"}), 400

    sub_id = new_id()
    ext = f.filename.rsplit(".", 1)[-1].lower()
    raw_path = os.path.join(Config.SUBS_DIR, f"{sub_id}.{ext}")
    f.save(raw_path)

    if ext == "srt":
        vtt_name = f"{sub_id}.vtt"
        convert_srt_to_vtt(raw_path, os.path.join(Config.SUBS_DIR, vtt_name))
        url = f"/stream/media/subs/{vtt_name}"
    else:
        url = f"/stream/media/subs/{sub_id}.{ext}"

    with LOCK:
        if not room:
            return jsonify({"error": "اتاق فعال نیست"}), 500
        item = room.find_item(item_id)
        if not item:
            return jsonify({"error": "آیتم پلی‌لیست پیدا نشد"}), 404
        item.setdefault("subtitles", []).append({"id": sub_id, "label": label, "lang": lang, "url": url})
        safe_save(room)
    broadcast_state()
    broadcast_notify("subtitle_add", name, title=item.get("title", ""), label=label)
    return jsonify({"id": sub_id, "url": url})


@app.route("/stream/api/subtitle-url", methods=["POST"])
def api_subtitle_url():
    data = request.get_json(force=True, silent=True) or {}
    item_id = data.get("item_id")
    url = (data.get("url") or "").strip()
    label = (data.get("label") or "زیرنویس").strip()
    lang = (data.get("lang") or "fa").strip()
    name = (data.get("name") or "ناشناس").strip()

    if not item_id or not url:
        return jsonify({"error": "ورودی ناقص است"}), 400

    ext = url_ext(url)
    if ext not in ("vtt", "srt"):
        return jsonify({"error": "فقط لینک مستقیم به فایل vtt یا srt پذیرفته می‌شود"}), 400

    sub_id = new_id()
    raw_path = os.path.join(Config.SUBS_DIR, f"{sub_id}.{ext}")
    try:
        _download_to_file(url, raw_path, timeout=15)
    except Exception as e:
        return jsonify({"error": f"دریافت فایل زیرنویس ناموفق بود: {e}"}), 400

    if ext == "srt":
        vtt_name = f"{sub_id}.vtt"
        convert_srt_to_vtt(raw_path, os.path.join(Config.SUBS_DIR, vtt_name))
        sub_url = f"/stream/media/subs/{vtt_name}"
    else:
        sub_url = f"/stream/media/subs/{sub_id}.{ext}"

    with LOCK:
        if not room:
            return jsonify({"error": "اتاق فعال نیست"}), 500
        item = room.find_item(item_id)
        if not item:
            return jsonify({"error": "آیتم پلی‌لیست پیدا نشد"}), 404
        item.setdefault("subtitles", []).append({"id": sub_id, "label": label, "lang": lang, "url": sub_url})
        safe_save(room)
    broadcast_state()
    broadcast_notify("subtitle_add", name, title=item.get("title", ""), label=label)
    return jsonify({"id": sub_id, "url": sub_url})


@app.route("/stream/api/audio-track", methods=["POST"])
def api_add_audio_track():
    data = request.get_json(force=True, silent=True) or {}
    item_id = data.get("item_id")
    label = (data.get("label") or "دوبله").strip()
    url = (data.get("url") or "").strip()
    name = (data.get("name") or "ناشناس").strip()

    if not item_id or not url:
        return jsonify({"error": "ورودی ناقص است"}), 400

    with LOCK:
        if not room:
            return jsonify({"error": "اتاق فعال نیست"}), 500
        item = room.find_item(item_id)
        if not item:
            return jsonify({"error": "آیتم پلی‌لیست پیدا نشد"}), 404
        item.setdefault("audio_tracks", []).append({"id": new_id(), "label": label, "url": url})
        safe_save(room)
    broadcast_state()
    broadcast_notify("audio_track_add", name, title=item.get("title", ""), label=label)
    return jsonify({"ok": True})


# ────────────────────────────────────────────────────────────────────────────
# REST API — chat & avatars
# ────────────────────────────────────────────────────────────────────────────

@app.route("/stream/api/chat/history")
def api_chat_history():
    with CHAT_LOCK:
        return jsonify({"messages": chat.public_history() if chat else []})


@app.route("/stream/api/chat/clear", methods=["POST"])
def api_chat_clear():
    name = (request.args.get("name") or request.form.get("name") or "ناشناس")
    with CHAT_LOCK:
        removed = chat.clear() if chat else []
    _delete_chat_images(removed)
    socketio.emit("chat_cleared", {"by": name})
    return jsonify({"ok": True, "removed": len(removed)})


@app.route("/stream/api/chat/image", methods=["POST"])
def api_chat_image():
    f = request.files.get("file")
    if not f or f.filename == "":
        return jsonify({"error": "فایلی انتخاب نشده"}), 400
    if not allowed(f.filename, Config.ALLOWED_IMAGE_EXT):
        return jsonify({"error": "فرمت عکس پشتیبانی نمی‌شود"}), 400
    ext = f.filename.rsplit(".", 1)[-1].lower()
    fname = f"{new_id()}.{ext}"
    f.save(os.path.join(Config.CHAT_IMG_DIR, fname))
    return jsonify({"url": f"/stream/media/chat/{fname}"})


@app.route("/stream/api/avatar", methods=["POST"])
def api_avatar_upload():
    f = request.files.get("file")
    name = (request.form.get("name") or "").strip()
    if not name:
        return jsonify({"error": "نام کاربر مشخص نیست"}), 400
    if not f or f.filename == "":
        return jsonify({"error": "فایلی انتخاب نشده"}), 400
    if not allowed(f.filename, Config.ALLOWED_IMAGE_EXT):
        return jsonify({"error": "فرمت عکس پشتیبانی نمی‌شود"}), 400

    ext = f.filename.rsplit(".", 1)[-1].lower()
    fname = f"{new_id()}.{ext}"
    f.save(os.path.join(Config.AVATAR_DIR, fname))
    url = f"/stream/media/avatars/{fname}"

    with CHAT_LOCK:
        if chat:
            chat.set_avatar(name, url)
    with LOCK:
        if room:
            for u in room.users.values():
                if u.get("name") == name:
                    u["avatar_url"] = url
    broadcast_presence()
    return jsonify({"url": url})


# ────────────────────────────────────────────────────────────────────────────
# Socket.IO
# ────────────────────────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    if room:
        emit("state_sync", room.to_public_dict())
    if chat:
        with CHAT_LOCK:
            emit("chat_history", {"messages": chat.public_history()})


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    with LOCK:
        if room:
            user = room.users.pop(sid, None)
        else:
            user = None
    if user:
        broadcast_notify("leave", user["name"])
        broadcast_presence()


@socketio.on("join")
def on_join(data):
    data = data or {}
    name = (data.get("name") or "ناشناس").strip()[:24] or "ناشناس"
    avatar_url = None
    if chat:
        with CHAT_LOCK:
            avatar_url = chat.get_avatar(name)
    with LOCK:
        if room:
            room.users[request.sid] = {"name": name, "avatar_url": avatar_url}
    if room:
        emit("state_sync", room.to_public_dict())
    broadcast_notify("join", name)
    broadcast_presence()


@socketio.on("control")
def on_control(data):
    data = data or {}
    action = data.get("action")
    name = (data.get("name") or "ناشناس").strip() or "ناشناس"

    with LOCK:
        if not room:
            return
        if action == "play":
            room.set_play(float(data.get("at", room.current_position())))
        elif action == "pause":
            room.set_pause(float(data.get("at", room.current_position())))
        elif action == "seek":
            room.seek(float(data.get("to", 0)))
        elif action == "rate":
            room.set_rate(float(data.get("rate", 1.0)))
        elif action == "select":
            room.select_item(int(data.get("index")))
        else:
            return
        safe_save(room)
        payload = room.to_public_dict()

    socketio.emit("state_sync", payload, include_self=False)
    broadcast_notify(f"ctrl_{action}", name, extra=data)


@socketio.on("request_sync")
def on_request_sync():
    if room:
        emit("state_sync", room.to_public_dict())


@socketio.on("chat_send")
def on_chat_send(data):
    data = data or {}
    name = (data.get("name") or "ناشناس").strip()[:24] or "ناشناس"
    text = (data.get("text") or "").strip()[:Config.CHAT_MAX_TEXT_LEN]
    image_url = (data.get("image_url") or "").strip() or None

    if not text and not image_url:
        return
    if image_url and "/stream/media/chat/" not in image_url:
        image_url = None

    avatar_url = None
    if chat:
        with CHAT_LOCK:
            avatar_url = chat.get_avatar(name)

    msg = {
        "id": new_id(), "name": name, "text": text,
        "image_url": image_url, "avatar_url": avatar_url,
        "ts": time.time(),
    }
    if chat:
        with CHAT_LOCK:
            chat.add_message(msg)
    socketio.emit("chat_message", msg)


if __name__ == "__main__":
    gevent.spawn(_chat_purge_loop)
    from gevent import pywsgi
    from geventwebsocket.handler import WebSocketHandler
    server = pywsgi.WSGIServer(
        (Config.HOST, Config.PORT),
        app,
        handler_class=WebSocketHandler,
    )
    log_debug(f"Running on http://{Config.HOST}:{Config.PORT} (gevent)")
    server.serve_forever()