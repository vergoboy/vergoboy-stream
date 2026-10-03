"""
وضعیت مشترک «اتاق تماشا». چون طبق طراحی فقط یک اتاق برای همه بازدیدکننده‌ها
وجود دارد، این وضعیت به‌صورت یک شیء سراسری در حافظه نگه‌داری می‌شود و برای
ماندگاری بین ری‌استارت‌ها، روی دیسک هم ذخیره می‌شود (فقط پلی‌لیست و موقعیت،
نه وضعیت آنلاین کاربران).

چت و آواتارها عمداً این‌جا نیستند: طبق تصمیم پروژه، چت کاملاً در حافظه است و
هرگز روی دیسک ذخیره نمی‌شود (نه در state.json و نه جای دیگر)، تا با هر
ری‌استارت سرور و همچنین به‌صورت خودکار هر ۲۴ ساعت پاک شود.
"""
import json
import os
import threading
import time

from config import Config

LOCK = threading.Lock()
CHAT_LOCK = threading.Lock()


class RoomState:
    def __init__(self, room_id: str = None):
        # room_id is the room's invite code. When set, state is persisted to a
        # per-room file (data/rooms/<code>.json) so each room keeps its own
        # playlist/position across restarts. None keeps the legacy single file.
        self.room_id = room_id
        self.playlist = []        # لیست آیتم‌های پلی‌لیست
        self.current_index = None
        self.playing = False
        self.position = 0.0       # ثانیه - آخرین موقعیت معتبر شناخته‌شده
        self.rate = 1.0
        self.shuffle = False
        self.shuffle_order = []          # shuffled order of playlist item IDs
        self.updated_at = time.time()
        self.users = {}           # sid -> {"name": ..., "avatar_url": ...}
        self._load()

    # ---------- محاسبه موقعیت لحظه‌ای ----------
    def current_position(self):
        if self.playing:
            return self.position + (time.time() - self.updated_at) * self.rate
        return self.position

    def to_public_dict(self):
        return {
            "playlist": [self._sanitize_item(it) for it in self.playlist],
            "current_index": self.current_index,
            "playing": self.playing,
            "position": self.current_position(),
            "rate": self.rate,
            "shuffle": self.shuffle,
            "shuffle_order": list(self.shuffle_order),
            "server_time": time.time(),
            "online": len(self.users_public_list()),
        }

    @staticmethod
    def _sanitize_item(item):
        """Strips server-internal fields (prefixed with '_', e.g. the raw
        source path/URL used later for on-demand quality encoding) before
        an item is sent to clients."""
        return {k: v for k, v in item.items() if not k.startswith("_")}

    def users_public_list(self):
        """Deduplicated per-user presence.

        One entry per PERSON (keyed by user id, falling back to the socket sid
        for guests), never per socket: the same account with three tabs open
        must appear exactly once in the lounge.

        Each entry carries the full set of simultaneous states that person
        currently holds, so the UI can render "watching while in voice" as
        one person instead of two unrelated entries:
          - `watching`: actively playing the shared media right now
          - `browsing`: connected to the room but not playing
          - `in_voice`: connected to the LiveKit voice room
          - `idle`:     connected, but not watching and not in voice
          - `last_seen`: unix time of the most recent activity, used for idle
        `id`/`can_control`/`is_owner` back the member-management panel.
        """
        public = {}
        for sid, user in self.users.items():
            identity = str(user.get("id") or sid)
            entry = public.get(identity)
            if entry is None:
                entry = public[identity] = {
                    "name": user.get("name", "ناشناس"),
                    "avatar_url": user.get("avatar_url"),
                    "in_voice": False,
                    "watching": False,
                    "idle": True,
                    "last_seen": 0,
                    "watching_item": None,
                    "id": user.get("id"),
                    "can_control": False,
                    "is_owner": False,
                    "sockets": 0,
                }
            # OR-merge: a person counts as present/active if ANY of their
            # sockets reports that state.
            entry["in_voice"] = entry["in_voice"] or bool(user.get("in_voice"))
            entry["watching"] = entry["watching"] or bool(user.get("watching"))
            entry["can_control"] = entry["can_control"] or bool(user.get("can_control"))
            entry["is_owner"] = entry["is_owner"] or bool(user.get("is_owner"))
            entry["avatar_url"] = entry["avatar_url"] or user.get("avatar_url")
            entry["last_seen"] = max(entry["last_seen"], float(user.get("last_seen") or 0))
            entry["sockets"] += 1
            if user.get("watching_item"):
                entry["watching_item"] = user["watching_item"]

        for entry in public.values():
            # A person is idle only when they are neither watching nor talking.
            entry["idle"] = not entry["watching"] and not entry["in_voice"]
            # Browsing = connected to the room, not currently watching.
            entry["browsing"] = not entry["watching"]
        return list(public.values())

    # ---------- تغییر وضعیت پخش ----------
    def set_play(self, at):
        self.playing = True
        self.position = max(0.0, at)
        self.updated_at = time.time()

    def set_pause(self, at):
        self.playing = False
        self.position = max(0.0, at)
        self.updated_at = time.time()

    def seek(self, to):
        self.position = max(0.0, to)
        self.updated_at = time.time()

    def set_shuffle(self, on: bool):
        import random as _r
        on = bool(on)
        self.shuffle = on
        if on:
            ids = [it["id"] for it in self.playlist]
            _r.shuffle(ids)
            self.shuffle_order = ids
            # keep the currently-playing item first (if it exists) so
            # playback continues naturally from where the room is.
            if self.current_index is not None and 0 <= self.current_index < len(self.playlist):
                cur = self.playlist[self.current_index].get("id")
                if cur in self.shuffle_order:
                    self.shuffle_order.remove(cur)
                    self.shuffle_order.insert(0, cur)
        else:
            self.shuffle_order = []
        self.updated_at = time.time()

    def shuffled_index_of(self, item_id):
        try:
            return self.shuffle_order.index(item_id)
        except ValueError:
            return -1

    def set_rate(self, rate):
        # برای پیوستگی موقعیت، قبل از تغییر نرخ، موقعیت لحظه‌ای را snapshot می‌کنیم
        self.position = self.current_position()
        self.updated_at = time.time()
        self.rate = rate

    def select_item(self, index):
        if index is None or index < 0 or index >= len(self.playlist):
            return
        self.current_index = index
        self.position = 0.0
        self.playing = False
        self.updated_at = time.time()

    # ---------- پلی‌لیست ----------
    def add_item(self, item):
        self.playlist.append(item)
        if self.shuffle:
            self.shuffle_order.append(item["id"])
        if self.current_index is None:
            self.current_index = len(self.playlist) - 1

    def remove_item(self, item_id):
        idx = next((i for i, x in enumerate(self.playlist) if x["id"] == item_id), None)
        if idx is None:
            return
        self.playlist.pop(idx)
        if self.shuffle and item_id in self.shuffle_order:
            self.shuffle_order.remove(item_id)
        if self.current_index is None:
            return
        if idx == self.current_index:
            self.current_index = None
            self.playing = False
            self.position = 0.0
        elif idx < self.current_index:
            self.current_index -= 1

    def find_item(self, item_id):
        return next((x for x in self.playlist if x["id"] == item_id), None)

    # ---------- ماندگاری روی دیسک ----------
    def _data_file(self) -> str:
        if self.room_id:
            d = os.path.join(os.path.dirname(Config.DATA_FILE), "rooms")
            os.makedirs(d, exist_ok=True)
            return os.path.join(d, f"{self.room_id}.json")
        return Config.DATA_FILE

    def save(self):
        try:
            with open(self._data_file(), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "room_id": self.room_id,
                        "playlist": self.playlist,
                        "current_index": self.current_index,
                        "position": self.position,
                        "rate": self.rate,
                        "shuffle": self.shuffle,
                        "shuffle_order": self.shuffle_order,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as e:
            print("[state] save error:", e)

    def _load(self):
        try:
            f = self._data_file()
            if os.path.exists(f):
                with open(f, encoding="utf-8") as fh:
                    d = json.load(fh)
                self.playlist = d.get("playlist", [])
                self.current_index = d.get("current_index")
                self.position = d.get("position", 0.0)
                self.rate = d.get("rate", 1.0)
                self.shuffle = bool(d.get("shuffle", False))
                self.shuffle_order = d.get("shuffle_order", []) or []
                self._migrate_playlist()
        except Exception as e:
            print("[state] load error:", e)

    def _migrate_playlist(self):
        """Backfills fields that older app.py versions didn't write yet, so
        state.json saved by a previous deploy doesn't confuse the current
        code (or the frontend) into thinking already-finished renditions
        are still being encoded. Safe to run every startup — a no-op once
        everything has already been migrated once."""
        changed = False
        for item in self.playlist:
            renditions = item.get("renditions")
            if not renditions:
                continue
            item_done = item.get("status") in ("complete", "ready")
            has_default = any(r.get("is_default") for r in renditions)
            for i, r in enumerate(renditions):
                if "status" not in r:
                    r["status"] = "complete" if item_done else "pending"
                    changed = True
                if "vbr" not in r:
                    r["vbr"] = r.get("vbr", "")
                    changed = True
                if "abr" not in r:
                    r["abr"] = r.get("abr", "")
                    changed = True
                if "is_default" not in r:
                    r["is_default"] = (not has_default and i == 0)
                    changed = True
        if changed:
            self.save()


class ChatState:
    """
    In-memory-only chat. Never persisted to disk on purpose (per project
    decision): cleared on every server restart AND auto-purged every 24h,
    plus a manual "clear chat" button. Also keeps a name -> avatar_url map
    so a user's custom-uploaded avatar sticks around for the life of the
    process even across reconnects (but, like chat, is not saved to disk).
    """

    def __init__(self):
        self.messages = []          # list of {id, name, text, image_url, ts}
        self.avatars_by_name = {}   # lowercased name -> avatar_url
        self._load_avatars()

    def add_message(self, msg):
        self.messages.append(msg)
        if len(self.messages) > Config.CHAT_MAX_MESSAGES:
            self.messages = self.messages[-Config.CHAT_MAX_MESSAGES:]

    def public_history(self):
        return list(self.messages)

    def clear(self):
        removed = self.messages
        self.messages = []
        return removed

    def purge_older_than(self, seconds):
        """Removes messages older than `seconds` and returns the removed
        message dicts (so the caller can also delete any attached image
        files from disk)."""
        cutoff = time.time() - seconds
        kept, removed = [], []
        for m in self.messages:
            (kept if m.get("ts", 0) >= cutoff else removed).append(m)
        self.messages = kept
        return removed

    def set_avatar(self, name, url):
        self.avatars_by_name[(name or "").strip().lower()] = url
        self._save_avatars()

    def get_avatar(self, name):
        return self.avatars_by_name.get((name or "").strip().lower())

    def _save_avatars(self):
        try:
            tmp = f"{Config.AVATAR_MAP_FILE}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.avatars_by_name, f, ensure_ascii=False, indent=2)
            os.replace(tmp, Config.AVATAR_MAP_FILE)
        except Exception as e:
            print("[state] avatars save error:", e)

    def _load_avatars(self):
        try:
            if os.path.exists(Config.AVATAR_MAP_FILE):
                with open(Config.AVATAR_MAP_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.avatars_by_name = {
                        str(k).strip().lower(): v for k, v in data.items() if v
                    }
        except Exception as e:
            print("[state] avatars load error:", e)


room = RoomState()
chat = ChatState()


class RoomManager:
    """Holds one in-memory RoomState + ChatState per room code. Rooms are
    materialized lazily (a room only exists in memory once someone is in it)
    and their playlist/position is persisted per-room to
    data/rooms/<code>.json."""

    def __init__(self):
        self._rooms: dict[str, RoomState] = {}
        self._chats: dict[str, ChatState] = {}
        self._lock = threading.Lock()

    def get(self, code: str) -> RoomState:
        code = (code or "").strip().upper()
        with self._lock:
            rs = self._rooms.get(code)
            if rs is None:
                rs = RoomState(room_id=code)
                self._rooms[code] = rs
            return rs

    def chat(self, code: str) -> ChatState:
        code = (code or "").strip().upper()
        with self._lock:
            cs = self._chats.get(code)
            if cs is None:
                cs = ChatState()
                self._chats[code] = cs
            return cs

    def drop(self, code: str):
        with self._lock:
            self._rooms.pop(code, None)
            self._chats.pop(code, None)


rooms = RoomManager()

