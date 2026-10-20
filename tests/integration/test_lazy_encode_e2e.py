"""Lazy encoding against a REAL ffmpeg: nothing is built at add time, then exactly one HLS build on demand.

Run: venv/bin/python -m pytest -m integration tests/integration/test_lazy_encode_e2e.py
"""
from __future__ import annotations

import os
import subprocess
import time

import pytest

from tests.support import require_ffmpeg

pytestmark = pytest.mark.integration

app_module = pytest.importorskip("app", reason="app requires a configured environment")
from state import RoomState  # noqa: E402
import native_media  # noqa: E402


def _make_video(path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=4",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", path], check=True, timeout=60)


def _wait(cond, timeout=120.0):
    import gevent
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        gevent.sleep(0.25)
    return False


@pytest.fixture
def lab(tmp_path, monkeypatch):
    require_ffmpeg("libx264", "aac")
    media, uploads, hls = tmp_path / "media", tmp_path / "media" / "uploads", tmp_path / "media" / "hls"
    uploads.mkdir(parents=True); hls.mkdir()
    for attr, val in (("MEDIA_DIR", str(media)), ("UPLOAD_DIR", str(uploads)), ("HLS_DIR", str(hls))):
        monkeypatch.setattr(app_module.Config, attr, val)
    monkeypatch.setattr(app_module.Config, "LAZY_ENCODE", True)
    for name in ("safe_save", "broadcast_state", "broadcast_notify"):
        monkeypatch.setattr(app_module, name, lambda *a, **k: None)
    src = str(uploads / "i1_src.mkv")
    _make_video(src)
    rs = RoomState("LZ2")
    rs.playlist = [{"id": "i1", "type": "movie", "title": "Clip", "status": "queued"}]
    monkeypatch.setitem(app_module.rooms._rooms, "LZ2", rs)
    return rs, src, str(hls)


def test_add_builds_nothing_then_ensure_builds_one_playable_hls(lab):
    rs, src, hls = lab
    app_module.start_item_encoding("i1", src, "alice", src)
    item = rs.playlist[0]

    # 1) after add: probed and recorded, but no HLS exists and nothing is running
    assert item["status"] == "idle"
    assert item["_duration"] and 3.0 < item["_duration"] < 5.0
    assert not os.path.exists(os.path.join(hls, "i1", "master.m3u8"))

    # 2) a native client can already play the original, with no transcode at all
    pick = native_media.pick_source(item, user_id="u-1", upload_dir=os.path.dirname(src),
                                    base_path="/w2g/native/uploads", secret="s" * 40, remote_ok=False, now=time.time())
    assert pick["kind"] == "original" and "/i1_src.mkv?" in pick["url"]

    # 3) two web clients open the item at once: one encode
    assert app_module.ensure_hls("i1", "bob") == (True, "started")
    assert app_module.ensure_hls("i1", "carol") == (True, "already started")
    assert _wait(lambda: item["status"] in ("ready", "complete", "error")), item
    assert item["status"] in ("ready", "complete"), item.get("error")

    # 4) the result is a real HLS stream
    master = os.path.join(hls, "i1", "master.m3u8")
    assert _wait(lambda: os.path.exists(master))
    assert open(master).read().startswith("#EXTM3U")
    assert item.get("src"), "item must expose the HLS source once built"
    segments = [f for r, _, fs in os.walk(os.path.join(hls, "i1")) for f in fs if f.endswith((".m4s", ".ts", ".mp4"))]
    assert segments, "no media segments were produced"
    # the later call is still a no-op and does not restart anything
    assert app_module.ensure_hls("i1", "dave") == (True, "already started")
