"""The frontier rebuild is checked against a real ffmpeg encode, not a mock.

The unit tests can only assert whatever arithmetic they were written with. This
one runs the app's own HLS flags, then asks ffmpeg itself how long the segments
really are, because the whole point of parsing #EXTINF instead of counting
files is that the two disagree whenever the final segment is partial.
"""
import os
import subprocess

from config import Config

import app

DUR = 22


def _ffprobe_duration(path) -> float:
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip())


def test_real_ffmpeg_output_parses_to_true_duration(tmp_path, monkeypatch):
    hls = tmp_path / "hls"
    rung = hls / "clip" / "720p"
    rung.mkdir(parents=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=duration={DUR}:size=320x240:rate=25",
         "-c:v", "libx264", "-preset", "ultrafast",
         "-g", "60", "-sc_threshold", "0",
         "-force_key_frames", "expr:gte(t,n_forced*4)",
         "-f", "hls", "-hls_time", str(Config.HLS_SEGMENT_SECONDS),
         "-hls_list_size", "0", "-hls_playlist_type", "event",
         "-hls_flags", "independent_segments+temp_file",
         "-hls_segment_filename", str(rung / "seg_%03d.ts"),
         str(rung / "index.m3u8")],
        check=True, capture_output=True)
    monkeypatch.setattr(Config, "HLS_DIR", str(hls))

    segs = sorted(f for f in os.listdir(rung) if f.endswith(".ts"))
    truth = sum(_ffprobe_duration(rung / s) for s in segs)
    got = app._frontier_from_disk("clip", "720p")
    naive = len(segs) * Config.HLS_SEGMENT_SECONDS

    assert len(segs) > 1, "need a multi-segment clip for this to mean anything"
    assert truth == DUR, "sanity: the encode covered the whole clip"
    assert abs(got - truth) < 0.01, f"parsed {got} != real {truth}"
    assert got <= naive, "must never exceed count * HLS_SEGMENT_SECONDS"
    assert naive > truth, "counting files really does over-report here"