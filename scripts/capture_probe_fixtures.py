#!/usr/bin/env python3
"""Regenerate the committed ffprobe fixtures under tests/fixtures/probe/.

The planner decision matrix is driven by *real* ffprobe output, not by
hand-written dicts. That matters: ffprobe's field names, the way it reports bit
depth for some pixel formats but not others, the `codec_tag` four-character
codes, and the colour-transfer strings the planner keys off ("smpte2084" for
PQ) are all easy to get subtly wrong in a hand-written fixture — and a fixture
that lies about the real shape of ffprobe output tests nothing useful.

So each case is generated here with ffmpeg's lavfi sources (no binaries are
committed), probed with the real ffprobe, and the resulting JSON is written to
disk as the committed fixture.

Run this whenever the fixtures need to be refreshed or a new case added:

    ./venv/bin/python scripts/capture_probe_fixtures.py

Adding a case means adding one entry to CASES; the fixture file, the
integration media generator and the unit decision matrix all key off the same
name so they cannot drift apart.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from media_pipeline.probe import _detect_faststart  # noqa: E402
OUT_DIR = ROOT / "tests" / "fixtures" / "probe"

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

# Kept short on purpose: these exist to be probed and planned, not watched.
DURATION = "2"


class Case:
    """One fixture: how to build the media, and what the planner must decide.

    ``expect_*`` is documentation of the decision this fixture is meant to
    pin down. The unit matrix asserts the same facts against the committed JSON,
    so a change in planner behaviour that silently flips one of these fails the
    test suite instead of quietly shipping.
    """

    def __init__(self, name, args, expect_mode, note="", expect=None, subtitle=None):
        self.name = name
        self.args = args
        self.expect_mode = expect_mode
        self.note = note
        self.expect = expect or {}
        #: "ass" muxes a real styled subtitle track. "pgs" appends a synthetic
        #: bitmap stream — see :func:`add_bitmap_subtitle_stream` for why that
        #: one cannot be produced by ffmpeg.
        self.subtitle = subtitle


CASES = [
    Case(
        "h264_8bit",
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=" + DURATION,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=" + DURATION,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high",
         "-c:a", "aac", "-shortest", "-movflags", "+faststart", "out.mp4"],
        "copy",
        "Baseline: 8-bit H.264 + AAC in a faststart MP4 is fully browser-safe.",
        {"direct_play": True, "hevc_only": False, "needs_compat": False,
         "hdr": False, "ten_bit": False},
    ),
    Case(
        "h264_8bit_mkv",
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=" + DURATION,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=" + DURATION,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", "out.mkv"],
        "copy",
        "Same codecs but Matroska: no faststart concept, so no direct play.",
        {"direct_play": False, "hevc_only": False, "ten_bit": False},
    ),
    Case(
        "hevc_main",
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=" + DURATION,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=" + DURATION,
         "-c:v", "libx265", "-pix_fmt", "yuv420p", "-profile:v", "main",
         "-tag:v", "hvc1", "-c:a", "aac", "-shortest",
         "-movflags", "+faststart", "out.mp4"],
        "copy",
        "8-bit HEVC: still cheap to copy, but gated on client HEVC support.",
        {"hevc_only": True, "ten_bit": False, "hdr": False},
    ),
    Case(
        "hevc_main10",
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=" + DURATION,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=" + DURATION,
         "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-profile:v", "main10",
         "-tag:v", "hvc1", "-c:a", "aac", "-shortest",
         "-movflags", "+faststart", "out.mp4"],
        "copy",
        "10-bit HEVC: copyable, but needs a client that reports 10-bit.",
        {"hevc_only": True, "ten_bit": True, "hdr": False},
    ),
    Case(
        "hevc_hdr10",
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=" + DURATION,
         "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-profile:v", "main10",
         "-tag:v", "hvc1", "-color_primaries", "bt2020",
         "-color_trc", "smpte2084", "-colorspace", "bt2020nc",
         "-x265-params", "hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,50)",
         "-an", "-movflags", "+faststart", "out.mp4"],
        "tonemap",
        "PQ (smpte2084): HDR must be tonemapped for the 8-bit ladder.",
        {"hdr": True, "ten_bit": True, "hevc_only": True,
         "needs_tonemap": True, "direct_play": False},
    ),
    Case(
        "h264_hi10p",
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=" + DURATION,
         "-c:v", "libx264", "-pix_fmt", "yuv420p10le", "-profile:v", "high10",
         "-an", "-movflags", "+faststart", "out.mp4"],
        "transcode",
        "Hi10P H.264: 10-bit in an H.264 stream is not a thing browsers decode "
        "in HLS, so it has to be re-encoded down to 8-bit.",
        {"ten_bit": True, "hdr": False, "hevc_only": False},
    ),
    Case(
        "av1_main",
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=" + DURATION,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=" + DURATION,
         "-c:v", "libsvtav1", "-preset", "12", "-crf", "50",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         "-movflags", "+faststart", "out.mp4"],
        "transcode",
        "AV1 8-bit: copy is possible in principle but no browser profile here "
        "claims AV1 in HLS, so the ladder re-encodes to H.264.",
        {"hevc_only": False, "ten_bit": False, "hdr": False},
    ),
    Case(
        "audio_ac3",
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=" + DURATION,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=" + DURATION,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "ac3",
         "-shortest", "out.mkv"],
        "copy",
        "Video is safely copyable; the AC-3 audio is not browser-safe, so audio "
        "transcodes while video passes through — the mixed case.",
        {"hevc_only": False, "audio_transcode": True},
    ),
    Case(
        "audio_dts",
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=" + DURATION,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=" + DURATION,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "dca",
         # ffmpeg ships the DCA encoder but gates it behind -strict, because it
         # is only conformant for the "Coherent Acoustics" profile, not general
         # DTS. Good enough as a fixture: the planner only reads the codec name.
         "-strict", "-2", "-shortest", "out.mkv"],
        "copy",
        "DTS audio: same shape as AC-3 — copy video, re-encode audio.",
        {"hevc_only": False, "audio_transcode": True},
    ),
    Case(
        "audio_flac",
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=" + DURATION,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=" + DURATION,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "flac",
         "-shortest", "out.mkv"],
        "copy",
        "FLAC audio: lossless and not a browser codec — transcode the audio.",
        {"hevc_only": False, "audio_transcode": True},
    ),
    Case(
        "subs_pgs",
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=" + DURATION,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=" + DURATION,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", "out.mkv"],
        "copy",
        "Bitmap (PGS) subtitles are muxed in for probing; the planner must "
        "SKIP them with a warning rather than burn them into the picture.",
        {"subtitle_action": "skip_bitmap", "audio_transcode": False},
        subtitle="pgs",
    ),
    Case(
        "subs_ass",
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=" + DURATION,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=" + DURATION,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", "out.mkv"],
        "copy",
        "Styled ASS subtitles are text and must be KEPT for client-side "
        "rendering, not flattened to SRT.",
        {"subtitle_action": "keep_text", "audio_transcode": False},
        subtitle="ass",
    ),
]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def build_media(case: Case, workdir: Path) -> Path:
    """Create the media for one case, returning the file path."""
    out = workdir / case.args[-1]
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *case.args[:-1], str(out)]
    # HDR tags need an explicit bitrate or x265 refuses; keep it tiny and fast.
    if case.name == "hevc_hdr10":
        cmd += ["-b:v", "200k", "-maxrate", "400k", "-bufsize", "600k"]
    p = run(cmd)
    if p.returncode != 0 or not out.exists():
        raise RuntimeError(f"{case.name}: ffmpeg failed\ncmd: {' '.join(cmd)}\n{p.stderr[-2000:]}")
    return out


ASS_SCRIPT = """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 240

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,fixture subtitle
"""


def mux_ass_subtitle(case: Case, media: Path, workdir: Path) -> Path:
    """Remux the video with a real styled ASS subtitle track attached.

    ASS is text, so ffmpeg can encode it from a script file — this produces a
    genuine subtitle stream that ffprobe reports, not a hand-written dict.
    """
    script = workdir / f"{case.name}.ass"
    script.write_text(ASS_SCRIPT)
    out = workdir / f"{case.name}_ass.mkv"
    p = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(media), "-i", str(script),
             "-map", "0:v", "-map", "0:a", "-map", "1:s",
             "-c:v", "copy", "-c:a", "copy", "-c:s", "ass",
             str(out)])
    if p.returncode != 0 or not out.exists():
        raise RuntimeError(f"{case.name}: ASS muxing failed\n{p.stderr[-2000:]}")
    return out


#: The exact stream dict ffprobe emits for a PGS track in Matroska. PGS is a
#: DVD/Blu-ray raster subtitle format and **no ffmpeg build has ever shipped a
#: PGS encoder** (`ffmpeg -encoders | grep -i pgs` returns nothing; only the
#: `pgssub` decoder exists), so there is no way to generate a real file to probe.
#: This entry is therefore the one hand-written fixture in the set, and it is
#: kept honest two ways: the video and audio streams around it are real ffprobe
#: output from an actual file, and every field below is copied from the shape
#: ffprobe documents for Matroska subtitle streams. The planner only reads
#: ``codec_name`` for its bitmap decision, so the risk of drift is contained to
#: that one string — and ``tests/unit/test_planner_matrix.py`` asserts the
#: fixture still parses to ``is_bitmap=True``.
PGS_STREAM_JSON = {
    "index": 2,
    "codec_name": "hdmv_pgs_subtitle",
    "codec_type": "subtitle",
    "codec_tag_string": "[17][0][0][0]",
    "codec_long_name": "HDMV Presentation Graphic Stream",
    "profile": "High",
    "width": 320,
    "height": 240,
    "r_frame_rate": "0/0",
    "avg_frame_rate": "0/0",
    "time_base": "1/1000",
    "start_pts": 0,
    "duration_ts": 2000,
    "duration": "0.200000",
    "tags": {"language": "eng"},
    "disposition": {"default": 0, "forced": 0},
}


def probe(path: Path) -> dict:
    p = run([FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {p.stderr[-2000:]}")
    return json.loads(p.stdout)


def inject_pgs_stream(data: dict) -> dict:
    """Append the synthetic PGS subtitle stream to real ffprobe output."""
    streams = list(data.get("streams") or [])
    pgs = dict(PGS_STREAM_JSON)
    pgs["index"] = max((s.get("index", 0) for s in streams), default=-1) + 1
    streams.append(pgs)
    data = dict(data)
    data["streams"] = streams
    return data


def detect_faststart(path: Path) -> bool | None:
    """Where the moov atom sat, recorded so tests need not keep the media."""
    if not path.suffix.lower() in (".mp4", ".m4v", ".mov"):
        return None
    try:
        return _detect_faststart(str(path))
    except Exception:  # noqa: BLE001 - an unreadable file is simply "unknown"
        return None


def main() -> int:
    if not FFMPEG or not FFPROBE:
        print("ffmpeg/ffprobe not found on PATH; cannot capture fixtures", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written, failed = [], []

    with tempfile.TemporaryDirectory(prefix="probe-fixtures-") as td:
        workdir = Path(td)
        for case in CASES:
            synthetic_pgs = False
            try:
                media = build_media(case, workdir)
                if case.subtitle == "ass":
                    media = mux_ass_subtitle(case, media, workdir)
                data = probe(media)
                if case.subtitle == "pgs":
                    data = inject_pgs_stream(data)
                    synthetic_pgs = True
            except Exception as exc:  # noqa: BLE001 - report and continue
                failed.append((case.name, str(exc)[:400]))
                print(f"  FAIL {case.name}: {str(exc)[:200]}")
                continue

            faststart = detect_faststart(media)
            data["_fixture"] = {
                "name": case.name,
                "note": case.note,
                "expect_mode": case.expect_mode,
                "expect": case.expect,
                # Passed to parse_probe(has_faststart=...) — the one answer that
                # needs the original file, which a committed JSON cannot carry.
                "has_faststart": faststart,
                # True only for subs_pgs: see PGS_STREAM_JSON for why.
                "synthetic_stream": "hdmv_pgs_subtitle" if synthetic_pgs else None,
            }
            dest = OUT_DIR / f"{case.name}.json"
            dest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
            vstreams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
            v = vstreams[0] if vstreams else {}
            subs = [s for s in data.get("streams", []) if s.get("codec_type") == "subtitle"]
            print(f"  ok   {case.name:16s} {v.get('codec_name','?'):6s} "
                  f"{v.get('profile','?'):10s} {v.get('pix_fmt','?'):11s} "
                  f"trc={v.get('color_transfer','-'):10s} "
                  f"subs={[s.get('codec_name') for s in subs] or '-'}")
            written.append(case.name)

    print(f"\nwrote {len(written)} fixture(s) to {OUT_DIR.relative_to(ROOT)}")
    if failed:
        print(f"FAILED: {', '.join(n for n, _ in failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
