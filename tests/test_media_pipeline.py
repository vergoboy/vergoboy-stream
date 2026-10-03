"""Golden tests for the media pipeline.

Run with the stdlib runner (pytest is not installed in this project):

    python3 -m unittest discover -s tests -v

These are *golden* tests for ffmpeg argv: the expected lists are written out in
full so that any change to the command line shows up as a deliberate diff
rather than as a silent behaviour change in the encoder.
"""

import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from media_pipeline import ClientCaps, HwAccel, Mode, plan, plan_compat
from media_pipeline.ffmpeg_cmd import (
    bitrate_for_height,
    build_direct_play_cmd,
    build_plan_args,
    build_subtitle_cmd,
    build_video_filter,
    detect_hwaccel,
    reset_hwaccel_cache,
    resolve_preset,
)
from media_pipeline.planner import AudioAction, SubtitleAction
from media_pipeline.probe import _detect_faststart, parse_probe

NO_HW = HwAccel()


# ── ffprobe payload builders ─────────────────────────────────────────────────


def probe_payload(video=None, audio=(), subs=(), container="mov,mp4,m4a,3gp,3g2,mj2", duration="10.0"):
    streams = []
    if video is not None:
        streams.append(dict({"index": 0, "codec_type": "video"}, **video))
    for i, a in enumerate(audio):
        streams.append(dict({"index": len(streams), "codec_type": "audio"}, **a))
    for s in subs:
        streams.append(dict({"index": len(streams), "codec_type": "subtitle"}, **s))
    return {"streams": streams, "format": {"format_name": container, "duration": duration}}


H264_720P = {
    "codec_name": "h264", "profile": "High", "pix_fmt": "yuv420p",
    "width": 1280, "height": 720, "color_transfer": "bt709",
}
H264_10BIT = dict(H264_720P, profile="High 10", pix_fmt="yuv420p10le", bits_per_raw_sample=10)
HEVC_8BIT = {
    "codec_name": "hevc", "profile": "Main", "pix_fmt": "yuv420p",
    "width": 1920, "height": 1080, "color_transfer": "bt709",
}
HEVC_10BIT = dict(HEVC_8BIT, pix_fmt="yuv420p10le", bits_per_raw_sample=10)
HDR_PQ = dict(H264_720P, color_transfer="smpte2084", color_primaries="bt2020",
              color_space="bt2020nc")
HDR_HLG = dict(HEVC_8BIT, color_transfer="arib-std-b67", color_primaries="bt2020",
               color_space="bt2020nc")
AV1 = {
    "codec_name": "av1", "profile": "Main", "pix_fmt": "yuv420p10le",
    "width": 1920, "height": 1080, "bits_per_raw_sample": 10,
}
MPEG2 = {
    "codec_name": "mpeg2video", "profile": "Main", "pix_fmt": "yuv420p",
    "width": 720, "height": 576,
}
YUV444 = dict(H264_720P, pix_fmt="yuv444p", profile="High 4:4:4 Predictive")

AAC_STEREO = {"codec_name": "aac", "channels": 2, "channel_layout": "stereo", "sample_rate": "48000"}
AAC_51 = {"codec_name": "aac", "channels": 6, "channel_layout": "5.1", "sample_rate": "48000"}
AC3_51 = {"codec_name": "ac3", "channels": 6, "channel_layout": "5.1", "sample_rate": "48000"}
OPUS = {"codec_name": "opus", "channels": 2, "channel_layout": "stereo"}
TRUEHD = {"codec_name": "truehd", "channels": 8, "channel_layout": "7.1"}

SRT_SUB = {"codec_name": "subrip", "tags": {"language": "eng"}}
ASS_SUB = {"codec_name": "ass", "tags": {"language": "per", "title": "Styled"}}
PGS_SUB = {"codec_name": "hdmv_pgs_subtitle", "tags": {"language": "eng"}}
VOBSUB = {"codec_name": "dvd_subtitle", "tags": {"language": "eng"}}


def build(**kw):
    return build_plan_args(kw.pop("p"), "in.mp4", 4,
                           "/out/720p", "/out/720p/seg_%05d.m4s", **kw)


# ── probe ────────────────────────────────────────────────────────────────────


class TestProbe(unittest.TestCase):
    def test_basic_fields(self):
        info = parse_probe(probe_payload(H264_720P, [AAC_STEREO]))
        self.assertEqual(info.container, "mov,mp4,m4a,3gp,3g2,mj2")
        self.assertEqual(info.duration, 10.0)
        self.assertEqual(info.video.codec, "h264")
        self.assertEqual(info.video.profile, "high")   # normalised to lowercase
        self.assertEqual(info.video.pix_fmt, "yuv420p")
        self.assertEqual(info.video.height, 720)
        self.assertFalse(info.video.is_hdr)
        self.assertFalse(info.video.is_10bit)
        self.assertEqual(info.audio[0].codec, "aac")
        self.assertEqual(info.audio[0].channels, 2)

    def test_hdr_detected_from_transfer_only(self):
        for transfer in ("smpte2084", "arib-std-b67"):
            with self.subTest(transfer=transfer):
                v = dict(H264_720P, color_transfer=transfer)
                self.assertTrue(parse_probe(probe_payload(v)).video.is_hdr)
        # BT.709 is explicitly NOT hdr
        self.assertFalse(parse_probe(probe_payload(dict(H264_720P, color_transfer="bt709"))).video.is_hdr)
        # Unknown transfer must not be guessed into hdr
        self.assertFalse(parse_probe(probe_payload(dict(H264_720P, color_transfer=None))).video.is_hdr)

    def test_10bit_from_pixfmt_without_bits_field(self):
        # ffprobe often omits bits_per_raw_sample; the pixel format must win.
        info = parse_probe(probe_payload(dict(HEVC_8BIT, pix_fmt="yuv420p10le")))
        self.assertTrue(info.video.is_10bit)

    def test_10bit_from_profile(self):
        info = parse_probe(probe_payload(dict(H264_720P, profile="High 10", pix_fmt=None)))
        self.assertTrue(info.video.is_10bit)

    def test_cover_art_is_not_the_video_stream(self):
        payload = probe_payload(H264_720P)
        payload["streams"].insert(0, {
            "index": 0, "codec_type": "video", "codec_name": "mjpeg",
            "tags": {"attached_pic": "1"},
        })
        info = parse_probe(payload)
        self.assertEqual(info.video.codec, "h264")

    def test_subtitle_classification(self):
        info = parse_probe(probe_payload(H264_720P, subs=[SRT_SUB, ASS_SUB, PGS_SUB, VOBSUB]))
        self.assertEqual([s.codec for s in info.text_subtitles], ["subrip", "ass"])
        self.assertEqual([s.codec for s in info.bitmap_subtitles], ["hdmv_pgs_subtitle", "dvd_subtitle"])
        self.assertTrue(info.subtitles[1].is_ass)
        self.assertFalse(info.subtitles[0].is_ass)
        self.assertEqual(info.subtitles[2].lang, "eng")

    def test_duration_falls_back_to_stream(self):
        payload = probe_payload(H264_720P, duration="0")
        payload["streams"][0]["duration"] = "42.5"
        self.assertEqual(parse_probe(payload).duration, 42.5)

    def test_missing_fields_stay_none_not_guessed(self):
        info = parse_probe(probe_payload({"codec_name": "h264"}))
        self.assertIsNone(info.video.width)
        self.assertIsNone(info.video.height)
        self.assertIsNone(info.video.pix_fmt)
        self.assertIsNone(info.video.profile)
        # Container duration IS reported, so it is real.
        self.assertEqual(info.duration, 10.0)

    def test_non_mp4_container_has_no_faststart_answer(self):
        # MKV has no movable moov atom; asking "is it faststart" is meaningless
        # and must not be answered True.
        info = parse_probe(probe_payload(H264_720P, container="matroska,webm"))
        self.assertIsNone(info.has_faststart)
        self.assertFalse(plan(info).direct_play)

    def test_faststart_remote_is_unknown(self):
        # Not a local file -> unknowable, must not be reported as False.
        self.assertIsNone(parse_probe(probe_payload(H264_720P), source="https://x/y.mp4").has_faststart)

    def test_faststart_atom_order(self):
        def build_atoms(order):
            buf = b""
            for name in order:
                payload = b"\x00" * 16
                buf += struct.pack(">I", 8 + len(payload)) + name + payload
            return buf
        with tempfile.TemporaryDirectory() as d:
            fast = os.path.join(d, "fast.mp4")
            slow = os.path.join(d, "slow.mp4")
            with open(fast, "wb") as fh:
                fh.write(build_atoms([b"ftyp", b"moov", b"mdat"]))
            with open(slow, "wb") as fh:
                fh.write(build_atoms([b"ftyp", b"mdat", b"moov"]))
            self.assertTrue(_detect_faststart(fast))
            self.assertFalse(_detect_faststart(slow))

    def test_faststart_missing_file_is_unknown(self):
        self.assertIsNone(_detect_faststart("/nonexistent/nope.mp4"))


# ── planner: decision matrix ─────────────────────────────────────────────────


class TestPlannerVideo(unittest.TestCase):
    def test_h264_8bit_is_copy(self):
        p = plan(parse_probe(probe_payload(H264_720P, [AAC_STEREO])))
        self.assertIs(p.mode, Mode.COPY)
        self.assertTrue(p.is_remux)
        self.assertIs(p.audio_action, AudioAction.COPY)

    def test_baseline_profile_is_copy(self):
        p = plan(parse_probe(probe_payload(dict(H264_720P, profile="Constrained Baseline"))))
        self.assertIs(p.mode, Mode.COPY)

    def test_h264_10bit_is_transcode_not_copy(self):
        p = plan(parse_probe(probe_payload(H264_10BIT)))
        self.assertIs(p.mode, Mode.TRANSCODE)
        self.assertTrue(p.needs_dither)
        self.assertEqual(p.pix_fmt, "yuv420p")
        self.assertEqual(p.target_bit_depth, 8)

    def test_hevc_8bit_copies_with_hvc1_tag(self):
        p = plan(parse_probe(probe_payload(HEVC_8BIT, [AAC_STEREO])))
        self.assertIs(p.mode, Mode.COPY)
        self.assertEqual(p.tag_v, "hvc1")
        self.assertTrue(p.hevc_only)

    def test_hevc_10bit_is_also_a_copy(self):
        # Requirement: HEVC 8/10-bit both copy with hvc1. 10-bit is exposed only
        # to clients that reported they can decode it, and a client that cannot
        # triggers the compatibility rendition.
        p = plan(parse_probe(probe_payload(HEVC_10BIT)))
        self.assertIs(p.mode, Mode.COPY)
        self.assertEqual(p.tag_v, "hvc1")
        self.assertTrue(p.hevc_only)
        self.assertEqual(p.target_bit_depth, 10)
        self.assertFalse(p.needs_dither)
        self.assertEqual(p.video_codec, "copy")   # passthrough, not an encoder

    def test_hevc_10bit_copy_has_no_filter_chain(self):
        # Copying must not insert a dither/scale filter that would contradict
        # the copy decision.
        p = plan(parse_probe(probe_payload(HEVC_10BIT)))
        self.assertEqual(build_video_filter(p), [])

    def test_av1_transcodes(self):
        self.assertIs(plan(parse_probe(probe_payload(AV1))).mode, Mode.TRANSCODE)

    def test_mpeg2_transcodes(self):
        self.assertIs(plan(parse_probe(probe_payload(MPEG2))).mode, Mode.TRANSCODE)

    def test_yuv444_transcodes(self):
        p = plan(parse_probe(probe_payload(YUV444)))
        self.assertIs(p.mode, Mode.TRANSCODE)
        self.assertEqual(p.pix_fmt, "yuv420p")

    def test_unknown_codec_transcodes(self):
        self.assertIs(plan(parse_probe(probe_payload({"codec_name": "theora"}))).mode, Mode.TRANSCODE)

    def test_missing_codec_warns_and_transcodes(self):
        p = plan(parse_probe(probe_payload({"width": 1280, "height": 720})))
        self.assertIs(p.mode, Mode.TRANSCODE)
        self.assertTrue(any("did not report a video codec" in w for w in p.warnings))


class TestPlannerHDR(unittest.TestCase):
    def test_hdr_h264_tonemaps_rather_than_copying(self):
        # The critical case: HDR *is* h264, so a codec-only check would copy it
        # and every SDR client would see garbage.
        p = plan(parse_probe(probe_payload(HDR_PQ)))
        self.assertIs(p.mode, Mode.TONEMAP)
        self.assertTrue(p.needs_tonemap)
        self.assertEqual(p.tonemap_algorithm, "hable")
        self.assertEqual(p.pix_fmt, "yuv420p")
        self.assertFalse(p.direct_play)

    def test_hdr_hlg_hevc_tonemaps(self):
        p = plan(parse_probe(probe_payload(HDR_HLG)))
        self.assertIs(p.mode, Mode.TONEMAP)
        self.assertFalse(p.hevc_only)

    def test_hdr_never_direct_play(self):
        self.assertFalse(plan(parse_probe(probe_payload(HDR_PQ))).direct_play)

    def test_tonemap_is_never_a_bare_format_conversion(self):
        p = plan(parse_probe(probe_payload(HDR_PQ)), target_height=480)
        vf = "".join(build_video_filter(p, src_width=1920))
        self.assertIn("zscale", vf)
        self.assertIn("tonemap=tonemap=hable", vf)
        # A bare "format=yuv420p" with no tone curve is exactly the bug.
        self.assertNotEqual(vf.strip(), "format=yuv420p")


class TestPlannerAudio(unittest.TestCase):
    def test_aac_copies(self):
        self.assertIs(plan(parse_probe(probe_payload(H264_720P, [AAC_STEREO]))).audio_action, AudioAction.COPY)

    def test_mp3_copies(self):
        mp3 = {"codec_name": "mp3", "channels": 2, "channel_layout": "stereo"}
        self.assertIs(plan(parse_probe(probe_payload(H264_720P, [mp3]))).audio_action, AudioAction.COPY)

    def test_ac3_transcodes_and_downmixes(self):
        p = plan(parse_probe(probe_payload(H264_720P, [AC3_51])))
        self.assertIs(p.audio_action, AudioAction.TRANSCODE)
        self.assertTrue(p.audio_downmix)
        self.assertEqual(p.audio_bitrate, "160k")

    def test_every_exotic_audio_transcodes(self):
        for audio in (OPUS, TRUEHD, {"codec_name": "flac", "channels": 2},
                      {"codec_name": "dts", "channels": 6, "channel_layout": "5.1"},
                      {"codec_name": "eac3", "channels": 6, "channel_layout": "5.1"}):
            with self.subTest(audio=audio["codec_name"]):
                p = plan(parse_probe(probe_payload(H264_720P, [audio])))
                self.assertIs(p.audio_action, AudioAction.TRANSCODE)

    def test_no_audio_stream(self):
        p = plan(parse_probe(probe_payload(H264_720P)))
        self.assertIs(p.audio_action, AudioAction.COPY)
        self.assertTrue(any("no audio" in r for r in p.reasons))

    def test_stereo_aac_not_downmixed(self):
        self.assertFalse(plan(parse_probe(probe_payload(H264_720P, [AAC_STEREO]))).audio_downmix)


class TestPlannerSubtitles(unittest.TestCase):
    def test_bitmap_subs_skipped_with_warning_and_never_burned(self):
        p = plan(parse_probe(probe_payload(H264_720P, subs=[PGS_SUB, VOBSUB])))
        self.assertIs(p.subtitle_action, SubtitleAction.SKIP_BITMAP)
        self.assertEqual(len(p.warnings), 2)
        for w in p.warnings:
            self.assertIn("never burned into the picture", w)

    def test_ass_is_preserved_for_client_rendering(self):
        p = plan(parse_probe(probe_payload(H264_720P, subs=[ASS_SUB])))
        self.assertIs(p.subtitle_action, SubtitleAction.KEEP_TEXT)
        self.assertTrue(any("ASS/SSA" in w for w in p.warnings))

    def test_text_and_bitmap_together(self):
        p = plan(parse_probe(probe_payload(H264_720P, subs=[SRT_SUB, PGS_SUB])))
        self.assertIs(p.subtitle_action, SubtitleAction.KEEP_TEXT)
        self.assertTrue(any("never burned" in w for w in p.warnings))
        self.assertTrue(any("client-side rendering" in w for w in p.warnings))

    def test_no_subtitles(self):
        self.assertIs(plan(parse_probe(probe_payload(H264_720P))).subtitle_action, SubtitleAction.NONE)


class TestPlannerClientCaps(unittest.TestCase):
    def test_hevc_without_capabilities_keeps_hevc(self):
        # Unreported capability is not a missing capability.
        p = plan(parse_probe(probe_payload(HEVC_8BIT)))
        self.assertTrue(p.hevc_only)
        self.assertFalse(p.needs_compat)

    def test_hevc_with_caps_claiming_yes_needs_no_compat(self):
        p = plan(parse_probe(probe_payload(HEVC_8BIT)), ClientCaps(hevc=True, ten_bit=True))
        self.assertFalse(p.needs_compat)

    def test_hevc_with_caps_claiming_no_needs_compat(self):
        p = plan(parse_probe(probe_payload(HEVC_8BIT)), ClientCaps(hevc=False))
        self.assertTrue(p.needs_compat)
        self.assertFalse(p.direct_play)
        self.assertTrue(any("compatibility rendition" in r for r in p.reasons))

    def test_10bit_capability_refusal_needs_compat(self):
        p = plan(parse_probe(probe_payload(HEVC_10BIT)), ClientCaps(hevc=True, ten_bit=False))
        self.assertTrue(p.needs_compat)

    def test_h264_never_needs_compat(self):
        p = plan(parse_probe(probe_payload(H264_720P)), ClientCaps(hevc=False, ten_bit=False))
        self.assertFalse(p.needs_compat)

    def test_compat_plan_is_always_8bit_h264(self):
        for video in (HEVC_8BIT, HEVC_10BIT, AV1, HDR_PQ, HDR_HLG):
            with self.subTest(video=video["codec_name"]):
                c = plan_compat(parse_probe(probe_payload(video)))
                self.assertEqual(c.video_codec, "libx264")
                self.assertEqual(c.pix_fmt, "yuv420p")
                self.assertEqual(c.target_bit_depth, 8)
                self.assertFalse(c.hevc_only)
                self.assertFalse(c.direct_play)
                self.assertIs(c.audio_action, AudioAction.TRANSCODE)

    def test_compat_plan_keeps_the_tonemap_for_hdr(self):
        c = plan_compat(parse_probe(probe_payload(HDR_PQ)))
        self.assertTrue(c.needs_tonemap)
        self.assertIs(c.mode, Mode.TONEMAP)

    def test_plan_is_deterministic(self):
        info = parse_probe(probe_payload(HDR_PQ, [AC3_51], [PGS_SUB]))
        self.assertEqual(plan(info), plan(info))
        self.assertEqual(plan_compat(info), plan_compat(info))

    def test_plan_is_frozen(self):
        p = plan(parse_probe(probe_payload(H264_720P)))
        with self.assertRaises(Exception):
            p.mode = Mode.COPY

    def test_no_video_stream_raises(self):
        with self.assertRaises(ValueError):
            plan(parse_probe(probe_payload(video=None, audio=[AAC_STEREO])))


class TestDirectPlay(unittest.TestCase):
    def test_faststart_safe_codecs_direct_play(self):
        info = parse_probe(probe_payload(H264_720P, [AAC_STEREO]))
        # simulate a local file whose moov precedes mdat
        object.__setattr__(info, "has_faststart", True)
        self.assertTrue(plan(info).direct_play)

    def test_without_faststart_no_direct_play(self):
        info = parse_probe(probe_payload(H264_720P, [AAC_STEREO]))
        object.__setattr__(info, "has_faststart", False)
        self.assertFalse(plan(info).direct_play)

    def test_unknown_faststart_no_direct_play(self):
        info = parse_probe(probe_payload(H264_720P, [AAC_STEREO]), source="https://x/y.mp4")
        self.assertIs(info.has_faststart, None)
        self.assertFalse(plan(info).direct_play)

    def test_unsafe_audio_blocks_direct_play(self):
        info = parse_probe(probe_payload(H264_720P, [AC3_51]))
        object.__setattr__(info, "has_faststart", True)
        self.assertFalse(plan(info).direct_play)


# ── ffmpeg argv: golden output ───────────────────────────────────────────────


class TestGoldenCopyCmd(unittest.TestCase):
    """H.264 8-bit source: the video and audio must be pure passthrough."""

    GOLDEN = [
        "ffmpeg", "-y", "-threads", "0",
        "-i", "in.mp4",
        "-c:v", "copy",
        "-g", "100", "-sc_threshold", "0",
        "-force_key_frames", "expr:gte(t,n_forced*4)",
        "-c:a", "copy",
        "-hls_segment_type", "fmp4",
        "-hls_fmp4_init_filename", "init.mp4",
        "-f", "hls",
        "-hls_time", "4",
        "-hls_list_size", "0",
        "-hls_playlist_type", "event",
        "-hls_flags", "independent_segments+temp_file",
        "-hls_segment_filename", "/out/720p/seg_%05d.m4s",
        "/out/720p/index.m3u8",
    ]

    def test_exact_argv(self):
        p = plan(parse_probe(probe_payload(H264_720P, [AAC_STEREO])))
        self.assertEqual(build(p=p), self.GOLDEN)

    def test_copy_has_no_filter_and_no_encoder_flags(self):
        p = plan(parse_probe(probe_payload(H264_720P)))
        cmd = build(p=p)
        self.assertNotIn("-vf", cmd)
        # -b:v is meaningless without an encoder and ffmpeg warns about it.
        for banned in ("-preset", "-pix_fmt", "-crf", "-tag:v", "-ac", "-b:v", "-b:a"):
            self.assertNotIn(banned, cmd)

    def test_copy_never_initialises_a_hardware_device(self):
        # A copy must stay bit-exact: no upload/download round-trip at all.
        cmd = build(p=plan(parse_probe(probe_payload(H264_720P))), hw=HwAccel("vaapi", "/dev/dri/renderD128"))
        self.assertNotIn("-hwaccel", cmd)
        self.assertNotIn("-vaapi_device", cmd)
        self.assertNotIn("-init_hw_device", cmd)
        self.assertNotIn("-vf", cmd)

    def test_always_fmp4(self):
        for video in (H264_720P, AV1, HDR_PQ, MPEG2):
            with self.subTest(video=video["codec_name"]):
                p = plan(parse_probe(probe_payload(video)))
                cmd = build(p=p)
                self.assertIn("-hls_segment_type", cmd)
                self.assertEqual(cmd[cmd.index("-hls_segment_type") + 1], "fmp4")


class TestGoldenHevcCmd(unittest.TestCase):
    def test_hevc_copy_tags_hvc1(self):
        p = plan(parse_probe(probe_payload(HEVC_8BIT)))
        cmd = build(p=p)
        self.assertEqual(cmd[cmd.index("-tag:v") + 1], "hvc1")
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "copy")


class TestGoldenTranscodeCmd(unittest.TestCase):
    def test_exact_argv_for_software_x264(self):
        # MPEG-2 8-bit → H.264 yuv420p 8-bit, AC-3 5.1 → AAC 160k stereo.
        p = plan(parse_probe(probe_payload(MPEG2, [AC3_51])), hw=NO_HW, target_height=720)
        self.assertEqual(build(p=p), [
            "ffmpeg", "-y", "-threads", "0",
            "-i", "in.mp4",
            "-vf", "scale=-2:720:flags=lanczos",
            "-c:v", "libx264",
            "-crf", "21",
            "-preset", "veryfast", "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-g", "100", "-sc_threshold", "0",
            "-force_key_frames", "expr:gte(t,n_forced*4)",
            "-c:a", "aac", "-b:a", "160k", "-ac", "2",
            "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "init.mp4",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_playlist_type", "event",
            "-hls_flags", "independent_segments+temp_file",
            "-hls_segment_filename", "/out/720p/seg_%05d.m4s",
            "/out/720p/index.m3u8",
        ])

    def test_default_preset_is_veryfast_not_ultrafast(self):
        cmd = build(p=plan(parse_probe(probe_payload(MPEG2)), hw=NO_HW))
        self.assertEqual(cmd[cmd.index("-preset") + 1], "veryfast")
        self.assertNotIn("ultrafast", " ".join(cmd))

    def test_ultrafast_only_when_explicitly_requested(self):
        cmd = build(p=plan(parse_probe(probe_payload(MPEG2)), hw=NO_HW), preset="ultrafast")
        self.assertEqual(cmd[cmd.index("-preset") + 1], "ultrafast")

    def test_stream_hls_preset_env_is_honoured(self):
        p = plan(parse_probe(probe_payload(MPEG2)), hw=NO_HW)
        old = os.environ.get("STREAM_HLS_PRESET")
        try:
            os.environ["STREAM_HLS_PRESET"] = "ultrafast"
            self.assertEqual(resolve_preset(), "ultrafast")
            self.assertEqual(build(p=p)[build(p=p).index("-preset") + 1], "ultrafast")
            # an explicit argument still wins over the environment
            self.assertEqual(resolve_preset("medium"), "medium")
        finally:
            if old is None:
                os.environ.pop("STREAM_HLS_PRESET", None)
            else:
                os.environ["STREAM_HLS_PRESET"] = old

    def test_ultrafast_is_not_used_when_env_is_unset(self):
        old = os.environ.pop("STREAM_HLS_PRESET", None)
        try:
            self.assertEqual(resolve_preset(), "veryfast")
        finally:
            if old is not None:
                os.environ["STREAM_HLS_PRESET"] = old

    def test_invalid_preset_env_fails_loudly_and_early(self):
        old = os.environ.get("STREAM_HLS_PRESET")
        os.environ["STREAM_HLS_PRESET"] = "turbo"
        try:
            with self.assertRaises(ValueError):
                resolve_preset()
        finally:
            if old is None:
                os.environ.pop("STREAM_HLS_PRESET", None)
            else:
                os.environ["STREAM_HLS_PRESET"] = old

    def test_10bit_source_gets_dither_chain(self):
        p = plan(parse_probe(probe_payload(H264_10BIT)), hw=NO_HW)
        vf = build_video_filter(p, src_width=1280)[0]
        self.assertIn("format=yuv420p10le", vf)
        self.assertIn("dither", vf)
        self.assertTrue(vf.rstrip().endswith("format=yuv420p"))

    def test_hdr_gets_tonemap_chain(self):
        p = plan(parse_probe(probe_payload(HDR_PQ)), target_height=480, hw=NO_HW)
        vf = build_video_filter(p, src_width=1280)[0]
        self.assertIn("t=linear", vf)
        self.assertIn("tonemap=tonemap=hable", vf)
        self.assertIn("p=bt709", vf)

    def test_scale_uses_lanczos_and_even_dims(self):
        p = plan(parse_probe(probe_payload(MPEG2)), target_height=721, hw=NO_HW)
        vf = build_video_filter(p, src_width=1281)[0]
        self.assertIn("flags=lanczos", vf)
        self.assertIn("1280:720", vf)   # both rounded down to even

    def test_crf_is_the_default_rate_control(self):
        p = plan(parse_probe(probe_payload(AV1)), hw=NO_HW)
        cmd = build(p=p)
        self.assertEqual(cmd[cmd.index("-crf") + 1], "21")
        self.assertNotIn("-b:v", cmd, "CRF and bitrate are mutually exclusive")

    def test_explicit_crf_overrides_the_default(self):
        p = plan(parse_probe(probe_payload(AV1)), hw=NO_HW)
        self.assertEqual(build(p=p, crf="18")[build(p=p, crf="18").index("-crf") + 1], "18")

    def test_explicit_bitrate_replaces_crf(self):
        p = plan(parse_probe(probe_payload(AV1)), hw=NO_HW)
        cmd = build(p=p, vbr="1500k")
        self.assertEqual(cmd[cmd.index("-b:v") + 1], "1500k")
        self.assertNotIn("-crf", cmd)

    def test_bitrate_scales_with_the_ladder_height(self):
        for height, expected in ((1080, "5000k"), (720, "2800k"), (480, "1400k"), (360, "800k")):
            with self.subTest(height=height):
                p = plan(parse_probe(probe_payload(AV1)), target_height=height, hw=NO_HW)
                cmd = build(p=p, hw=HwAccel("vaapi", "/dev/dri/renderD128"))
                self.assertEqual(cmd[cmd.index("-b:v") + 1], expected)

    def test_audio_copy_never_sets_a_bitrate(self):
        p = plan(parse_probe(probe_payload(H264_720P, [AAC_STEREO])))
        self.assertEqual(build(p=p)[build(p=p).index("-c:a") + 1], "copy")
        self.assertNotIn("-b:a", build(p=p))


class TestHwAccelSwap(unittest.TestCase):
    def test_vaapi_swaps_the_encoder_and_keeps_the_ladder_bitrate(self):
        p = plan(parse_probe(probe_payload(MPEG2)))
        cmd = build(p=p, hw=HwAccel("vaapi", "/dev/dri/renderD128"))
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_vaapi")
        self.assertIn("-vaapi_device", cmd)
        self.assertNotIn("-hwaccel", cmd)   # software decode + single upload
        # Hardware encoders ignore CRF, so exactly one -b:v must be emitted,
        # derived from the ladder height (144 -> falls back to 2800k).
        self.assertEqual(cmd.count("-b:v"), 1)
        self.assertEqual(cmd[cmd.index("-b:v") + 1], "2800k")
        self.assertNotIn("-crf", cmd)
        self.assertNotIn("-preset", cmd)

    def test_nvenc_swaps_the_encoder(self):
        cmd = build(p=plan(parse_probe(probe_payload(MPEG2))), hw=HwAccel("nvenc"))
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_nvenc")

    def test_vaapi_uploads_frames_before_the_encoder(self):
        # h264_vaapi consumes GPU frames; software filters produce system
        # frames. Missing hwupload fails the encode with -38/-22 at runtime.
        p = plan(parse_probe(probe_payload(MPEG2)), hw=NO_HW, target_height=144)
        cmd = build(p=p, hw=HwAccel("vaapi", "/dev/dri/renderD128"))
        vf = cmd[cmd.index("-vf") + 1]
        self.assertTrue(vf.endswith("format=nv12,hwupload=extra_hw_frames=64"), vf)
        # The software filter must still run before the upload.
        self.assertIn("scale=-2:144:flags=lanczos", vf)
        self.assertLess(vf.index("scale=-2:144"), vf.index("hwupload"))

    def test_vaapi_upload_even_with_no_other_filter(self):
        p = plan(parse_probe(probe_payload(MPEG2)), hw=NO_HW, target_height=None)
        cmd = build(p=p, hw=HwAccel("vaapi", "/dev/dri/renderD128"))
        self.assertEqual(cmd[cmd.index("-vf") + 1], "format=nv12,hwupload=extra_hw_frames=64")

    def test_software_path_has_no_hwupload(self):
        p = plan(parse_probe(probe_payload(MPEG2)), target_height=144)
        self.assertNotIn("hwupload", build(p=p, hw=NO_HW)[0:0] or "".join(build(p=p, hw=NO_HW)))
        self.assertNotIn("hwupload", build_video_filter(p, src_width=1280)[0])

    def test_hw_is_never_swapped_in_for_a_copy(self):
        for hw in (HwAccel("vaapi", "/dev/dri/renderD128"), HwAccel("nvenc"), HwAccel("qsv")):
            with self.subTest(hw=hw.name):
                cmd = build(p=plan(parse_probe(probe_payload(H264_720P))), hw=hw)
                self.assertEqual(cmd[cmd.index("-c:v") + 1], "copy")


class TestArgvSafety(unittest.TestCase):
    """The package must never build a shell string."""

    HOSTILE = "/tmp/a b; rm -rf ~/'$(whoami)'`id`.mp4"

    def test_hostile_filename_survives_as_one_argv_element(self):
        p = plan(parse_probe(probe_payload(H264_720P)))
        cmd = build_plan_args(p, self.HOSTILE, "2800k", "veryfast", 4,
                              "/out/720p", "/out/720p/seg_%05d.m4s")
        self.assertIn(self.HOSTILE, cmd)
        self.assertEqual(cmd[cmd.index("-i") + 1], self.HOSTILE)

    def test_builders_return_lists(self):
        p = plan(parse_probe(probe_payload(H264_720P)))
        for cmd in (build(p=p), build_direct_play_cmd(p, "a.mp4", "b.mp4"),
                    build_subtitle_cmd("a.mkv", 3, "out.srt")):
            self.assertIsInstance(cmd, list)
            self.assertTrue(all(isinstance(a, str) for a in cmd))

    def test_direct_play_uses_faststart(self):
        p = plan(parse_probe(probe_payload(H264_720P)))
        cmd = build_direct_play_cmd(p, "in.mp4", "out.mp4")
        self.assertEqual(cmd[cmd.index("-movflags") + 1], "+faststart")
        self.assertEqual(cmd[-1], "out.mp4")

    def test_subtitle_cmd_targets_one_stream(self):
        cmd = build_subtitle_cmd("in.mkv", 7, "out.srt")
        self.assertEqual(cmd[cmd.index("-map") + 1], "0:7")
        self.assertEqual(cmd[-1], "out.srt")


class TestHwDetection(unittest.TestCase):
    def test_detection_is_cached(self):
        a = detect_hwaccel()
        b = detect_hwaccel()
        self.assertIs(a, b, "detect_hwaccel must be cached for the process lifetime")

    def test_cache_can_be_reset_for_tests(self):
        before = detect_hwaccel()
        reset_hwaccel_cache()
        after = detect_hwaccel()
        self.assertEqual(before, after, "re-probing must reach the same conclusion")
        self.assertIsNot(before, after, "cache_clear must produce a fresh detection")

    def test_detection_returns_a_real_answer(self):
        hw = detect_hwaccel()
        self.assertIn(hw.name, ("nvenc", "vaapi", "qsv", "none"))
        if hw.name == "vaapi":
            self.assertTrue(os.path.exists(hw.device))
        if hw.name == "none":
            self.assertIsNone(hw.device)


if __name__ == "__main__":
    unittest.main(verbosity=2)