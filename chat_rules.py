"""Chat rules that do not depend on where messages are stored: who may edit/delete, @mentions,
mutes and slow mode. Pure functions + two tiny in-memory books, so they are easy to test and
the storage layer (memory today, database later) can call them unchanged.

Messages are plain dicts: {id, user_id, name, text, image_url, ts, edited_ts?, deleted?, pinned?, mentions?}.
All user-facing reasons are Persian, matching the rest of the API.
"""
from __future__ import annotations

import re
from typing import Iterable

MAX_MENTIONS = 10
DEFAULT_PIN_LIMIT = 5

_MENTION = re.compile(r"(?<![A-Za-z0-9_.\-@])@([A-Za-z0-9_.\-]{3,24})")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # keeps \t and \n


def clean_text(text) -> str:
    """Strip control characters (they break rendering and logs) and surrounding whitespace."""
    return _CONTROL.sub("", str(text or "")).strip()


def text_error(text: str, max_len: int) -> str | None:
    if not text:
        return "پیام خالی است"
    if len(text) > max_len:
        return f"پیام نباید بیشتر از {max_len} حرف باشد"
    return None


def parse_mentions(text: str, members: Iterable[dict], limit: int = MAX_MENTIONS) -> list[str]:
    """User ids mentioned with @username, in order of first appearance, de-duplicated.

    Only people in `members` ({id, username}) count; an exact username wins, otherwise a
    case-insensitive match is used only when it is unambiguous."""
    people = [(m["id"], str(m.get("username") or "")) for m in members if m.get("id")]
    exact = {name: uid for uid, name in people}
    lowered: dict[str, list[str]] = {}
    for uid, name in people:
        lowered.setdefault(name.lower(), []).append(uid)
    out: list[str] = []
    for found in _MENTION.findall(text or ""):
        uid = exact.get(found)
        if uid is None:
            cands = lowered.get(found.lower(), [])
            uid = cands[0] if len(cands) == 1 else None
        if uid and uid not in out:
            out.append(uid)
            if len(out) >= limit:
                break
    return out


# ------------------------------------------------------------------ edit / delete / pin

def edit_error(msg: dict, actor_id: str, now: float, window_s: float | None) -> str | None:
    """Why `actor_id` may not edit `msg` now, or None when allowed. `window_s` None/0 = no limit."""
    if msg.get("deleted"):
        return "این پیام حذف شده است"
    if msg.get("user_id") != actor_id:
        return "فقط نویسنده می‌تواند پیامش را ویرایش کند"
    if window_s and now - float(msg.get("ts", 0)) > window_s:
        return "زمان ویرایش این پیام گذشته است"
    return None


def apply_edit(msg: dict, new_text: str, now: float, members: Iterable[dict], max_len: int) -> dict:
    """A new message dict with the edited text, `edited_ts` and recomputed mentions. Raises ValueError."""
    text = clean_text(new_text)
    bad = text_error(text, max_len)
    if bad:
        raise ValueError(bad)
    out = dict(msg)
    out["text"] = text
    out["edited_ts"] = now
    out["mentions"] = parse_mentions(text, members)
    return out


def delete_error(msg: dict, actor_id: str, can_delete_others: bool) -> str | None:
    if msg.get("deleted"):
        return "این پیام قبلاً حذف شده است"
    if msg.get("user_id") == actor_id or can_delete_others:
        return None
    return "فقط نویسنده یا مدیر می‌تواند این پیام را حذف کند"


def pin_error(msg: dict, currently_pinned: int, limit: int = DEFAULT_PIN_LIMIT) -> str | None:
    if msg.get("deleted"):
        return "پیام حذف‌شده را نمی‌شود پین کرد"
    if msg.get("pinned"):
        return None  # pinning again is a no-op, not an error
    if currently_pinned >= limit:
        return f"حداکثر {limit} پیام می‌تواند پین باشد؛ اول یکی را بردار"
    return None


# ------------------------------------------------------------------ mute

class MuteBook:
    """Who is muted in one room's chat. `until=None` means until someone unmutes them."""

    def __init__(self):
        self._m: dict[str, dict] = {}

    def mute(self, user_id: str, now: float, duration_s: float | None = None, by: str | None = None, reason: str = "") -> dict:
        entry = {"until": (now + duration_s) if duration_s else None, "by": by, "reason": str(reason or "")[:200], "since": now}
        self._m[user_id] = entry
        return dict(entry)

    def unmute(self, user_id: str) -> bool:
        return self._m.pop(user_id, None) is not None

    def status(self, user_id: str, now: float) -> dict | None:
        """The active mute for the user, or None (an expired mute is removed on the way)."""
        e = self._m.get(user_id)
        if e is None:
            return None
        if e["until"] is not None and now >= e["until"]:
            del self._m[user_id]
            return None
        return dict(e)

    def active(self, now: float) -> dict[str, dict]:
        return {uid: e for uid in list(self._m) if (e := self.status(uid, now)) is not None}


class SlowMode:
    """At most one message per `interval_s` per person (moderators are exempted by the caller)."""

    def __init__(self):
        self._last: dict[str, float] = {}

    def wait_seconds(self, user_id: str, now: float, interval_s: float | None) -> float:
        if not interval_s:
            return 0.0
        last = self._last.get(user_id)
        if last is None:
            return 0.0
        return max(0.0, round(last + interval_s - now, 3))

    def record(self, user_id: str, now: float) -> None:
        self._last[user_id] = now
