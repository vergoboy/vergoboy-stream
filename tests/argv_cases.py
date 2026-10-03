"""The single definition of every golden-argv scenario.

Both the capture script (``scripts/capture_argv_fixtures.py``, which writes the
goldens) and the golden test import this table. Keeping one definition is the
whole point: if the test carried its own copy of the inputs, a golden could be
regenerated from different arguments than the test used, and the comparison
would quietly stop meaning anything.

Inputs are deliberately absolute and fixed. ``/srv/media/...`` rather than
``media/...`` because a relative path would make the goldens depend on the
working directory pytest happened to run from.
"""
from __future__ import annotations

from media_pipeline.ffmpeg_cmd import (
    HwAccel,
    build_direct_play_cmd,
    build_plan_args,
    build_subtitle_cmd,
)
from media_pipeline.planner import plan, plan_compat

from tests.support import probe_info

SOURCE = "/srv/media/uploads/source.mkv"
RENDITION_DIR = "/srv/media/hls/item-1/720p"
SEGMENT_TEMPLATE = f"{RENDITION_DIR}/seg_%05d.ts"
PLAYLIST = "index.m3u8"


def _hls(plan_obj, **overrides) -> list[str]:
    kwargs = dict(
        plan=plan_obj,
        source=SOURCE,
        segment_seconds=4,
        rendition_dir=RENDITION_DIR,
        segment_template=SEGMENT_TEMPLATE,
        playlist_name=PLAYLIST,
        gop_seconds=4,
    )
    kwargs.update(overrides)
    return build_plan_args(**kwargs)


#: name -> zero-arg callable returning the argv for that scenario.
#: Each entry covers a distinct branch of the builder; between them they touch
#: copy, audio transcode, genpts, scale, dither, tonemap, VBR, both hardware
#: paths, the compat rendition, direct play and subtitle conversion.
CASES = {
    "hls_copy": lambda: _hls(plan(probe_info("h264_8bit"))),
    "hls_copy_ac3_audio": lambda: _hls(plan(probe_info("audio_ac3"))),
    "hls_copy_genpts": lambda: _hls(
        plan(probe_info("h264_8bit_mkv")), genpts=True
    ),
    "hls_transcode_hevc": lambda: _hls(plan(probe_info("hevc_main"))),
    "hls_tonemap_hdr": lambda: _hls(plan(probe_info("hevc_hdr10"))),
    "hls_dither_hi10p": lambda: _hls(plan(probe_info("h264_hi10p"))),
    "hls_transcode_av1": lambda: _hls(plan(probe_info("av1_main"))),
    # target_height is a planner input, not a builder input: the Plan carries it
    # so the builder has one place to read the ladder height from. src_width is a
    # builder input because it describes the *source*, which the plan cannot
    # know without probing again.
    "hls_scaled": lambda: _hls(
        plan(probe_info("h264_8bit"), target_height=720), src_width=1080,
    ),
    "hls_vbr": lambda: _hls(plan(probe_info("h264_hi10p")), vbr="2500k"),
    "hls_hw_vaapi": lambda: _hls(
        plan(probe_info("h264_hi10p")),
        hw=HwAccel(name="vaapi", device="/dev/dri/renderD128"),
    ),
    "hls_hw_nvenc": lambda: _hls(
        plan(probe_info("h264_hi10p")), hw=HwAccel(name="nvenc")
    ),
    "compat_rendition": lambda: _hls(plan_compat(probe_info("hevc_hdr10"))),
    "direct_play": lambda: build_direct_play_cmd(
        plan(probe_info("h264_8bit")), SOURCE, f"{RENDITION_DIR}/faststart.mp4"
    ),
    # .srt, not .vtt: the builder pins the srt codec and ffmpeg chooses its muxer
    # from the extension. WebVTT conversion is a separate step in app.py.
    "subtitle_convert": lambda: build_subtitle_cmd(SOURCE, 3, f"{RENDITION_DIR}/subs.srt"),
}


def render(argv: list[str]) -> str:
    """One argument per line.

    Deliberately not shell-quoted. These are argv lists that reach ``execve``
    and never a shell, so nothing here needs quoting; a value containing a space
    shows up as a visibly odd line instead of being normalised away by a quoting
    function that nothing in the real path uses.
    """
    return "\n".join(argv) + "\n"
