"""Phase 2 review findings on the seek frontier.

Three cases called out in review, each pinned here. They are deliberately kept
separate from the protocol-v2 work: the frontier guard is the web/HLS safety
net and must keep working while the sync slice is still being lifted.

1. An item with no `renditions` — a manually pasted HLS URL — is not an unsafe
   unknown. Nothing is being encoded for it, so there is no frontier to clamp
   to. Today it returns 0.0, which pins the whole room at the start forever.

2. After a restart the in-memory progress store is empty, so every selectable
   unfinished rung looks "unknown" and the room clamps to 0 until the encode
   is re-kicked. The segments are still on disk; the frontier should be
   rebuilt from them.

3. A rendition must be marked complete *before* its progress is cleared,
   otherwise there is a window where the item is neither complete nor
   measurable and the guard reports 0.0.
"""
from __future__ import annotations

import math
import os

import pytest

from config import Config

import app


# ── 1. manual HLS items have no renditions and must not clamp ───────────────

def manual_hls_item() -> dict:
    """Exactly the shape _add_url_item builds for a pasted .m3u8 URL:
    needs_encode is False, so status is "ready", src is the m3u8, and there
    is no `renditions` key and no `mode`."""
    return {
        "id": "hls1",
        "type": "url",
        "title": "live channel",
        "src": "https://example.com/stream/master.m3u8",
        "subtitles": [],
        "audio_tracks": [],
        "added_by": "someone",
        "status": "ready",
    }


def test_manual_hls_item_is_not_clamped_to_zero():
    assert app.seek_frontier(manual_hls_item(), {}) == math.inf


def test_manual_hls_item_allows_a_seek_anywhere():
    assert app.clamp_seek_target(7200.0, app.seek_frontier(manual_hls_item(), {})) == 7200.0


def test_a_queued_item_with_no_ladder_is_still_pinned_to_zero():
    """The opposite case must NOT be relaxed: an item that is about to be
    encoded but whose ladder has not been recorded yet really is unplayable."""
    queued = {"id": "q1", "type": "url", "src": None, "status": "queued"}
    assert app.seek_frontier(queued, {}) == 0.0


def test_an_encoding_item_with_no_ladder_is_still_pinned_to_zero():
    encoding = {"id": "e1", "type": "url", "src": None, "status": "encoding"}
    assert app.seek_frontier(encoding, {}) == 0.0


# ── 2. restart: rebuild the frontier from segments on disk ──────────────────

def in_progress_item() -> dict:
    return {
        "id": "item9",
        "type": "url",
        "src": None,
        "status": "encoding",
        "mode": "transcode",
        "renditions": [
            {"label": "480p", "status": "ready", "mode": "transcode"},
            {"label": "720p", "status": "ready", "mode": "transcode"},
        ],
    }


def test_empty_progress_after_restart_clamps_to_zero_today():
    """Documents today's behaviour; the fix below changes it."""
    assert app.seek_frontier(in_progress_item(), {}) == 0.0


def write_rung(hls_dir, item_id, label, durations):
    """Write one rung the way ffmpeg does: seg_%03d.ts per segment plus an
    index.m3u8 carrying one #EXTINF per completed segment."""
    d = hls_dir / item_id / label
    d.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:4",
             "#EXT-X-MEDIA-SEQUENCE:0"]
    for n, dur in enumerate(durations):
        (d / f"seg_{n:03d}.ts").write_bytes(b"")
        lines.append(f"#EXTINF:{dur:.6f},")
        lines.append(f"seg_{n:03d}.ts")
    (d / "index.m3u8").write_text("\n".join(lines) + "\n")


def test_frontier_is_rebuilt_from_segments_on_disk_after_restart(tmp_path, monkeypatch):
    hls_dir = tmp_path / "hls"
    write_rung(hls_dir, "item9", "480p", [4.0] * 30)
    write_rung(hls_dir, "item9", "720p", [4.0] * 10)
    monkeypatch.setattr(Config, "HLS_DIR", str(hls_dir))

    frontier = app.seek_frontier(in_progress_item(), {})
    assert frontier == 40.0
    assert frontier > 0, "must not pin the room to the start after a restart"


def test_rebuild_uses_the_slowest_rung_not_the_fastest(tmp_path, monkeypatch):
    hls_dir = tmp_path / "hls"
    write_rung(hls_dir, "item9", "480p", [4.0] * 5)
    write_rung(hls_dir, "item9", "720p", [4.0] * 40)
    monkeypatch.setattr(Config, "HLS_DIR", str(hls_dir))

    assert app.seek_frontier(in_progress_item(), {}) == 20.0


def test_rebuild_accounts_for_a_partial_final_segment(tmp_path, monkeypatch):
    """Measured against real ffmpeg output: 22.0s of a 22s clip encodes as five
    4s segments and one 2s segment. Counting files would claim 24s and hand out a
    seek target 2s past the last byte actually on disk."""
    hls_dir = tmp_path / "hls"
    measured = [4.0, 4.0, 4.0, 4.0, 4.0, 2.0]
    write_rung(hls_dir, "item9", "480p", measured)
    write_rung(hls_dir, "item9", "720p", measured)
    monkeypatch.setattr(Config, "HLS_DIR", str(hls_dir))

    frontier = app.seek_frontier(in_progress_item(), {})
    assert frontier == 22.0
    assert frontier != 24.0, "must not round up to count * HLS_SEGMENT_SECONDS"
    assert frontier < 24.0, "must never exceed what is provably on disk"


def test_file_count_fallback_is_one_segment_pessimistic(tmp_path, monkeypatch):
    """No playlist (crashed encode, or written before the first flush): the
    count is still better than pinning to 0, but must not over-report."""
    hls_dir = tmp_path / "hls"
    for label, n in (("480p", 10), ("720p", 4)):
        d = hls_dir / "item9" / label
        d.mkdir(parents=True)
        for i in range(n):
            (d / f"seg_{i:03d}.ts").write_bytes(b"")
    monkeypatch.setattr(Config, "HLS_DIR", str(hls_dir))

    frontier = app.seek_frontier(in_progress_item(), {})
    assert frontier == 3 * Config.HLS_SEGMENT_SECONDS


def test_single_segment_without_a_playlist_is_not_claimed_as_measurable(tmp_path, monkeypatch):
    hls_dir = tmp_path / "hls"
    d = hls_dir / "item9" / "720p"
    d.mkdir(parents=True)
    (d / "seg_000.ts").write_bytes(b"")
    (hls_dir / "item9" / "480p").mkdir(parents=True)
    monkeypatch.setattr(Config, "HLS_DIR", str(hls_dir))
    assert app.seek_frontier(in_progress_item(), {}) == 0.0


def test_rebuild_is_zero_when_nothing_is_on_disk_yet(tmp_path, monkeypatch):
    hls_dir = tmp_path / "hls"
    (hls_dir / "item9" / "480p").mkdir(parents=True)
    (hls_dir / "item9" / "720p").mkdir(parents=True)
    monkeypatch.setattr(Config, "HLS_DIR", str(hls_dir))
    assert app.seek_frontier(in_progress_item(), {}) == 0.0


def test_rebuild_ignores_playlists_and_other_files(tmp_path, monkeypatch):
    hls_dir = tmp_path / "hls"
    write_rung(hls_dir, "item9", "480p", [4.0] * 3)
    write_rung(hls_dir, "item9", "720p", [4.0] * 3)
    noise = hls_dir / "item9" / "720p"
    (noise / "master.m3u8").write_text("#EXTM3U\n")
    (noise / "notes.txt").write_text("x")
    (noise / ".seg_hidden.ts").write_bytes(b"")
    monkeypatch.setattr(Config, "HLS_DIR", str(hls_dir))
    assert app.seek_frontier(in_progress_item(), {}) == 12.0


def test_one_unmeasurable_rung_still_pins_the_room(tmp_path, monkeypatch):
    """A rung advertised as selectable but with nothing on disk is genuinely
    broken, so the conservative 0.0 is correct — the rebuild must not paper over
    it by silently ignoring the rung."""
    hls_dir = tmp_path / "hls"
    write_rung(hls_dir, "item9", "720p", [4.0] * 30)
    (hls_dir / "item9" / "480p").mkdir(parents=True)
    monkeypatch.setattr(Config, "HLS_DIR", str(hls_dir))
    assert app.seek_frontier(in_progress_item(), {}) == 0.0


# ── 3. completion must be marked before progress is cleared ─────────────────

def test_clearing_progress_first_would_pin_the_room_to_zero():
    """If progress is dropped while the default rung is still 'encoding', the
    item is neither complete nor measurable and the guard reports 0.0. This is
    the window the ordering requirement exists to close."""
    item = in_progress_item()
    item["status"] = "encoding"
    # progress cleared, nothing marked complete:
    assert app.seek_frontier(item, {}) == 0.0

    # same state, but the rung was marked complete first:
    fixed = dict(item)
    fixed["renditions"] = [dict(r, status="complete") for r in item["renditions"]]
    assert app.seek_frontier(fixed, {}) == math.inf


def test_progress_survives_completion_of_one_non_default_rung():
    """A finished non-default rung must not wipe the slowest rung's progress."""
    item = in_progress_item()
    progress = {"item9:480p": {"encoded_seconds": 40.0},
                "item9:720p": {"encoded_seconds": 8.0}}
    # 720p is still encoding, so it still governs:
    assert app.seek_frontier(item, progress) == 8.0