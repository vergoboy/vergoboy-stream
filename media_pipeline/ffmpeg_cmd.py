"""ffmpeg argv construction.

Two hard rules:

1. **argv lists only.** Every builder here returns ``list[str]``. Nothing is
   ever concatenated into a shell string, so a filename containing a space, a
   quote, or a ``;`` cannot become a shell injection.

2. **The hardware decision is made once.** Probing for a usable encoder costs a
   subprocess spawn, so it happens at boot (or lazily on first use) and is
   cached forever. See :func:`detect_hwaccel`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from typing import Optional

from .planner import AudioAction, HwAccel, Plan

log = logging.getLogger(__name__)

#: The ladder is 8-bit SDR H.264 everywhere. Stated once so every filter chain
#: agrees with it.
TARGET_SDR = "yuv420p"

#: libx264 speed presets, most to least aggressive. ``ultrafast`` is a real
#: member but is deliberately NOT the default: it roughly doubles the output
#: bitrate for the same quality, which would quietly multiply bandwidth cost on
#: every rendition.
X264_PRESETS = frozenset(
    {"ultrafast", "superfast", "veryfast", "faster", "fast",
     "medium", "slow", "slower", "veryslow"}
)

#: The x264 preset used unless one is explicitly requested. "veryfast" is the
#: quality/speed knee of the curve: within a few percent of "medium" size at a
#: fraction of the CPU.
DEFAULT_PRESET = "veryfast"

#: Constant-quality target used on the software path. CRF and bitrate are
#: mutually exclusive in libx264, so only one is ever emitted.
DEFAULT_CRF = "21"

#: Bitrates used on the *hardware* path. The hardware H.264 encoders (VAAPI,
#: QSV, NVENC) do not implement CRF-based rate control — the option is accepted
#: and then ignored — so a bitrate has to be supplied instead. Derived from the
#: ladder's own ladder rather than invented per call.
_HW_BITRATE_BY_HEIGHT = ((1080, "5000k"), (720, "2800k"), (480, "1400k"), (360, "800k"))
_HW_BITRATE_FALLBACK = "2800k"


def resolve_preset(explicit: Optional[str] = None) -> str:
    """Decide the x264 speed preset.

    Precedence: an explicit argument, then ``STREAM_HLS_PRESET``, then
    :data:`DEFAULT_PRESET`. ``ultrafast`` is therefore only ever used when
    somebody deliberately asked for it.
    """
    if explicit:
        value = explicit.strip().lower()
        if value not in X264_PRESETS:
            raise ValueError(f"unknown x264 preset {explicit!r}")
        return value
    from_env = (os.environ.get("STREAM_HLS_PRESET") or "").strip().lower()
    if from_env:
        if from_env not in X264_PRESETS:
            # A typo would otherwise be passed to ffmpeg and fail the encode at
            # the worst possible moment, so fail loudly here instead.
            raise ValueError(
                f"STREAM_HLS_PRESET={from_env!r} is not a valid x264 preset "
                f"(expected one of: {', '.join(sorted(X264_PRESETS))})"
            )
        return from_env
    return DEFAULT_PRESET


def bitrate_for_height(height: Optional[int]) -> str:
    """Bitrate for a hardware encode at the given ladder height."""
    if height:
        for limit, bitrate in _HW_BITRATE_BY_HEIGHT:
            if height >= limit:
                return bitrate
        return _HW_BITRATE_FALLBACK
    return _HW_BITRATE_FALLBACK


# ── hardware detection ───────────────────────────────────────────────────────


def _has_encoder(name: str) -> bool:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, timeout=20, check=False,
        ).stdout.decode(errors="ignore")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


def _render_nodes() -> list[str]:
    """VAAPI render nodes, most specific first."""
    try:
        return sorted(
            p for p in os.listdir("/dev/dri")
            if p.startswith("renderD")
        )
    except OSError:
        return []


def _probe_vaapi(node: str) -> bool:
    """Actually run a one-frame VAAPI encode.

    Presence of the encoder in ``-encoders`` proves nothing: on this machine the
    encoder is listed but the render node may be busy, wedged, or backed by a
    software driver. The only trustworthy signal is a successful tiny encode.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "probe.mp4")
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-vaapi_device", node,
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1:r=5",
                    "-vf", "format=nv12,hwupload",
                    "-c:v", "h264_vaapi",
                    "-f", "mp4", out,
                ],
                capture_output=True, timeout=30, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0


def _probe_qsv() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "probe.mp4")
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1:r=5",
                    "-vf", "format=nv12,hwupload=extra_hw_frames=64",
                    "-c:v", "h264_qsv",
                    "-f", "mp4", out,
                ],
                capture_output=True, timeout=30, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0


def _probe_nvenc() -> bool:
    if not shutil.which("nvidia-smi"):
        return False
    try:
        proc = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=15, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    if not _has_encoder("h264_nvenc"):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "probe.mp4")
        try:
            run = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1:r=5",
                    "-c:v", "h264_nvenc",
                    "-f", "mp4", out,
                ],
                capture_output=True, timeout=30, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return run.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0


@lru_cache(maxsize=1)
def detect_hwaccel() -> HwAccel:
    """Find a usable hardware encoder. Cached for the process lifetime.

    Order is deliberate: NVENC first (fastest and by far the least CPU-hungry),
    then VAAPI, then QSV. Anything that fails its real encode probe falls through
    to the next candidate rather than being reported as available.
    """
    if os.environ.get("STREAM_DISABLE_HWACCEL", "").strip().lower() in ("1", "true", "yes"):
        log.info("hwaccel detection disabled by STREAM_DISABLE_HWACCEL")
        return HwAccel()

    if _probe_nvenc():
        log.info("hwaccel: nvenc")
        return HwAccel("nvenc")

    for node in _render_nodes():
        full = f"/dev/dri/{node}"
        if _probe_vaapi(full):
            log.info("hwaccel: vaapi on %s", full)
            return HwAccel("vaapi", full)

    if _probe_qsv():
        log.info("hwaccel: qsv")
        return HwAccel("qsv")

    log.info("hwaccel: none (software libx264)")
    return HwAccel()


def reset_hwaccel_cache() -> None:
    """Test hook: forget the cached detection so the next call re-probes."""
    detect_hwaccel.cache_clear()


# ── filter chains ────────────────────────────────────────────────────────────


def _lanczos_scale(height: Optional[int], width: Optional[int] = None) -> list[str]:
    """Even-dimension scaling with Lanczos.

    ``-2`` keeps the derived dimension even without rounding, which is what
    yuv420p demands; a plain ``scale=720`` can produce an odd width and a
    corrupt/black frame in H.264.
    """
    if height and width:
        return ["scale={}:{}:flags=lanczos".format(_even(width), _even(height))]
    if height:
        return ["scale=-2:{}:flags=lanczos".format(height)]
    return []


def _even(value: int) -> int:
    return value if value % 2 == 0 else value - 1


#: HDR transfer functions zscale can be told to expect. A plan is only TONEMAP
#: when one of these was positively identified, so this is never a guess.
_HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}


def _tonemap_parts(plan: Plan) -> list[str]:
    """HDR → 8-bit SDR, as individual filter stages.

    ``zscale`` does the transfer-function conversion properly (PQ/ARIB-HLG are
    *display-referred* curves, so they must be linearised, tone-mapped, then
    re-encoded with the sRGB transfer). ``format=yuv420p`` on an HDR frame would
    keep the PQ values and just relabel them, producing the washed-out or
    magenta picture everyone recognises from naive HDR "conversion".

    The **input** colour space is stated explicitly. Left to itself zscale
    refuses to guess and aborts with "no path between colorspaces" whenever the
    source omits its VUI colour tags.
    """
    transfer = plan.input_transfer
    primaries = plan.input_primaries or "bt2020"
    matrix = plan.input_matrix or "bt2020nc"
    if transfer not in _HDR_TRANSFERS:
        # Unreachable via plan() (is_hdr implies one of these), but this filter
        # is also reachable directly, and a wrong transfer would silently
        # produce a plausible-looking but wrong picture.
        raise ValueError(f"refusing to tonemap: unrecognised input transfer {transfer!r}")

    return [
        # linearise from the HDR curve into linear light
        f"zscale=pin={primaries}:tin={transfer}:t=linear:npl=100",
        f"tonemap=tonemap={plan.tonemap_algorithm or 'hable'}:desat=0",
        # BT.2020/PQ (or whatever came in) → BT.709 SDR
        "zscale=p=bt709:t=bt709:m=bt709:r=tv",
        "format=yuv420p",
        *([f"scale=-2:{plan.target_height}:flags=lanczos"] if plan.target_height else []),
    ]


def _dither_parts(height: Optional[int]) -> list[str]:
    """10-bit → 8-bit without banding, as individual filter stages.

    Reducing bit depth with a plain format conversion throws away the low bits,
    which shows up as banding in gradients. ``format=yuv420p10le`` first forces
    an explicit high-precision working format so the dither has something to
    operate on.
    """
    parts = ["format=yuv420p10le", "zscale=dither=error_diffusion"]
    if height:
        parts.append(f"scale=-2:{height}:flags=lanczos")
    parts.append("format=yuv420p")
    return parts


def build_video_filter(plan: Plan, src_width: Optional[int] = None, hw: Optional[HwAccel] = None) -> list[str]:
    """The ``-vf`` chain for a plan, as argv values (empty list means no ``-vf``).

    The whole chain is joined into exactly one string here and nowhere else.
    Returning several elements would be a silent bug: the caller consumes
    ``[0]`` and would drop the rest without any error.
    """
    hw = hw or HwAccel()

    # A copy must stay bit-exact: no filters, and in particular no hardware
    # upload/download round-trip.
    if plan.video_passthrough:
        return []

    if plan.needs_tonemap:
        parts = _tonemap_parts(plan)
    elif plan.needs_dither:
        parts = _dither_parts(plan.target_height)
    elif plan.target_height:
        parts = _lanczos_scale(plan.target_height, src_width)
    else:
        parts = []

    # VAAPI/QSV encoders consume GPU frames, not system-memory frames, so the
    # filtered frames must be uploaded before them. Without this the encode
    # fails with -38/-22. NVENC is deliberately excluded: it accepts ordinary
    # yuv420p system frames, so an upload would be pure overhead.
    if hw.name in ("vaapi", "qsv"):
        parts = parts + ["format=nv12", "hwupload=extra_hw_frames=64"]

    if not parts:
        return []
    return [",".join(parts)]


# ── input args ───────────────────────────────────────────────────────────────

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36\r\n"
)


def _input_args(source: str) -> list[str]:
    args: list[str] = []
    if isinstance(source, str) and source.startswith(("http://", "https://")):
        # Several mirrors 403/404 a plain ffmpeg Lavf request but serve the
        # browser's own request happily.
        args += ["-headers", f"User-Agent: {_BROWSER_UA}"]
    args += ["-i", source]
    return args


def _hw_device_args(plan: Plan, hw: HwAccel) -> list[str]:
    """Global hardware-device initialisation, before the input.

    Deliberately **no** ``-hwaccel vaapi``: hardware decode would emit VAAPI
    frames, and every filter in this pipeline is a software filter (lanczos,
    zscale, tonemap, dither), so the frames would have to be downloaded again
    immediately. Decoding in software and uploading once for the encode is both
    simpler and measurably less work.

    ``-vaapi_device`` (an input-side option) is what makes ``hwupload`` usable
    at all; ``-init_hw_device``/``-filter_hw_device`` was tried and does not
    work with the encoder here ("Could not open encoder before EOF").
    """
    if plan.video_passthrough:
        return []
    if hw.name == "vaapi" and hw.device:
        return ["-vaapi_device", hw.device]
    if hw.name == "qsv":
        # QSV needs an explicit device too. Unverified on this host (the QSV
        # probe fails here), but it follows the documented recipe and only takes
        # effect where detect_hwaccel() actually proved the encoder works.
        return ["-init_hw_device", "qsv=qs:0", "-filter_hw_device", "qs"]
    return []


def _hw_encode_args(plan: Plan, hw: HwAccel) -> list[str]:
    """Swap libx264 for the hardware encoder when one is really available."""
    if not plan.video_passthrough and plan.video_codec == "libx264" and hw.available:
        if hw.name == "vaapi":
            return ["-c:v", "h264_vaapi"]
        if hw.name == "nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p4"]
        if hw.name == "qsv":
            return ["-c:v", "h264_qsv", "-preset", "veryfast"]
    return ["-c:v", plan.video_codec or "libx264"]


def build_plan_args(
    plan: Plan,
    source: str,
    segment_seconds: int,
    rendition_dir: str,
    segment_template: str,
    playlist_name: str = "index.m3u8",
    gop_seconds: int = 4,
    hw: Optional[HwAccel] = None,
    preset: Optional[str] = None,
    crf: Optional[str] = DEFAULT_CRF,
    vbr: Optional[str] = None,
    src_width: Optional[int] = None,
    genpts: bool = False,
) -> list[str]:
    """Full ffmpeg argv for one HLS rendition. Returns a list, never a string.

    Rate control: constant quality (``-crf``, the default) unless a caller
    passes ``vbr``, because libx264 ignores CRF/bitrate when they are combined.
    On a hardware encoder a bitrate is always used, since those encoders accept
    ``-crf`` and then quietly ignore it.
    """
    hw = hw or HwAccel()
    cmd: list[str] = ["ffmpeg", "-y", "-threads", "0"]

    if genpts:
        # Regenerate presentation timestamps. A source whose timestamps are
        # broken (or which merely starts at a non-zero dts) makes ffmpeg emit
        # "non monotonically increasing dts" and drop or duplicate packets; this
        # is the first rung of the runner's copy-failure fallback chain.
        cmd += ["-fflags", "+genpts"]

    cmd += _hw_device_args(plan, hw)
    cmd += _input_args(source)

    vf = build_video_filter(plan, src_width=src_width, hw=hw)
    if vf:
        cmd += ["-vf", vf[0]]

    cmd += _hw_encode_args(plan, hw)

    # A copy emits no rate-control flags at all: -b:v/-crf are meaningless
    # without an encoder and ffmpeg warns about the unused options.
    if not plan.video_passthrough:
        if hw.available or vbr:
            cmd += ["-b:v", vbr or bitrate_for_height(plan.target_height)]
        elif crf:
            cmd += ["-crf", crf]
        if plan.video_codec == "libx264" and not hw.available:
            cmd += ["-preset", resolve_preset(preset), "-tune", "zerolatency"]
    if plan.pix_fmt:
        cmd += ["-pix_fmt", plan.pix_fmt]

    # A copy still needs a keyframe grid so hls.js can switch rendition and
    # start at a segment boundary, so these apply to every mode.
    cmd += ["-g", str(gop_seconds * 25), "-sc_threshold", "0"]
    cmd += ["-force_key_frames", f"expr:gte(t,n_forced*{gop_seconds})"]

    if plan.audio_action == AudioAction.TRANSCODE:
        cmd += ["-c:a", "aac", "-b:a", plan.audio_bitrate]
        if plan.audio_downmix:
            cmd += ["-ac", "2"]
    else:
        cmd += ["-c:a", "copy"]

    if plan.tag_v:
        cmd += ["-tag:v", plan.tag_v]

    if plan.hls_fmp4:
        cmd += ["-hls_segment_type", "fmp4"]
        cmd += ["-hls_fmp4_init_filename", "init.mp4"]

    cmd += [
        "-f", "hls",
        "-hls_time", str(segment_seconds),
        "-hls_list_size", "0",
        "-hls_playlist_type", "event",
        "-hls_flags", "independent_segments+temp_file",
        "-hls_segment_filename", segment_template,
        playlist_name if os.path.isabs(playlist_name) else os.path.join(rendition_dir, playlist_name),
    ]
    return cmd


def build_direct_play_cmd(plan: Plan, source: str, destination: str) -> list[str]:
    """ffmpeg argv for the no-HLS faststart MP4 path."""
    cmd: list[str] = ["ffmpeg", "-y", "-threads", "0"]
    cmd += _input_args(source)
    cmd += ["-c:v", "copy", "-c:a", "copy"]
    if plan.tag_v:
        cmd += ["-tag:v", plan.tag_v]
    # +faststart is the entire point of this path: it moves the moov atom in
    # front of mdat so playback can begin before the download finishes.
    cmd += ["-movflags", "+faststart", destination]
    return cmd


def build_subtitle_cmd(source: str, stream_index: int, destination: str) -> list[str]:
    """Extract one text subtitle stream as SubRip. Bitmap streams are never sent here.

    ``destination`` must be an ``.srt`` path. The codec is pinned to ``srt`` and
    ffmpeg picks its muxer from the extension, so handing this a ``.vtt`` path
    selects the WebVTT muxer, which rejects the srt codec outright — the encode
    dies and leaves a zero-byte file behind rather than raising anything
    obvious. WebVTT conversion is a separate, deliberate step
    (``srt_to_vtt.convert_srt_to_vtt``); keep the two apart.
    """
    return ["ffmpeg", "-y", "-i", source, "-map", f"0:{stream_index}", "-c:s", "srt", destination]
