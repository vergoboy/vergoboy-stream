from gevent import monkey
monkey.patch_all()

import gevent
import json
import logging
import math
import os
import re as _re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, g, jsonify, redirect, render_template, request, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room
import requests
from sqlalchemy import text as sql_text
from sqlalchemy.orm import selectinload

from config import Config
from media_pipeline import (
    Attempt,
    EncodeError,
    EncodeRequest,
    EncodeRunner,
    ErrorKind,
    build_fallback_chain,
    cleanup_orphaned_partials,
)
from media_domain.adapters.http import download_to_file
from media_domain.services.metadata import (
    find_text_subtitle_streams,
    get_duration_s,
    get_video_height,
    get_video_width,
)
from media_domain.services.url_intake import (
    check_link_ok,
    resolve_media_url,
    url_ext,
)
import archive_scraper
from archive_network import create_direct_media_session, direct_media_environment
from archive_logging import configure as configure_archive_logging, event as archive_event, redact, reset_request_id, safe_target, set_request_id, timer as ArchiveTimer
from archive_filters import FilterValidationError, SearchFilters, available_options
import app_logging
from app_logging import diagnostics as log_diagnostics, record_client_error
import db as dbmod
from db import User as DBUser
import permissions as permmod
from livekit_auth import generate_livekit_token
import mail as mailmod
from srt_to_vtt import convert_srt_to_vtt


def log_debug(msg):
    # The stream prefix is kept because run.sh and the Tauri host both key off
    # it, but the line also goes to data/logs/app.log so a GUI launch has
    # somewhere to read after the fact.
    print(f"[DEBUG_STREAM] {msg}", file=sys.stderr, flush=True)
    try:
        app_log.log("%s", msg, level=logging.DEBUG)
    except Exception:
        pass  # never let logging be the reason something dies


def _safe_source_for_log(source: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(source)
        if not parsed.scheme or not parsed.netloc:
            return source
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return "<invalid media URL>"


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
app_log = app_logging.configure(os.environ.get("STREAM_LOG_LEVEL", "INFO"), stream=sys.stderr)
configure_archive_logging(Config.ARCHIVE_LOG_LEVEL)
app_logging.install_gevent_hook()
app_log.info("backend starting: pid=%s env=%s log_level=%s python=%s",
             os.getpid(), Config.ENVIRONMENT, os.environ.get("STREAM_LOG_LEVEL", "INFO"),
             sys.version.split()[0])

CORS(
    app,
    resources={
        r"/stream/api/*": {
            "origins": list(Config.CORS_ORIGINS),
            "supports_credentials": False,
            "methods": ["GET", "POST", "OPTIONS", "DELETE"],
        },
        r"/stream/media/*": {
            "origins": list(Config.CORS_ORIGINS),
            "supports_credentials": False,
            "methods": ["GET", "OPTIONS"],
        },
    },
)

socketio = SocketIO(
    app,
    path="stream/socket.io",
    cors_allowed_origins=list(Config.CORS_ORIGINS),
    async_mode="gevent",
)

ENCODE_SEMAPHORE = gevent.lock.BoundedSemaphore(Config.MAX_CONCURRENT_ENCODES)

# One runner for the whole process. It owns the hardware circuit breaker,
# which is deliberately global: a GPU that has stopped working is a fact
# about the machine, not about one room, and per-call breakers would have
# every room rediscover it on its own.
ENCODE_RUNNER = EncodeRunner()

# This client is deliberately distinct from the Digimoviez archive client.
# ``trust_env=False`` prevents HTTP(S)_PROXY / ALL_PROXY from routing a movie
# download through the archive proxy.
MEDIA_CLIENT = create_direct_media_session()


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
        user = session.execute(
            dbmod.select(DBUser)
            .where(DBUser.id == payload.get("sub"))
            .options(selectinload(DBUser.own_room))
        ).scalar_one_or_none()
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


@app.errorhandler(Exception)
def _handle_unexpected_error(e):
    """Log anything that reaches the WSGI layer.

    Without this Flask answers with a bare 500 and the exception never reaches
    stderr in a way that survives the process, so a request-time crash is
    invisible. Explicit HTTP errors keep their own status and are not recorded
    as crashes.
    """
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify({"error": e.description}), e.code
    app_logging.record_crash(
        "request_exception", f"{type(e).__name__}: {e}",
        traceback_text="".join(traceback.format_exception(type(e), e, e.__traceback__)),
        fields={"where": "flask errorhandler", "method": request.method,
                "path": request.path, "client_ip": request.remote_addr or ""})
    log_debug(f"[request] {request.method} {request.path} -> {type(e).__name__}: {e}")
    return jsonify({"error": "internal error"}), 500


def _current_room():
    """RoomState for the authenticated request's current room (or None)."""
    user = _current_user()
    if not user or not user.current_room_id:
        return None
    return rooms.get(user.current_room_id)


def _current_room_code() -> str | None:
    user = _current_user()
    return (user.current_room_id if user else None)


def _perm_snapshot(user: DBUser) -> permmod.PermissionView:
    """Resolved capabilities for this request. Cached on flask.g."""
    if not user:
        return permmod.resolve_view(permmod.ResolveContext(
            user_id="", role="watcher", email=None, is_active=False,
            can_control=False, youtube_allowed=False, room_id=None, room_owner_id=None,
        ))
    key = (user.id, user.current_room_id)
    cached = getattr(g, "_perm_cache", None)
    if cached and cached[0] == key:
        return cached[1]
    session = dbmod.SessionLocal()
    try:
        fresh = session.get(DBUser, user.id)
        if not fresh:
            view = permmod.resolve_view(permmod.ResolveContext(
                user_id=user.id, role="watcher", email=None, is_active=False,
                can_control=False, youtube_allowed=False, room_id=None, room_owner_id=None,
            ))
        else:
            view = dbmod.resolve_user_permissions(session, fresh, fresh.current_room_id)
        g._perm_cache = (key, view)
        return view
    finally:
        session.close()


def _has_perm(user: DBUser, perm: str) -> bool:
    if not user or not user.is_active:
        return False
    return _perm_snapshot(user).allowed(perm)


def _deny_unless(user: DBUser, perm: str, message: str = "اجازه این کار را نداری"):
    if _has_perm(user, perm):
        return None
    return jsonify({"error": message}), 403


def _quota_ok(user: DBUser) -> bool:
    if not user or not user.is_active:
        return False
    view = _perm_snapshot(user)
    if view.is_site_admin or view.is_root or view.is_owner:
        return True
    return user.upload_quota == -1 or (user.uploads_used or 0) < user.upload_quota


def _may_control(user: DBUser) -> bool:
    """True when the user may change shared playback (legacy helper)."""
    if not user or not user.is_active:
        return False
    view = _perm_snapshot(user)
    return bool(
        view.is_owner or view.is_site_admin or view.is_root
        or view.allowed(permmod.PERM_SELECT_PLAYLIST_ITEM)
        or view.allowed(permmod.PERM_CHANGE_PLAYBACK_SPEED)
        or view.allowed(permmod.PERM_PROMOTE_USER)
    )


def _may_youtube(user: DBUser) -> bool:
    return _has_perm(user, permmod.PERM_ADD_YOUTUBE)


def _may_add(user: DBUser) -> bool:
    if not user or not user.is_active:
        return False
    if not (
        _has_perm(user, permmod.PERM_ADD_VIDEO)
        or _has_perm(user, permmod.PERM_ADD_VIDEO_URL)
        or _has_perm(user, permmod.PERM_ADD_VIDEO_FILE)
        or _has_perm(user, permmod.PERM_ADD_YOUTUBE)
        or _has_perm(user, permmod.PERM_ADD_STREAM)
    ):
        return False
    return _quota_ok(user)


def _is_admin(user: DBUser) -> bool:
    return bool(user and user.is_active and user.role == "admin")


def _is_room_owner(user: DBUser) -> bool:
    """True when the user is currently inside the room they own. Depends on
    `own_room` being eager-loaded (both `_current_user` and the socket
    `on_connect` load it via selectinload)."""
    return bool(
        user
        and user.is_active
        and user.own_room is not None
        and user.current_room_id
        and user.own_room.id == user.current_room_id
    )


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
        "users": room_obj.users_public_list(),
    }
    payload["online"] = len(payload["users"])
    if room_obj.room_id:
        socketio.emit("presence", payload, to=_room_channel(room_obj.room_id))
    else:
        socketio.emit("presence", payload)


def _refresh_socket_perms(code: str, user_id: str, can_control: bool = None):
    """Updates the in-memory presence entry after a promote/demote so the
    member panel reflects the new permission without a reconnect."""
    rs = rooms.get(code) if code else None
    if rs is None:
        return
    with LOCK:
        for entry in rs.users.values():
            if entry.get("id") == user_id and can_control is not None:
                entry["can_control"] = bool(can_control)
    broadcast_presence(rs)


def _reload_socket_user(user_id: str):
    """Re-binds every live socket for this account to a fresh DB user so
    permission checks after promote/demote/override do not use a stale row."""
    session = dbmod.SessionLocal()
    try:
        fresh = session.execute(
            dbmod.select(DBUser)
            .where(DBUser.id == user_id)
            .options(selectinload(DBUser.own_room))
        ).scalar_one_or_none()
        if not fresh:
            return
        view = dbmod.resolve_user_permissions(session, fresh, fresh.current_room_id)
        dbmod.sync_legacy_flags(fresh, view)
        session.commit()
        for sid, (code, _old) in list(SOCKET_SESSIONS.items()):
            if _old.id == user_id:
                SOCKET_SESSIONS[sid] = (code, fresh)
        if fresh.current_room_id:
            _refresh_socket_perms(
                fresh.current_room_id, user_id,
                can_control=bool(fresh.can_control or view.is_owner or view.is_site_admin),
            )
        socketio.emit(
            "permissions_sync",
            {"user_id": user_id, "permissions": view.effective, "room_role": view.room_role},
            to=_room_channel(fresh.current_room_id) if fresh.current_room_id else None,
        )
    finally:
        session.close()


def _kick_room_user(code: str, user_id: str):
    """Force-disconnects every socket of `user_id` currently in room `code`."""
    for sid, (scode, suser) in list(SOCKET_SESSIONS.items()):
        if scode == code and suser.id == user_id:
            try:
                socketio.emit("kicked", {"reason": "banned"}, to=sid)
                socketio.server.disconnect(sid)
            except Exception as e:
                log_debug(f"kick failed for {sid}: {e}")


def _room_for_item(item_id: str):
    """Finds the RoomState that currently contains the given playlist item
    (used by background encode greenlets, which outlive the request)."""
    for code, rs in rooms._rooms.items():
        if any(it.get("id") == item_id for it in rs.playlist):
            return rs
    return None


def free_space_mb(path: str) -> float:
    return shutil.disk_usage(path).free / (1024 * 1024)


MIN_FREE_MB_FOR_TRANSCODE = 500

# ── encoded-frontier bookkeeping ─────────────────────────────────────────────
# The client keeps its own encoded_frontier (Player.tsx) purely as a courtesy:
# nothing stopped a hand-crafted `control` event from seeking straight past the
# encoded timeline into a segment that does not exist yet, which shows up as a
# black screen for everyone in the room. These make that guard enforceable.
#
# Keyed "<item_id>:<rendition label>", matching the client's transcodeProgress
# key so both sides agree on which rendition a number describes.
TRANSCODE_PROGRESS: dict = {}
PROGRESS_LOCK = gevent.lock.Semaphore()

# Kept in step with the client's MARGIN (Player.tsx). A viewer is allowed to
# scrub right up to the last safely encoded second, minus a little slack, so
# that a segment boundary a moment behind the reported progress still plays.
SEEK_FRONTIER_MARGIN = 3.0

# The rendition statuses _rewrite_master_playlist advertises, i.e. the rungs a
# viewer can actually pick right now. A rung in any other state is invisible in
# the master playlist, so it must not constrain the room's seek range: one
# queued-but-unadvertised 1080p rung would otherwise pin everybody at 0s until
# it came up.
SELECTABLE_RENDITION_STATUSES = ("ready", "complete")

# A rung that died. It is not selectable *and* it will never become selectable,
# so including it in the minimum would freeze the whole room's seek range at 0s
# for the rest of the item's life.
DEAD_RENDITION_STATUSES = ("error", "failed", "cancelled", "canceled")

# Plan modes that lay the timeline down as fast as the disk allows instead of
# encoding it over time, so they have no live frontier to clamp against.
COPY_LIKE_MODES = ("copy", "remux", "direct_play", "directplay")

# A rung this far behind its peers caps the whole room. Logged rather than
# silently endured, because a permanently slow rung is a transcoding problem
# the operator has to see, and it changes who can seek where.
LAG_WARN_THRESHOLD_S = 30.0
LAG_WARN_INTERVAL_S = 60.0

# Named logger for playback-sync problems. Separate from log_debug's stderr
# prints because these survive into production logs and must be filterable by
# name (`journalctl -t stream.sync`) instead of grepping a DEBUG prefix.
# Propagation is left on so a host that configures logging owns the output and
# test capture still sees the records; the handler below only exists so the line
# appears even when nothing has configured the root logger at all.
SYNC_LOGGER = logging.getLogger("stream.sync")
if not SYNC_LOGGER.handlers:
    _sync_handler = logging.StreamHandler(sys.stderr)
    _sync_handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s %(message)s"))
    SYNC_LOGGER.addHandler(_sync_handler)
SYNC_LOGGER.setLevel(logging.WARNING)

# item_id -> monotonic timestamp of the last lagging-rung warning, so a slow
# rung produces one line a minute instead of one per seek.
_LAG_WARNED_AT: dict = {}


def _record_encoded_progress(item_id: str, label: str, encoded_seconds: float) -> None:
    """Remember how far one rendition has encoded, for the seek guard."""
    if not item_id or not label:
        return
    try:
        value = float(encoded_seconds)
    except (TypeError, ValueError):
        return
    with PROGRESS_LOCK:
        TRANSCODE_PROGRESS[f"{item_id}:{label}"] = {"encoded_seconds": value}


def _clear_encoded_progress(item_id: str) -> None:
    """Drop every rendition's progress for an item.

    Called when an item finishes or is removed. Without this a completed item
    keeps a stale frontier forever, and a re-encode of the same id would start
    clamped to the previous run's numbers.
    """
    if not item_id:
        return
    prefix = f"{item_id}:"
    with PROGRESS_LOCK:
        for key in [k for k in TRANSCODE_PROGRESS if k.startswith(prefix)]:
            TRANSCODE_PROGRESS.pop(key, None)
        _LAG_WARNED_AT.pop(item_id, None)


def _rendition_mode(item: dict, rendition: dict) -> str:
    """The plan mode for a rung: its own if recorded, else the item's."""
    mode = rendition.get("mode") or item.get("mode") or ""
    return str(mode).strip().lower()


def _incomplete_rendition_frontiers(item: dict, progress: dict) -> tuple:
    """``(frontiers_by_label, unknown_advertised)`` for the live seek guard.

    Walks only the rungs that (a) a viewer can select right now, because they are
    in the master playlist, and (b) are not finished, because a finished rung
    imposes no ceiling. Failed and not-yet-advertised rungs are skipped rather
    than treated as zero: neither can be selected, so neither should be able to
    hold the whole room at the start of the item.

    ``unknown_advertised`` is True when a selectable rung has no recorded
    progress at all. That should not happen -- progress is stored on the same
    tick that marks a rung ready -- and it is genuinely unknowable rather than
    "slow", so the caller treats the room as unsafe instead of guessing.
    """
    frontiers: dict = {}
    unknown = False
    item_id = item.get("id")
    for rendition in item.get("renditions") or []:
        status = rendition.get("status")
        if status in DEAD_RENDITION_STATUSES:
            continue
        if status not in SELECTABLE_RENDITION_STATUSES:
            continue
        if status == "complete":
            continue
        info = progress.get(f"{item_id}:{rendition.get('label')}")
        encoded = (info or {}).get("encoded_seconds")
        if encoded is None:
            unknown = True
            continue
        frontiers[rendition.get("label")] = float(encoded)
    return frontiers, unknown


def seek_frontier(item: dict, progress: dict) -> float:
    """How far it is safe to seek in this item, in seconds (``inf`` = no limit).

    Pure: takes the recorded progress as an argument and touches no globals, so
    the whole policy is unit-testable without touching the encoder or the clock.

    Room-wide rather than per-viewer, because the server cannot know which rung a
    given viewer happens to be watching. The **minimum** across the live
    selectable rungs is the only value safe for every viewer at once; clamping
    against a single rung would still let somebody on the slower one seek into a
    hole. The client keeps its own per-level guard on top -- this is the safety
    net for a hand-crafted `control` event, not a replacement.

    No clamp at all applies when there is nothing being encoded right now: a live
    stream, a finished item, a copy/remux plan that already has the whole
    timeline, or a set of rungs that are all complete.
    """
    if not item or item.get("type") == "live":
        return math.inf
    if item.get("status") == "complete":
        return math.inf
    if _rendition_mode(item, {}) in COPY_LIKE_MODES:
        # No live frontier exists at all: the plan is copy/remux/direct-play.
        return math.inf

    renditions = item.get("renditions") or []
    if not renditions:
        # `complete` was handled above, so reaching here means the ladder is not
        # recorded yet. Nothing is known, so nothing is safe.
        return 0.0

    if not any(r.get("status") in SELECTABLE_RENDITION_STATUSES for r in renditions):
        # Every rung is still queued/pending/encoding: none of them is in the
        # master playlist, so there is nothing for a viewer to select and not a
        # single encoded second exists yet. Excluding unadvertised rungs from the
        # *minimum* must not turn this into "no limit" -- it means nothing is
        # playable, which is the most unsafe state of all.
        return 0.0

    # A completed copy-like rung means the whole timeline is already on disk and
    # the master playlist can hand it to anyone, so there is nothing to guard.
    for rendition in renditions:
        if rendition.get("status") == "complete" and _rendition_mode(item, rendition) in COPY_LIKE_MODES:
            return math.inf

    frontiers, unknown = _incomplete_rendition_frontiers(item, progress)
    if unknown:
        return 0.0
    return min(frontiers.values()) if frontiers else math.inf


def clamp_seek_target(target: float, frontier: float,
                      margin: float = SEEK_FRONTIER_MARGIN) -> float:
    """Pure: where a requested seek should actually land.

    Unchanged when the target is already safe (including exactly at the limit),
    floored at zero when it is not, and a no-op when the frontier is infinite.
    """
    try:
        value = float(target)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    if not math.isfinite(frontier):
        return max(0.0, value)
    limit = frontier - margin
    if value > limit:
        return max(0.0, limit)
    return max(0.0, value)


def _warn_if_lagging(item: dict, progress: dict) -> None:
    """Log a WARNING when one selectable rung is far behind its peers.

    The slowest live rung is what caps the room, so when it stalls everybody
    feels it -- but nothing in the UI explains why seeking stopped early. One
    line per item per minute, naming the item, the lagging rung and the gap,
    is enough to diagnose it without flooding the log with one line per seek.
    """
    if not item:
        return
    frontiers, _unknown = _incomplete_rendition_frontiers(item, progress)
    if len(frontiers) < 2:
        return
    lagging_label, lagging_value = min(frontiers.items(), key=lambda kv: kv[1])
    lead = max(frontiers.values())
    lag = lead - lagging_value
    if lag < LAG_WARN_THRESHOLD_S:
        return
    item_id = item.get("id")
    now = time.monotonic()
    last = _LAG_WARNED_AT.get(item_id)
    if last is not None and (now - last) < LAG_WARN_INTERVAL_S:
        return
    _LAG_WARNED_AT[item_id] = now
    SYNC_LOGGER.warning(
        "rendition %s lags item %s by %.1fs (frontier %.1fs vs best %.1fs); "
        "room seeks are capped by the slowest rung",
        lagging_label, item_id, lag, lagging_value, lead,
    )


def _encoded_frontier(item: dict) -> float:
    """Snapshot the progress store and apply :func:`seek_frontier` to it."""
    with PROGRESS_LOCK:
        snapshot = dict(TRANSCODE_PROGRESS)
    frontier = seek_frontier(item, snapshot)
    _warn_if_lagging(item, snapshot)
    return frontier


def _current_item(rs) -> dict:
    """The item the room is playing right now, or ``{}``."""
    index = getattr(rs, "current_index", None)
    playlist = getattr(rs, "playlist", None) or []
    if index is None or not (0 <= index < len(playlist)):
        return {}
    return playlist[index] or {}


LANG_LABELS = {
    "fa": "فارسی", "per": "فارسی", "fas": "فارسی",
    "en": "انگلیسی", "eng": "انگلیسی",
    "ar": "عربی", "ara": "عربی",
}



# ────────────────────────────────────────────────────────────────────────────
# ffprobe / stream inspection
# ────────────────────────────────────────────────────────────────────────────

def _ffprobe_source(source: str, timeout: int = 20) -> dict:
    log_debug(f"Starting ffprobe on: {_safe_source_for_log(source)}")
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
        proc = subprocess.run(args, capture_output=True, timeout=timeout + 10, env=direct_media_environment())
        log_debug(f"ffprobe done in {time.time() - start_time:.2f}s, returncode={proc.returncode}")
        if proc.returncode != 0:
            log_debug(f"ffprobe stderr: {proc.stderr.decode(errors='ignore')[:300]}")
            return {}
        return json.loads(proc.stdout or "{}")
    except Exception as e:
        log_debug(f"ffprobe exception: {e}")
        return {}


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
        "-c:a", "aac", "-ac", "2", "-b:a", abr,
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


def _run_progressive_ffmpeg(cmd: list, item_id: str, duration_s: float, label: str,
                            on_ready=None, timeout: int = 10800,
                            output_paths=None):
    """Runs one rendition's ffmpeg command, emitting real transcode_progress
    events (real % from the real duration — never a byte-count heuristic) and
    firing `on_ready` exactly once, the moment enough of the timeline is encoded
    for safe early playback.

    Now backed by media_pipeline's EncodeRunner, which owns the parts that were
    previously missing here: a stall watchdog (a wedged ffmpeg used to hold its
    semaphore until the hard timeout), a duration-scaled timeout, a process
    group killed as a group (proc.kill() left orphans holding the output), a
    bounded stderr buffer (the old version accumulated the whole log in memory
    and then handed it to the client), typed failures, and output written to a
    ``.partial`` directory that is renamed into place only on success.

    The signature, the emitted event payloads and the (returncode, stderr) shape
    are unchanged, so the swap is not a behavioural change for the frontend —
    except that a failure is now reported as a translated message instead of
    ffmpeg's last stderr line.
    """
    output_paths = tuple(output_paths or ())
    last_pct = [-1]
    ready_fired = [False]
    duration_holder = [duration_s or 0.0]

    def emit(elapsed_s):
        """Same payload shape the Player already expects."""
        dur = duration_holder[0]
        pct = min(99, int(elapsed_s / dur * 100)) if dur > 0 else min(90, int(elapsed_s / 3))
        socketio.emit("transcode_progress", {
            "id": item_id, "label": label, "pct": pct,
            "encoded_seconds": round(elapsed_s, 1),
            "duration": round(dur, 1) if dur > 0 else None,
            "ready": ready_fired[0],
        })
        # Record before/with the emit so a rendition that reports ready always
        # has a recorded frontier behind it; the seek guard treats a missing
        # entry as "nothing is safe".
        _record_encoded_progress(item_id, label, elapsed_s)
        last_pct[0] = pct

    def on_progress(ev):
        dur = ev.get("duration") or duration_holder[0]
        if dur:
            duration_holder[0] = dur
        elapsed = ev.get("encoded_seconds") or 0.0
        pct = min(99, int(elapsed / duration_holder[0] * 100)) if duration_holder[0] > 0 \
            else min(90, int(elapsed / 3))
        # Emit on a changed percentage, exactly as before, so the event volume
        # on the socket does not change just because the runner reports more
        # often than the old loop did.
        if pct != last_pct[0] or ready_fired[0]:
            emit(elapsed)

    def on_ready_tick(elapsed_s):
        duration_holder[0] = duration_holder[0] or duration_s or 0.0
        ready_fired[0] = True
        emit(elapsed_s)
        if on_ready:
            try:
                on_ready(elapsed_s)
            except Exception as e:
                log_debug(f"on_ready callback error: {e}")

    chain = build_fallback_chain(Attempt(
        argv=cmd,
        label=label,
        output_paths=output_paths,
    ))

    log_debug(f"FFmpeg [{item_id}/{label}] start: {' '.join(cmd[:4])} ...")
    started = time.time()
    result = ENCODE_RUNNER.run(
        EncodeRequest(
            item_id=item_id, label=label, duration_s=duration_s or 0.0,
            # The runner derives the partial directory from this.
            output_dir=os.path.dirname(output_paths[-1]) if output_paths else "",
            on_progress=on_progress,
            on_ready=on_ready_tick,
            ready_after_s=Config.HLS_READY_AFTER_SECONDS,
            log_prefix=f"{item_id}/{label}",
        ),
        chain,
    )
    elapsed = time.time() - started
    log_debug(f"FFmpeg [{item_id}/{label}] finished ok={result.ok} in {elapsed:.1f}s "
              f"history={result.history}")

    if not result.ok:
        err = result.error or EncodeError(ErrorKind.UNKNOWN, "no attempt succeeded")
        # The client gets the translated sentence only. `detail` and the stderr
        # tail stay in the log — the old code put ffmpeg's own wording (and
        # sometimes a filesystem path) straight into item["error"], which is
        # broadcast to every viewer in the room.
        socketio.emit("transcode_error", err.client_payload(item_id))
        log_debug(f"FFmpeg [{item_id}/{label}] failed kind={err.kind.value} "
                  f"detail={err.detail} history={result.history}")
        return result.returncode or -1, (err.stderr_tail or err.detail).encode(), err

    total = result.duration or duration_holder[0] or duration_s or 0.0
    # Reproduces the old completion event field for field. It is deliberately
    # not routed through emit(): that clamps to 99 and reports the real
    # ready_fired flag, and on a clip shorter than HLS_READY_AFTER_SECONDS the
    # old code still sent pct=100/ready=true here. The Player keys off this.
    socketio.emit("transcode_progress", {
        "id": item_id, "label": label, "pct": 100,
        "encoded_seconds": round(total, 1) if total else None,
        "duration": round(total, 1) if total else None,
        "ready": True, "complete": True,
    })
    return 0, b"", None


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
                capture_output=True, timeout=120, check=True, env=direct_media_environment(),
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
            dur = get_duration_s(_ffprobe_source(source))

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
        # The runner needs to know which arguments are outputs so it can build
        # into a .partial directory and rename on success. It matches them by
        # exact string, so it can never mistake the input for an output.
        output_paths = (
            os.path.join(rendition_dir, "seg_%03d.ts"),
            os.path.join(rendition_dir, "index.m3u8"),
        )
        returncode, stderr_bytes, encode_error = _run_progressive_ffmpeg(
            cmd, item_id, dur, label, on_ready=on_ready, output_paths=output_paths,
        )

        if returncode != 0:
            # The translated message goes to viewers; ffmpeg's own wording stays
            # in the log. Previously the last stderr line was put straight into
            # item["error"], which is broadcast to the whole room.
            err = encode_error or EncodeError(ErrorKind.UNKNOWN, "no attempt succeeded")
            tail = (stderr_bytes or b"").decode(errors="ignore")[-1500:]
            log_debug(f"Encode failure [{item_id}/{label}] kind={err.kind.value} "
                      f"detail={err.detail} stderr_tail={tail}")
            fail(f"تبدیل ({label}) ناموفق بود: {err.user_message}")
            return

        if dur and dur > 0:
            actual = get_duration_s(_ffprobe_source(os.path.join(rendition_dir, "index.m3u8"), timeout=30))
            if actual and actual < dur - 5:
                fail(f"تبدیل ({label}) ناقص بود: فقط {int(actual)} از {int(dur)} ثانیه تولید شد (منبع ناقص/قطع شد).")
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
                # The whole timeline now exists, so the seek guard has nothing
                # left to enforce and the recorded progress is dead weight.
                _clear_encoded_progress(item_id)
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
    log_debug(f"start_item_encoding: {item_id} <- {_safe_source_for_log(source)}")

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
    duration_s = get_duration_s(probe)
    height = get_video_height(probe)
    width = get_video_width(probe)
    aspect = (width / height) if (width and height) else (16 / 9)
    sub_streams = find_text_subtitle_streams(probe)
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
        # yt-dlp writes "<output>.mp4.part" while downloading — never delete
        # a half-written file, the rename to the final name happens at the end
        # and cleanup would otherwise break in-progress downloads.
        if fname.endswith(".part"):
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


def _probe_storage() -> bool:
    for directory in (Config.MEDIA_DIR, Config.DATA_DIR):
        try:
            with tempfile.TemporaryFile(dir=directory):
                pass
        except OSError as exc:
            log_debug(f"[health] storage probe failed ({type(exc).__name__})")
            return False
    return True


def _probe_transcoder() -> bool:
    for name in ("ffmpeg", "ffprobe"):
        binary = shutil.which(name)
        if not binary:
            return False
        try:
            result = subprocess.run(
                [binary, "-version"], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=3, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
    return True


def _probe_livekit() -> bool:
    if not Config.LIVEKIT_CONFIGURED:
        return False
    parsed = urllib.parse.urlsplit(Config.LIVEKIT_URL)
    scheme = {"wss": "https", "ws": "http"}.get(parsed.scheme)
    if not scheme or not parsed.netloc:
        return False
    health_url = urllib.parse.urlunsplit(
        (scheme, parsed.netloc, parsed.path.rstrip("/") + "/", "", "")
    )
    try:
        req = urllib.request.Request(health_url, headers={"User-Agent": "vergoboy-stream-health"})
        with urllib.request.urlopen(req, timeout=3) as response:
            return 200 <= response.status < 300
    except Exception as exc:
        log_debug(f"[health] LiveKit probe failed ({type(exc).__name__})")
        return False


@app.route("/stream/api/health")
def api_health():
    checks = {}
    try:
        with dbmod.engine.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
        checks["database"] = True
    except Exception as exc:
        log_debug(f"[health] database probe failed ({type(exc).__name__})")
        checks["database"] = False
    checks["storage"] = _probe_storage()
    checks["transcoder"] = _probe_transcoder()
    checks["livekit"] = _probe_livekit()
    healthy = all(checks.values())
    return jsonify({"status": "healthy" if healthy else "degraded", "checks": checks}), (200 if healthy else 503)


@app.route("/stream/api/log/client", methods=["POST"])
def api_log_client():
    """Accept an error the webview caught. See app_logging.record_client_error."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    ident = record_client_error(payload, client_ip=request.remote_addr or "")
    return jsonify({"ok": True, "id": ident}), 201


@app.route("/stream/api/log/diagnostics")
def api_log_diagnostics():
    """Log files, sizes, mtimes and the tail of error/crash logs."""
    return jsonify(log_diagnostics()), 200


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
        resp.headers["Content-Type"] = "application/vnd.apple.mpegurl"
        resp.headers["Cache-Control"] = "no-cache"
    elif filename.endswith(".ts"):
        resp.headers["Content-Type"] = "video/mp2t"
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
    denied = _deny_unless(user, permmod.PERM_ADD_VIDEO_FILE, "اجازه آپلود فایل را نداری")
    if denied:
        return denied
    if not _quota_ok(user):
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

# Each archive failure gets its own HTTP status and its own operator-facing
# message, so the UI can tell "sign in", "solve it yourself", "your session
# died", "the proxy is down" and "the site refused us" apart instead of
# collapsing everything into one generic archive error.
_ARCHIVE_ERRORS = {
    archive_scraper.KIND_AUTH_REQUIRED: (401, "برای دیدن لینک‌های دانلود باید وارد حساب دیجی‌موویز شوید"),
    archive_scraper.KIND_MANUAL_CHALLENGE: (503, "دیجی‌موویز سؤال امنیتی می‌پرسد؛ نشست را کامل کنید و دوباره تلاش کنید"),
    archive_scraper.KIND_SESSION_EXPIRED: (401, "نشست دیجی‌موویز منقضی شده؛ دوباره وارد شوید"),
    archive_scraper.KIND_AUTH_UNAVAILABLE: (503, "ورود به آرشیو ممکن نشد؛ تنظیمات حساب را بررسی کنید"),
    archive_scraper.KIND_PROXY_UNAVAILABLE: (502, "پروکسی آرشیو در دسترس نیست"),
    archive_scraper.KIND_PROXY_CONFIG: (500, "آدرس پروکسی آرشیو نامعتبر است"),
    archive_scraper.KIND_NETWORK_TIMEOUT: (504, "دیجی‌موویز پاسخ نداد؛ دوباره تلاش کنید"),
    archive_scraper.KIND_HTTP_ERROR: (502, "دیجی‌موویز درخواست را رد کرد"),
    archive_scraper.KIND_PARSE_ERROR: (502, "ساختار صفحه دیجی‌موویز تغییر کرده و قابل خواندن نبود"),
}
_ARCHIVE_DEFAULT_ERROR = (502, "خطا در ارتباط با آرشیو")
# Only these conditions can be cleared by importing a completed session.
ARCHIVE_MANUAL_SESSION_KINDS = frozenset({
    archive_scraper.KIND_MANUAL_CHALLENGE, archive_scraper.KIND_AUTH_REQUIRED,
    archive_scraper.KIND_SESSION_EXPIRED,
})


def _archive_err(e, archive_request_id=None):
    kind = getattr(e, "kind", None)
    archive_event("api", "request_error", level=logging.ERROR, error_class=type(e).__name__,
                  error=redact(str(e))[:300], classification=kind or "unexpected")
    if isinstance(e, archive_scraper.ArchiveRequestError):
        status, message = _ARCHIVE_ERRORS.get(e.kind, _ARCHIVE_DEFAULT_ERROR)
        payload = {"error": message, "kind": e.kind, "detail": redact(str(e))[:300],
                   "manual_session_helpful": e.kind in ARCHIVE_MANUAL_SESSION_KINDS}
        if archive_request_id:
            payload["archive_request_id"] = archive_request_id
        return jsonify(payload), status
    if "timed out" in str(e).lower() or "timeout" in str(e).lower():
        status, message = 504, "سایت مبدأ پاسخ نداد؛ دوباره تلاش کنید"
        kind = archive_scraper.KIND_NETWORK_TIMEOUT
    else:
        status, message = 502, "خطا در دریافت اطلاعات از سایت مبدأ"
        kind = "unexpected"
    payload = {"error": message, "kind": kind, "manual_session_helpful": False}
    if archive_request_id:
        payload["archive_request_id"] = archive_request_id
    return jsonify(payload), status


@app.route("/stream/api/archive/search", methods=["POST"])
def api_archive_search():
    user = _require_user()
    denied = _deny_unless(user, permmod.PERM_SEARCH_ARCHIVE, "اجازه جستجوی آرشیو را نداری")
    if denied:
        return denied
    if not _has_perm(user, permmod.PERM_ACCESS_ARCHIVE):
        return jsonify({"error": "اجازه دسترسی به آرشیو را نداری"}), 403
    data = request.get_json(force=True, silent=True) or {}
    token = set_request_id(str(data.get("archive_request_id") or uuid.uuid4().hex[:12]))
    started = ArchiveTimer()
    try:
        archive_event("api", "request_start", method=request.method, endpoint=request.path,
                      filters={key: value for key, value in (data.get("filters") or data).items() if key not in {"password", "cookies"}})
        filters = SearchFilters.from_mapping(data.get("filters") or data)
        if not filters.query and not any((filters.director, filters.actors)):
            raise FilterValidationError("عبارت جستجو خالی است")
        results = archive_scraper.get_collector().search(filters)
        archive_event("api", "request_complete", status=200, elapsed_ms=started.ms, result_count=len(results))
        return jsonify({"results": results, "archive_request_id": data.get("archive_request_id")})
    except FilterValidationError as e:
        archive_event("api", "request_complete", status=400, elapsed_ms=started.ms, error=str(e))
        return jsonify({"error": str(e), "kind": archive_scraper.KIND_VALIDATION, "detail": str(e),
                        "manual_session_helpful": False,
                        "archive_request_id": str(data.get("archive_request_id") or "")}), 400
    except Exception as e:
        archive_event("api", "request_complete", status=502, elapsed_ms=started.ms,
                      error_class=type(e).__name__)
        return _archive_err(e, data.get("archive_request_id"))
    finally:
        reset_request_id(token)


@app.route("/stream/api/archive/filters", methods=["GET"])
def api_archive_filters():
    """UI option lists are derived from i.txt, never duplicated in JavaScript."""
    user = _require_user()
    denied = _deny_unless(user, permmod.PERM_ACCESS_ARCHIVE, "اجازه دسترسی به آرشیو را نداری")
    if denied:
        return denied
    return jsonify({"types": [{"value": "post", "label": "فیلم"}, {"value": "series", "label": "سریال"}],
                    "countries": available_options().get("adv_country", ()),
                    "age_ratings": available_options().get("adv_age", ()),
                    "qualities": available_options().get("adv_quality", ()),
                    "sorts": available_options().get("adv_order", ()),
                    "year_min": 1888, "year_max": datetime.now().year,
                    "rating_min": 0, "rating_max": 10, "rating_step": 0.1})


@app.route("/stream/api/archive/auth/status", methods=["GET"])
def api_archive_auth_status():
    user = _require_user()
    denied = _deny_unless(user, permmod.PERM_ACCESS_ARCHIVE, "اجازه دسترسی به آرشیو را نداری")
    if denied:
        return denied
    manager = archive_scraper.get_collector().auth
    return jsonify({"enabled": manager.settings.enabled, "state": manager.state.value,
                    "proxy_configured": bool(manager.settings.http_proxy),
                    "manual_session_kinds": sorted(ARCHIVE_MANUAL_SESSION_KINDS)})


@app.route("/stream/api/archive/auth/manual-session", methods=["POST"])
def api_archive_manual_session():
    """Operator-only continuation after a manually completed challenge.

    The browser session the operator completed the challenge in is imported
    here, verified against the account endpoint, and from then on reused as-is.
    Cookie values are never logged and never echoed back.
    """
    user = _require_user()
    denied = _deny_unless(user, permmod.PERM_ACCESS_ARCHIVE, "اجازه دسترسی به آرشیو را نداری")
    if denied:
        return denied
    token = set_request_id(str(uuid.uuid4().hex[:12]))
    cookies = (request.get_json(force=True, silent=True) or {}).get("cookies")
    if not isinstance(cookies, dict) or not cookies or not all(
            isinstance(k, str) and isinstance(v, str) and k and v for k, v in cookies.items()):
        return jsonify({"error": "کوکی‌های جلسه معتبر نیستند", "kind": archive_scraper.KIND_VALIDATION}), 400
    manager = archive_scraper.get_collector().auth
    try:
        manager.import_manual_session(cookies)
        # Prove the imported session actually works before telling the operator
        # to retry, so a bad import is reported here instead of as a mystery
        # failure on the next search.
        verified = manager.check()
    except Exception as e:
        return _archive_err(e)
    finally:
        reset_request_id(token)
    if verified is not True:
        return jsonify({"error": "کوکی‌های واردشده جلسه فعالی ایجاد نکردند", "kind": manager.state.value,
                        "state": manager.state.value}), 401
    return jsonify({"ok": True, "state": manager.state.value})


@app.route("/stream/api/archive/title", methods=["POST"])
def api_archive_title():
    user = _require_user()
    denied = _deny_unless(user, permmod.PERM_ACCESS_ARCHIVE, "اجازه دسترسی به آرشیو را نداری")
    if denied:
        return denied
    data = request.get_json(force=True, silent=True) or {}
    source = (data.get("source") or "").strip()
    url = (data.get("url") or "").strip()
    if source not in archive_scraper.SOURCES:
        return jsonify({"error": "منبع ناشناخته است", "kind": archive_scraper.KIND_VALIDATION}), 400
    if not url.startswith(Config.ARCHIVE_BASE_URL + "/"):
        return jsonify({"error": "آدرس صفحه معتبر نیست", "kind": archive_scraper.KIND_VALIDATION}), 400
    token = set_request_id(str(data.get("archive_request_id") or uuid.uuid4().hex[:12]))
    try:
        info = archive_scraper.title(source, url)
    except Exception as e:
        return _archive_err(e, data.get("archive_request_id"))
    finally:
        reset_request_id(token)
    return jsonify(info)


@app.route("/stream/api/archive/files", methods=["POST"])
def api_archive_files():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    # CDN directory crawling belonged exclusively to the disabled animex
    # collector and must never produce archive network requests.
    return jsonify({"error": "این جمع‌آورنده غیرفعال است"}), 410


def _add_url_item(url: str, title: str, name: str, room_code: str = None,
                  added_by_user_id: str = None):
    """Shared logic for the single add-url endpoint and the bulk archive add."""
    url = resolve_media_url(MEDIA_CLIENT, url)
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
    token = set_request_id(str(data.get("archive_request_id") or uuid.uuid4().hex[:12]))
    items = data.get("items") or []
    name = (data.get("name") or "ناشناس").strip()
    archive_event("playlist", "creation_start", item_count=len(items) if isinstance(items, list) else 0,
                  proxy_enabled=False, direct_media_only=True)
    if not isinstance(items, list) or not items:
        reset_request_id(token)
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
    verdicts = Group().map(lambda it: check_link_ok(MEDIA_CLIENT, it.get("url", "").strip()), items)

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
    archive_event("playlist", "creation_complete", added=added, skipped=skipped, dead=len(dead),
                  direct_media_only=True, proxy_enabled=False)
    reset_request_id(token)
    return jsonify({"added": added, "skipped": skipped, "dead": dead, "archive_request_id": data.get("archive_request_id")})


_LANG_NAME = {
    "aa": "Afar", "ab": "Abkhaz", "ae": "Avestan", "af": "Afrikaans", "ak": "Akan",
    "am": "Amharic", "an": "Aragonese", "ar": "Arabic", "as": "Assamese", "av": "Avaric",
    "ay": "Aymara", "az": "Azerbaijani", "ba": "Bashkir", "be": "Belarusian",
    "bg": "Bulgarian", "bh": "Bihari", "bi": "Bislama", "bm": "Bambara", "bn": "Bengali",
    "bo": "Tibetan", "br": "Breton", "bs": "Bosnian", "ca": "Catalan", "ce": "Chechen",
    "ch": "Chamorro", "co": "Corsican", "cr": "Cree", "cs": "Czech", "cu": "Church Slavic",
    "cv": "Chuvash", "cy": "Welsh", "da": "Danish", "de": "German", "dv": "Divehi",
    "dz": "Dzongkha", "ee": "Ewe", "el": "Greek", "en": "English", "eo": "Esperanto",
    "es": "Spanish", "et": "Estonian", "eu": "Basque", "fa": "Persian", "ff": "Fulah",
    "fi": "Finnish", "fj": "Fijian", "fo": "Faroese", "fr": "French", "fy": "Western Frisian",
    "ga": "Irish", "gd": "Scottish Gaelic", "gl": "Galician", "gn": "Guarani",
    "gu": "Gujarati", "gv": "Manx", "ha": "Hausa", "he": "Hebrew", "hi": "Hindi",
    "ho": "Hiri Motu", "hr": "Croatian", "ht": "Haitian", "hu": "Hungarian",
    "hy": "Armenian", "hz": "Herero", "ia": "Interlingua", "id": "Indonesian",
    "ie": "Interlingue", "ig": "Igbo", "ii": "Nuosu", "ik": "Inupiaq", "io": "Ido",
    "is": "Icelandic", "it": "Italian", "iu": "Inuktitut", "ja": "Japanese",
    "jv": "Javanese", "ka": "Georgian", "kg": "Kongo", "ki": "Kikuyu", "kj": "Kwanyama",
    "kk": "Kazakh", "kl": "Kalaallisut", "km": "Khmer", "kn": "Kannada", "ko": "Korean",
    "kr": "Kanuri", "ks": "Kashmiri", "ku": "Kurdish", "kv": "Komi", "kw": "Cornish",
    "ky": "Kyrgyz", "la": "Latin", "lb": "Luxembourgish", "lg": "Ganda", "li": "Limburgish",
    "ln": "Lingala", "lo": "Lao", "lt": "Lithuanian", "lu": "Luba-Katanga",
    "lv": "Latvian", "mg": "Malagasy", "mh": "Marshallese", "mi": "Maori", "mk": "Macedonian",
    "ml": "Malayalam", "mn": "Mongolian", "mr": "Marathi", "ms": "Malay",
    "mt": "Maltese", "my": "Burmese", "na": "Nauru", "nb": "Norwegian Bokmal",
    "nd": "North Ndebele", "ne": "Nepali", "ng": "Ndonga", "nl": "Dutch",
    "nn": "Norwegian Nynorsk", "no": "Norwegian", "nr": "South Ndebele", "nv": "Navajo",
    "ny": "Chichewa", "oc": "Occitan", "oj": "Ojibwa", "om": "Oromo", "or": "Oriya",
    "os": "Ossetian", "pa": "Punjabi", "pi": "Pali", "pl": "Polish", "ps": "Pashto",
    "pt": "Portuguese", "qu": "Quechua", "rm": "Romansh", "rn": "Kirundi",
    "ro": "Romanian", "ru": "Russian", "rw": "Kinyarwanda", "sa": "Sanskrit",
    "sc": "Sardinian", "sd": "Sindhi", "se": "Northern Sami", "sg": "Sango",
    "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "sm": "Samoan", "sn": "Shona",
    "so": "Somali", "sq": "Albanian", "sr": "Serbian", "ss": "Swati", "st": "Southern Sotho",
    "su": "Sundanese", "sv": "Swedish", "sw": "Swahili", "ta": "Tamil", "te": "Telugu",
    "tg": "Tajik", "th": "Thai", "ti": "Tigrinya", "tk": "Turkmen", "tl": "Tagalog",
    "tn": "Tswana", "to": "Tongan", "tr": "Turkish", "ts": "Tsonga", "tt": "Tatar",
    "tw": "Twi", "ty": "Tahitian", "ug": "Uyghur", "uk": "Ukrainian", "ur": "Urdu",
    "uz": "Uzbek", "ve": "Venda", "vi": "Vietnamese", "vo": "Volapuk", "wa": "Walloon",
    "wo": "Wolof", "xh": "Xhosa", "yi": "Yiddish", "yo": "Yoruba", "za": "Zhuang",
    "zh": "Chinese", "zu": "Zulu", "zh-Hans": "Chinese (Simplified)", "zh-Hant": "Chinese (Traditional)",
}


def _lang_display(code: str, yt_name: str = "") -> str:
    code = (code or "").strip()
    if yt_name:
        return yt_name
    if code in _LANG_NAME:
        return _LANG_NAME[code]
    parts = code.split("-")
    if parts and parts[0] in _LANG_NAME:
        return _LANG_NAME[parts[0]]
    return code or "نامشخص"


_YT_BOT_RE = _re.compile(r"Sign in to confirm|Sign in to continue|not a bot", _re.I)


def _run_ytdlp(cmd, timeout):
    """Runs a yt-dlp command. YouTube frequently flags the proxy exit IP and
    answers with a "Sign in to confirm you're not a bot" wall while the
    server's direct connection works fine — so on that error we retry once
    without the proxy."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        raise
    if r.returncode != 0 and _YT_BOT_RE.search(r.stderr.decode(errors="ignore")):
        stripped = []
        skip = False
        for a in cmd:
            if skip:
                skip = False
                continue
            if a == "--proxy":
                skip = True
                continue
            stripped.append(a)
        if stripped != cmd:
            r = subprocess.run(stripped, capture_output=True, timeout=timeout)
    return r


def _ytdlp_cmd(extra=None, timeout=45):
    """Builds the shared yt-dlp command with proxy/cookies from Config."""
    cmd = ["yt-dlp", "--force-ipv4"]
    cmd += ["--extractor-args", "youtube:player_client=web,android,ios"]
    if extra:
        cmd += extra
    if Config.YTDLP_PROXY:
        cmd += ["--proxy", Config.YTDLP_PROXY]
    if Config.YTDLP_COOKIES and os.path.exists(Config.YTDLP_COOKIES):
        cmd += ["--cookies", Config.YTDLP_COOKIES]
    return cmd


@app.route("/stream/api/youtube-formats", methods=["POST"])
def api_youtube_formats():
    """Scans a YouTube / yt-dlp-supported link and returns video info:
    title, thumbnail, duration, channel, view count, subtitle/caption languages
    and available formats. For playlist URLs, returns playlist entries."""
    user = _require_user()
    if not _may_youtube(user):
        return jsonify({"error": "مجوز افزودن لینک یوتیوب برای تو فعال نیست؛ با ادمین هماهنگ کن"}), 403
    data = request.get_json(force=True, silent=True) or {}
    yt_url = (data.get("url") or "").strip()
    if not yt_url:
        return jsonify({"error": "لینک خالی است"}), 400

    is_playlist = "playlist?list=" in yt_url or "&list=" in yt_url or "/playlist?list=" in yt_url

    cmd = _ytdlp_cmd(["--no-playlist" if not is_playlist else "--flat-playlist", "-J"])
    cmd.append(yt_url)

    try:
        r = _run_ytdlp(cmd, 90 if is_playlist else 60)
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

    # ---- playlist URL → return entries ------------------------------------
    if is_playlist and info.get("_type") == "playlist":
        entries = []
        for e in info.get("entries") or []:
            if not e or e.get("_type") not in ("video", "url") or not e.get("id"):
                continue
            eid = e["id"]
            entries.append({
                "id": eid,
                "title": e.get("title") or "(بدون عنوان)",
                "duration": e.get("duration"),
                "duration_string": e.get("duration_string") or "",
                "thumbnail": e.get("thumbnail") or f"https://i.ytimg.com/vi/{eid}/mqdefault.jpg",
                "url": f"https://www.youtube.com/watch?v={eid}",
            })
        return jsonify({
            "type": "playlist",
            "playlist_title": info.get("playlist_title") or info.get("title") or "",
            "playlist_count": info.get("playlist_count") or len(entries),
            "entries": entries,
        })

    # ---- single video → full info ------------------------------------------
    title = info.get("title", "")
    thumbnail = info.get("thumbnail", "")

    subtitles = []
    for code, tracks in (info.get("subtitles") or {}).items():
        name = (tracks[0].get("name") if tracks else "") or ""
        subtitles.append({"code": code, "name": _lang_display(code, name)})
    subtitles.sort(key=lambda x: x["name"].lower())

    auto_captions = []
    for code, tracks in (info.get("automatic_captions") or {}).items():
        if code in ("en-orig",):
            continue
        name = (tracks[0].get("name") if tracks else "") or ""
        auto_captions.append({"code": code, "name": _lang_display(code, name)})
    auto_captions.sort(key=lambda x: x["name"].lower())

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
    formats.insert(0, {"format_id": "__best__", "height": 9999, "ext": "mp4", "tbr": 0,
                       "label": "بالاترین کیفیت"})

    return jsonify({
        "type": "video",
        "title": title,
        "thumbnail": thumbnail,
        "duration": info.get("duration"),
        "duration_string": info.get("duration_string") or "",
        "channel": info.get("channel") or info.get("uploader") or "",
        "view_count": info.get("view_count"),
        "upload_date": info.get("upload_date") or "",
        "subtitles": subtitles,
        "auto_captions": auto_captions,
        "formats": formats,
    })


def _fetch_and_encode_youtube(item_id: str, yt_url: str, fmt_selector: str, requester_name: str,
                              sub_lang: str = ""):
    """Downloads the chosen source quality locally first (rather than
    handing ffmpeg the ephemeral googlevideo URL directly), which avoids
    URL-expiry / missing-header failures, then feeds it into the normal
    per-item encoding pipeline. When `sub_lang` is set, the matching
    subtitle track is downloaded alongside and attached to the item."""
    raw_path = os.path.join(Config.UPLOAD_DIR, f"{item_id}_src.mp4")
    cmd = [
        "yt-dlp", "--force-ipv4", "-f", fmt_selector,
        "--extractor-args", "youtube:player_client=web,android,ios",
        "--no-playlist", "--merge-output-format", "mp4", "-o", raw_path,
    ]
    if sub_lang:
        cmd += ["--write-subs", "--write-auto-subs", "--sub-langs", sub_lang, "--convert-subs", "srt"]
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
        r = _run_ytdlp(cmd, 1800)
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

    if sub_lang:
        _attach_yt_subtitle(item_id, raw_path, sub_lang, requester_name)

    start_item_encoding(item_id, raw_path, requester_name, raw_path)


def _attach_yt_subtitle(item_id: str, raw_path: str, sub_lang: str, requester_name: str):
    """Finds the subtitle track yt-dlp wrote next to the video and attaches
    it to the item as a normal VTT subtitle so it shows in the sub menu."""
    base = os.path.splitext(raw_path)[0]
    srt_path = f"{base}.{sub_lang}.srt"
    if not os.path.exists(srt_path):
        for fname in os.listdir(Config.UPLOAD_DIR):
            if fname.startswith(f"{os.path.basename(base)}.") and fname.endswith(f".{sub_lang}.srt"):
                srt_path = os.path.join(Config.UPLOAD_DIR, fname)
                break
    if not os.path.exists(srt_path):
        log_debug(f"No downloaded subtitle found for {item_id} ({sub_lang})")
        return
    try:
        sub_id = new_id()
        vtt_name = f"{sub_id}.vtt"
        convert_srt_to_vtt(srt_path, os.path.join(Config.SUBS_DIR, vtt_name))
        label = _lang_display(sub_lang) or f"زیرنویس"
        rs = _room_for_item(item_id)
        if rs is None:
            return
        with LOCK:
            item = rs.find_item(item_id)
            if item:
                item.setdefault("subtitles", []).append({
                    "id": sub_id, "label": label, "lang": sub_lang,
                    "url": f"/stream/media/subs/{vtt_name}",
                })
                safe_save(rs)
                title = item["title"]
            else:
                title = ""
        broadcast_state(rs)
        broadcast_notify("subtitle_auto_extracted", requester_name, room_code=rs.room_id,
                         title=title, count=1)
    except Exception as e:
        log_debug(f"Failed to attach YouTube subtitle for {item_id}: {e}")


def _queue_youtube_item(cur, user, yt_url, title, name, fmt_selector, count=1, sub_lang=""):
    """Adds a single YouTube item to the room playlist and spawns the
    background download+encode. Returns the item dict (or None)."""
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
        _bump_upload_used(session, user, count)
        session.commit()
    finally:
        session.close()
    broadcast_state(cur)
    broadcast_notify("playlist_add_processing", name, room_code=cur.room_id, title=title)
    gevent.spawn(_fetch_and_encode_youtube, item_id, yt_url, fmt_selector, name, sub_lang)
    return item


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
    sub_lang = (data.get("sub_lang") or "").strip()

    if not yt_url:
        return jsonify({"error": "لینک خالی است"}), 400

    fmt_selector = (format_id if format_id and format_id != "__best__" else Config.YTDLP_FORMAT)
    title = title or yt_url

    item = _queue_youtube_item(cur, user, yt_url, title, name, fmt_selector, sub_lang=sub_lang)
    if item is None:
        return jsonify({"error": "اتاق فعال نیست"}), 500
    return jsonify(item)


@app.route("/stream/api/add-youtube-playlist", methods=["POST"])
def api_add_youtube_playlist():
    """Adds every entry of a YouTube playlist to the room playlist."""
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
    if "list=" not in yt_url:
        return jsonify({"error": "این لینک یک پلی‌لیست نیست"}), 400

    cmd = _ytdlp_cmd(["--flat-playlist", "-J"], timeout=60)
    cmd.append(yt_url)
    try:
        r = _run_ytdlp(cmd, 60)
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

    entries = []
    for e in info.get("entries") or []:
        if not e or e.get("_type") not in ("video", "url") or not e.get("id"):
            continue
        eid = e["id"]
        entries.append({
            "title": e.get("title") or f"YouTube {eid}",
            "url": f"https://www.youtube.com/watch?v={eid}",
        })

    limit = max(1, min(ARCHIVE_MAX_ADD, 30))
    entries = entries[:limit]
    if not entries:
        return jsonify({"error": "هیچ ویدیویی در پلی‌لیست پیدا نشد"}), 422

    remaining = _remaining_add_slots(user)
    if remaining is not None:
        entries = entries[:max(0, remaining)]
    if not entries:
        return jsonify({"error": "سهمیه افزودن ویدیوی تو پر شده؛ با ادمین هماهنگ کن"}), 403

    fmt_selector = Config.YTDLP_FORMAT
    added = 0
    for e in entries:
        dup = False
        with LOCK:
            dup = any(itm.get("yt_url") == e["url"] for itm in cur.playlist)
        if dup:
            continue
        item = _queue_youtube_item(cur, user, e["url"], e["title"], name, fmt_selector)
        if item is not None:
            added += 1

    if added == 0:
        return jsonify({"error": "هیچ مورد جدیدی اضافه نشد (تکراری یا سهمیه)"}), 200
    broadcast_notify("playlist_add_many", name, room_code=cur.room_id,
                     title=title or "پلی‌لیست", count=added, skipped=0, dead=0)
    return jsonify({"added": added})


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

    if url.startswith(("https://", "http://")) and not check_link_ok(MEDIA_CLIENT, url):
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
    host = urllib.parse.urlparse(Config.RTMP_PUSH_URL_TEMPLATE.format(key="")).netloc.split(":")[0]
    primary_port = urllib.parse.urlparse(Config.RTMP_PUSH_URL_TEMPLATE.format(key="")).port or 1935
    alt_ports = [int(p) for p in Config.RTMP_ALTERNATE_PORTS if str(p) != str(primary_port)]
    scheme = urllib.parse.urlparse(Config.RTMP_PUSH_URL_TEMPLATE.format(key="")).scheme
    return jsonify({
        "key": key,
        "push_url": Config.RTMP_PUSH_URL_TEMPLATE.format(key=key),
        # Server address WITHOUT the stream key — this is what goes in OBS's
        # "Server" field; the key goes in the separate "Stream Key" field.
        "server_url": f"{scheme}://{host}:{primary_port}/live",
        "push_urls": [Config.RTMP_PUSH_URL_TEMPLATE.format(key=key)]
                     + [f"rtmp://{host}:{p}/live/{key}" for p in alt_ports],
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
    if not Config.LIVEKIT_CONFIGURED:
        return jsonify({
            "error": "Voice room is unavailable because LiveKit is not configured in this environment. Set real STREAM_LIVEKIT_API_KEY and STREAM_LIVEKIT_API_SECRET values plus a reachable STREAM_LIVEKIT_URL before joining voice."
        }), 503
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
    _clear_encoded_progress(item_id)
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
        download_to_file(MEDIA_CLIENT, url, raw_path, timeout=15)
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

        is_owner = dbmod.is_special_account(email)
        needs_email = True
        user = DBUser(
            username=username,
            display_name=display_name or username,
            email=email,
            email_verified=not needs_email,
            password_hash=dbmod.hash_password(password),
            role="watcher",
            can_control=False,
            youtube_allowed=False,
            upload_quota=Config.DEFAULT_UPLOAD_QUOTA,
        )
        session.add(user)
        session.flush()
        own = dbmod.ensure_own_room(session, user)
        user.current_room_id = own.id

        if needs_email:
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
        if dbmod.is_special_account(user.email):
            user.role = "admin"
            user.can_control = True
            user.youtube_allowed = True
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
        if user.email_verified:
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


@app.route("/stream/api/auth/forgot-password", methods=["POST"])
def api_auth_forgot_password():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "ایمیل را وارد کن"}), 400
    session = dbmod.SessionLocal()
    try:
        user = dbmod.get_user_by_email(session, email)
        if user:
            user.reset_token = dbmod.new_verification_token()
            user.reset_expires = datetime.now(timezone.utc) + timedelta(
                minutes=Config.RESET_TOKEN_TTL_MINUTES
            )
            session.commit()
            reset_url = f"{Config.SITE_BASE_URL}/api/auth/reset?token={user.reset_token}"
            mailmod.send_password_reset_email(user.email, reset_url)
        return jsonify({"ok": True})  # don't leak which emails exist
    finally:
        session.close()


@app.route("/stream/api/auth/reset")
def api_auth_reset_page():
    token = (request.args.get("token") or "").strip()
    session = dbmod.SessionLocal()
    try:
        user = session.execute(
            dbmod.select(DBUser).where(DBUser.reset_token == token)
        ).scalar_one_or_none()
        if not user:
            return _reset_page(None, "این لینک بازنشانی معتبر نیست یا قبلاً استفاده شده است.")
        exp = user.reset_expires
        if exp and exp.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            return _reset_page(None, "این لینک بازنشانی منقضی شده است؛ دوباره از صفحه ورود درخواست بده.")
        return _reset_page(token, None)
    finally:
        session.close()


@app.route("/stream/api/auth/reset-password", methods=["POST"])
def api_auth_reset_password():
    data = request.get_json(force=True, silent=True) or {}
    if not data:
        data = dict(request.form)
    token = (data.get("token") or "").strip()
    password = data.get("password") or ""
    if not token:
        return jsonify({"error": "توکن نامعتبر است"}), 400
    if len(password) < 6:
        return jsonify({"error": "رمز عبور باید حداقل ۶ کاراکتر باشد"}), 400
    session = dbmod.SessionLocal()
    try:
        user = session.execute(
            dbmod.select(DBUser).where(DBUser.reset_token == token)
        ).scalar_one_or_none()
        if not user:
            msg = "این لینک بازنشانی معتبر نیست یا قبلاً استفاده شده است."
            return jsonify({"error": msg}), 400
        exp = user.reset_expires
        if exp and exp.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            msg = "این لینک بازنشانی منقضی شده است؛ دوباره از صفحه ورود درخواست بده."
            return jsonify({"error": msg}), 400
        user.password_hash = dbmod.hash_password(password)
        user.reset_token = None
        user.reset_expires = None
        # Revoke every refresh token so the old password's sessions are signed out.
        session.execute(
            dbmod.delete(dbmod.RefreshToken).where(dbmod.RefreshToken.user_id == user.id)
        )
        session.commit()
        return jsonify({"ok": True})
    finally:
        session.close()


def _reset_page(token, error=None, success=False):
    form = f"""\
<form method="post" action="{Config.SITE_BASE_URL}/api/auth/reset-password" style="text-align:right">
  <input type="hidden" name="token" value="{token}">
  <label style="display:block;margin:0 0 6px;font-size:13px;color:#444">رمز عبور جدید</label>
  <input type="password" name="password" required minlength="6" placeholder="حداقل ۶ کاراکتر" style="width:100%;box-sizing:border-box;padding:12px 14px;border:1px solid #ddd;border-radius:10px;font-size:14px;margin-bottom:14px">
  <label style="display:block;margin:0 0 6px;font-size:13px;color:#444">تکرار رمز عبور</label>
  <input type="password" id="pw2" required minlength="6" placeholder="دوباره بنویس" style="width:100%;box-sizing:border-box;padding:12px 14px;border:1px solid #ddd;border-radius:10px;font-size:14px;margin-bottom:18px">
  <button type="submit" style="width:100%;background:linear-gradient(135deg,#d4a017,#6b4e93);color:#fff;border:0;font-weight:bold;font-size:15px;padding:13px;border-radius:12px;cursor:pointer">بازنشانی رمز عبور</button>
</form>"""
    if success:
        body = f"""<div class="icon" style="color:#2c8f6b">✓</div>
<h2>رمز عبورت تغییر کرد</h2>
<p>حالا می‌توانی با رمز جدید وارد حساب‌ات شوی.</p>
<a class="btn" href="{Config.SITE_BASE_URL}/">ورود به تماشای مشترک</a>"""
    elif error:
        body = f"""<div class="icon" style="color:#c0392b">✗</div>
<h2>بازنشانی ناموفق</h2>
<p>{error}</p>
<a class="btn" href="{Config.SITE_BASE_URL}/">بازگشت به صفحه ورود</a>"""
    else:
        body = f"""<h2>تعیین رمز عبور جدید</h2>
<p style="margin:0 0 22px;color:#555;font-size:14px;line-height:1.9">رمز جدیدی برای حساب‌ات انتخاب کن.</p>
{form}"""
    return f"""<!doctype html><html dir="rtl" lang="fa"><head><meta charset="utf-8">
<title>بازنشانی رمز عبور</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{margin:0;font-family:Tahoma,Arial,sans-serif;background:#f2f0ea;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.box{{max-width:440px;background:#fff;border-radius:18px;padding:36px;text-align:center;border:1px solid #e6e2d8;margin:20px;width:100%;box-sizing:border-box}}
.icon{{font-size:46px;font-weight:bold}}
h2{{margin:14px 0 10px;font-size:18px;color:#2b2b2b}}
p{{margin:0 0 22px;color:#555;font-size:14px;line-height:1.9}}
a.btn{{display:inline-block;background:linear-gradient(135deg,#d4a017,#6b4e93);color:#fff;text-decoration:none;font-weight:bold;padding:13px 34px;border-radius:12px;font-size:14px}}</style>
</head><body><div class="box">{body}
<script>document.querySelector('form')?.addEventListener('submit',function(e){{
  var a=e.target.password.value,b=document.getElementById('pw2').value;
  if(a!==b){{e.preventDefault();alert('رمزها یکی نیستند');}}
}});</script>
</div></body></html>"""


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
        if not user.email_verified:
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
        verified_email = bool(info.get("verified"))
        is_admin = verified_email and dbmod.is_special_account(info.get("email"))
        user = DBUser(
            username=username,
            display_name=(info.get("name") or "").strip()[:64] or username,
            email=(info.get("email") or "").lower() or None,
            email_verified=verified_email,
            password_hash=dbmod.hash_password(secrets.token_urlsafe(24)),
            role="admin" if is_admin else "watcher",
            can_control=is_admin,
            youtube_allowed=is_admin,
            upload_quota=Config.DEFAULT_UPLOAD_QUOTA,
            oauth_provider=info["provider"],
            oauth_id=info["oauth_id"],
        )
        session.add(user)
        session.flush()
    if user.email_verified and dbmod.is_special_account(user.email):
        user.role = "admin"
        user.can_control = True
        user.youtube_allowed = True
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
        if not fresh.current_room_id or dbmod.is_banned(session, fresh.current_room_id, fresh.id):
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
        if dbmod.is_banned(session, room_row.id, fresh.id):
            return jsonify({"error": "از این اتاق اخراج شده‌ای و امکان ورود دوباره نداری"}), 403
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
# Room member management — promote/demote/ban/unban by the room owner or a
# promoted controller (the "مدیریت اعضا" panel).
# ────────────────────────────────────────────────────────────────────────────

def _room_manage_guard(session, user: DBUser):
    """Returns (room_row, None, None) when `user` may manage the room they're
    currently in, or (None, error_response, status) otherwise."""
    code = user.current_room_id
    if not code:
        return None, jsonify({"error": "اتاق فعال نیست — اول وارد حساب شو"}), 400
    if not _may_control(user):
        return None, jsonify({"error": "فقط صاحب اتاق یا کنترلر می‌تواند اعضا را مدیریت کند"}), 403
    room_row = session.get(dbmod.Room, code)
    if not room_row:
        return None, jsonify({"error": "اتاق پیدا نشد"}), 404
    return room_row, None, None


@app.route("/stream/api/room/members", methods=["POST"])
def api_room_members():
    user = _require_user()
    session = dbmod.SessionLocal()
    try:
        room_row, err, status = _room_manage_guard(session, user)
        if err is not None:
            return err, status
        rs = rooms.get(room_row.id)
        # One entry per PERSON, not per socket. Iterating rs.users.values()
        # directly (one entry per socket sid) listed the same account once per
        # open tab, so a member with 3 tabs appeared 3 times in the panel.
        with LOCK:
            online = list(rs.users_public_list())
        online.sort(key=lambda u: (not u["is_owner"], not u["can_control"], (u["name"] or "").lower()))
        bans = session.execute(
            dbmod.select(dbmod.RoomBan).where(dbmod.RoomBan.room_id == room_row.id)
        ).scalars().all()
        banned = []
        if bans:
            targets = session.execute(
                dbmod.select(DBUser).where(
                    DBUser.id.in_([b.user_id for b in bans])
                )
            ).scalars().all()
            for t in targets:
                banned.append({
                    "id": t.id,
                    "name": t.username,
                    "display_name": t.display_name,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                })
            banned.sort(key=lambda u: (u["name"] or "").lower())
        return jsonify({"owner_id": room_row.owner_id, "online": online, "banned": banned})
    finally:
        session.close()


@app.route("/stream/api/room/members/promote", methods=["POST"])
def api_room_promote():
    user = _require_user()
    session = dbmod.SessionLocal()
    try:
        room_row, err, status = _room_manage_guard(session, user)
        if err is not None:
            return err, status
        data = request.get_json(force=True, silent=True) or {}
        target = session.get(DBUser, data.get("user_id") or "")
        if not target:
            return jsonify({"error": "کاربر پیدا نشد"}), 404
        if target.id == room_row.owner_id:
            return jsonify({"error": "صاحب اتاق از قبل مدیر است"}), 400
        target.can_control = True
        session.commit()
        _refresh_socket_perms(room_row.id, target.id, can_control=True)
        broadcast_notify("room_promote", user.username, room_code=room_row.id,
                         extra={"target": target.username})
        return jsonify({"ok": True, "can_control": True})
    finally:
        session.close()


@app.route("/stream/api/room/members/demote", methods=["POST"])
def api_room_demote():
    user = _require_user()
    session = dbmod.SessionLocal()
    try:
        room_row, err, status = _room_manage_guard(session, user)
        if err is not None:
            return err, status
        data = request.get_json(force=True, silent=True) or {}
        target = session.get(DBUser, data.get("user_id") or "")
        if not target:
            return jsonify({"error": "کاربر پیدا نشد"}), 404
        if target.id == room_row.owner_id:
            return jsonify({"error": "صاحب اتاق را نمی‌توانی از مدیریت برداری"}), 400
        if target.id == user.id:
            return jsonify({"error": "نمی‌توانی خودت را از کنترلر خارج کنی"}), 400
        target.can_control = False
        session.commit()
        _refresh_socket_perms(room_row.id, target.id, can_control=False)
        broadcast_notify("room_demote", user.username, room_code=room_row.id,
                         extra={"target": target.username})
        return jsonify({"ok": True, "can_control": False})
    finally:
        session.close()


@app.route("/stream/api/room/members/ban", methods=["POST"])
def api_room_ban():
    user = _require_user()
    session = dbmod.SessionLocal()
    try:
        room_row, err, status = _room_manage_guard(session, user)
        if err is not None:
            return err, status
        data = request.get_json(force=True, silent=True) or {}
        target = session.get(DBUser, data.get("user_id") or "")
        if not target:
            return jsonify({"error": "کاربر پیدا نشد"}), 404
        if target.id == room_row.owner_id:
            return jsonify({"error": "صاحب اتاق را نمی‌توانی اخراج کنی"}), 400
        if target.id == user.id:
            return jsonify({"error": "نمی‌توانی خودت را اخراج کنی"}), 400
        if dbmod.is_banned(session, room_row.id, target.id):
            return jsonify({"ok": True, "already": True})
        dbmod.ban_user(session, room_row.id, target.id, banned_by=user.id)
        if target.current_room_id == room_row.id:
            target.current_room_id = None
        session.commit()
        _kick_room_user(room_row.id, target.id)
        broadcast_notify("room_ban", user.username, room_code=room_row.id,
                         extra={"target": target.username})
        return jsonify({"ok": True})
    finally:
        session.close()


@app.route("/stream/api/room/members/unban", methods=["POST"])
def api_room_unban():
    user = _require_user()
    session = dbmod.SessionLocal()
    try:
        room_row, err, status = _room_manage_guard(session, user)
        if err is not None:
            return err, status
        data = request.get_json(force=True, silent=True) or {}
        target = session.get(DBUser, data.get("user_id") or "")
        if not target:
            return jsonify({"error": "کاربر پیدا نشد"}), 404
        dbmod.unban_user(session, room_row.id, target.id)
        session.commit()
        broadcast_notify("room_unban", user.username, room_code=room_row.id,
                         extra={"target": target.username})
        return jsonify({"ok": True})
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


@app.route("/stream/api/admin/users/<user_id>", methods=["DELETE"])
def api_admin_delete_user(user_id):
    admin = _require_user()
    if not _is_admin(admin):
        return jsonify({"error": "دسترسی ادمین لازم است"}), 403
    session = dbmod.SessionLocal()
    try:
        target = session.get(DBUser, user_id)
        if not target:
            return jsonify({"error": "کاربر پیدا نشد"}), 404
        if target.id == admin.id:
            return jsonify({"error": "نمی‌توانی حساب خودت را حذف کنی"}), 400

        # Revoke every refresh token the user holds.
        session.execute(
            dbmod.delete(dbmod.RefreshToken).where(dbmod.RefreshToken.user_id == target.id)
        )

        # Remove rooms owned by the user (normally just their own room) after
        # pointing any visitor's current_room_id away from them.
        rooms_owned = session.execute(
            dbmod.select(dbmod.Room).where(dbmod.Room.owner_id == target.id)
        ).scalars().all()
        for room in rooms_owned:
            code = room.id
            session.execute(
                dbmod.update(DBUser)
                .where(DBUser.current_room_id == code)
                .values(current_room_id=None)
            )
            rooms.drop(code)
            session.delete(room)

        # Drop any live socket connections the user holds.
        for sid, (_code, u) in list(SOCKET_SESSIONS.items()):
            if u.id == target.id:
                SOCKET_SESSIONS.pop(sid, None)
                try:
                    socketio.server.disconnect(sid, namespace="/")
                except Exception:
                    pass

        username = target.username
        session.delete(target)
        session.commit()
        log_debug(f"Admin '{admin.username}' deleted user '{username}'")
        return jsonify({"ok": True})
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
        user = session.execute(
            dbmod.select(DBUser)
            .where(DBUser.id == payload.get("sub"))
            .options(selectinload(DBUser.own_room))
        ).scalar_one_or_none()
        if not user or not user.is_active or not user.current_room_id:
            return False
        code = user.current_room_id
        if dbmod.is_banned(session, code, user.id):
            log_debug(f"Socket connect rejected (banned from {code}): {user.username}")
            return False
        SOCKET_SESSIONS[request.sid] = (code, user)
        join_room(_room_channel(code))
        rs = rooms.get(code)
        chat_obj = rooms.chat(code)
        with LOCK:
            rs.users[request.sid] = {
                "name": user.username, "display_name": user.display_name,
                "avatar_url": None, "in_voice": False,
                # A freshly connected tab starts as browsing/idle. The client
                # flips this to True the moment playback actually starts, so
                # "watching" on the lounge couch always reflects real playback
                # rather than mere page presence.
                "watching": False, "last_seen": time.time(),
                "id": user.id, "can_control": bool(user.can_control),
                "is_owner": bool(user.own_room and user.own_room.id == code),
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


@socketio.on("watching")
def on_watching(data):
    """Client reports whether THIS tab is actually playing the shared media.

    This is what separates "watching" from "browsing" on the lounge couches.
    The value comes from the real HTMLMediaElement state, so it can never
    claim someone is watching when they are not.
    """
    code, _user = _socket_session()
    if not code:
        return
    rs = rooms.get(code)
    data = data or {}
    watching = bool(data.get("watching"))
    item_id = (data.get("item_id") or "").strip() or None
    with LOCK:
        entry = rs.users.get(request.sid)
        if entry is None:
            return
        entry["watching"] = watching
        entry["last_seen"] = time.time()
        if watching and item_id:
            entry["watching_item"] = item_id
        elif not watching:
            entry.pop("watching_item", None)
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
    data = data or {}
    action = data.get("action")
    if action not in ("play", "pause", "seek", "rate", "select", "shuffle"):
        return
    # play/pause/seek are open to everyone in the room; changing the shared
    # speed or switching the selected media needs a room manager.
    if action in ("rate", "select", "shuffle") and not _may_control(user):
        emit("notify", {
            "type": "control_denied", "name": user.username,
            "ts": time.time(),
            "extra": {"action": action},
        })
        return
    rs = rooms.get(code)
    name = user.username or "ناشناس"
    clamped_to = None

    with LOCK:
        if action == "play":
            rs.set_play(float(data.get("at", rs.current_position())))
        elif action == "pause":
            rs.set_pause(float(data.get("at", rs.current_position())))
        elif action == "seek":
            target = float(data.get("to", 0))
            # Enforce the encoded frontier here rather than trusting the client's
            # own guard: `control` is a plain socket event, so without this a
            # hand-crafted seek lands past the encoded timeline and the room
            # stares at a black screen. See seek_frontier / clamp_seek_target.
            frontier = _encoded_frontier(_current_item(rs))
            safe_target = clamp_seek_target(target, frontier)
            if safe_target != target:
                clamped_to = safe_target
            target = safe_target
            rs.seek(target)
        elif action == "rate":
            rs.set_rate(float(data.get("rate", 1.0)))
        elif action == "select":
            rs.select_item(int(data.get("index")))
        elif action == "shuffle":
            rs.set_shuffle(bool(data.get("on", not rs.shuffle)))
        else:
            return
        safe_save(rs)
        payload = rs.to_public_dict()

    if clamped_to is not None:
        # Tell the room why the seek landed short, so the client can say so
        # instead of the position silently disagreeing with the request.
        socketio.emit("notify", {
            "type": "seek_clamped", "name": name, "ts": time.time(),
            "extra": {"requested": float(data.get("to", 0)), "position": clamped_to},
        }, to=_room_channel(code))
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


def _resume_interrupted_encodes():
    """Re-kicks encodes that were left in status "queued" when the process
    stopped (a crash, deploy, or restart mid-encode). Rooms materialize
    lazily, so scan the per-room JSON on disk and hand each item back to
    start_item_encoding — which re-probes, rebuilds the rendition ladder and
    spawns the default rendition's encode. Purely best-effort: anything not
    in "queued" (completed, error, pending…) is left alone."""
    for fname in sorted(os.listdir(os.path.join(Config.DATA_DIR, "rooms"))):
        if not fname.endswith(".json"):
            continue
        code = fname[:-5]
        try:
            with open(os.path.join(Config.DATA_DIR, "rooms", fname), encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            log_debug(f"resume: skip {fname} ({e})")
            continue
        count = 0
        for it in data.get("playlist", []) or []:
            if it.get("status") != "queued":
                continue
            source = it.get("_source")
            if not source:
                continue
            rs = rooms.get(code)
            item = rs.find_item(it.get("id"))
            if item is None:
                continue
            gevent.spawn(start_item_encoding, it["id"], source, it.get("added_by") or "ناشناس")
            count += 1
        if count:
            log_debug(f"resume: {count} interrupted encode(s) re-queued in room {code}")


if __name__ == "__main__":
    Config.validate()
    dbmod.init_db()
    log_debug("Database tables ready.")
    # A crash or `kill -9` mid-encode leaves a .partial directory behind and
    # nothing will ever finish it. Clear them before anything else looks at the
    # HLS tree, so a stale partial can never be served or counted as output.
    try:
        orphans = cleanup_orphaned_partials(Config.HLS_DIR)
        if orphans:
            log_debug(f"Boot cleanup removed {len(orphans)} orphaned partial encode(s).")
    except Exception as e:
        log_debug(f"Boot cleanup of partial encodes failed: {e}")
    gevent.spawn(_resume_interrupted_encodes)
    gevent.spawn(_chat_purge_loop)
    if archive_scraper.enabled_collectors(Config):
        archive_scraper.get_collector().auth.start()
        log_debug("COLLECTOR_ENABLED name=digimoviez kind=movies")
    else:
        log_debug("COLLECTOR_DISABLED name=digimoviez")
    log_debug("COLLECTOR_DISABLED name=series")
    log_debug("COLLECTOR_DISABLED name=anime")
    log_debug("COLLECTOR_DISABLED name=animation")
    from gevent import pywsgi
    from geventwebsocket.handler import WebSocketHandler
    server = pywsgi.WSGIServer(
        (Config.HOST, Config.PORT),
        app,
        handler_class=WebSocketHandler,
    )
    log_debug(f"Running on http://{Config.HOST}:{Config.PORT} (gevent)")
    server.serve_forever()
