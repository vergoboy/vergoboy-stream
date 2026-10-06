"""Cleanup must never delete media owned by rooms that are still on disk.

The wipe bug (failing-before/passing-after): on boot the in-memory registry is
empty, and /stream/api/delete and /stream/api/cleanup collect "referenced
files" only from rooms that happen to be open in-process. A room JSON sitting
in data/rooms — the developer's real room they will reconnect to — was counted
as owning nothing, so its uploads/HLS/subs were deleted as "orphans". That is
how a single delete on one item took down every other room's media.
"""
from __future__ import annotations

import json

import pytest

import app
from config import Config


@pytest.fixture
def isolated_media(tmp_path, monkeypatch):
    """All four Config dirs into tmp so the test can never touch repo/media."""
    rooms = tmp_path / "data" / "rooms"
    uploads = tmp_path / "media" / "uploads"
    subs = tmp_path / "media" / "subs"
    hls = tmp_path / "media" / "hls"
    for d in (rooms, uploads, subs, hls, hls / "colditem"):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(Config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(Config, "SUBS_DIR", str(subs))
    monkeypatch.setattr(Config, "HLS_DIR", str(hls))
    return rooms, uploads, subs, hls


def _write_cold_room(data_rooms, code="COLD99"):
    room_file = data_rooms / f"{code}.json"
    room_file.write_text(json.dumps({
        "room_id": code,
        "playlist": [{
            "id": "colditem",
            "type": "file",
            "title": "a cold room's item",
            "_raw_path": "/media/uploads/cold-upload.mp4",
            "src": "/stream/media/hls/colditem/master.m3u8",
            "renditions": [{"label": "720p", "status": "complete"}],
            "subtitles": [{"id": "s1", "url": "/stream/media/subs/cold.vtt"}],
        }],
    }))
    return room_file


def test_cleanup_keeps_media_owned_by_a_cold_room(isolated_media):
    """Failing-before: the cold room's files are removed by cleanup."""
    rooms, uploads, subs, hls = isolated_media
    _write_cold_room(rooms)
    upload = uploads / "cold-upload.mp4"
    upload.write_text("x")
    sub = subs / "cold.vtt"
    sub.write_text("x")
    master = hls / "colditem" / "master.m3u8"
    master.write_text("#EXTM3U\n")

    assert app._cleanup_orphans() == {"uploads": [], "subs": [], "hls": []}
    assert upload.exists()
    assert sub.exists()
    assert master.exists()


def test_disk_rooms_are_seen_even_when_not_open_in_process(isolated_media):
    rooms, _, _, _ = isolated_media
    _write_cold_room(rooms)
    upload_refs, sub_refs, hls_ids = app._collect_referenced_files()
    assert "cold-upload.mp4" in upload_refs
    assert "cold.vtt" in sub_refs
    assert "colditem" in hls_ids


def test_cleanup_still_removes_files_not_referenced_by_any_room(isolated_media):
    """The guard must not neuter cleanup for genuinely orphaned files."""
    rooms, uploads, subs, hls = isolated_media
    _write_cold_room(rooms)
    orphan = uploads / "unreferenced.mp4"
    orphan.write_text("x")
    assert "unreferenced.mp4" in app._cleanup_orphans()["uploads"]
    assert not orphan.exists()