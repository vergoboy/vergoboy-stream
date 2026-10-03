"""Runtime tests: the argv must actually work in ffmpeg, not just look right.

The golden tests in test_media_pipeline.py compare strings. These tests close
the loop: they synthesise real media, run the exact argv the package produces
through a real ffmpeg subprocess (as an argv list, never a shell string), and
then probe the output to confirm the promise the plan made was kept.

    python3 -m unittest tests.test_media_pipeline_runtime -v

Skipped automatically when ffmpeg/ffprobe are unavailable. Each test builds the
fixtures it needs, so any single test can be run on its own.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from media_pipeline import HwAccel, plan, plan_compat
from media_pipeline.ffmpeg_cmd import build_direct_play_cmd, build_plan_args, detect_hwaccel
from media_pipeline.probe import _detect_faststart, probe

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
NO_HW = HwAccel()

W, H, FPS, DURATION = 320, 240, 10, 1
AUDIO_AAC = ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]
AUDIO_AC3 = ["-c:a", "ac3", "-b:a", "640k", "-ac", "6"]


def run(args, timeout=180):
    """Run an argv list. Never shell=True — that is the point of the package."""
    return subprocess.run(args, capture_output=True, timeout=timeout, check=False)


def make_source(path, video_args, audio_args=(), extra=()):
    """Build a small synthetic clip. Returns True on success."""
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:d={DURATION}:r={FPS}"]
    if audio_args:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=48000"]
    cmd += list(video_args) + list(extra)
    if audio_args:
        cmd += list(audio_args)
    cmd.append(path)
    proc = run(cmd)
    return proc.returncode == 0 and os.path.exists(path)


def segments_of(playlist):
    """Parse media segment URIs out of a media playlist."""
    uris = []
    base = os.path.dirname(playlist)
    with open(playlist) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                uris.append(os.path.join(base, line))
    return uris


def probe_output(playlist):
    """Probe the finished HLS presentation.

    A standalone .m4s media segment is not self-describing (its init segment is
    separate), so the playlist is what ffprobe must be pointed at.
    """
    return probe(playlist)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not installed")
class RuntimePipeline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    # ── fixtures (cached per test so tests stay independent) ───────────────

    def _fixture(self, name, builder):
        path = os.path.join(self.tmp, name)
        if not os.path.exists(path):
            proc_msg = ""
            self.assertTrue(builder(path), f"could not build fixture {name}: {proc_msg}")
        return path

    def h264_src(self):
        """H.264 8-bit High + AAC stereo in MP4 (the copy fast path)."""
        return self._fixture("h264.mp4", lambda p: make_source(
            p, ["-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-preset", "ultrafast"], AUDIO_AAC))

    def hevc10_src(self):
        """HEVC 10-bit + AAC stereo in MKV (HEVC copy, dithering not wanted)."""
        return self._fixture("hevc10.mkv", lambda p: make_source(
            p, ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "ultrafast",
                "-x265-params", "log-level=none"], AUDIO_AAC,
            extra=["-tag:v", "hvc1"]))

    def hdr_src(self):
        """Genuinely HDR: BT.2020 / PQ / 10-bit.

        The VUI must be told explicitly, and MP4+libx265 is the one combination
        that actually persists colour_transfer through to ffprobe — x264 and
        Matroska both silently drop it in this ffmpeg build.
        """
        return self._fixture("hdr.mp4", lambda p: make_source(
            p, ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "ultrafast",
                "-x265-params", "log-level=none:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc"],
            AUDIO_AAC,
            extra=["-color_primaries", "bt2020", "-color_trc", "smpte2084",
                   "-colorspace", "bt2020nc"]))

    def h264hi10_src(self):
        """H.264 High 10 profile (dither down to 8-bit)."""
        return self._fixture("h264hi10.mp4", lambda p: make_source(
            p, ["-c:v", "libx264", "-profile:v", "high10", "-pix_fmt", "yuv420p10le",
                "-preset", "ultrafast"], AUDIO_AAC))

    def mpeg2_src(self):
        """MPEG-2 video + AC-3 5.1 in MKV (transcode + downmix)."""
        return self._fixture("mpeg2.mkv", lambda p: make_source(
            p, ["-c:v", "mpeg2video", "-pix_fmt", "yuv420p", "-b:v", "800k"], AUDIO_AC3))

    # ── helpers ────────────────────────────────────────────────────────────

    def _hls(self, plan_obj, src, name, preset=None, hw=None, **kw):
        out = os.path.join(self.tmp, name)
        os.makedirs(out, exist_ok=True)
        argv = build_plan_args(
            plan_obj, src, 1,
            out, os.path.join(out, "seg_%05d.m4s"),
            hw=hw, src_width=W, preset=preset, **kw,
        )
        proc = run(argv)
        self.assertEqual(
            proc.returncode, 0,
            msg=(f"ffmpeg failed:\n{' '.join(argv)}\n"
                 f"{proc.stderr.decode(errors='ignore')[-3000:]}"),
        )
        playlist = os.path.join(out, "index.m3u8")
        self.assertTrue(os.path.exists(playlist), "no playlist produced")
        return playlist, argv

    # ── copy path ───────────────────────────────────────────────────────────

    def test_h264_8bit_copy_produces_fmp4_segments(self):
        src = self.h264_src()
        info = probe(src)
        self.assertEqual(info.video.codec, "h264")
        self.assertFalse(info.video.is_10bit)
        self.assertFalse(info.video.is_hdr)

        p = plan(info, hw=NO_HW)
        self.assertTrue(p.is_remux, "H.264 8-bit must take the copy path")

        playlist, argv = self._hls(p, src, "h264_out")
        self.assertEqual(argv[argv.index("-c:v") + 1], "copy")

        with open(playlist) as fh:
            text = fh.read()
        # fMP4 is proven by the init-segment EXT-X-MAP tag, not by the filename.
        self.assertIn("#EXT-X-MAP:URI=", text, "segments must be fMP4")
        self.assertNotIn(".ts", text)

        segs = segments_of(playlist)
        self.assertGreater(len(segs), 0)
        for seg in segs:
            self.assertTrue(os.path.exists(seg), f"missing segment {seg}")
            self.assertTrue(seg.endswith(".m4s"))
            # A real fMP4 segment is an ISO-BMFF box. A media segment opens
            # with `styp` (segment type); `ftyp` lives in the separate init
            # segment referenced by #EXT-X-MAP. Either proves it is not MPEG-TS.
            with open(seg, "rb") as fh:
                head = fh.read(12)
            self.assertIn(head[4:8], (b"styp", b"ftyp"),
                          f"{seg} is not an ISO-BMFF segment (box={head[4:8]!r})")

        out = probe_output(playlist)
        self.assertEqual(out.video.codec, "h264")
        self.assertEqual(out.video.pix_fmt, "yuv420p")
        self.assertEqual(out.audio[0].codec, "aac")
        self.assertEqual(out.audio[0].channels, 2)

    def test_aac_audio_is_copied_not_re_encoded(self):
        p = plan(probe(self.h264_src()), hw=NO_HW)
        self.assertEqual(p.audio_action.value, "copy")
        playlist, argv = self._hls(p, self.h264_src(), "aac_out")
        self.assertEqual(argv[argv.index("-c:a") + 1], "copy")
        self.assertNotIn("-ac", argv)
        out = probe_output(playlist)
        self.assertEqual(out.audio[0].codec, "aac")

    # ── HEVC copy + hvc1 tag ────────────────────────────────────────────────

    def test_hevc_10bit_is_copied_and_tagged_hvc1(self):
        src = self.hevc10_src()
        info = probe(src)
        self.assertEqual(info.video.codec, "hevc")
        self.assertTrue(info.video.is_10bit, "10-bit must be detected for the copy decision")
        self.assertFalse(info.video.is_hdr)

        p = plan(info, hw=NO_HW)
        self.assertEqual(p.mode.value, "copy")
        self.assertEqual(p.tag_v, "hvc1")
        self.assertTrue(p.hevc_only)
        self.assertEqual(p.target_bit_depth, 10)

        playlist, argv = self._hls(p, src, "hevc_out")
        self.assertEqual(argv[argv.index("-tag:v") + 1], "hvc1")
        out = probe_output(playlist)
        # 10-bit must survive a copy untouched — no dithering, no depth change.
        self.assertEqual(out.video.codec, "hevc")
        self.assertEqual(out.video.pix_fmt, "yuv420p10le")
        self.assertTrue(out.video.is_10bit)
        # hvc1 is what makes Safari/hls.js accept the track.
        self.assertEqual(out.video.codec_tag, "hvc1")

    # ── HDR → SDR tonemap ───────────────────────────────────────────────────

    def test_hdr_source_is_tonemapped_to_8bit_sdr(self):
        src = self.hdr_src()
        info = probe(src)
        self.assertTrue(info.video.is_hdr, "PQ transfer must be detected as HDR")
        self.assertTrue(info.video.is_10bit)

        p = plan(info, hw=NO_HW, target_height=144)
        self.assertEqual(p.mode.value, "tonemap")
        self.assertTrue(p.needs_tonemap)
        self.assertFalse(p.direct_play, "HDR must never be direct-played")

        playlist, argv = self._hls(p, src, "hdr_out")
        self.assertIn("tonemap=tonemap=hable", argv[argv.index("-vf") + 1])

        out = probe_output(playlist)
        self.assertEqual(out.video.pix_fmt, "yuv420p", "output must be 8-bit SDR")
        self.assertFalse(out.video.is_10bit)
        self.assertEqual(out.video.height, 144, "target height must be applied")
        self.assertFalse(out.video.is_hdr, "output must no longer be HDR")
        # The real proof of tone mapping: values were compressed, not relabelled.
        self.assertNotEqual(out.video.color_transfer, "smpte2084")

    # ── transcode path with audio downmix ───────────────────────────────────

    def test_ac3_51_is_downmixed_to_aac_stereo(self):
        src = self.mpeg2_src()
        info = probe(src)
        self.assertEqual(info.video.codec, "mpeg2video")
        self.assertEqual(info.audio[0].codec, "ac3")
        self.assertEqual(info.audio[0].channels, 6)
        # MKV has no faststart concept at all.
        self.assertIsNone(info.has_faststart)
        self.assertFalse(plan(info, hw=NO_HW).direct_play)

        p = plan(info, hw=NO_HW, target_height=144)
        self.assertEqual(p.mode.value, "transcode")
        self.assertTrue(p.audio_downmix)
        self.assertFalse(p.is_remux, "a real transcode must take a transcode slot")

        playlist, argv = self._hls(p, src, "ac3_out")
        self.assertIn("-ac", argv, "5.1 source must be downmixed")
        out = probe_output(playlist)
        self.assertEqual(out.video.codec, "h264")
        self.assertEqual(out.video.height, 144)
        self.assertEqual(out.audio[0].codec, "aac")
        self.assertEqual(out.audio[0].channels, 2, "5.1 must come out as stereo")

    # ── 10-bit H.264 dither path ────────────────────────────────────────────

    def test_h264_10bit_dithers_to_8bit(self):
        info = probe(self.h264hi10_src())
        self.assertEqual(info.video.codec, "h264")
        self.assertTrue(info.video.is_10bit)

        p = plan(info, hw=NO_HW, target_height=144)
        self.assertEqual(p.mode.value, "transcode")
        self.assertTrue(p.needs_dither)
        self.assertEqual(p.target_bit_depth, 8)

        playlist, argv = self._hls(p, src := self.h264hi10_src(), "hi10_out")
        self.assertIn("dither", argv[argv.index("-vf") + 1])
        out = probe_output(playlist)
        self.assertEqual(out.video.pix_fmt, "yuv420p")
        self.assertFalse(out.video.is_10bit)
        self.assertEqual(out.video.height, 144)

    # ── compatibility rendition ─────────────────────────────────────────────

    def test_compat_rendition_from_hevc10_is_playable_h264(self):
        info = probe(self.hevc10_src())
        self.assertEqual(info.video.codec, "hevc")

        c = plan_compat(info, hw=NO_HW)
        self.assertEqual(c.video_codec, "libx264")
        self.assertFalse(c.hevc_only)
        self.assertFalse(c.direct_play)

        playlist, argv = self._hls(c, self.hevc10_src(), "compat_out")
        self.assertEqual(argv[argv.index("-c:v") + 1], "libx264")
        out = probe_output(playlist)
        self.assertEqual(out.video.codec, "h264")
        self.assertEqual(out.video.pix_fmt, "yuv420p")

    def test_compat_rendition_from_hdr_is_still_tonemapped(self):
        c = plan_compat(probe(self.hdr_src()), hw=NO_HW)
        self.assertTrue(c.needs_tonemap)
        playlist, argv = self._hls(c, self.hdr_src(), "compat_hdr_out")
        self.assertIn("tonemap", argv[argv.index("-vf") + 1])
        out = probe_output(playlist)
        self.assertEqual(out.video.pix_fmt, "yuv420p")
        self.assertFalse(out.video.is_hdr)

    # ── direct play ─────────────────────────────────────────────────────────

    def test_direct_play_produces_a_faststart_mp4(self):
        src = self.h264_src()
        info = probe(src)
        self.assertEqual(info.video.codec, "h264")
        p = plan(info, hw=NO_HW)
        self.assertIsInstance(p.direct_play, bool)

        dest = os.path.join(self.tmp, "direct.mp4")
        argv = build_direct_play_cmd(p, src, dest)
        proc = run(argv)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr.decode(errors="ignore")[-2000:])
        self.assertTrue(os.path.exists(dest))
        # The whole point: moov before mdat.
        self.assertTrue(_detect_faststart(dest), "direct-play output must be faststart")
        self.assertEqual(probe(dest).video.codec, "h264")
        self.assertEqual(probe(dest).audio[0].codec, "aac")

    # ── hardware path ───────────────────────────────────────────────────────

    def test_detected_hardware_encoder_produces_playable_output(self):
        hw = detect_hwaccel()
        if hw.name == "none":
            self.skipTest("no working hardware encoder on this host")
        # A copy-mode source never reaches an encoder, so the hardware swap
        # would be dead code. Try sources that genuinely transcode.
        for src in (self.h264hi10_src(), self.mpeg2_src(), self.hdr_src()):
            p = plan(probe(src), hw=hw, target_height=144)
            if p.is_remux:
                continue
            playlist, argv = self._hls(p, src, f"hw_{hw.name}_{os.path.basename(src)}", hw=hw)
            self.assertNotEqual(argv[argv.index("-c:v") + 1], "libx264",
                                f"hardware path should have swapped the encoder, got {argv[argv.index('-c:v') + 1]}")
            out = probe_output(playlist)
            self.assertEqual(out.video.codec, "h264")
            self.assertEqual(out.video.height, 144)
            return
        self.skipTest("hardware encoder could not handle any transcodable fixture")

    # ── preset wiring ───────────────────────────────────────────────────────

    def test_preset_reaches_the_encoder(self):
        # Must be a source that actually transcodes, otherwise there is no
        # encoder to configure and -preset would be (correctly) absent.
        src = self.mpeg2_src()
        p = plan(probe(src), hw=NO_HW, target_height=144)
        self.assertEqual(p.mode.value, "transcode")
        for preset in ("veryfast", "ultrafast", "medium"):
            with self.subTest(preset=preset):
                playlist, argv = self._hls(p, src, f"preset_{preset}", preset=preset)
                self.assertEqual(argv[argv.index("-preset") + 1], preset)
                self.assertTrue(os.path.exists(playlist))

    def test_runs_are_deterministic(self):
        p = plan(probe(self.h264_src()), hw=NO_HW, target_height=144)
        first, _ = self._hls(p, self.h264_src(), "det_a")
        second, _ = self._hls(p, self.h264_src(), "det_b")
        with open(first) as a, open(second) as b:
            self.assertEqual(a.read(), b.read())

    def test_hostile_filename_is_not_a_shell_injection(self):
        # A path with spaces, quotes and a semicolon must be handled as one
        # argv element, and must not execute anything.
        # No "/" in the name: an absolute marker path would introduce a
        # subdirectory that does not exist and ffmpeg would fail for the
        # wrong reason. "PWNED" is relative to ffmpeg's cwd.
        hostile = os.path.join(self.tmp, "a b; touch PWNED; '$(id)'`id`.mp4")
        self.assertTrue(make_source(hostile,
                                    ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast"],
                                    AUDIO_AAC))
        p = plan(probe(hostile), hw=NO_HW)
        playlist, argv = self._hls(p, hostile, "hostile_out")
        self.assertIn(hostile, argv)
        self.assertEqual(argv[argv.index("-i") + 1], hostile)
        self.assertFalse(os.path.exists("PWNED"), "argv must never reach a shell")
        self.assertEqual(probe_output(playlist).video.codec, "h264")


if __name__ == "__main__":
    unittest.main(verbosity=2)