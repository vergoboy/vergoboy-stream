"""Granular room capabilities, defaults, overrides, and delegation ceilings.

This extends the existing role flags (``users.role``, ``can_control``,
``youtube_allowed``) rather than replacing them. Effective permission for a
user in a room is:

    role_defaults  +  per-user overrides  ∩  delegator's current effective set

Site root (configured admin email), site admins, and the room owner sit above
the delegation graph and cannot be demoted by ordinary room admins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

PERM_PLAY_VIDEO = "play_video"
PERM_PAUSE_VIDEO = "pause_video"
PERM_SEEK_FORWARD = "seek_forward"
PERM_SEEK_BACKWARD = "seek_backward"
PERM_SELECT_PLAYLIST_ITEM = "select_playlist_item"
PERM_PLAY_NEXT = "play_next"
PERM_CHANGE_PLAYBACK_SPEED = "change_playback_speed"
PERM_JOIN_VOICE = "join_voice"
PERM_ADD_VIDEO = "add_video"
PERM_ADD_VIDEO_URL = "add_video_url"
PERM_ADD_VIDEO_FILE = "add_video_file"
PERM_ADD_YOUTUBE = "add_youtube"
PERM_ADD_STREAM = "add_stream"
PERM_START_STREAM = "start_stream"
PERM_ADD_AUDIO = "add_audio"
PERM_ADD_SUBTITLE = "add_subtitle"
PERM_ACCESS_ARCHIVE = "access_archive"
PERM_SEARCH_ARCHIVE = "search_archive"
PERM_DELETE_ARCHIVE_ITEM = "delete_archive_item"
PERM_SEND_CHAT = "send_chat"
PERM_SEND_CHAT_IMAGE = "send_chat_image"
PERM_CLEAR_CHAT = "clear_chat"
PERM_PROMOTE_USER = "promote_user"
PERM_DEMOTE_USER = "demote_user"
PERM_MANAGE_PERMISSIONS = "manage_permissions"
PERM_MANAGE_DEFAULT_PERMISSIONS = "manage_default_permissions"
PERM_ADD_ADMIN = "add_admin"
PERM_REMOVE_ADMIN = "remove_admin"
PERM_DELEGATE_PERMISSIONS = "delegate_permissions"

CATEGORIES: list[tuple[str, str, tuple[str, ...]]] = [
    ("playback", "پخش", (
        PERM_PLAY_VIDEO, PERM_PAUSE_VIDEO, PERM_SEEK_BACKWARD, PERM_SEEK_FORWARD,
        PERM_SELECT_PLAYLIST_ITEM, PERM_PLAY_NEXT, PERM_CHANGE_PLAYBACK_SPEED,
    )),
    ("voice", "اتاق صدا", (PERM_JOIN_VOICE,)),
    ("media", "افزودن مدیا", (
        PERM_ADD_VIDEO, PERM_ADD_VIDEO_URL, PERM_ADD_VIDEO_FILE, PERM_ADD_YOUTUBE,
        PERM_ADD_STREAM, PERM_START_STREAM, PERM_ADD_AUDIO, PERM_ADD_SUBTITLE,
    )),
    ("archive", "آرشیو", (
        PERM_ACCESS_ARCHIVE, PERM_SEARCH_ARCHIVE, PERM_DELETE_ARCHIVE_ITEM,
    )),
    ("chat", "چت", (PERM_SEND_CHAT, PERM_SEND_CHAT_IMAGE, PERM_CLEAR_CHAT)),
    ("administration", "مدیریت", (
        PERM_PROMOTE_USER, PERM_DEMOTE_USER, PERM_ADD_ADMIN, PERM_REMOVE_ADMIN,
        PERM_MANAGE_PERMISSIONS, PERM_MANAGE_DEFAULT_PERMISSIONS,
        PERM_DELEGATE_PERMISSIONS,
    )),
]

LABELS_FA: dict[str, str] = {
    PERM_PLAY_VIDEO: "پخش ویدیو",
    PERM_PAUSE_VIDEO: "مکث ویدیو",
    PERM_SEEK_FORWARD: "جلو بردن زمان",
    PERM_SEEK_BACKWARD: "عقب بردن زمان",
    PERM_SELECT_PLAYLIST_ITEM: "انتخاب از پلی‌لیست",
    PERM_PLAY_NEXT: "پخش ویدیوی بعدی",
    PERM_CHANGE_PLAYBACK_SPEED: "تغییر سرعت پخش",
    PERM_JOIN_VOICE: "ورود به اتاق صدا",
    PERM_ADD_VIDEO: "افزودن ویدیو",
    PERM_ADD_VIDEO_URL: "افزودن با لینک مستقیم",
    PERM_ADD_VIDEO_FILE: "افزودن با آپلود فایل",
    PERM_ADD_YOUTUBE: "افزودن لینک یوتیوب",
    PERM_ADD_STREAM: "افزودن استریم",
    PERM_START_STREAM: "شروع استریم",
    PERM_ADD_AUDIO: "افزودن ترک صدا",
    PERM_ADD_SUBTITLE: "افزودن زیرنویس",
    PERM_ACCESS_ARCHIVE: "دسترسی به آرشیو",
    PERM_SEARCH_ARCHIVE: "جستجوی آرشیو",
    PERM_DELETE_ARCHIVE_ITEM: "حذف نتیجه آرشیو",
    PERM_SEND_CHAT: "ارسال پیام چت",
    PERM_SEND_CHAT_IMAGE: "ارسال عکس در چت",
    PERM_CLEAR_CHAT: "پاک‌کردن چت",
    PERM_PROMOTE_USER: "ارتقا کاربران",
    PERM_DEMOTE_USER: "تنزل کاربران",
    PERM_ADD_ADMIN: "افزودن ادمین",
    PERM_REMOVE_ADMIN: "حذف ادمین",
    PERM_MANAGE_PERMISSIONS: "مدیریت مجوزهای کاربر",
    PERM_MANAGE_DEFAULT_PERMISSIONS: "مدیریت مجوزهای پیش‌فرض",
    PERM_DELEGATE_PERMISSIONS: "واگذاری مجوز به ادمین دیگر",
}

ALL_PERMISSIONS: tuple[str, ...] = tuple(
    perm for _cid, _label, perms in CATEGORIES for perm in perms
)
ALL_PERMISSION_SET = frozenset(ALL_PERMISSIONS)

ADMINISTRATION_PERMISSIONS = frozenset(
    CATEGORIES[-1][2]
)

# Watcher defaults match the previous hard-coded watcher (play/pause/seek and
# chat/voice), so existing rooms stay watchable. Admins can open the rest from
# the control panel; those changes do not wipe per-user overrides.
WATCHER_DEFAULTS: dict[str, bool] = {perm: False for perm in ALL_PERMISSIONS}
WATCHER_DEFAULTS.update({
    PERM_PLAY_VIDEO: True,
    PERM_PAUSE_VIDEO: True,
    PERM_SEEK_FORWARD: True,
    PERM_SEEK_BACKWARD: True,
    PERM_JOIN_VOICE: True,
    PERM_SEND_CHAT: True,
    PERM_SEND_CHAT_IMAGE: True,
})

# Promoted controllers previously gated by can_control: drive the room, add
# media, moderate members. YouTube stays behind youtube_allowed unless granted.
CONTROLLER_DEFAULTS: dict[str, bool] = {perm: True for perm in ALL_PERMISSIONS}
CONTROLLER_DEFAULTS[PERM_ADD_YOUTUBE] = False

ADMIN_DEFAULTS: dict[str, bool] = {perm: True for perm in ALL_PERMISSIONS}

ROOM_ROLE_USER = "user"
ROOM_ROLE_ADMIN = "admin"

RANK_ROOT = 100
RANK_SITE_ADMIN = 90
RANK_OWNER = 80
RANK_ROOM_ADMIN = 50
RANK_USER = 10

SOURCE_DEFAULT = "default"
SOURCE_ALLOW = "allow"
SOURCE_DENY = "deny"


def catalog_public() -> dict:
    return {
        "permissions": [
            {"id": perm, "label": LABELS_FA[perm], "category": cid}
            for cid, _label, perms in CATEGORIES for perm in perms
        ],
        "categories": [
            {"id": cid, "label": label, "permissions": list(perms)}
            for cid, label, perms in CATEGORIES
        ],
        "watcher_defaults": dict(WATCHER_DEFAULTS),
        "admin_defaults": dict(ADMIN_DEFAULTS),
    }


def normalize_perm_map(raw: Mapping[str, Any] | None, fallback: Mapping[str, bool]) -> dict[str, bool]:
    out = {perm: bool(fallback.get(perm, False)) for perm in ALL_PERMISSIONS}
    if not raw:
        return out
    for key, value in raw.items():
        if key in ALL_PERMISSION_SET:
            out[key] = bool(value)
    return out


def normalize_overrides(raw: Mapping[str, Any] | None) -> dict[str, bool]:
    if not raw:
        return {}
    return {key: bool(value) for key, value in raw.items() if key in ALL_PERMISSION_SET}


def enabled_set(mapping: Mapping[str, bool]) -> frozenset[str]:
    return frozenset(perm for perm, on in mapping.items() if on)


@dataclass
class PermissionView:
    effective: dict[str, bool]
    sources: dict[str, str]
    defaults: dict[str, bool]
    overrides: dict[str, bool]
    ceiling: frozenset[str]
    room_role: str
    granted_by_id: str | None
    is_owner: bool
    is_site_admin: bool
    is_root: bool
    rank: int
    ancestor_ids: tuple[str, ...] = ()

    def allowed(self, perm: str) -> bool:
        return bool(self.effective.get(perm, False))

    def as_public(self) -> dict:
        return {
            "effective": dict(self.effective),
            "sources": dict(self.sources),
            "overrides": dict(self.overrides),
            "defaults": dict(self.defaults),
            "ceiling": sorted(self.ceiling),
            "room_role": self.room_role,
            "granted_by_id": self.granted_by_id,
            "is_owner": self.is_owner,
            "is_site_admin": self.is_site_admin,
            "is_root": self.is_root,
            "rank": self.rank,
        }


@dataclass
class ResolveContext:
    """Everything resolve() needs, so unit tests can run without SQLAlchemy."""

    user_id: str
    role: str
    email: str | None
    is_active: bool
    can_control: bool
    youtube_allowed: bool
    room_id: str | None
    room_owner_id: str | None
    room_role: str = ROOM_ROLE_USER
    overrides: dict[str, bool] = field(default_factory=dict)
    granted_by_id: str | None = None
    watcher_defaults: dict[str, bool] = field(default_factory=lambda: dict(WATCHER_DEFAULTS))
    admin_defaults: dict[str, bool] = field(default_factory=lambda: dict(ADMIN_DEFAULTS))
    # granted_by_id -> its already-computed effective set; filled while walking
    # the chain so we never recurse infinitely.
    ancestor_effective: dict[str, frozenset[str]] = field(default_factory=dict)
    ancestor_ids: tuple[str, ...] = ()
    root_emails: frozenset[str] = field(default_factory=frozenset)


def is_root_email(email: str | None, root_emails: Iterable[str]) -> bool:
    if not email:
        return False
    return email.strip().lower() in {e.strip().lower() for e in root_emails if e}


def resolve_view(ctx: ResolveContext) -> PermissionView:
    if not ctx.is_active:
        empty = {perm: False for perm in ALL_PERMISSIONS}
        return PermissionView(
            effective=empty,
            sources={perm: SOURCE_DEFAULT for perm in ALL_PERMISSIONS},
            defaults=empty,
            overrides={},
            ceiling=frozenset(),
            room_role=ROOM_ROLE_USER,
            granted_by_id=None,
            is_owner=False,
            is_site_admin=False,
            is_root=False,
            rank=0,
        )

    is_root = is_root_email(ctx.email, ctx.root_emails)
    is_site_admin = ctx.role == "admin"
    is_owner = bool(ctx.room_id and ctx.room_owner_id and ctx.user_id == ctx.room_owner_id)
    room_admin = ctx.room_role == ROOM_ROLE_ADMIN or is_owner or is_site_admin or is_root

    if is_root:
        rank = RANK_ROOT
    elif is_site_admin:
        rank = RANK_SITE_ADMIN
    elif is_owner:
        rank = RANK_OWNER
    elif ctx.room_role == ROOM_ROLE_ADMIN:
        rank = RANK_ROOM_ADMIN - min(len(ctx.ancestor_ids), 20)
    else:
        rank = RANK_USER

    if is_root or is_site_admin or is_owner:
        defaults = dict(ADMIN_DEFAULTS)
        ceiling = ALL_PERMISSION_SET
        overrides: dict[str, bool] = {}
        sources = {perm: SOURCE_DEFAULT for perm in ALL_PERMISSIONS}
        return PermissionView(
            effective=dict(defaults),
            sources=sources,
            defaults=defaults,
            overrides=overrides,
            ceiling=ceiling,
            room_role=ROOM_ROLE_ADMIN if room_admin else ROOM_ROLE_USER,
            granted_by_id=ctx.granted_by_id,
            is_owner=is_owner,
            is_site_admin=is_site_admin,
            is_root=is_root,
            rank=rank,
            ancestor_ids=ctx.ancestor_ids,
        )

    if ctx.room_role == ROOM_ROLE_ADMIN:
        defaults = normalize_perm_map(ctx.admin_defaults, ADMIN_DEFAULTS)
    elif ctx.can_control:
        defaults = normalize_perm_map(ctx.admin_defaults, CONTROLLER_DEFAULTS)
        # Legacy promoted controllers keep youtube behind the dedicated flag
        # until an explicit override exists.
        defaults[PERM_ADD_YOUTUBE] = bool(ctx.youtube_allowed)
    else:
        defaults = normalize_perm_map(ctx.watcher_defaults, WATCHER_DEFAULTS)
        if ctx.youtube_allowed:
            defaults[PERM_ADD_YOUTUBE] = True

    overrides = normalize_overrides(ctx.overrides)
    sources: dict[str, str] = {}
    merged: dict[str, bool] = {}
    for perm in ALL_PERMISSIONS:
        if perm in overrides:
            merged[perm] = overrides[perm]
            sources[perm] = SOURCE_ALLOW if overrides[perm] else SOURCE_DENY
        else:
            merged[perm] = bool(defaults.get(perm, False))
            sources[perm] = SOURCE_DEFAULT

    ceiling = ALL_PERMISSION_SET
    if ctx.granted_by_id and ctx.granted_by_id in ctx.ancestor_effective:
        ceiling = ctx.ancestor_effective[ctx.granted_by_id]
    elif ctx.granted_by_id:
        # Unknown delegator: fail closed for administration, keep the rest of
        # the merged set. The DB resolver always fills ancestor_effective.
        ceiling = enabled_set(merged) - ADMINISTRATION_PERMISSIONS

    effective = {
        perm: bool(merged[perm]) and perm in ceiling
        for perm in ALL_PERMISSIONS
    }
    return PermissionView(
        effective=effective,
        sources=sources,
        defaults=defaults,
        overrides=overrides,
        ceiling=ceiling,
        room_role=ROOM_ROLE_ADMIN if ctx.room_role == ROOM_ROLE_ADMIN else ROOM_ROLE_USER,
        granted_by_id=ctx.granted_by_id,
        is_owner=is_owner,
        is_site_admin=is_site_admin,
        is_root=is_root,
        rank=rank,
        ancestor_ids=ctx.ancestor_ids,
    )


def clips_ceiling(desired: Mapping[str, bool], caller_effective: Mapping[str, bool]) -> list[str]:
    """Permission ids the caller is trying to grant but does not possess."""
    missing = []
    for perm, on in desired.items():
        if on and not caller_effective.get(perm, False):
            missing.append(perm)
    return missing


def can_act_on_target(caller: PermissionView, target: PermissionView, caller_id: str, target_id: str) -> bool:
    if caller_id == target_id:
        return False
    if target.is_root:
        return False
    if target.is_owner and not caller.is_root:
        return False
    if target.rank > caller.rank:
        return False
    if caller_id in target.ancestor_ids:
        return True
    if target_id in caller.ancestor_ids:
        return False
    if caller.is_root or caller.is_site_admin or caller.is_owner:
        return True
    if caller.rank > target.rank:
        return True
    return False


def permission_summary(effective: Mapping[str, bool]) -> list[str]:
    """Short labels for the members list."""
    bits = []
    if effective.get(PERM_SELECT_PLAYLIST_ITEM) or effective.get(PERM_CHANGE_PLAYBACK_SPEED):
        bits.append("پخش")
    if any(effective.get(p) for p in (
        PERM_ADD_VIDEO, PERM_ADD_VIDEO_URL, PERM_ADD_VIDEO_FILE, PERM_ADD_YOUTUBE,
        PERM_ADD_STREAM,
    )):
        bits.append("مدیا")
    if effective.get(PERM_JOIN_VOICE):
        bits.append("صدا")
    if effective.get(PERM_MANAGE_PERMISSIONS) or effective.get(PERM_PROMOTE_USER):
        bits.append("مدیریت")
    return bits
