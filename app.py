from gevent import monkey
monkey.patch_all()

import gevent
import json
import os
import re as _re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit, join_room

from config import Config
import archive_scraper
import db as dbmod
from db import User as DBUser
from livekit_auth import generate_livekit_token
import mail as mailmod
from srt_to_vtt import convert_srt_to_vtt


def log_debug(msg):
    print(f"[DEBUG_STREAM] {msg}", file=sys.stderr, flush=True)


def safe_save(room_obj):
    try:
        room_obj.save()
    except Exception as e:
        log_debug(f"[CRITICAL] State save error: {e}")


try:
    from state import LOCK, CHAT_LOCK, room, chat, rooms
    log_debug("Successfully imported state module (room + chat + rooms).")
except ImportError as e:
    log_debug(f"Failed to import state elements: {e}")
    LOCK = gevent.lock.Semaphore()
    CHAT_LOCK = gevent.lock.Semaphore()
    room = None
    chat = None
    from state import rooms


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


# ────────────────────────────────────────────────────────────────────────────
# Auth / room resolution helpers
# ────────────────────────────────────────────────────────────────────────────

def _bearer_token() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    t = request.args.get("token") or (request.form.get("token") or "").strip()
    if t:
        return t
    try:
        body = request.get_json(force=True, silent=True) or {}
        return str(body.get("token") or "").strip()
    except Exception:
        return ""


def _current_user() -> DBUser | None:
    if getattr(g, "user", None) is not None:
        return g.user
    token = _bearer_token()
    if not token:
        return None
    payload = dbmod.decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    session = dbmod.SessionLocal()
    try:
        user = session.get(DBUser, payload.get("sub"))
        g.user = user if (user and user.is_active) else None
        return g.user
    finally:
        session.close()


def _require_user() -> DBUser:
    u = _current_user()
    if not u:
        raise _AuthError()
    return u


class _AuthError(Exception):
    pass


@app.errorhandler(_AuthError)
def _handle_auth_error(e):
    return jsonify({"error": "ابتدا وارد حساب شو (دوباره لاگین کن)"}), 401


def _current_room():
    """RoomState for the authenticated request's current room (or None)."""
    user = _current_user()
    if not user or not user.current_room_id:
        return None
    return rooms.get(user.current_room_id)


def _current_room_code() -> str | None:
    user = _current_user()
    return (user.current_room_id if user else None)


def _may_control(user: DBUser) -> bool:
    return bool(user and user.is_active and (user.can_control or user.role == "admin"))


def _may_youtube(user: DBUser) -> bool:
    return bool(user and user.is_active and (user.youtube_allowed or user.role == "admin"))


def _may_add(user: DBUser) -> bool:
    if not user or not user.is_active:
        return False
    if user.role == "admin":
        return True
    return user.upload_quota == -1 or user.uploads_used < user.upload_quota


def _is_admin(user: DBUser) -> bool:
    return bool(user and user.is_active and user.role == "admin")


def _bump_upload_used(session, user: DBUser, n: int = 1):
    if user.role == "admin":
        return
    user.uploads_used = max(0, (user.uploads_used or 0) + n)


def _remaining_add_slots(user: DBUser) -> int | None:
    """None = unlimited (admin or quota -1). Otherwise the number of media
    this user can still add."""
    if user.role == "admin" or user.upload_quota == -1:
        return None
    return max(0, user.upload_quota - (user.uploads_used or 0))


def _room_channel(code: str) -> str:
    return f"room:{code}"


def broadcast_state(room_obj=None):
    room_obj = room_obj or room
    if room_obj is None:
        return
    if room_obj.room_id:
        socketio.emit("state_sync", room_obj.to_public_dict(), to=_room_channel(room_obj.room_id))
    else:
        socketio.emit("state_sync", room_obj.to_public_dict())


def broadcast_notify(ntype: str, name: str, room_code: str = None, **extra):
    payload = {"type": ntype, "name": name, "ts": time.time()}
    payload.update(extra)
    if room_code:
        socketio.emit("notify", payload, to=_room_channel(room_code))
    else:
        socketio.emit("notify", payload)


def broadcast_presence(room_obj=None):
    room_obj = room_obj or room
    if room_obj is None:
        return
    payload = {
        "online": len(room_obj.users),
        "users": room_obj.users_public_list(),
    }
    if room_obj.room_id:
        socketio.emit("presence", payload, to=_room_channel(room_obj.room_id))
    else:
        socketio.emit("presence", payload)


def _room_for_item(item_id: str):
    """Finds the RoomState that currently contains the given playlist item
    (used by background encode greenlets, which outlive the request)."""
    for code, rs in rooms._rooms.items():
        if any(it.get("id") == item_id for it in rs.playlist):
            return rs
    return None


def url_ext(url: str) -> str:
    clean = url.split("?", 1)[0].split("#", 1)[0]
    return clean.rsplit(".", 1)[-1].lower() if "." in clean else ""


def free_space_mb(path: str) -> float:
    return shutil.disk_usage(path).free / (1024 * 1024)


def _download_to_file(url: str, dest_path: str, timeout: int = 25) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest_path, "wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 1024)


def _check_link_ok(url: str, timeout: int = 15) -> bool:
    """Cheap reachability check for a direct media URL, mirroring what ffmpeg
    will do at encode time: browser User-Agent, follow redirects, TLS
    verification, plain GET. Reads a single byte then drops the connection, so
    even a server that ignores Range/HEAD never transfers the whole file.

    Returns False for HTTP >= 400, TLS/certificate failures, DNS/connection
    errors and timeouts — i.e. any URL that could never be encoded."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "video/*,*/*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok = 200 <= resp.status < 400
            resp.read(1)
            return ok
    except Exception:
        return False


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
        args = [
            "ffprobe", "-v", "error",
            "-print_format", "json",
            "-show_streams", "-show_format",
            "-rw_timeout", str(timeout * 1_000_000),
        ]
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            args += ["-headers",
                     "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36\r\n"]
        args += [source]
        proc = subprocess.run(args, capture_output=True, timeout=timeout + 10)
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
    """Pick the highest available rendition as the default so playback starts
    at max quality (the ladder is already capped at the source height by
    _build_ladder, so this never upscales). The rest of the ladder is still
    only encoded on-demand, the moment a viewer asks for it (see
    _encode_rendition / the /api/request-quality endpoint)."""
    return ladder[0] if ladder else None


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
    cmd = ["ffmpeg", "-y", "-threads", "0"]
    if isinstance(source, str) and source.startswith(("http://", "https://")):
        # Some download mirrors (aparatchi-dlcenter, hollowofthealley, ...) 404
        # or 403 plain ffmpeg/Lavf requests. Send a browser User-Agent so the
        # direct-link encodes behave like the browser's own download does.
        cmd += ["-headers",
                "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36\r\n"]
    cmd += [
        "-i", source,
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
    return cmd


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
    rs = _room_for_item(item_id)
    if rs is None:
        return
    with LOCK:
        item = rs.find_item(item_id)
        if item:
            item.setdefault("subtitles", []).extend(new_subs)
            safe_save(rs)
            title = item["title"]
        else:
            title = ""
    broadcast_state(rs)
    broadcast_notify("subtitle_auto_extracted", requester_name, room_code=rs.room_id,
                     title=title, count=len(new_subs))


def _encode_rendition(item_id: str, source: str, label: str, height: int, vbr: str, abr: str,
                       requester_name: str, is_default: bool, duration_s: float = None):
    """Encodes exactly one rendition. Used both for the default rendition
    kicked off right after an item is added, and for any other rendition a
    viewer later requests via /api/request-quality."""
    out_dir = os.path.join(Config.HLS_DIR, item_id)
    rendition_dir = os.path.join(out_dir, label)

    def set_rendition_status(status, error=None):
        with LOCK:
            rs = _room_for_item(item_id)
            item = rs.find_item(item_id) if rs else None
            if not item:
                return None
            for r in item.get("renditions", []):
                if r["label"] == label:
                    r["status"] = status
                    if error:
                        r["error"] = error[:200]
                    else:
                        r.pop("error", None)
            if rs:
                safe_save(rs)
            return item

    def collect_ready(item):
        return [r for r in item.get("renditions", []) if r["status"] in ("ready", "complete")]

    def fail(msg: str):
        log_debug(f"Rendition encode failed [{item_id}/{label}]: {msg}")
        shutil.rmtree(rendition_dir, ignore_errors=True)
        item = set_rendition_status("error", msg)
        rs = _room_for_item(item_id)
        if item and is_default and rs is not None:
            with LOCK:
                item["status"] = "error"
                item["error"] = msg[:300]
                safe_save(rs)
        if rs is None:
            return
        broadcast_state(rs)
        title = item["title"] if item else ""
        if is_default:
            broadcast_notify("media_error", requester_name, room_code=rs.room_id, id=item_id, title=title)
        else:
            broadcast_notify("quality_error", requester_name, room_code=rs.room_id,
                             id=item_id, title=title, label=label)

    free_mb = free_space_mb(Config.MEDIA_DIR)
    if free_mb < MIN_FREE_MB_FOR_TRANSCODE:
        fail(f"فضای آزاد دیسک سرور ناکافی است (فقط {free_mb:.0f}MB آزاد).")
        return

    set_rendition_status("queued")
    rs = _room_for_item(item_id)
    if is_default and rs is not None:
        with LOCK:
            item = rs.find_item(item_id)
            if item:
                item["status"] = "queued"
                safe_save(rs)
    if rs is None:
        return
    broadcast_state(rs)

    acquired = ENCODE_SEMAPHORE.acquire()  # blocks this greenlet only
    try:
        rs = _room_for_item(item_id)
        if rs is None:
            log_debug(f"Item {item_id} removed while queued — aborting rendition encode.")
            return

        set_rendition_status("encoding")
        if is_default:
            with LOCK:
                item = rs.find_item(item_id)
                if item:
                    item["status"] = "encoding"
                    safe_save(rs)
        broadcast_state(rs)

        dur = duration_s
        if dur is None:
            dur = _get_duration_s(_ffprobe_source(source))

        os.makedirs(rendition_dir, exist_ok=True)

        def on_ready(elapsed_s):
            item = set_rendition_status("ready")
            if not item:
                return
            rrs = _room_for_item(item_id)
            if rrs is None:
                return
            with LOCK:
                aspect = item.get("_aspect") or 16 / 9
                _rewrite_master_playlist(out_dir, collect_ready(item), aspect)
                if is_default:
                    item["status"] = "ready"
                    item["src"] = f"/stream/media/hls/{item_id}/master.m3u8"
                    safe_save(rrs)
                title = item["title"]
            broadcast_state(rrs)
            if is_default:
                broadcast_notify("media_partial_ready", requester_name, room_code=rrs.room_id,
                                 id=item_id, title=title)
            else:
                broadcast_notify("quality_partial_ready", requester_name, room_code=rrs.room_id,
                                 id=item_id, title=title, label=label)

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
        rrs = _room_for_item(item_id)
        if rrs is None:
            return
        with LOCK:
            aspect = item.get("_aspect") or 16 / 9
            _rewrite_master_playlist(out_dir, collect_ready(item), aspect)
            if is_default:
                item["status"] = "complete"
                item["src"] = f"/stream/media/hls/{item_id}/master.m3u8"
                item.pop("error", None)
            safe_save(rrs)
            title = item["title"]
        broadcast_state(rrs)
        if is_default:
            broadcast_notify("media_ready", requester_name, room_code=rrs.room_id,
                             id=item_id, title=title)
        else:
            broadcast_notify("quality_ready", requester_name, room_code=rrs.room_id,
                             id=item_id, title=title, label=label)

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
        rs = _room_for_item(item_id)
        if rs is None:
            return
        with LOCK:
            item = rs.find_item(item_id)
            if item:
                item["status"] = "error"
                item["error"] = msg[:300]
                safe_save(rs)
                title = item["title"]
            else:
                title = ""
        broadcast_state(rs)
        broadcast_notify("media_error", requester_name, room_code=rs.room_id, id=item_id, title=title)

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

    rs = _room_for_item(item_id)
    if rs is None:
        log_debug(f"Item {item_id} vanished before encoding could start.")
        return
    with LOCK:
        item = rs.find_item(item_id)
        if not item:
            log_debug(f"Item {item_id} vanished before encoding could start.")
            return
        item["renditions"] = renditions
        item["status"] = "queued"
        item["_source"] = source
        item["_aspect"] = aspect
        if local_raw_path:
            item["_raw_path"] = local_raw_path
        safe_save(rs)
    broadcast_state(rs)

    os.makedirs(os.path.join(Config.HLS_DIR, item_id), exist_ok=True)
    gevent.spawn(_extract_subtitles_async, item_id, source, sub_streams, requester_name)
    gevent.spawn(_encode_rendition, item_id, source, default_label, default_height,
                 default_vbr, default_abr, requester_name, True, duration_s)


def request_quality(item_id: str, label: str, requester_name: str):
    """Kicks off an on-demand rendition encode when a viewer picks a
    not-yet-ready quality from the menu. Idempotent: a second request for a
    rendition that's already queued/encoding/ready is a no-op."""
    rs = _room_for_item(item_id)
    if rs is None:
        return False, "آیتم پیدا نشد"
    with LOCK:
        item = rs.find_item(item_id)
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
        safe_save(rs)
    broadcast_state(rs)
    gevent.spawn(_encode_rendition, item_id, source, label, height, vbr, abr, requester_name, False)
    return True, "started"


# ────────────────────────────────────────────────────────────────────────────
# Orphan-file cleanup helpers
# ────────────────────────────────────────────────────────────────────────────

def _collect_referenced_files() -> tuple:
    upload_refs, sub_refs, hls_ids = set(), set(), set()
    seen = set()
    for rs in list(rooms._rooms.values()) + ([room] if room is not None else []):
        for item in rs.playlist:
            if item.get("id") in seen:
                continue
            seen.add(item["id"])
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
        targets = dict(rooms._chats)
        if chat is not None:
            targets.setdefault(None, chat)
        for code, chat_obj in targets.items():
            with CHAT_LOCK:
                removed = chat_obj.purge_older_than(Config.CHAT_RETENTION_SECONDS)
            if removed:
                _delete_chat_images(removed)
                if code:
                    socketio.emit("chat_purged", {"removed": len(removed)}, to=_room_channel(code))
                else:
                    socketio.emit("chat_purged", {"removed": len(removed)})
                log_debug(f"Chat auto-purge removed {len(removed)} message(s) from room {code}.")


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
    _require_user()
    cur = _current_room()
    return jsonify(cur.to_public_dict() if cur else {})


@app.route("/stream/api/upload", methods=["POST"])
def api_upload():
    user = _require_user()
    cur = _current_room()
    if not cur:
        return jsonify({"error": "اتاق فعال نیست — اول وارد حساب شو"}), 400
    if not _may_add(user):
        return jsonify({"error": "سهمیه افزودن ویدیوی تو پر شده؛ با ادمین هماهنگ کن"}), 403
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
        "added_at": time.time(), "status": "queued", "_room_id": cur.room_id,
        "added_by_user_id": user.id,
    }
    with LOCK:
        cur.add_item(item)
        safe_save(cur)
    session = dbmod.SessionLocal()
    try:
        _bump_upload_used(session, user)
        session.commit()
    finally:
        session.close()
    broadcast_state(cur)
    broadcast_notify("playlist_add_processing", name, room_code=cur.room_id, title=item["title"])
    gevent.spawn(start_item_encoding, item_id, raw_path, name, raw_path)
    return jsonify(item)


# ────────────────────────────────────────────────────────────────────────────
# Search archive (DonyayeSerial / Animex) + bulk "add all episodes"
# ────────────────────────────────────────────────────────────────────────────

ARCHIVE_MAX_ADD = 60


def _archive_err(e):
    msg = getattr(e, "reason", None) or str(e)
    if "timed out" in str(e).lower() or "timeout" in str(e).lower():
        return jsonify({"error": "سایت مبدأ پاسخ نداد؛ دوباره تلاش کن"}), 504
    return jsonify({"error": "خطا در دریافت اطلاعات از سایت مبدأ"}), 502


@app.route("/stream/api/archive/search", methods=["POST"])
def api_archive_search():
    data = request.get_json(force=True, silent=True) or {}
    q = (data.get("q") or "").strip()
    sources = data.get("sources") or list(archive_scraper.SOURCES)
    if not q:
        return jsonify({"error": "عبارت جستجو خالی است"}), 400
    if len(q) > 80:
        return jsonify({"error": "عبارت جستجو خیلی طولانی است"}), 400
    try:
        results = archive_scraper.search(q, sources)
    except Exception as e:
        return _archive_err(e)
    return jsonify({"results": results})


@app.route("/stream/api/archive/title", methods=["POST"])
def api_archive_title():
    data = request.get_json(force=True, silent=True) or {}
    source = (data.get("source") or "").strip()
    url = (data.get("url") or "").strip()
    if source not in archive_scraper.SOURCES:
        return jsonify({"error": "منبع ناشناخته است"}), 400
    if not url.startswith((archive_scraper.DS_BASE, archive_scraper.AX_BASE)):
        return jsonify({"error": "آدرس صفحه معتبر نیست"}), 400
    try:
        info = archive_scraper.title(source, url)
    except Exception as e:
        return _archive_err(e)
    return jsonify(info)


@app.route("/stream/api/archive/files", methods=["POST"])
def api_archive_files():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url or "hollowofthealley" not in url:
        return jsonify({"error": "آدرس پوشه دانلود معتبر نیست"}), 400
    try:
        files = archive_scraper.list_dir(url)
    except Exception as e:
        return _archive_err(e)
    return jsonify({"files": files})


def _add_url_item(url: str, title: str, name: str, room_code: str = None,
                  added_by_user_id: str = None):
    """Shared logic for the single add-url endpoint and the bulk archive add."""
    item_id = new_id()
    is_hls = _is_hls(url)
    needs_encode = not is_hls
    rs = rooms.get(room_code) if room_code else room
    if rs is None:
        return None
    item = {
        "id": item_id, "type": "url",
        "title": title or url.rsplit("/", 1)[-1].split("?")[0] or "ویدیو",
        "src": None if needs_encode else url,
        "subtitles": [], "audio_tracks": [], "added_by": name,
        "added_at": time.time(),
        "status": "queued" if needs_encode else "ready",
        "_room_id": rs.room_id,
        "added_by_user_id": added_by_user_id,
    }
    with LOCK:
        rs.add_item(item)
        safe_save(rs)
    broadcast_state(rs)
    if needs_encode:
        broadcast_notify("playlist_add_processing", name, room_code=rs.room_id, title=item["title"])
        gevent.spawn(start_item_encoding, item_id, url, name)
    else:
        broadcast_notify("playlist_add", name, room_code=rs.room_id, title=item["title"])
    return item


@app.route("/stream/api/add-many", methods=["POST"])
def api_add_many():
    """Bulk-adds episodes/qualities scraped from the archive to the playlist."""
    user = _require_user()
    cur = _current_room()
    if not cur:
        return jsonify({"error": "اتاق فعال نیست — اول وارد حساب شو"}), 400
    if not _may_add(user):
        return jsonify({"error": "سهمیه افزودن ویدیوی تو پر شده؛ با ادمین هماهنگ کن"}), 403
    data = request.get_json(force=True, silent=True) or {}
    items = data.get("items") or []
    name = (data.get("name") or "ناشناس").strip()
    if not isinstance(items, list) or not items:
        return jsonify({"error": "هیچ قسمتی برای افزودن نیست"}), 400
    if len(items) > ARCHIVE_MAX_ADD:
        return jsonify({"error": f"بیش از حد مجاز است — حداکثر {ARCHIVE_MAX_ADD} قسمت در هر بار"}), 400

    added = 0
    skipped = 0
    dead = []
    first_title = ""
    items = [it for it in items[:ARCHIVE_MAX_ADD] if isinstance(it, dict) and
             (it.get("url") or "").strip().startswith(("https://", "http://"))]
    if not items:
        return jsonify({"error": "هیچ لینک معتبری برای افزودن نیست"}), 400

    remaining_slots = _remaining_add_slots(user)
    if remaining_slots is not None and remaining_slots <= 0:
        return jsonify({"error": "سهمیه افزودن ویدیوی تو پر شده؛ با ادمین هماهنگ کن"}), 403

    from gevent.pool import Group
    verdicts = Group().map(lambda it: _check_link_ok(it.get("url", "").strip()), items)

    for it, ok in zip(items, verdicts):
        url = (it.get("url") or "").strip()
        title = (it.get("title") or "").strip()
        if not ok:
            dead.append(title or url)
            continue
        if remaining_slots is not None and added >= remaining_slots:
            skipped += 1
            continue
        with LOCK:
            dup = any(
                itm.get("src") == url or itm.get("_source") == url
                for itm in cur.playlist
            )
        if dup:
            skipped += 1
            continue
        item = _add_url_item(url, title, name, room_code=cur.room_id, added_by_user_id=user.id)
        if item is None:
            continue
        added += 1
        first_title = item["title"]
    if added > 0:
        session = dbmod.SessionLocal()
        try:
            _bump_upload_used(session, user, added)
            session.commit()
        finally:
            session.close()
    if added == 0:
        return jsonify({"added": 0, "skipped": skipped, "dead": dead})
    broadcast_notify("playlist_add_many", name, room_code=cur.room_id,
                     title=first_title, count=added, skipped=skipped, dead=len(dead))
    return jsonify({"added": added, "skipped": skipped, "dead": dead})


@app.route("/stream/api/youtube-formats", methods=["POST"])
def api_youtube_formats():
    user = _require_user()
    if not _may_youtube(user):
        return jsonify({"error": "مجوز افزودن لینک یوتیوب برای تو فعال نیست؛ با ادمین هماهنگ کن"}), 403
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
        rs = _room_for_item(item_id)
        if rs is None:
            return
        with LOCK:
            item = rs.find_item(item_id)
            if item:
                item["status"] = "error"
                item["error"] = msg[:300]
                safe_save(rs)
                title = item["title"]
            else:
                title = ""
        broadcast_state(rs)
        broadcast_notify("media_error", requester_name, room_code=rs.room_id, id=item_id, title=title)

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
    user = _require_user()
    cur = _current_room()
    if not cur:
        return jsonify({"error": "اتاق فعال نیست — اول وارد حساب شو"}), 400
    if not _may_youtube(user):
        return jsonify({"error": "مجوز افزودن لینک یوتیوب برای تو فعال نیست؛ با ادمین هماهنگ کن"}), 403
    if not _may_add(user):
        return jsonify({"error": "سهمیه افزودن ویدیوی تو پر شده؛ با ادمین هماهنگ کن"}), 403
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
        "_room_id": cur.room_id, "added_by_user_id": user.id,
    }
    with LOCK:
        cur.add_item(item)
        safe_save(cur)
    session = dbmod.SessionLocal()
    try:
        _bump_upload_used(session, user)
        session.commit()
    finally:
        session.close()
    broadcast_state(cur)
    broadcast_notify("playlist_add_processing", name, room_code=cur.room_id, title=title)
    gevent.spawn(_fetch_and_encode_youtube, item_id, yt_url, fmt_selector, name)
    return jsonify(item)


VIDEO_EXTS = {"mp4", "webm", "ogg", "ogv", "mov", "m4v", "mkv", "avi", "ts"}


def _is_hls(url: str) -> bool:
    return url_ext(url) == "m3u8" or "m3u8" in url.split("?")[0].lower()


@app.route("/stream/api/add-url", methods=["POST"])
def api_add_url():
    user = _require_user()
    cur = _current_room()
    if not cur:
        return jsonify({"error": "اتاق فعال نیست — اول وارد حساب شو"}), 400
    if not _may_add(user):
        return jsonify({"error": "سهمیه افزودن ویدیوی تو پر شده؛ با ادمین هماهنگ کن"}), 403
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    title = (data.get("title") or "").strip()
    name = (data.get("name") or "ناشناس").strip()

    if not url:
        return jsonify({"error": "لینک خالی است"}), 400

    if url.startswith(("https://", "http://")) and not _check_link_ok(url):
        return jsonify({"error": "این لینک روی سرور منبع پاسخ نمی‌دهد (خراب است)"}), 422

    item = _add_url_item(url, title, name, room_code=cur.room_id, added_by_user_id=user.id)
    if item is None:
        return jsonify({"error": "اتاق فعال نیست"}), 500
    session = dbmod.SessionLocal()
    try:
        _bump_upload_used(session, user)
        session.commit()
    finally:
        session.close()
    return jsonify(item)


@app.route("/stream/api/request-quality", methods=["POST"])
def api_request_quality():
    user = _require_user()
    if not _may_control(user):
        return jsonify({"error": "فقط کنترل‌کننده یا ادمین می‌تواند کیفیت را تغییر دهد"}), 403
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


# ────────────────────────────────────────────────────────────────────────────
# LiveKit voice room
# ────────────────────────────────────────────────────────────────────────────

@app.route("/stream/api/voice/token", methods=["POST"])
def api_voice_token():
    """Issues a short-lived LiveKit access token scoped to the user's current
    stream room, so each room gets its own voice channel.

    identity is always unique per connection (same person may join from two
    tabs/devices); display name and avatar are carried in `name` / `metadata`
    so other clients can render them without extra lookups."""
    user = _require_user()
    room_code = _current_room_code()
    if not room_code:
        return jsonify({"error": "اتاق فعال نیست — اول وارد حساب شو"}), 400
    if not Config.LIVEKIT_API_KEY or not Config.LIVEKIT_API_SECRET:
        return jsonify({"error": "LiveKit روی سرور تنظیم نشده است"}), 500
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or user.username or "ناشناس").strip()[:24] or "ناشناس"
    avatar = (data.get("avatar_url") or "").strip()
    identity = f"{name}-{uuid.uuid4().hex[:6]}"
    metadata = json.dumps({"name": name, "avatar_url": avatar}, ensure_ascii=False)
    voice_room = f"{Config.LIVEKIT_ROOM}-{room_code}"
    token = generate_livekit_token(
        Config.LIVEKIT_API_KEY, Config.LIVEKIT_API_SECRET,
        voice_room, identity, name, metadata=metadata,
        ttl=Config.LIVEKIT_TOKEN_TTL,
    )
    return jsonify({
        "url": Config.LIVEKIT_URL,
        "token": token,
        "room": voice_room,
        "identity": identity,
    })


@app.route("/stream/api/add-live", methods=["POST"])
def api_add_live():
    user = _require_user()
    cur = _current_room()
    if not cur:
        return jsonify({"error": "اتاق فعال نیست — اول وارد حساب شو"}), 400
    if not _may_add(user):
        return jsonify({"error": "سهمیه افزودن ویدیوی تو پر شده؛ با ادمین هماهنگ کن"}), 403
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
        "_room_id": cur.room_id, "added_by_user_id": user.id,
    }
    with LOCK:
        cur.add_item(item)
        safe_save(cur)
    session = dbmod.SessionLocal()
    try:
        _bump_upload_used(session, user)
        session.commit()
    finally:
        session.close()
    broadcast_state(cur)
    broadcast_notify("playlist_add_live", name, room_code=cur.room_id, title=title)
    return jsonify(item)


@app.route("/stream/api/playlist/<item_id>", methods=["DELETE"])
def api_remove_item(item_id):
    user = _require_user()
    if not _may_control(user):
        return jsonify({"error": "فقط کنترل‌کننده یا ادمین می‌تواند آیتم را حذف کند"}), 403
    cur = _current_room()
    name = request.args.get("name", user.username or "ناشناس")
    do_cleanup = request.args.get("cleanup", "1") != "0"
    if cur is None:
        return jsonify({"error": "اتاق فعال نیست"}), 400
    with LOCK:
        item = cur.find_item(item_id)
        title = item["title"] if item else "ویدیو"
        raw_path = item.get("_raw_path") if item else None
        cur.remove_item(item_id)
        safe_save(cur)
    broadcast_state(cur)
    broadcast_notify("playlist_remove", name, room_code=cur.room_id, title=title)
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
    _require_user()
    cur = _current_room()
    if not cur:
        return jsonify({"error": "اتاق فعال نیست — اول وارد حساب شو"}), 400
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
        item = cur.find_item(item_id)
        if not item:
            return jsonify({"error": "آیتم پلی‌لیست پیدا نشد"}), 404
        item.setdefault("subtitles", []).append({"id": sub_id, "label": label, "lang": lang, "url": url})
        safe_save(cur)
    broadcast_state(cur)
    broadcast_notify("subtitle_add", name, room_code=cur.room_id,
                     title=item.get("title", ""), label=label)
    return jsonify({"id": sub_id, "url": url})


@app.route("/stream/api/subtitle-url", methods=["POST"])
def api_subtitle_url():
    _require_user()
    cur = _current_room()
    if not cur:
        return jsonify({"error": "اتاق فعال نیست — اول وارد حساب شو"}), 400
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
        item = cur.find_item(item_id)
        if not item:
            return jsonify({"error": "آیتم پلی‌لیست پیدا نشد"}), 404
        item.setdefault("subtitles", []).append({"id": sub_id, "label": label, "lang": lang, "url": sub_url})
        safe_save(cur)
    broadcast_state(cur)
    broadcast_notify("subtitle_add", name, room_code=cur.room_id,
                     title=item.get("title", ""), label=label)
    return jsonify({"id": sub_id, "url": sub_url})


@app.route("/stream/api/audio-track", methods=["POST"])
def api_add_audio_track():
    _require_user()
    cur = _current_room()
    if not cur:
        return jsonify({"error": "اتاق فعال نیست — اول وارد حساب شو"}), 400
    data = request.get_json(force=True, silent=True) or {}
    item_id = data.get("item_id")
    label = (data.get("label") or "دوبله").strip()
    url = (data.get("url") or "").strip()
    name = (data.get("name") or "ناشناس").strip()

    if not item_id or not url:
        return jsonify({"error": "ورودی ناقص است"}), 400

    with LOCK:
        item = cur.find_item(item_id)
        if not item:
            return jsonify({"error": "آیتم پلی‌لیست پیدا نشد"}), 404
        item.setdefault("audio_tracks", []).append({"id": new_id(), "label": label, "url": url})
        safe_save(cur)
    broadcast_state(cur)
    broadcast_notify("audio_track_add", name, room_code=cur.room_id,
                     title=item.get("title", ""), label=label)
    return jsonify({"ok": True})


# ────────────────────────────────────────────────────────────────────────────
# REST API — chat & avatars
# ────────────────────────────────────────────────────────────────────────────

@app.route("/stream/api/chat/history")
def api_chat_history():
    user = _require_user()
    if not user.current_room_id:
        return jsonify({"messages": []})
    chat_obj = rooms.chat(user.current_room_id)
    with CHAT_LOCK:
        return jsonify({"messages": chat_obj.public_history()})


@app.route("/stream/api/chat/clear", methods=["POST"])
def api_chat_clear():
    _require_user()
    room_code = _current_room_code()
    if not room_code:
        return jsonify({"error": "اتاق فعال نیست"}), 400
    chat_obj = rooms.chat(room_code)
    name = (request.args.get("name") or request.form.get("name") or "ناشناس")
    with CHAT_LOCK:
        removed = chat_obj.clear()
    _delete_chat_images(removed)
    socketio.emit("chat_cleared", {"by": name}, to=_room_channel(room_code))
    return jsonify({"ok": True, "removed": len(removed)})


@app.route("/stream/api/chat/image", methods=["POST"])
def api_chat_image():
    _require_user()
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
    user = _require_user()
    room_code = _current_room_code()
    f = request.files.get("file")
    name = (request.form.get("name") or user.username or "").strip()
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

    if room_code:
        chat_obj = rooms.chat(room_code)
        with CHAT_LOCK:
            chat_obj.set_avatar(name, url)
        rs = rooms.get(room_code)
        with LOCK:
            for u in rs.users.values():
                if u.get("name") == name:
                    u["avatar_url"] = url
        broadcast_presence(rs)
    return jsonify({"url": url})


# ────────────────────────────────────────────────────────────────────────────
# Auth — accounts & sessions (mirrors the arman-music pattern: bcrypt
# password hashes + JWT access/refresh tokens, DB-backed)
# ────────────────────────────────────────────────────────────────────────────

def _issue_tokens(session, user) -> dict:
    access = dbmod.create_access_token(user.id)
    refresh = dbmod.create_refresh_token(user.id)
    session.add(dbmod.RefreshToken(
        user_id=user.id, token=refresh,
        expires_at=datetime.now(timezone.utc) + timedelta(days=Config.JWT_REFRESH_TTL_DAYS),
    ))
    return {"access_token": access, "refresh_token": refresh}


@app.route("/stream/api/auth/register", methods=["POST"])
def api_auth_register():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if len(username) < 3 or len(username) > 24:
        return jsonify({"error": "نام کاربری باید ۳ تا ۲۴ حرف باشد"}), 400
    if not _re.match(r"^[A-Za-z0-9_.\-]+$", username):
        return jsonify({"error": "نام کاربری فقط حروف انگلیسی، عدد، _ و - می‌تواند باشد"}), 400
    if len(password) < 6:
        return jsonify({"error": "رمز عبور باید حداقل ۶ کاراکتر باشد"}), 400
    if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "یک ایمیل معتبر وارد کن"}), 400

    session = dbmod.SessionLocal()
    try:
        if dbmod.get_user_by_username_or_email(session, username):
            return jsonify({"error": "این نام کاربری قبلاً ثبت شده است"}), 409
        if dbmod.get_user_by_email(session, email):
            return jsonify({"error": "این ایمیل قبلاً ثبت شده است"}), 409

        is_first = dbmod.first_user_count(session)
        is_owner = dbmod.is_special_account(email)
        user = DBUser(
            username=username,
            display_name=display_name or username,
            email=email,
            email_verified=is_owner,
            password_hash=dbmod.hash_password(password),
            role="admin" if (is_first or is_owner) else "watcher",
            can_control=bool(is_first or is_owner),
            youtube_allowed=bool(is_first or is_owner),
            upload_quota=Config.DEFAULT_UPLOAD_QUOTA,
        )
        session.add(user)
        session.flush()
        own = dbmod.ensure_own_room(session, user)
        user.current_room_id = own.id

        if not is_owner:
            user.verification_token = dbmod.new_verification_token()
            user.verification_expires = datetime.now(timezone.utc) + timedelta(
                hours=Config.VERIFY_TOKEN_TTL_HOURS
            )
            session.commit()
            verify_url = (
                f"{Config.SITE_BASE_URL}/api/auth/verify-email?token={user.verification_token}"
            )
            mailmod.send_verification_email(user.email, verify_url)
            log_debug(f"Registered user '{username}' (unverified) — verification email sent")
            return jsonify({
                "needs_verification": True,
                "email": user.email,
                "message": "ایمیل تأیید برایت ارسال شد؛ لینک داخل آن را باز کن تا وارد شوی",
            }), 201

        tokens = _issue_tokens(session, user)
        session.commit()
        log_debug(f"Registered user '{username}' as {'admin' if is_owner else 'watcher'}")
        return jsonify({**tokens, "user": user.public_dict(), "room": own.public_dict()}), 201
    finally:
        session.close()


@app.route("/stream/api/auth/verify-email")
def api_auth_verify_email():
    token = (request.args.get("token") or "").strip()
    if not token:
        return "توکن نامعتبر است", 400
    session = dbmod.SessionLocal()
    try:
        user = session.execute(
            dbmod.select(DBUser).where(DBUser.verification_token == token)
        ).scalar_one_or_none()
        if not user:
            return _verify_page(False, "این لینک تأیید معتبر نیست یا قبلاً استفاده شده است.")
        exp = user.verification_expires
        if exp and exp.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            return _verify_page(False, "این لینک تأیید منقضی شده است؛ دوباره درخواست ارسال کن.")
        user.email_verified = True
        user.verification_token = None
        user.verification_expires = None
        session.commit()
        return _verify_page(True, "ایمیلت تأیید شد! حالا می‌توانی وارد حساب‌ات شوی.")
    finally:
        session.close()


def _verify_page(ok: bool, message: str):
    color = "#2c8f6b" if ok else "#c0392b"
    icon = "✓" if ok else "✗"
    return f"""<!doctype html><html dir="rtl" lang="fa"><head><meta charset="utf-8">
<title>تأیید ایمیل</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{margin:0;font-family:Tahoma,Arial,sans-serif;background:#f2f0ea;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.box{{max-width:440px;background:#fff;border-radius:18px;padding:36px;text-align:center;border:1px solid #e6e2d8;margin:20px}}
.icon{{font-size:46px;color:{color};font-weight:bold}}
h2{{margin:14px 0 10px;font-size:18px;color:#2b2b2b}}
p{{margin:0 0 22px;color:#555;font-size:14px;line-height:1.9}}
a.btn{{display:inline-block;background:linear-gradient(135deg,#d4a017,#6b4e93);color:#fff;text-decoration:none;font-weight:bold;padding:13px 34px;border-radius:12px;font-size:14px}}</style>
</head><body><div class="box">
<div class="icon">{icon}</div>
<h2>{"تأیید موفق بود" if ok else "تأیید ناموفق"}</h2>
<p>{message}</p>
<a class="btn" href="{Config.SITE_BASE_URL}/">ورود به تماشای مشترک</a>
</div></body></html>"""


@app.route("/stream/api/auth/resend-verification", methods=["POST"])
def api_auth_resend_verification():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "ایمیل را وارد کن"}), 400
    session = dbmod.SessionLocal()
    try:
        user = dbmod.get_user_by_email(session, email)
        if not user:
            return jsonify({"ok": True}), 200  # don't leak which emails exist
        if user.email_verified or dbmod.is_special_account(user.email):
            return jsonify({"ok": True, "already_verified": True}), 200
        user.verification_token = dbmod.new_verification_token()
        user.verification_expires = datetime.now(timezone.utc) + timedelta(
            hours=Config.VERIFY_TOKEN_TTL_HOURS
        )
        session.commit()
        verify_url = (
            f"{Config.SITE_BASE_URL}/api/auth/verify-email?token={user.verification_token}"
        )
        mailmod.send_verification_email(user.email, verify_url)
        return jsonify({"ok": True})
    finally:
        session.close()


@app.route("/stream/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json(force=True, silent=True) or {}
    login = (data.get("username") or data.get("login") or "").strip()
    password = data.get("password") or ""
    if not login or not password:
        return jsonify({"error": "نام کاربری و رمز عبور را وارد کن"}), 400

    session = dbmod.SessionLocal()
    try:
        user = dbmod.get_user_by_username_or_email(session, login)
        if not user or not dbmod.verify_password(password, user.password_hash):
            return jsonify({"error": "نام کاربری یا رمز عبور اشتباه است"}), 401
        if not user.is_active:
            return jsonify({"error": "حساب تو غیرفعال شده؛ با ادمین تماس بگیر"}), 403
        if not user.email_verified and not dbmod.is_special_account(user.email):
            return jsonify({
                "error": "اول ایمیلت را تأیید کن؛ لینک تأیید برایت ارسال شده است",
                "needs_verification": True,
                "email": user.email or "",
            }), 403
        own = dbmod.ensure_own_room(session, user)
        if not user.current_room_id:
            user.current_room_id = own.id
        tokens = _issue_tokens(session, user)
        session.commit()
        return jsonify({**tokens, "user": user.public_dict(), "room": own.public_dict()})
    finally:
        session.close()


# ────────────────────────────────────────────────────────────────────────────
# OAuth2 login — Google & GitHub (reuses the arman-music app credentials)
# ────────────────────────────────────────────────────────────────────────────

OAUTH_STATES = {}  # state -> (provider, issued_at) — single-server, in-memory


def _oauth_redirect_uri(provider: str) -> str:
    return f"{Config.SITE_BASE_URL}/api/auth/oauth/{provider}/callback"


def _new_oauth_state(provider: str) -> str:
    state = secrets.token_urlsafe(24)
    OAUTH_STATES[state] = (provider, time.time())
    return state


def _clean_oauth_states():
    now = time.time()
    for k, (_, ts) in list(OAUTH_STATES.items()):
        if now - ts > 600:
            OAUTH_STATES.pop(k, None)


def _oauth_start(provider: str):
    _clean_oauth_states()
    state = _new_oauth_state(provider)
    redirect_uri = urllib.parse.quote(_oauth_redirect_uri(provider), safe="")
    if provider == "google":
        cid = Config.GOOGLE_OAUTH_CLIENT_ID
        if not cid:
            return jsonify({"error": "ورود با گوگل هنوز تنظیم نشده است"}), 503
        return redirect(
            "https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={cid}&redirect_uri={redirect_uri}&response_type=code"
            "&scope=openid%20email%20profile&state=" + state
        )
    if provider == "github":
        cid = Config.GITHUB_OAUTH_CLIENT_ID
        if not cid:
            return jsonify({"error": "ورود با گیت‌هاب هنوز تنظیم نشده است"}), 503
        return redirect(
            "https://github.com/login/oauth/authorize?"
            f"client_id={cid}&redirect_uri={redirect_uri}"
            "&scope=read:user%20user:email&state=" + state
        )
    return jsonify({"error": "روش ورود نامعتبر است"}), 400


def _post_form_json(url: str, data: dict):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _oauth_exchange(provider: str, code: str) -> dict | None:
    """Trades the auth code for a profile. Returns None on any failure."""
    try:
        if provider == "google":
            tok = _post_form_json("https://oauth2.googleapis.com/token", {
                "code": code,
                "client_id": Config.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": Config.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": _oauth_redirect_uri("google"),
                "grant_type": "authorization_code",
            })
            if "access_token" not in tok:
                return None
            req = urllib.request.Request(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {tok['access_token']}"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                info = json.loads(resp.read().decode())
            return {
                "provider": "google",
                "oauth_id": str(info.get("id") or ""),
                "email": (info.get("email") or "").lower(),
                "verified": bool(info.get("verified_email")),
                "name": info.get("name") or "",
            }
        if provider == "github":
            tok = _post_form_json("https://github.com/login/oauth/access_token", {
                "code": code,
                "client_id": Config.GITHUB_OAUTH_CLIENT_ID,
                "client_secret": Config.GITHUB_OAUTH_CLIENT_SECRET,
                "redirect_uri": _oauth_redirect_uri("github"),
            })
            access = tok.get("access_token")
            if not access:
                return None
            headers = {"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"}
            req = urllib.request.Request("https://api.github.com/user", headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                user = json.loads(resp.read().decode())
            email, verified = "", False
            try:
                req2 = urllib.request.Request("https://api.github.com/user/emails", headers=headers)
                with urllib.request.urlopen(req2, timeout=15) as resp:
                    emails = json.loads(resp.read().decode())
                for e in emails:
                    if e.get("primary") and e.get("verified"):
                        email, verified = (e.get("email") or "").lower(), True
                        break
                if not email:
                    for e in emails:
                        if e.get("verified"):
                            email, verified = (e.get("email") or "").lower(), True
                            break
            except Exception:
                pass
            return {
                "provider": "github",
                "oauth_id": str(user.get("id") or ""),
                "email": email,
                "verified": verified,
                "name": user.get("name") or user.get("login") or "",
            }
    except Exception as e:
        log_debug(f"[oauth] {provider} exchange error: {e}")
    return None


def _oauth_finish_session(session, info: dict) -> dict | None:
    """Finds or creates the user for an OAuth profile and returns tokens."""
    user = dbmod.get_user_by_oauth(session, info["provider"], info["oauth_id"])
    if user is None and info.get("email"):
        user = dbmod.get_user_by_email(session, info["email"])
        if user is not None:
            user.oauth_provider = info["provider"]
            user.oauth_id = info["oauth_id"]
            if info.get("verified"):
                user.email_verified = True
    if user is None:
        base = (_re.sub(r"[^A-Za-z0-9_.\-]", "", info.get("email") or "").split("@")[0]) or "user"
        username = base[:24]
        if dbmod.get_user_by_username_or_email(session, username):
            username = f"{base[:20]}{secrets.choice('0123456789')}{secrets.choice('0123456789')}"
        is_first = dbmod.first_user_count(session)
        user = DBUser(
            username=username,
            display_name=(info.get("name") or "").strip()[:64] or username,
            email=(info.get("email") or "").lower() or None,
            email_verified=bool(info.get("verified")) or dbmod.is_special_account(info.get("email")),
            password_hash=dbmod.hash_password(secrets.token_urlsafe(24)),
            role="admin" if is_first else "watcher",
            can_control=bool(is_first),
            youtube_allowed=bool(is_first),
            upload_quota=Config.DEFAULT_UPLOAD_QUOTA,
            oauth_provider=info["provider"],
            oauth_id=info["oauth_id"],
        )
        session.add(user)
        session.flush()
    if not user.is_active:
        return None
    own = dbmod.ensure_own_room(session, user)
    if not user.current_room_id:
        user.current_room_id = own.id
    tokens = _issue_tokens(session, user)
    session.commit()
    return tokens


def _oauth_redirect_back(message: str):
    q = urllib.parse.urlencode({"oauth_error": message})
    return redirect(f"{Config.SITE_BASE_URL}/?{q}")


@app.route("/stream/api/auth/oauth/<provider>")
def api_oauth_login(provider: str):
    return _oauth_start(provider)


@app.route("/stream/api/auth/oauth/<provider>/callback")
def api_oauth_callback(provider: str):
    if provider not in ("google", "github"):
        return jsonify({"error": "روش ورود نامعتبر است"}), 400
    _clean_oauth_states()
    if request.args.get("error"):
        return _oauth_redirect_back("ورود با حساب خارجی لغو شد")
    state = request.args.get("state") or ""
    if not state or OAUTH_STATES.get(state, (None, 0))[0] != provider:
        return _oauth_redirect_back("نشست ورود معتبر نیست؛ دوباره تلاش کن")
    OAUTH_STATES.pop(state, None)
    code = request.args.get("code") or ""
    if not code:
        return _oauth_redirect_back("کد ورود دریافت نشد")
    info = _oauth_exchange(provider, code)
    if not info or not info.get("oauth_id"):
        return _oauth_redirect_back("ورود با حساب خارجی ناموفق بود")
    session = dbmod.SessionLocal()
    try:
        tokens = _oauth_finish_session(session, info)
    except Exception as e:
        log_debug(f"[oauth] {provider} finish error: {e}")
        session.rollback()
        return _oauth_redirect_back("ورود با حساب خارجی ناموفق بود")
    finally:
        session.close()
    if not tokens:
        return _oauth_redirect_back("این حساب غیرفعال شده است")
    return redirect(
        f"{Config.SITE_BASE_URL}/?oauth=1"
        f"#access_token={tokens['access_token']}&refresh_token={tokens['refresh_token']}"
    )


@app.route("/stream/api/auth/refresh", methods=["POST"])
def api_auth_refresh():
    data = request.get_json(force=True, silent=True) or {}
    token = (data.get("refresh_token") or "").strip()
    session = dbmod.SessionLocal()
    try:
        row = session.execute(
            dbmod.select(dbmod.RefreshToken).where(dbmod.RefreshToken.token == token)
        ).scalar_one_or_none()
        if not row:
            return jsonify({"error": "نشست نامعتبر است؛ دوباره وارد شو"}), 401
        if row.expires_at and row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            session.delete(row)
            session.commit()
            return jsonify({"error": "نشست منقضی شده؛ دوباره وارد شو"}), 401
        user = session.get(DBUser, row.user_id)
        if not user or not user.is_active:
            return jsonify({"error": "کاربر غیرفعال است"}), 403
        new_refresh = dbmod.create_refresh_token(user.id)
        row.token = new_refresh
        row.expires_at = datetime.now(timezone.utc) + timedelta(days=Config.JWT_REFRESH_TTL_DAYS)
        session.commit()
        return jsonify({
            "access_token": dbmod.create_access_token(user.id),
            "refresh_token": new_refresh,
            "user": user.public_dict(),
        })
    finally:
        session.close()


@app.route("/stream/api/auth/logout", methods=["POST"])
def api_auth_logout():
    data = request.get_json(force=True, silent=True) or {}
    token = (data.get("refresh_token") or "").strip()
    if token:
        session = dbmod.SessionLocal()
        try:
            row = session.execute(
                dbmod.select(dbmod.RefreshToken).where(dbmod.RefreshToken.token == token)
            ).scalar_one_or_none()
            if row:
                session.delete(row)
                session.commit()
        finally:
            session.close()
    return jsonify({"ok": True})


@app.route("/stream/api/auth/me")
def api_auth_me():
    user = _require_user()
    session = dbmod.SessionLocal()
    try:
        fresh = session.get(DBUser, user.id)
        if not fresh or not fresh.is_active:
            return jsonify({"error": "حساب غیرفعال است"}), 401
        own = dbmod.ensure_own_room(session, fresh)
        if not fresh.current_room_id:
            fresh.current_room_id = own.id
            session.commit()
        room_row = session.get(dbmod.Room, fresh.current_room_id) if fresh.current_room_id else None
        rs = rooms.get(fresh.current_room_id) if fresh.current_room_id else rooms.get(own.id)
        return jsonify({
            "user": fresh.public_dict(),
            "room": room_row.public_dict() if room_row else None,
            "own_room": own.public_dict(),
            "online": len(rs.users) if rs else 0,
        })
    finally:
        session.close()


# ────────────────────────────────────────────────────────────────────────────
# Rooms — invite code, join-by-code search, return to own room
# ────────────────────────────────────────────────────────────────────────────

def _room_info_json(session, room_row: dbmod.Room) -> dict:
    rs = rooms.get(room_row.id)
    return {
        "id": room_row.id,
        "name": room_row.name,
        "owner_id": room_row.owner_id,
        "online": len(rs.users) if rs else 0,
        "items": len(rs.playlist) if rs else 0,
    }


@app.route("/stream/api/room/info")
def api_room_info():
    _require_user()
    code = (request.args.get("code") or "").strip().upper()
    if not code:
        return jsonify({"error": "کد اتاق را وارد کن"}), 400
    session = dbmod.SessionLocal()
    try:
        room_row = session.get(dbmod.Room, code)
        if not room_row:
            return jsonify({"error": f"اتاق با کد {code} پیدا نشد"}), 404
        return jsonify({"room": _room_info_json(session, room_row)})
    finally:
        session.close()


@app.route("/stream/api/room/join", methods=["POST"])
def api_room_join():
    user = _require_user()
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip().upper()
    if not code:
        return jsonify({"error": "کد اتاق را وارد کن"}), 400
    session = dbmod.SessionLocal()
    try:
        room_row = session.get(dbmod.Room, code)
        if not room_row:
            return jsonify({"error": f"اتاق با کد {code} پیدا نشد"}), 404
        fresh = session.get(DBUser, user.id)
        fresh.current_room_id = room_row.id
        session.commit()
        return jsonify({"room": _room_info_json(session, room_row), "user": fresh.public_dict()})
    finally:
        session.close()


@app.route("/stream/api/room/mine", methods=["POST"])
def api_room_mine():
    user = _require_user()
    session = dbmod.SessionLocal()
    try:
        fresh = session.get(DBUser, user.id)
        own = dbmod.ensure_own_room(session, fresh)
        fresh.current_room_id = own.id
        session.commit()
        return jsonify({"room": _room_info_json(session, own), "user": fresh.public_dict()})
    finally:
        session.close()


# ────────────────────────────────────────────────────────────────────────────
# Admin dashboard (role=admin only) — everything DB-synced
# ────────────────────────────────────────────────────────────────────────────

@app.route("/stream/api/admin/stats")
def api_admin_stats():
    admin = _require_user()
    if not _is_admin(admin):
        return jsonify({"error": "دسترسی ادمین لازم است"}), 403
    session = dbmod.SessionLocal()
    try:
        users_count = session.execute(dbmod.select(dbmod.func.count()).select_from(DBUser)).scalar()
        rooms_count = session.execute(dbmod.select(dbmod.func.count()).select_from(dbmod.Room)).scalar()
        online = sum(len(rs.users) for rs in rooms._rooms.values())
        media = sum(len(rs.playlist) for rs in rooms._rooms.values())
        return jsonify({
            "users": users_count or 0,
            "rooms": rooms_count or 0,
            "online": online,
            "media": media,
        })
    finally:
        session.close()


@app.route("/stream/api/admin/users")
def api_admin_users():
    admin = _require_user()
    if not _is_admin(admin):
        return jsonify({"error": "دسترسی ادمین لازم است"}), 403
    session = dbmod.SessionLocal()
    try:
        users = session.execute(
            dbmod.select(DBUser).order_by(DBUser.created_at.desc())
        ).scalars().all()
        out = []
        for u in users:
            rs = rooms.get(u.current_room_id) if u.current_room_id else None
            out.append({
                **u.public_dict(),
                "online": len(rs.users) if rs else 0,
                "room_items": len(rs.playlist) if rs else 0,
                "own_room_id": u.own_room.id if u.own_room else None,
            })
        return jsonify({"users": out})
    finally:
        session.close()


@app.route("/stream/api/admin/users/<user_id>", methods=["POST"])
def api_admin_update_user(user_id):
    admin = _require_user()
    if not _is_admin(admin):
        return jsonify({"error": "دسترسی ادمین لازم است"}), 403
    data = request.get_json(force=True, silent=True) or {}
    session = dbmod.SessionLocal()
    try:
        target = session.get(DBUser, user_id)
        if not target:
            return jsonify({"error": "کاربر پیدا نشد"}), 404
        is_self = (target.id == admin.id)

        if "role" in data:
            role = str(data["role"] or "").strip().lower()
            if role not in ("admin", "controller", "watcher"):
                return jsonify({"error": "نقش نامعتبر است"}), 400
            if is_self and role != "admin":
                return jsonify({"error": "نمی‌توانی نقش خودت را از ادمین تغییر دهی"}), 400
            target.role = role
        if "can_control" in data:
            target.can_control = bool(data["can_control"])
        if "youtube_allowed" in data:
            target.youtube_allowed = bool(data["youtube_allowed"])
        if "upload_quota" in data:
            try:
                q = int(data["upload_quota"])
            except (TypeError, ValueError):
                return jsonify({"error": "سهمیه باید عدد باشد"}), 400
            if q < -1:
                return jsonify({"error": "سهمیه نمی‌تواند منفی باشد (برای نامحدود از 1- استفاده کن)"}), 400
            target.upload_quota = q
        if "display_name" in data:
            target.display_name = (str(data["display_name"] or "").strip())[:64]
        if "is_active" in data:
            if is_self and not bool(data["is_active"]):
                return jsonify({"error": "نمی‌توانی حساب خودت را غیرفعال کنی"}), 400
            target.is_active = bool(data["is_active"])

        session.commit()
        return jsonify({"user": target.public_dict()})
    finally:
        session.close()


# ────────────────────────────────────────────────────────────────────────────
# Socket.IO
# ────────────────────────────────────────────────────────────────────────────

# sid -> (room_code, DBUser) for every authenticated socket connection.
SOCKET_SESSIONS: dict = {}


def _socket_session():
    """(code, user) for the current socket's sid, or (None, None)."""
    sess = SOCKET_SESSIONS.get(request.sid)
    if not sess:
        return None, None
    return sess[0], sess[1]


@socketio.on("connect")
def on_connect(auth=None):
    token = ""
    if isinstance(auth, dict):
        token = (auth.get("token") or "").strip()
    if not token:
        token = (request.args.get("token") or "").strip()
    payload = dbmod.decode_token(token) if token else None
    if not payload or payload.get("type") != "access":
        log_debug(f"Socket connect rejected (no/invalid token from {request.sid})")
        return False
    session = dbmod.SessionLocal()
    try:
        user = session.get(DBUser, payload.get("sub"))
        if not user or not user.is_active or not user.current_room_id:
            return False
        code = user.current_room_id
        SOCKET_SESSIONS[request.sid] = (code, user)
        join_room(_room_channel(code))
        rs = rooms.get(code)
        chat_obj = rooms.chat(code)
        with LOCK:
            rs.users[request.sid] = {
                "name": user.username, "display_name": user.display_name,
                "avatar_url": None, "in_voice": False,
            }
        emit("state_sync", rs.to_public_dict())
        with CHAT_LOCK:
            emit("chat_history", {"messages": chat_obj.public_history()})
        broadcast_notify("join", user.username, room_code=code)
        broadcast_presence(rs)
        log_debug(f"Socket connected: {user.username} -> room {code} ({request.sid})")
    finally:
        session.close()


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    code, _user = SOCKET_SESSIONS.pop(sid, (None, None))
    if not code:
        return
    rs = rooms.get(code)
    with LOCK:
        user = rs.users.pop(sid, None)
    if user:
        broadcast_notify("leave", user.get("name", "ناشناس"), room_code=code)
        broadcast_presence(rs)


@socketio.on("join")
def on_join(data):
    code, _user = _socket_session()
    if not code:
        return
    rs = rooms.get(code)
    chat_obj = rooms.chat(code)
    data = data or {}
    name = (data.get("name") or "").strip()[:24]
    avatar_url = None
    with CHAT_LOCK:
        avatar_url = chat_obj.get_avatar(name)
    with LOCK:
        entry = rs.users.get(request.sid)
        if entry is None:
            return
        if name:
            entry["name"] = name
        if avatar_url:
            entry["avatar_url"] = avatar_url
    emit("state_sync", rs.to_public_dict())
    broadcast_presence(rs)


@socketio.on("voice_joined")
def on_voice_joined(data):
    code, _user = _socket_session()
    if not code:
        return
    rs = rooms.get(code)
    data = data or {}
    name = (data.get("name") or "").strip()
    with LOCK:
        user = rs.users.get(request.sid)
        if user is None:
            return
        if name and name != user.get("name"):
            return
        user["in_voice"] = True
    broadcast_presence(rs)


@socketio.on("voice_left")
def on_voice_left(data):
    code, _user = _socket_session()
    if not code:
        return
    rs = rooms.get(code)
    data = data or {}
    name = (data.get("name") or "").strip()
    with LOCK:
        user = rs.users.get(request.sid)
        if user is None:
            return
        if name and name != user.get("name"):
            return
        user["in_voice"] = False
    broadcast_presence(rs)


@socketio.on("control")
def on_control(data):
    code, user = _socket_session()
    if not code or not user:
        return
    if not _may_control(user):
        emit("notify", {
            "type": "control_denied", "name": user.username,
            "ts": time.time(),
            "extra": {"action": (data or {}).get("action", "")},
        })
        return
    rs = rooms.get(code)
    data = data or {}
    action = data.get("action")
    name = user.username or "ناشناس"

    with LOCK:
        if action == "play":
            rs.set_play(float(data.get("at", rs.current_position())))
        elif action == "pause":
            rs.set_pause(float(data.get("at", rs.current_position())))
        elif action == "seek":
            rs.seek(float(data.get("to", 0)))
        elif action == "rate":
            rs.set_rate(float(data.get("rate", 1.0)))
        elif action == "select":
            rs.select_item(int(data.get("index")))
        else:
            return
        safe_save(rs)
        payload = rs.to_public_dict()

    socketio.emit("state_sync", payload, to=_room_channel(code), include_self=False)
    broadcast_notify(f"ctrl_{action}", name, room_code=code, extra=data)


@socketio.on("request_sync")
def on_request_sync():
    code, _user = _socket_session()
    if not code:
        return
    rs = rooms.get(code)
    emit("state_sync", rs.to_public_dict())


@socketio.on("chat_send")
def on_chat_send(data):
    code, user = _socket_session()
    if not code or not user:
        return
    chat_obj = rooms.chat(code)
    data = data or {}
    name = user.username or "ناشناس"
    text = (data.get("text") or "").strip()[:Config.CHAT_MAX_TEXT_LEN]
    image_url = (data.get("image_url") or "").strip() or None

    if not text and not image_url:
        return
    if image_url and "/stream/media/chat/" not in image_url:
        image_url = None

    avatar_url = None
    with CHAT_LOCK:
        avatar_url = chat_obj.get_avatar(name)

    msg = {
        "id": new_id(), "name": name, "text": text,
        "image_url": image_url, "avatar_url": avatar_url,
        "ts": time.time(),
    }
    with CHAT_LOCK:
        chat_obj.add_message(msg)
    socketio.emit("chat_message", msg, to=_room_channel(code))


if __name__ == "__main__":
    try:
        dbmod.init_db()
        log_debug("Database tables ready.")
    except Exception as e:
        log_debug(f"[CRITICAL] Database init failed: {e}")
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