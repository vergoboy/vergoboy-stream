"""Typed ffprobe layer.

Everything the planner needs to make a decision is extracted here exactly once,
into frozen dataclasses, so `planner.plan()` stays a pure function over data and
never has to re-parse ffprobe's JSON shape.

Design rules that matter:
  * Nothing here guesses. A field ffprobe did not report stays ``None`` rather
    than being defaulted to a plausible-looking value.
  * ``has_faststart`` is tri-state: ``True``/``False`` when we could actually
    read the container's atom order, ``None`` when the source is remote and
    therefore not seekable. Callers must treat ``None`` as "unknown", not False.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

from .errors import ProbeError
from archive_network import direct_media_environment

log = logging.getLogger(__name__)

# ffprobe hard timeout. A remote URL that stalls must not wedge the caller.
PROBE_TIMEOUT_S = 30

# ── codec classification ─────────────────────────────────────────────────────

#: Codecs a browser can play back directly from an MP4/HLS stream without a
#: transcode. Kept deliberately narrow — this is the list that decides whether
#: a "direct play" (no HLS) faststart remux is offered.
BROWSER_SAFE_VIDEO = frozenset({"h264"})
BROWSER_SAFE_AUDIO = frozenset({"aac", "mp3"})

#: H.264/H.265 profiles. Anything outside this set for H.264 is treated as
#: unknown and therefore transcoded.
H264_BASELINE_PROFILES = frozenset({"baseline", "constrained baseline", "main"})
H264_HIGH_PROFILES = frozenset({"high", "progressive high"})
H264_10BIT_PROFILES = frozenset({"high 10", "high 4:2:2 10", "high 4:4:4 predictive 10"})

#: Subtitle codecs ffmpeg can hand to the client as text. ASS/SSA are kept
#: verbatim (styled, client-renderable); everything else is converted to SRT.
TEXT_SUBTITLE_CODECS = frozenset(
    {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text", "microdvd", "stl", "subviewer"}
)
#: Bitmap subtitle codecs. These are pictures, not text: there is nothing to
#: extract and they must never be burned into the picture. They are reported so
#: the planner can emit a WARNING.
BITMAP_SUBTITLE_CODECS = frozenset(
    {"hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvdsub", "vobsub", "dvb_subtitle", "dvbsub", "xsub"}
)

#: Colour transfer values that mean "high dynamic range".
HDR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67"})

#: Audio codecs worth remuxing untouched; everything else becomes AAC.
AUDIO_COPY_CODECS = frozenset({"aac", "mp3"})


def _s(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text.lower() or None


def _i(value: Any) -> Optional[int]:
    try:
        if value is None or value == "N/A":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _f(value: Any) -> Optional[float]:
    try:
        if value is None or value == "N/A":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# ── dataclasses ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VideoStream:
    """One video stream, normalised."""

    index: int
    codec: Optional[str]
    profile: Optional[str]
    pix_fmt: Optional[str]
    bit_depth: Optional[int]
    color_transfer: Optional[str]
    color_primaries: Optional[str]
    color_space: Optional[str]
    width: Optional[int]
    height: Optional[int]
    avg_frame_rate: Optional[str]
    #: Four-character container tag, e.g. "avc1" / "hvc1". This is what Safari
    #: keys off, and it is the only way to confirm that ``-tag:v hvc1`` really
    #: landed in the output.
    codec_tag: Optional[str] = None
    #: True only when colour metadata positively identifies an HDR transfer.
    is_hdr: bool = False

    @property
    def is_h264(self) -> bool:
        return self.codec == "h264"

    @property
    def is_hevc(self) -> bool:
        return self.codec in ("hevc", "h265")

    @property
    def is_10bit(self) -> bool:
        """True when either the profile or the pixel format says 10-bit.

        ffprobe reports bit depth only for some pixel formats; `p010le` and
        `yuv420p10le` are unambiguous, so they are treated as authoritative.
        """
        if self.pix_fmt in ("p010le", "p010be", "yuv420p10le", "yuv422p10le", "yuv444p10le"):
            return True
        if self.bit_depth is not None and self.bit_depth >= 10:
            return True
        return bool(self.profile and "10" in self.profile)


@dataclass(frozen=True)
class AudioStream:
    """One audio stream, normalised."""

    index: int
    codec: Optional[str]
    channels: Optional[int]
    channel_layout: Optional[str]
    sample_rate: Optional[int]
    bit_rate: Optional[int]

    @property
    def is_stereo_or_less(self) -> bool:
        return self.channels is not None and self.channels <= 2


@dataclass(frozen=True)
class SubtitleStream:
    """One subtitle stream, normalised.

    ``is_bitmap`` drives the "skip with a warning, never burn in" rule; ``is_ass``
    lets the pipeline keep the styled source for client-side rendering instead of
    flattening it to SRT.
    """

    index: int
    codec: Optional[str]
    lang: Optional[str]
    title: Optional[str]
    is_bitmap: bool
    is_text: bool

    @property
    def is_ass(self) -> bool:
        return self.codec in ("ass", "ssa")


@dataclass(frozen=True)
class MediaInfo:
    """Everything the planner is allowed to see."""

    container: Optional[str]
    duration: Optional[float]
    bit_rate: Optional[int]
    video: Optional[VideoStream]
    audio: tuple[AudioStream, ...]
    subtitles: tuple[SubtitleStream, ...]
    #: None means "could not be determined" (non-seekable remote source).
    has_faststart: Optional[bool]
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    # -- convenience used all over the planner --------------------------------
    @property
    def text_subtitles(self) -> tuple[SubtitleStream, ...]:
        return tuple(s for s in self.subtitles if s.is_text)

    @property
    def bitmap_subtitles(self) -> tuple[SubtitleStream, ...]:
        return tuple(s for s in self.subtitles if s.is_bitmap)

    @property
    def has_video(self) -> bool:
        return self.video is not None




# ── faststart detection ──────────────────────────────────────────────────────


def _detect_faststart(path: str) -> Optional[bool]:
    """True when the MP4/MOV ``moov`` atom precedes ``mdat``.

    A file without faststart still *plays* everywhere once fully downloaded,
    but browsers and hls.js want to start playing before the whole body has
    arrived, so faststart is what makes direct play viable. Returns None when
    the answer is unknowable (not a seekable file, or not an MP4-family box).
    """
    try:
        with open(path, "rb") as fh:
            # Walk top-level atoms. Sizes are big-endian; a size of 1 means the
            # real 64-bit size follows in the next 8 bytes.
            for _ in range(64):
                header = fh.read(8)
                if len(header) < 8:
                    break
                size = struct.unpack(">I", header[:4])[0]
                atom = header[4:8]
                if atom == b"moov":
                    return True
                if atom == b"mdat":
                    return False
                # Bytes already consumed by this atom's header. With an
                # extended size we have read 16, not 8 — skipping size-8 from
                # here would overshoot the atom by 8 bytes and desynchronise
                # the walk.
                consumed = 8
                if size == 1:
                    ext = fh.read(8)
                    if len(ext) < 8:
                        break
                    size = struct.unpack(">Q", ext)[0]
                    consumed = 16
                elif size == 0:
                    # "extends to end of file" — nothing useful can follow.
                    break
                if size < consumed:
                    break
                fh.seek(size - consumed, os.SEEK_CUR)
            return None
    except OSError:
        return None


#: Containers that actually have an atom structure with a movable ``moov``.
#: ffprobe reports comma-joined lists, e.g. "mov,mp4,m4a,3gp,3g2,mj2".
MP4_FAMILY = frozenset({"mov", "mp4", "m4a", "m4v", "3gp", "3g2", "mj2"})


def _is_mp4_family(container: Optional[str]) -> bool:
    """Whether ``faststart`` is even a meaningful question for this container."""
    if not container:
        return False
    return bool(set(container.lower().split(",")) & MP4_FAMILY)


# ── ffprobe ──────────────────────────────────────────────────────────────────

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36\r\n"
)


def probe(source: str, timeout: int = PROBE_TIMEOUT_S) -> MediaInfo:
    """Run ffprobe and return a typed :class:`MediaInfo`.

    Raises :class:`ProbeError` when the source cannot be described — callers
    should treat that as "unknown media", never as "safe to copy".
    """
    args = [
        "ffprobe", "-v", "error",
        "-of", "json",
        "-show_streams", "-show_format",
        "-rw_timeout", str(timeout * 1_000_000),
    ]
    if isinstance(source, str) and source.startswith(("http://", "https://")):
        args += ["-headers", f"User-Agent: {_BROWSER_UA}"]
    args.append(source)

    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, check=False,
                              env=direct_media_environment())
    except FileNotFoundError as exc:
        raise ProbeError("ffprobe is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out after {timeout}s") from exc

    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="ignore").strip().splitlines()
        raise ProbeError(detail[-1] if detail else f"ffprobe exit code {proc.returncode}")

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned unparseable JSON") from exc

    return parse_probe(data, source=source)


#: Sentinel for "work the faststart answer out from ``source`` yourself".
#: A plain ``None`` cannot be used for this because ``None`` is a meaningful
#: answer here — "unknowable" — and the caller must be able to say it.
_FASTSTART_UNSET: Any = object()


def parse_probe(
    data: dict,
    source: Optional[str] = None,
    *,
    has_faststart: Any = _FASTSTART_UNSET,
) -> MediaInfo:
    """Build :class:`MediaInfo` from an already-parsed ffprobe payload.

    Split out from :func:`probe` so it can be exercised against captured JSON in
    tests without spawning ffprobe.

    ``has_faststart`` overrides the one answer that would otherwise require
    touching the filesystem. By default it is detected from ``source`` exactly
    as before; pass it explicitly (including ``None`` for "unknowable") when the
    answer is already known — a caller holding a cached ffprobe payload, or a
    test replaying a captured fixture, should not have to keep the original file
    around just to learn where its ``moov`` atom sat.
    """
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    tags = fmt.get("tags") or {}

    video: Optional[VideoStream] = None
    audio: list[AudioStream] = []
    subs: list[SubtitleStream] = []

    for stream in streams:
        kind = stream.get("codec_type")
        index = _i(stream.get("index")) or 0
        stags = stream.get("tags") or {}

        if kind == "video" and video is None:
            # Cover art in an MP3 is a video stream we must not treat as the
            # movie. ffprobe tags it attached_pic.
            if _i(stags.get("attached_pic")) == 1:
                continue
            transfer = _s(stream.get("color_transfer"))
            video = VideoStream(
                index=index,
                codec=_s(stream.get("codec_name")),
                profile=_s(stream.get("profile")),
                pix_fmt=_s(stream.get("pix_fmt")),
                bit_depth=_i(stream.get("bits_per_raw_sample")),
                color_transfer=transfer,
                color_primaries=_s(stream.get("color_primaries")),
                color_space=_s(stream.get("color_space")),
                width=_i(stream.get("width")),
                height=_i(stream.get("height")),
                avg_frame_rate=_s(stream.get("avg_frame_rate")),
                codec_tag=_s(stream.get("codec_tag_string")),
                is_hdr=bool(transfer and transfer in HDR_TRANSFERS),
            )
        elif kind == "audio":
            audio.append(AudioStream(
                index=index,
                codec=_s(stream.get("codec_name")),
                channels=_i(stream.get("channels")),
                channel_layout=_s(stream.get("channel_layout")),
                sample_rate=_i(stream.get("sample_rate")),
                bit_rate=_i(stream.get("bit_rate")),
            ))
        elif kind == "subtitle":
            codec = _s(stream.get("codec_name"))
            subs.append(SubtitleStream(
                index=index,
                codec=codec,
                lang=_s(stags.get("language")),
                title=stags.get("title") or None,
                is_bitmap=bool(codec and codec in BITMAP_SUBTITLE_CODECS),
                is_text=bool(codec and codec in TEXT_SUBTITLE_CODECS),
            ))

    container = _s(fmt.get("format_name"))
    duration = _f(fmt.get("duration"))
    if duration is None or duration <= 0:
        # Some formats only carry per-stream durations.
        for stream in streams:
            duration = _f(stream.get("duration"))
            if duration and duration > 0:
                break

    # Faststart is an MP4-atom property, so it is only asked when both the
    # source is a seekable local file AND the container has atoms at all. A
    # remote URL, or an MKV/WebM, yields None ("unknown") rather than a guess —
    # claiming faststart for a file that does not have it would advertise
    # direct play for something a browser cannot start playing early.
    if has_faststart is not _FASTSTART_UNSET:
        faststart: Optional[bool] = has_faststart
    else:
        faststart = None
        if source and os.path.isfile(source) and _is_mp4_family(container):
            faststart = _detect_faststart(source)

    return MediaInfo(
        container=container,
        duration=duration,
        bit_rate=_i(fmt.get("bit_rate")),
        video=video,
        audio=tuple(audio),
        subtitles=tuple(subs),
        has_faststart=faststart,
        raw=data,
    )
