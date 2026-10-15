"""Dead-media revalidation: a playlist item whose encoded HLS output (and its
source) has disappeared from disk must surface as a clear error instead of the
player spinning forever on a 404 master.m3u8.

The wipe incident left exactly this shape on disk: the room JSON said the item
was "complete" while both media/hls/<id>/master.m3u8 and the upload under
media/uploads/ had been deleted — so every client tried to play a 404 and
stalled.
"""
from __future__ import annotations

import json

import pytest

import state
from config import Config

PERSIAN_MSG = "فایل این ویدیو حذف شده — دوباره اضافه‌ش کن."


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    data = tmp_path / "data"
    rooms = data / "rooms"
    hls = tmp_path / "media" / "hls"
    uploads = tmp_path / "media" / "uploads"
    for d in (rooms, hls, uploads):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Config, "DATA_FILE", str(data / "state.json"))
    monkeypatch.setattr(Config, "HLS_DIR", str(hls))
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(uploads))
    return rooms, hls, uploads


def _write_room(rooms, code, playlist):
    room_file = rooms / f"{code}.json"
    room_file.write_text(json.dumps({
        "room_id": code,
        "playlist": playlist,
        "current_index": 0,
        "position": 0.0,
        "rate": 1.0,
        "shuffle": False,
        "shuffle_order": [],
    }, ensure_ascii=False))
    return room_file


def _hls_item(item_id, *, status="complete", source=None, src=None):
    item = {
        "id": item_id,
        "type": "file",
        "title": "gone video",
        "status": status,
        "src": src or f"/stream/media/hls/{item_id}/master.m3u8",
        "renditions": [{"label": "720p", "status": "complete"}],
    }
    if source is not None:
        item["_source"] = source
    return item


def _load(rooms, code):
    room_file = rooms / f"{code}.json"
    return json.loads(room_file.read_text(encoding="utf-8"))


def test_missing_hls_and_source_becomes_error(isolated):
    rooms, hls, uploads = isolated
    src_path = str(uploads / "gone_src.mp4")
    _write_room(rooms, "RMISS1", [_hls_item("gone1", source=src_path)])

    rs = state.RoomState(room_id="RMISS1")

    item = rs.find_item("gone1")
    assert item is not None
    assert item["status"] == "error"
    assert item["error"] == PERSIAN_MSG
    saved = _load(rooms, "RMISS1")["playlist"][0]
    assert saved["status"] == "error"  # persisted, not just in-memory


def test_missing_hls_with_master_present_stays_complete(isolated):
    rooms, hls, uploads = isolated
    master = hls / "present1" / "master.m3u8"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_text("#EXTM3U\n")
    _write_room(rooms, "RPRES1", [_hls_item("present1")])

    state.RoomState(room_id="RPRES1")
    saved = _load(rooms, "RPRES1")["playlist"][0]
    assert saved["status"] == "complete"


def test_missing_hls_but_remote_source_is_reencodable(isolated):
    """Master gone but the item is a re-fetchable URL — keep it 'complete',
    the on-demand encoder may still rebuild it."""
    rooms, hls, uploads = isolated
    _write_room(rooms, "RREM1", [
        _hls_item("remote1", source="https://example.com/src.mp4"),
    ])

    state.RoomState(room_id="RREM1")
    saved = _load(rooms, "RREM1")["playlist"][0]
    assert saved["status"] == "complete"


def test_missing_hls_but_local_source_exists_is_reencodable(isolated):
    rooms, hls, uploads = isolated
    src = uploads / "still.mp4"
    src.write_text("x")
    _write_room(rooms, "RLOC1", [
        _hls_item("local1", source=str(uploads / "still.mp4")),
    ])

    state.RoomState(room_id="RLOC1")
    saved = _load(rooms, "RLOC1")["playlist"][0]
    assert saved["status"] == "complete"


def test_non_hls_item_is_untouched(isolated):
    rooms, hls, uploads = isolated
    _write_room(rooms, "RMP41", [{
        "id": "direct1",
        "type": "url",
        "title": "direct mp4",
        "status": "complete",
        "src": "https://cdn.example.com/movie.mp4",
        "_source": "https://cdn.example.com/movie.mp4",
    }])

    state.RoomState(room_id="RMP41")
    saved = _load(rooms, "RMP41")["playlist"][0]
    assert saved["status"] == "complete"


def test_error_state_survives_reload(isolated):
    """Once flagged and saved, a fresh load keeps reporting the error."""
    rooms, hls, uploads = isolated
    _write_room(rooms, "RREP1", [_hls_item("reload1", source="/nope/missing.mp4")])

    state.RoomState(room_id="RREP1")
    again = state.RoomState(room_id="RREP1")
    item = again.find_item("reload1")
    assert item["status"] == "error"
    assert item["error"] == PERSIAN_MSG