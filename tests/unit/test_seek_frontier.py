"""The server-side encoded-frontier seek guard.

The client clamps seeks to the encoded timeline (Player.tsx `encodedFrontier`),
but `control` is an ordinary socket event — before this guard existed, any
client could hand-craft one and seek the whole room past the encoded timeline,
where the segment simply does not exist. Everyone then watches a black screen.

These tests pin the policy: the room-wide frontier is the **minimum** across all
non-complete renditions, because the server cannot know which rendition a given
viewer is watching and must be safe for all of them at once.
"""
from __future__ import annotations

import math

import pytest

app_module = pytest.importorskip("app", reason="app requires a configured environment")


@pytest.fixture(autouse=True)
def _clean_progress():
    """Every test starts and ends with an empty progress table."""
    app_module.TRANSCODE_PROGRESS.clear()
    yield
    app_module.TRANSCODE_PROGRESS.clear()


def _rend(label, status):
    return {"label": label, "status": status}


def _item(renditions, status="encoding", item_id="item-1"):
    return {"id": item_id, "type": "movie", "status": status, "renditions": renditions}


def _record(**kwargs):
    app_module._record_encoded_progress(kwargs["item_id"], kwargs["label"], kwargs["seconds"])


# ── recording ────────────────────────────────────────────────────────────────


def test_record_stores_the_encoded_seconds():
    _record(item_id="item-1", label="720p", seconds=42.5)
    assert app_module.TRANSCODE_PROGRESS["item-1:720p"]["encoded_seconds"] == 42.5


def test_record_overwrites_with_the_newer_value():
    _record(item_id="item-1", label="720p", seconds=10.0)
    _record(item_id="item-1", label="720p", seconds=90.0)
    assert app_module.TRANSCODE_PROGRESS["item-1:720p"]["encoded_seconds"] == 90.0


def test_record_keeps_renditions_separate():
    """Collapsing the ladder into one number would clamp everyone by the
    slowest rung even when the viewer is watching a finished one."""
    _record(item_id="item-1", label="360p", seconds=10.0)
    _record(item_id="item-1", label="1080p", seconds=300.0)
    assert len(app_module.TRANSCODE_PROGRESS) == 2


def test_record_ignores_missing_id_or_label():
    app_module._record_encoded_progress("", "720p", 5.0)
    app_module._record_encoded_progress("item-1", "", 5.0)
    assert app_module.TRANSCODE_PROGRESS == {}


def test_record_ignores_a_non_numeric_value():
    """A malformed progress payload must not poison the guard with a string."""
    app_module._record_encoded_progress("item-1", "720p", "not-a-number")
    assert app_module.TRANSCODE_PROGRESS == {}


def test_clear_drops_every_rendition_of_one_item_only():
    _record(item_id="item-1", label="360p", seconds=10.0)
    _record(item_id="item-1", label="1080p", seconds=20.0)
    _record(item_id="item-2", label="720p", seconds=30.0)
    app_module._clear_encoded_progress("item-1")
    assert list(app_module.TRANSCODE_PROGRESS) == ["item-2:720p"]


def test_clear_does_not_prefix_collide():
    """'item-1' must not wipe 'item-10'.

    A plain `startswith(f"{item_id}:")` is right precisely because of the colon;
    this test is here so nobody 'simplifies' it to `startswith(item_id)`.
    """
    _record(item_id="item-1", label="720p", seconds=10.0)
    _record(item_id="item-10", label="720p", seconds=20.0)
    app_module._clear_encoded_progress("item-1")
    assert "item-10:720p" in app_module.TRANSCODE_PROGRESS


# ── the frontier itself ──────────────────────────────────────────────────────


def test_live_items_have_no_frontier():
    """A live stream is not progressively encoded, so there is nothing to
    clamp against and every seek is legal."""
    assert app_module._encoded_frontier({"type": "live", "renditions": []}) == math.inf


def test_missing_item_has_no_frontier():
    assert app_module._encoded_frontier({}) == math.inf
    assert app_module._encoded_frontier(None) == math.inf


def test_complete_item_with_no_renditions_is_unlimited():
    assert app_module._encoded_frontier({"status": "complete", "renditions": []}) == math.inf


def test_unfinished_item_with_no_renditions_is_unsafe():
    """No ladder and not complete means we do not know what exists — assume
    nothing, rather than letting a viewer outrun the encoder."""
    assert app_module._encoded_frontier({"status": "encoding", "renditions": []}) == 0.0


def test_a_ready_rendition_uses_its_recorded_progress():
    _record(item_id="item-1", label="720p", seconds=120.0)
    item = _item([_rend("720p", "ready")])
    assert app_module._encoded_frontier(item) == 120.0


def test_frontier_is_the_minimum_across_non_complete_renditions():
    """The core of the chosen policy.

    360p is far ahead, 1080p is behind. A viewer on either must be safe, so the
    frontier is the slower one.
    """
    _record(item_id="item-1", label="360p", seconds=300.0)
    _record(item_id="item-1", label="1080p", seconds=45.0)
    item = _item([_rend("360p", "ready"), _rend("1080p", "ready")])
    assert app_module._encoded_frontier(item) == 45.0


def test_complete_renditions_do_not_constrain_the_frontier():
    """A finished rung is fully available, so it must not drag the frontier
    down to its last reported progress."""
    _record(item_id="item-1", label="360p", seconds=300.0)
    item = _item([_rend("360p", "ready"), _rend("1080p", "complete")])
    assert app_module._encoded_frontier(item) == 300.0


def test_a_rendition_with_no_status_is_not_treated_as_playable():
    """Deliberate reversal of the earlier, permissive reading.

    A rendition dict with no `status` used to be assumed complete, so a
    malformed item was freely seekable. That is the unsafe direction: the whole
    point of this guard is that seeking into a segment which does not exist
    shows a black screen to everyone. No code path writes a status-less
    rendition -- `start_item_encoding` is the only writer and always sets one --
    so this is malformed data, and the safe reading is "nothing is playable".
    """
    _record(item_id="item-1", label="720p", seconds=60.0)
    item = _item([{"label": "720p"}])
    assert app_module._encoded_frontier(item) == 0.0

    # An item already marked complete is unaffected: that check comes first, so a
    # legacy finished item stays freely seekable.
    done = _item([{"label": "720p"}], status="complete")
    assert app_module._encoded_frontier(done) == math.inf


def test_a_rendition_not_yet_advertised_does_not_block_everything():
    """A rung that is still encoding is not in the master playlist yet.

    It must not cap the room: nobody can select it, so it cannot leave anybody
    with a hole, and treating it as "nothing is safe" would pin the whole room
    at 0s until the slowest rung finished -- which, for the top of a ladder, is
    most of the item.
    """
    _record(item_id="item-1", label="360p", seconds=300.0)
    item = _item([_rend("360p", "ready"), _rend("1080p", "encoding")])
    assert app_module._encoded_frontier(item) == 300.0


@pytest.mark.parametrize("status", ["queued", "pending", "encoding"])
def test_a_queued_or_pending_rung_is_ignored(status):
    """Queued, pending and encoding are all pre-advertisement states.

    Ignored *as long as some other rung is playable*. The all-queued case is
    different and covered separately: with nothing advertised there is nothing
    to watch.
    """
    _record(item_id="item-1", label="360p", seconds=300.0)
    item = _item([_rend("360p", "ready"), _rend("1080p", status)])
    assert app_module._encoded_frontier(item) == 300.0


def test_no_rung_is_playable_yet_so_nothing_is_safe():
    """Every rung still queued: there is not one encoded second to seek to.

    Excluding unadvertised rungs from the minimum must not be read as "no
    limit". If it were, the first few seconds of every encode would let any
    viewer seek anywhere, which is exactly the black screen this guard exists to
    prevent -- and it would happen on every single item.
    """
    item = _item([
        _rend("360p", "queued"),
        _rend("720p", "queued"),
        _rend("1080p", "queued"),
    ])
    assert app_module.seek_frontier(item, {}) == 0.0


def test_a_dead_rung_alone_leaves_nothing_playable():
    """The mirror of the mixed case: if the only ready rung failed, there is
    nothing left to watch and the room is not freely seekable."""
    item = _item([_rend("360p", "error"), _rend("720p", "queued")])
    assert app_module.seek_frontier(item, {"i:360p": {"encoded_seconds": 900.0}}) == 0.0


@pytest.mark.parametrize("status", ["error", "failed", "cancelled", "canceled"])
def test_a_dead_rung_does_not_freeze_the_room(status):
    """A failed rung will never become selectable.

    Left in the minimum it would hold every viewer at 0s for the rest of the
    item's life, because its last recorded progress stays where it died.
    """
    _record(item_id="item-1", label="360p", seconds=300.0)
    _record(item_id="item-1", label="1080p", seconds=12.0)
    item = _item([_rend("360p", "ready"), _rend("1080p", status)])
    assert app_module._encoded_frontier(item) == 300.0


def test_ready_with_no_recorded_progress_blocks_everything():
    """Unknown is not 'unlimited'.

    A rendition is marked ready on the same tick its progress is recorded, so
    this state should not arise; if it does, refusing is the safe reading. Note
    the asymmetry with the rung above: this one *is* advertised, so a viewer can
    select it, and nothing is known about how far it goes.
    """
    item = _item([_rend("720p", "ready")])
    assert app_module._encoded_frontier(item) == 0.0


def test_progress_from_a_different_item_is_ignored():
    _record(item_id="other", label="720p", seconds=999.0)
    assert app_module._encoded_frontier(_item([_rend("720p", "ready")])) == 0.0


def test_progress_from_a_different_label_is_ignored():
    _record(item_id="item-1", label="1080p", seconds=999.0)
    assert app_module._encoded_frontier(_item([_rend("720p", "ready")])) == 0.0


# ── the clamp itself: clamp_seek_target, a pure function ─────────────────────
# Everything above drives the real progress table. These call the pure helpers
# directly, so the policy is pinned independently of any encoder having run.


def test_margin_is_three_seconds_matching_the_client():
    """Drift between the two guards would let the client and server disagree
    about whether a given seek is legal, which is worse than either alone."""
    client_source = (
        __import__("pathlib").Path(app_module.__file__).resolve().parents[0]
        / "webapp" / "components" / "Player.tsx"
    ).read_text()
    assert "const MARGIN = 3;" in client_source, (
        "client margin changed; update SEEK_FRONTIER_MARGIN to match"
    )
    assert app_module.SEEK_FRONTIER_MARGIN == 3.0


@pytest.mark.parametrize("frontier,target,expected", [
    (120.0, 10.0, 10.0),        # well within range: untouched
    (120.0, 117.0, 117.0),      # exactly at the limit (frontier - margin): allowed
    (120.0, 117.001, 117.0),    # a hair past the limit: clamped
    (120.0, 118.0, 117.0),
    (120.0, 5000.0, 117.0),     # wildly over: clamped
    (10.0, 5000.0, 7.0),        # still a usable position
    (2.0, 5000.0, 0.0),         # frontier shorter than the margin: floor at 0
    (1.0, 5000.0, 0.0),
    (0.0, 5000.0, 0.0),         # nothing encoded yet
    (-50.0, 10.0, 0.0),         # a negative target is floored, never negative
])
def test_clamp_seek_target_arithmetic(frontier, target, expected):
    assert app_module.clamp_seek_target(target, frontier) == pytest.approx(expected)


def test_a_target_exactly_at_the_frontier_is_clamped_back_by_the_margin():
    """The limit is `frontier - MARGIN`, so the frontier itself is already past it.

    Asking for the last reported encoded second lands 3s earlier on purpose: the
    margin covers a segment boundary that is a moment behind the number the
    encoder reported. Allowing the frontier itself would put that boundary back
    inside the request, which is the black screen this guard exists to prevent.
    """
    assert app_module.clamp_seek_target(120.0, 120.0) == pytest.approx(117.0)
    # The last position that is *not* clamped is the limit itself.
    assert app_module.clamp_seek_target(117.0, 120.0) == pytest.approx(117.0)
    assert app_module.clamp_seek_target(117.001, 120.0) == pytest.approx(117.0)


def test_an_unlimited_frontier_never_clamps():
    """`inf - MARGIN` is still `inf`, so the comparison is always False."""
    for target in (10.0, 1e9, 0.0):
        assert app_module.clamp_seek_target(target, math.inf) == pytest.approx(target)


def test_a_target_at_the_item_duration_is_never_clamped_by_a_longer_frontier():
    """A frontier past the duration is harmless.

    ffprobe occasionally reports a duration slightly under what actually
    encoded, so `frontier > duration` is normal noise, not a reason to reject a
    seek to the end.
    """
    duration = 5400.0
    frontier = 5412.5  # ahead of the reported duration
    assert app_module.clamp_seek_target(duration, frontier) == pytest.approx(duration)
    # ...but a target beyond even that still gets capped.
    assert app_module.clamp_seek_target(9000.0, frontier) == pytest.approx(frontier - 3.0)


def test_the_clamp_ignores_a_non_numeric_or_infinite_target():
    """A hand-crafted event must not put NaN into the shared position."""
    assert app_module.clamp_seek_target(float("nan"), 120.0) == 0.0
    assert app_module.clamp_seek_target(float("inf"), 120.0) == 0.0
    assert app_module.clamp_seek_target("not-a-number", 120.0) == 0.0
    assert app_module.clamp_seek_target(None, 120.0) == 0.0


# ── seek_frontier: the policy, as a pure function of (item, progress) ─────────


def test_seek_frontier_takes_the_slowest_selectable_rung():
    """Three rungs at three speeds: the frontier is the slowest live one."""
    progress = {
        "i1:360p": {"encoded_seconds": 300.0},
        "i1:720p": {"encoded_seconds": 140.0},
        "i1:1080p": {"encoded_seconds": 22.0},
    }
    item = {
        "id": "i1", "type": "movie", "status": "encoding",
        "renditions": [
            {"label": "360p", "status": "ready"},
            {"label": "720p", "status": "ready"},
            {"label": "1080p", "status": "ready"},
        ],
    }
    assert app_module.seek_frontier(item, progress) == 22.0


def test_one_failed_and_one_queued_rung_leave_only_the_live_one():
    """The mixed case the policy exists for.

    One rung is ready and far ahead, one died, one is queued but not yet
    advertised. Only the ready rung may cap the room: the failed one will never
    be selectable, and the queued one is not in the master playlist yet.
    """
    progress = {
        "i2:360p": {"encoded_seconds": 480.0},
        "i2:1080p": {"encoded_seconds": 31.0},   # died here; must be ignored
        "i2:720p": {"encoded_seconds": 0.0},     # queued, nothing encoded yet
    }
    item = {
        "id": "i2", "type": "movie", "status": "encoding",
        "renditions": [
            {"label": "360p", "status": "ready"},
            {"label": "720p", "status": "queued"},
            {"label": "1080p", "status": "error"},
        ],
    }
    assert app_module.seek_frontier(item, progress) == 480.0


def test_every_selectable_rung_complete_means_no_clamp():
    """Nothing is still encoding, so the whole timeline exists."""
    progress = {"i3:720p": {"encoded_seconds": 600.0}}
    item = {
        "id": "i3", "type": "movie", "status": "complete",
        "renditions": [
            {"label": "720p", "status": "complete"},
            {"label": "1080p", "status": "complete"},
        ],
    }
    assert app_module.seek_frontier(item, progress) == math.inf


def test_all_rungs_complete_while_the_item_still_says_encoding_also_means_no_clamp():
    """The item status lags the rung statuses, so the rungs get the last word."""
    progress = {"i4:720p": {"encoded_seconds": 600.0}}
    item = {
        "id": "i4", "type": "movie", "status": "encoding",
        "renditions": [
            {"label": "720p", "status": "complete"},
            {"label": "1080p", "status": "complete"},
        ],
    }
    assert app_module.seek_frontier(item, progress) == math.inf


def test_a_copy_plan_has_no_frontier_even_while_encoding():
    """copy/remux/direct-play lay the timeline down at disk speed.

    There is no live frontier to guard, and the item is advertised essentially
    immediately, so clamping it would block seeking a file that is already
    fully there.
    """
    progress = {"i5:720p": {"encoded_seconds": 10.0}}
    item = {
        "id": "i5", "type": "movie", "status": "encoding", "mode": "copy",
        "renditions": [{"label": "720p", "status": "ready"}],
    }
    assert app_module.seek_frontier(item, progress) == math.inf


def test_a_completed_copy_rung_lifts_the_clamp_for_the_whole_item():
    """Once a copy rung is complete the entire timeline is on disk.

    The transcode rung is still crawling, but the master playlist can hand the
    finished copy to anyone, so there is nothing left to protect against.
    """
    progress = {
        "i6:360p": {"encoded_seconds": 900.0},
        "i6:720p": {"encoded_seconds": 40.0},
    }
    item = {
        "id": "i6", "type": "movie", "status": "encoding",
        "renditions": [
            {"label": "360p", "status": "complete", "mode": "copy"},
            {"label": "720p", "status": "ready"},
        ],
    }
    assert app_module.seek_frontier(item, progress) == math.inf


def test_a_ready_copy_rung_does_not_lift_the_clamp():
    """Rule 3 needs the copy rung to be *complete*.

    A copy rung that is still being written is at 5s, and a viewer who selected
    it cannot seek past 5s -- so the room is still capped by the slowest rung
    that is genuinely incomplete.
    """
    progress = {
        "i6b:360p": {"encoded_seconds": 5.0},
        "i6b:720p": {"encoded_seconds": 900.0},
    }
    item = {
        "id": "i6b", "type": "movie", "status": "encoding",
        "renditions": [
            {"label": "360p", "status": "ready", "mode": "copy"},
            {"label": "720p", "status": "ready"},
        ],
    }
    assert app_module.seek_frontier(item, progress) == 5.0


def test_no_renditions_at_all_is_unsafe_unless_the_item_is_done():
    """Nothing is known yet, so nothing is safe."""
    assert app_module.seek_frontier(
        {"id": "i7", "type": "movie", "status": "encoding", "renditions": []}, {}
    ) == 0.0
    assert app_module.seek_frontier(
        {"id": "i7", "type": "movie", "status": "complete", "renditions": []}, {}
    ) == math.inf
    assert app_module.seek_frontier(
        {"id": "i7", "type": "movie", "status": "encoding"}, {}
    ) == 0.0


def test_the_policy_is_pure_and_does_not_read_the_live_store():
    """`seek_frontier` must be decided entirely by its arguments.

    If it reached for the global table instead, a test could pass or fail
    depending on what an earlier test happened to leave behind.
    """
    app_module.TRANSCODE_PROGRESS["i8:720p"] = {"encoded_seconds": 999.0}
    item = _item([_rend("720p", "ready")], item_id="i8")
    assert app_module.seek_frontier(item, {}) == 0.0
    assert app_module.seek_frontier(item, {"i8:720p": {"encoded_seconds": 12.0}}) == 12.0


# ── the lagging-rung warning ─────────────────────────────────────────────────


def test_a_lagging_rung_is_logged_once_with_its_gap(caplog):
    """The slowest live rung is what caps the room, so say so in the log."""
    import logging

    app_module._LAG_WARNED_AT.pop("i9", None)
    progress = {
        "i9:360p": {"encoded_seconds": 400.0},
        "i9:1080p": {"encoded_seconds": 120.0},
    }
    item = {
        "id": "i9", "type": "movie", "status": "encoding",
        "renditions": [{"label": "360p", "status": "ready"},
                       {"label": "1080p", "status": "ready"}],
    }
    with caplog.at_level(logging.WARNING, logger="stream.sync"):
        app_module._warn_if_lagging(item, progress)

    messages = [r.getMessage() for r in caplog.records if r.name == "stream.sync"]
    assert len(messages) == 1, messages
    text = messages[0]
    assert "1080p" in text          # the lagging level, named
    assert "i9" in text             # the item, named
    assert "280.0s" in text         # the gap, quantified


def test_the_lagging_warning_is_rate_limited_to_one_per_minute(caplog):
    """A seek storm must not become a log flood."""
    import logging

    app_module._LAG_WARNED_AT.pop("i10", None)
    progress = {
        "i10:360p": {"encoded_seconds": 400.0},
        "i10:1080p": {"encoded_seconds": 100.0},
    }
    item = {
        "id": "i10", "type": "movie", "status": "encoding",
        "renditions": [{"label": "360p", "status": "ready"},
                       {"label": "1080p", "status": "ready"}],
    }
    with caplog.at_level(logging.WARNING, logger="stream.sync"):
        for _ in range(20):
            app_module._warn_if_lagging(item, progress)
    messages = [r for r in caplog.records if r.name == "stream.sync"]
    assert len(messages) == 1, [r.getMessage() for r in messages]

    # Pretend the interval has elapsed: the next call warns again.
    app_module._LAG_WARNED_AT["i10"] = 0.0
    with caplog.at_level(logging.WARNING, logger="stream.sync"):
        app_module._warn_if_lagging(item, progress)
    assert len([r for r in caplog.records if r.name == "stream.sync"]) == 2


def test_a_healthy_ladder_logs_nothing(caplog):
    """Two rungs within the threshold are normal, not an incident."""
    import logging

    app_module._LAG_WARNED_AT.pop("i11", None)
    progress = {
        "i11:360p": {"encoded_seconds": 300.0},
        "i11:720p": {"encoded_seconds": 295.0},
    }
    item = {
        "id": "i11", "type": "movie", "status": "encoding",
        "renditions": [{"label": "360p", "status": "ready"},
                       {"label": "720p", "status": "ready"}],
    }
    with caplog.at_level(logging.WARNING, logger="stream.sync"):
        app_module._warn_if_lagging(item, progress)
    assert [r for r in caplog.records if r.name == "stream.sync"] == []


def test_a_lone_rung_cannot_lag(caplog):
    """One rung has nothing to lag behind, however slow it is."""
    import logging

    app_module._LAG_WARNED_AT.pop("i12", None)
    progress = {"i12:720p": {"encoded_seconds": 3.0}}
    item = {
        "id": "i12", "type": "movie", "status": "encoding",
        "renditions": [{"label": "720p", "status": "ready"}],
    }
    with caplog.at_level(logging.WARNING, logger="stream.sync"):
        app_module._warn_if_lagging(item, progress)
    assert [r for r in caplog.records if r.name == "stream.sync"] == []


def test_clearing_an_item_forgets_its_warning_stamp():
    """A re-encode of the same id must be able to warn again immediately."""
    app_module._record_encoded_progress("i13", "720p", 10.0)
    app_module._LAG_WARNED_AT["i13"] = 12345.0
    app_module._clear_encoded_progress("i13")
    assert "i13" not in app_module._LAG_WARNED_AT
