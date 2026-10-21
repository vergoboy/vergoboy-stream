"""Lazy encoding: adding an item stores + probes only; the HLS build starts once, on demand.

Default is OFF (STREAM_LAZY_ENCODE unset): behaviour must be exactly as before.
"""
from __future__ import annotations

import threading

import pytest

app_module = pytest.importorskip("app", reason="app requires a configured environment")
from state import RoomState  # noqa: E402


@pytest.fixture
def env(monkeypatch):
    spawned: list[str] = []
    monkeypatch.setattr(app_module.gevent, "spawn", lambda fn, *a, **k: spawned.append(fn.__name__))
    for name in ("safe_save", "broadcast_state", "broadcast_notify"):
        monkeypatch.setattr(app_module, name, lambda *a, **k: None)
    monkeypatch.setattr(app_module, "free_space_mb", lambda p: 10_000)
    monkeypatch.setattr(app_module, "_ffprobe_source", lambda *a, **k: {})
    monkeypatch.setattr(app_module, "get_duration_s", lambda p: 7200.0)
    monkeypatch.setattr(app_module, "get_video_height", lambda p: 1080)
    monkeypatch.setattr(app_module, "get_video_width", lambda p: 1920)
    monkeypatch.setattr(app_module, "find_text_subtitle_streams", lambda p: [])
    rs = RoomState("LZ1")
    rs.playlist = [{"id": "i1", "type": "movie", "title": "T", "status": "queued"}]
    monkeypatch.setitem(app_module.rooms._rooms, "LZ1", rs)
    return rs, spawned


def add(rs, lazy, monkeypatch):
    monkeypatch.setattr(app_module.Config, "LAZY_ENCODE", lazy)
    app_module.start_item_encoding("i1", "/srv/media/uploads/i1_src.mkv", "alice", "/srv/media/uploads/i1_src.mkv")
    return rs.playlist[0]


def encodes(spawned):
    return spawned.count("_encode_rendition")


def test_default_behaviour_is_unchanged(env, monkeypatch):
    rs, spawned = env
    item = add(rs, False, monkeypatch)
    assert item["status"] == "queued"
    assert [r["status"] for r in item["renditions"] if r["is_default"]] == ["queued"]
    assert encodes(spawned) == 1


def test_lazy_add_only_stores_and_probes(env, monkeypatch):
    rs, spawned = env
    # lazy add must not even look at disk space (nothing is about to be transcoded)
    monkeypatch.setattr(app_module, "free_space_mb", lambda p: (_ for _ in ()).throw(AssertionError("disk checked")))
    item = add(rs, True, monkeypatch)
    assert item["status"] == "idle"
    assert all(r["status"] == "pending" for r in item["renditions"])
    assert item["_duration"] == 7200.0 and item["_source"].endswith("i1_src.mkv") and item["_raw_path"]
    assert encodes(spawned) == 0
    assert "_extract_subtitles_async" in spawned     # subtitles stay eager: cheap and needed for the track list


def test_ensure_hls_starts_exactly_one_encode_even_when_called_concurrently(env, monkeypatch):
    rs, spawned = env
    add(rs, True, monkeypatch)
    results = []
    threads = [threading.Thread(target=lambda: results.append(app_module.ensure_hls("i1", "bob"))) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert encodes(spawned) == 1
    assert sorted(m for _, m in results).count("started") == 1
    assert all(ok for ok, _ in results)
    item = rs.playlist[0]
    assert item["status"] == "queued"
    assert [r["status"] for r in item["renditions"] if r["is_default"]] == ["queued"]
    assert app_module.ensure_hls("i1", "bob") == (True, "already started")   # and later calls stay no-ops
    assert encodes(spawned) == 1


@pytest.mark.parametrize("status", ["queued", "encoding", "ready", "complete", "error"])
def test_ensure_is_a_noop_unless_idle(env, monkeypatch, status):
    rs, spawned = env
    rs.playlist[0]["status"] = status
    assert app_module.ensure_hls("i1", "bob") == (True, "already started")
    assert encodes(spawned) == 0


def test_ensure_refuses_items_without_source_or_default_rendition_and_unknown_items(env):
    rs, spawned = env
    rs.playlist[0]["status"] = "idle"
    assert app_module.ensure_hls("i1", "bob")[0] is False              # no _source / renditions
    rs.playlist[0].update(_source="/x.mkv", renditions=[{"label": "720p", "is_default": False, "status": "pending"}])
    assert app_module.ensure_hls("i1", "bob")[0] is False              # no default rendition
    assert app_module.ensure_hls("nope", "bob")[0] is False
    assert encodes(spawned) == 0


def test_low_disk_marks_the_item_as_error_instead_of_encoding(env, monkeypatch):
    rs, spawned = env
    add(rs, True, monkeypatch)
    monkeypatch.setattr(app_module, "free_space_mb", lambda p: 10)
    ok, _ = app_module.ensure_hls("i1", "bob")
    assert ok is False and encodes(spawned) == 0
    assert rs.playlist[0]["status"] == "error" and "فضای" in rs.playlist[0]["error"]


def test_asking_for_a_quality_on_an_idle_item_makes_it_in_progress(env, monkeypatch):
    rs, spawned = env
    item = add(rs, True, monkeypatch)
    label = next(r["label"] for r in item["renditions"] if not r["is_default"])
    assert app_module.request_quality("i1", label, "bob") == (True, "started")
    assert item["status"] == "queued" and encodes(spawned) == 1
    assert app_module.ensure_hls("i1", "bob") == (True, "already started")   # must not start a second encode
    assert encodes(spawned) == 1


@pytest.mark.parametrize("current,item,may,want", [
    ("i1", "i1", False, True),      # a watcher opening the current item
    ("i1", "i2", False, False),     # a watcher cannot queue other films
    ("i1", "i2", True, True),       # a controller can ("admin explicitly asks")
    (None, "i1", False, False),     # nothing selected
    (None, "i1", True, True),
])
def test_who_may_trigger_the_build(current, item, may, want):
    assert app_module.can_trigger_hls(current, item, may) is want


def test_add_stores_a_bounded_media_summary_of_the_original(env, monkeypatch):
    import json, pathlib
    rs, _ = env
    probe = json.loads((pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "probe" / "hevc_hdr10.json").read_text())
    monkeypatch.setattr(app_module, "_ffprobe_source", lambda *a, **k: probe)
    item = add(rs, True, monkeypatch)
    v = item["media"]["video"][0]
    assert (v["codec"], v["bit_depth"], v["hdr"]) == ("hevc", 10, "hdr10")
    assert not any(k.startswith("_") for k in item["media"])      # public data only


def test_a_failed_probe_leaves_no_media_summary(env, monkeypatch):
    rs, _ = env                                                    # fixture probe is {}
    assert "media" not in add(rs, True, monkeypatch)
