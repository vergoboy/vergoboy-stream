"""summarize_media: facts about an original file for native clients (tracks, depth, HDR), from ffprobe fixtures."""
from __future__ import annotations

import json
import pathlib

import pytest

from media_domain.services.metadata import summarize_media

FIX = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "probe"


def load(name):
    return summarize_media(json.loads((FIX / f"{name}.json").read_text()))


def test_plain_h264_mp4():
    m = load("h264_8bit")
    assert m["container"].startswith("mov,mp4") and m["duration"] == 2.0
    v = m["video"][0]
    assert (v["codec"], v["width"], v["height"], v["fps"], v["bit_depth"], v["hdr"]) == ("h264", 320, 240, 24.0, 8, None)
    a = m["audio"][0]
    assert (a["codec"], a["channels"], a["layout"], a["lang"]) == ("aac", 1, "mono", "und")
    assert m["subtitles"] == []


def test_10_bit_is_detected_from_raw_bits_or_from_the_pixel_format():
    assert load("h264_hi10p")["video"][0]["bit_depth"] == 10      # bits_per_raw_sample = 10
    assert load("hevc_main10")["video"][0]["bit_depth"] == 10     # only pix_fmt yuv420p10le says so
    assert load("hevc_main")["video"][0]["bit_depth"] == 8        # only pix_fmt yuv420p


def test_hdr10_is_reported_and_sdr_is_not():
    assert load("hevc_hdr10")["video"][0]["hdr"] == "hdr10"
    assert load("hevc_main10")["video"][0]["hdr"] is None         # 10 bit does not mean HDR
    assert load("h264_8bit")["video"][0]["hdr"] is None


def test_hlg_and_dolby_vision_from_synthetic_streams():
    hlg = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc", "color_transfer": "arib-std-b67"}]}
    assert summarize_media(hlg)["video"][0]["hdr"] == "hlg"
    dv = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc", "color_transfer": "smpte2084",
                       "side_data_list": [{"side_data_type": "DOVI configuration record"}]}]}
    assert summarize_media(dv)["video"][0]["hdr"] == "dolby_vision"


@pytest.mark.parametrize("name,codec", [("audio_ac3", "ac3"), ("audio_dts", "dts"), ("audio_flac", "flac")])
def test_audio_codecs_a_browser_could_not_play_are_still_listed(name, codec):
    m = load(name)
    assert [a["codec"] for a in m["audio"]] == [codec] and m["container"].startswith("matroska")


def test_text_and_bitmap_subtitles_are_both_listed_and_told_apart():
    ass = load("subs_ass")["subtitles"][0]
    assert (ass["codec"], ass["text"]) == ("ass", True)
    pgs = load("subs_pgs")["subtitles"][0]
    assert (pgs["codec"], pgs["text"], pgs["lang"]) == ("hdmv_pgs_subtitle", False, "eng")


def test_av1_and_default_flags():
    m = load("av1_main")
    assert m["video"][0]["codec"] == "av1" and m["video"][0]["default"] is True and m["audio"][0]["default"] is True


def test_cover_art_is_not_a_video_track():
    probe = {"streams": [
        {"index": 0, "codec_type": "audio", "codec_name": "mp3", "channels": 2},
        {"index": 1, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}}]}
    m = summarize_media(probe)
    assert m["video"] == [] and len(m["audio"]) == 1


def test_a_failed_probe_gives_nothing():
    assert summarize_media({}) == {} and summarize_media({"streams": []}) == {}


def test_hostile_tags_are_bounded_and_cleaned():
    probe = {"streams": [{"index": 1, "codec_type": "audio", "codec_name": "aac",
                          "tags": {"title": "x" * 5000 + "\n\x00\x1b[31m", "language": "a" * 100}}]}
    a = summarize_media(probe)["audio"][0]
    assert len(a["title"]) <= 120 and "\n" not in a["title"] and "\x00" not in a["title"] and "\x1b" not in a["title"]
    assert len(a["lang"]) <= 8


def test_output_is_json_serialisable_and_bounded():
    streams = [{"index": i, "codec_type": "subtitle", "codec_name": "subrip"} for i in range(500)]
    m = summarize_media({"streams": streams})
    assert len(m["subtitles"]) == 32
    json.dumps(m)
