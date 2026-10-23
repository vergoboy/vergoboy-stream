"""chat_rules: edit/delete/pin permissions, mentions, mutes and slow mode (storage-independent)."""
from __future__ import annotations

import pytest

import chat_rules as cr

MEMBERS = [
    {"id": "u1", "username": "alice"}, {"id": "u2", "username": "Bob"},
    {"id": "u3", "username": "carol.x"}, {"id": "u4", "username": "BOB2"},
]


def msg(**kw):
    base = {"id": "m1", "user_id": "u1", "name": "alice", "text": "hello", "ts": 1000.0}
    base.update(kw)
    return base


# ------------------------------------------------------------------ text
def test_clean_text_removes_control_chars_but_keeps_newlines_and_tabs():
    assert cr.clean_text("  a\x00b\x1b[31m\n\tc \x7f ") == "ab[31m\n\tc"
    assert cr.clean_text(None) == ""


@pytest.mark.parametrize("text,ok", [("hi", True), ("", False), ("x" * 500, True), ("x" * 501, False)])
def test_text_limits(text, ok):
    assert (cr.text_error(text, 500) is None) is ok


# ------------------------------------------------------------------ mentions
def test_mentions_resolve_members_in_order_without_duplicates():
    assert cr.parse_mentions("hey @alice and @carol.x, @alice again", MEMBERS) == ["u1", "u3"]


def test_mentions_ignore_strangers_emails_and_too_short_names():
    assert cr.parse_mentions("mail me at x@alice.com or @zed or @al", MEMBERS) == []
    assert cr.parse_mentions("@@alice", MEMBERS) == []


def test_exact_case_wins_and_case_insensitive_needs_to_be_unambiguous():
    assert cr.parse_mentions("@Bob", MEMBERS) == ["u2"]       # exact
    assert cr.parse_mentions("@ALICE", MEMBERS) == ["u1"]     # only one 'alice' ignoring case
    two = MEMBERS + [{"id": "u9", "username": "bob"}]
    assert cr.parse_mentions("@BOB", two) == []               # 'Bob' and 'bob': ambiguous, so nobody


def test_mention_count_is_capped():
    members = [{"id": f"u{i}", "username": f"user{i:03d}"} for i in range(40)]
    text = " ".join(f"@user{i:03d}" for i in range(40))
    assert len(cr.parse_mentions(text, members)) == cr.MAX_MENTIONS


# ------------------------------------------------------------------ edit
def test_only_the_author_can_edit_and_only_inside_the_window():
    m = msg(ts=1000)
    assert cr.edit_error(m, "u1", 1100, 3600) is None
    assert cr.edit_error(m, "u2", 1100, 3600) == "فقط نویسنده می‌تواند پیامش را ویرایش کند"
    assert cr.edit_error(m, "u1", 1000 + 3601, 3600) == "زمان ویرایش این پیام گذشته است"
    assert cr.edit_error(m, "u1", 10**9, None) is None         # no window = no limit
    assert cr.edit_error(m, "u1", 10**9, 0) is None


def test_a_moderator_with_delete_rights_still_cannot_edit_someone_elses_words():
    assert cr.edit_error(msg(), "mod", 1001, None) is not None


def test_deleted_messages_cannot_be_edited():
    assert cr.edit_error(msg(deleted=True), "u1", 1001, None) == "این پیام حذف شده است"


def test_apply_edit_sets_edit_time_text_and_new_mentions_without_touching_the_original():
    original = msg(text="hi @alice", mentions=["u1"])
    out = cr.apply_edit(original, "  hi @carol.x \x00 ", 2000.0, MEMBERS, 500)
    assert out["text"] == "hi @carol.x" and out["edited_ts"] == 2000.0 and out["mentions"] == ["u3"]
    assert original["text"] == "hi @alice" and "edited_ts" not in original      # input untouched
    assert out["id"] == "m1" and out["ts"] == 1000.0                              # identity and order kept


@pytest.mark.parametrize("bad", ["", "   ", "\x00\x01", "x" * 501])
def test_apply_edit_rejects_empty_and_too_long_text(bad):
    with pytest.raises(ValueError):
        cr.apply_edit(msg(), bad, 2000.0, MEMBERS, 500)


# ------------------------------------------------------------------ delete / pin
def test_delete_rules():
    m = msg()
    assert cr.delete_error(m, "u1", False) is None             # own message
    assert cr.delete_error(m, "u2", False) is not None         # someone else's, no rights
    assert cr.delete_error(m, "u2", True) is None              # moderator
    assert cr.delete_error(msg(deleted=True), "u1", True) == "این پیام قبلاً حذف شده است"


def test_pin_rules_and_limit():
    assert cr.pin_error(msg(), 0) is None
    assert cr.pin_error(msg(), cr.DEFAULT_PIN_LIMIT - 1) is None
    assert cr.pin_error(msg(), cr.DEFAULT_PIN_LIMIT) is not None
    assert cr.pin_error(msg(pinned=True), cr.DEFAULT_PIN_LIMIT) is None      # already pinned: not an error
    assert cr.pin_error(msg(deleted=True), 0) is not None
    assert cr.pin_error(msg(), 9, limit=10) is None


# ------------------------------------------------------------------ mute
def test_timed_mute_expires_by_itself_and_permanent_mute_does_not():
    b = cr.MuteBook()
    b.mute("u1", 100.0, duration_s=60, by="mod", reason="spam")
    b.mute("u2", 100.0)
    assert b.status("u1", 159.9)["by"] == "mod" and b.status("u1", 159.9)["until"] == 160.0
    assert b.status("u1", 160.0) is None                       # expired exactly at `until`
    assert b.status("u2", 10**9)["until"] is None               # permanent
    assert set(b.active(10**9)) == {"u2"}


def test_unmute_and_unknown_users():
    b = cr.MuteBook()
    b.mute("u1", 1.0)
    assert b.unmute("u1") is True and b.unmute("u1") is False and b.status("u1", 2.0) is None
    assert b.status("nobody", 1.0) is None


def test_mute_reason_is_bounded_and_the_returned_entry_is_a_copy():
    b = cr.MuteBook()
    e = b.mute("u1", 1.0, reason="x" * 1000)
    assert len(e["reason"]) == 200
    e["until"] = 5
    assert b.status("u1", 2.0)["until"] is None


# ------------------------------------------------------------------ slow mode
def test_slow_mode_waits_only_for_the_remaining_time():
    s = cr.SlowMode()
    assert s.wait_seconds("u1", 100.0, 10) == 0               # first message is free
    s.record("u1", 100.0)
    assert s.wait_seconds("u1", 104.0, 10) == 6.0
    assert s.wait_seconds("u1", 110.0, 10) == 0.0
    assert s.wait_seconds("u2", 104.0, 10) == 0               # other people are independent
    assert s.wait_seconds("u1", 101.0, None) == 0 and s.wait_seconds("u1", 101.0, 0) == 0   # disabled
