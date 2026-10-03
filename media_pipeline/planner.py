"""The pure decision matrix.

``plan()`` takes a :class:`~media_pipeline.probe.MediaInfo` and returns a
:class:`Plan`. It is deliberately free of I/O, config lookups, and ffmpeg argv
construction so the whole decision table can be read — and tested — as data.

The ordering of the rules is the substance of this module, so read it top to
bottom before changing anything:

1. No video stream at all → nothing we can build a ladder from.
2. HDR that must land on an 8-bit SDR ladder → **tonemap**. This is checked
   before the generic "is it browser safe" test on purpose: an HDR H.264 source
   *is* H.264, so a naive codec check would happily stream it and every SDR
   client would see blown-out magenta. A plain ``format=yuv420p`` is never the
   answer for HDR — it reinterprets the values, it does not compress them.
3. Codec in the browser-safe set → copy/remux.
4. Anything else → transcode.

Only after the video decision is made does audio get decided, because a copy
of the video can still require an audio transcode (and vice versa).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .probe import (
    AUDIO_COPY_CODECS,
    BROWSER_SAFE_AUDIO,
    BROWSER_SAFE_VIDEO,
    H264_10BIT_PROFILES,
    H264_BASELINE_PROFILES,
    H264_HIGH_PROFILES,
    AudioStream,
    MediaInfo,
    SubtitleStream,
    VideoStream,
)


class Mode(str, Enum):
    """How the video track gets into the output container."""

    COPY = "copy"
    REMUX = "remux"
    TRANSCODE = "transcode"
    TONEMAP = "tonemap"


class AudioAction(str, Enum):
    COPY = "copy"
    TRANSCODE = "transcode"


class SubtitleAction(str, Enum):
    """Bitmap subtitles are never burned in; text ones are kept."""

    KEEP_TEXT = "keep_text"
    SKIP_BITMAP = "skip_bitmap"
    NONE = "none"


#: Tonemapping algorithms, best first. Chosen at plan time so the ffmpeg layer
#: only has to substitute the algorithm name.
TONEMAP_ALGORITHMS = ("hable", "mobius", "reinhard", "bt2390")


@dataclass(frozen=True)
class ClientCaps:
    """What the browser said it can decode.

    All fields are optional and all default to "unknown". ``None`` must never be
    read as ``False``: an unreported capability is not a missing capability.
    """

    #: Can the client decode HEVC (hvc1) in HLS?
    hevc: Optional[bool] = None
    #: Can the client decode AV1 in HLS?
    av1: Optional[bool] = None
    #: Can the client decode 10-bit video?
    ten_bit: Optional[bool] = None
    #: Content types the client explicitly claimed support for, e.g. the keys
    #: returned by MediaCapabilities.decodingInfo().contentType.
    supported: frozenset[str] = field(default_factory=frozenset)

    def claims(self, content_type: str) -> Optional[bool]:
        """True/False if the client answered about ``content_type``, else None."""
        if content_type in self.supported:
            return True
        return None


@dataclass(frozen=True)
class HwAccel:
    """Which hardware path to use, decided once at boot."""

    #: "nvenc" | "vaapi" | "qsv" | "none"
    name: str = "none"
    #: Render node for VAAPI, e.g. /dev/dri/renderD128.
    device: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.name != "none"


NO_HW = HwAccel()


@dataclass(frozen=True)
class Plan:
    """The full decision. Consumed by :mod:`media_pipeline.ffmpeg_cmd`."""

    mode: Mode
    audio_action: AudioAction
    subtitle_action: SubtitleAction

    #: Output video codec name for ffmpeg (-c:v). None in COPY mode.
    video_codec: Optional[str]
    #: Output pixel format (-pix_fmt). None in COPY mode.
    pix_fmt: Optional[str]
    #: Force an mp4-style codec tag (-tag:v). Needed so Apple/Safari will play
    #: HEVC out of an fMP4/TS segment instead of sniffing hev1.
    tag_v: Optional[str]

    #: Required output bit depth. 8 or 10. Drives the scale/dither filter.
    target_bit_depth: int
    #: True when the source depth differs from the target, so the filter chain
    #: must dither rather than truncate.
    needs_dither: bool
    #: True when a tonemap chain must be inserted.
    needs_tonemap: bool
    tonemap_algorithm: Optional[str]

    #: Target ladder height in pixels; None means "keep the source size".
    target_height: Optional[int]
    #: Copy audio/video streams to stereo instead of preserving channel layout.
    audio_downmix: bool
    #: Audio bitrate target when transcoding.
    audio_bitrate: str

    #: Produce fMP4 HLS segments (-hls_segment_type fmp4).
    hls_fmp4: bool
    #: This item can be served as a faststart MP4 with no HLS at all.
    direct_play: bool
    #: Only offer this output to clients that reported HEVC support.
    hevc_only: bool
    #: A compatibility rendition (8-bit H.264) is meaningful for this source.
    needs_compat: bool

    #: Input colour description. This has to travel with the Plan because
    #: zscale cannot work out a conversion path from a source that omits its
    #: VUI colour metadata — it fails with "no path between colorspaces"
    #: instead of guessing. Any field may be None when ffprobe did not report it.
    input_primaries: Optional[str] = None
    input_transfer: Optional[str] = None
    input_matrix: Optional[str] = None

    #: Human-readable reasons, surfaced in logs and in the item's error/warning
    #: surface so nobody has to guess why a file was re-encoded.
    reasons: tuple[str, ...] = ()
    #: Non-fatal problems (skipped bitmap subtitles, unknown codecs).
    warnings: tuple[str, ...] = ()

    @property
    def is_remux(self) -> bool:
        """Cheap job: no re-encoding, so it must not take a transcode slot."""
        return self.mode in (Mode.COPY, Mode.REMUX)

    @property
    def video_passthrough(self) -> bool:
        return self.mode in (Mode.COPY, Mode.REMUX)


def _video_decision(
    video: VideoStream, target_height: Optional[int], hw: HwAccel, caps: Optional[ClientCaps]
) -> tuple[Mode, str, Optional[str], Optional[str], int, bool, bool, Optional[str], bool, tuple[str, ...]]:
    """Returns (mode, codec, pix_fmt, tag_v, depth, dither, tonemap, algorithm, hevc_only, reasons)."""
    reasons: list[str] = []
    codec = (video.codec or "").lower()
    profile = (video.profile or "").lower()
    target_depth = 8

    # ── HDR going out to an 8-bit SDR ladder ────────────────────────────────
    # The ladder is always 8-bit H.264, so any HDR source needs a real
    # tone-mapping curve. Order matters: this must be tested before the codec
    # checks below, because HDR H.264 looks "browser safe" by codec alone.
    if video.is_hdr:
        return (
            Mode.TONEMAP,
            "libx264",
            "yuv420p",
            None,
            8,
            True,
            True,
            TONEMAP_ALGORITHMS[0],
            False,
            tuple(reasons) + (
                f"HDR source (color_transfer={video.color_transfer}) needs an 8-bit SDR ladder; "
                "tonemapping rather than a plain format conversion",
            ),
        )

    # ── H.264 8-bit: the copy fast path ────────────────────────────────────
    if codec == "h264" and not video.is_10bit:
        if profile and profile not in (H264_BASELINE_PROFILES | H264_HIGH_PROFILES):
            reasons.append(f"unrecognised H.264 profile {video.profile!r}; transcoding to be safe")
        else:
            reasons.append(f"H.264 {profile or 'unknown profile'} 8-bit — video copy")
            return Mode.COPY, "copy", None, None, 8, False, False, None, False, tuple(reasons)

    # ── HEVC 8-bit AND 10-bit: copy, tag hvc1, HEVC-only exposure ──────────
    # 10-bit HEVC is still a copy. The ladder carries the codec tag and the
    # 10-bit pixel format survives untouched; a client that cannot decode 10-bit
    # gets the on-demand compatibility rendition instead, which is cheaper for
    # the server than transcoding everything for everybody.
    if video.is_hevc:
        # hvc1 rather than hev1: Safari will only play the mp4-style tag, and
        # hls.js on Chromium needs the same tag to attach a decoder.
        return (
            Mode.COPY,
            "copy",
            None,
            "hvc1",
            10 if video.is_10bit else 8,
            False,
            False,
            None,
            True,
            tuple(reasons + [
                f"HEVC {'10-bit' if video.is_10bit else '8-bit'} — video copy with -tag:v hvc1, "
                "offered only to HEVC-capable clients"
            ]),
        )

    # ── H.264 Hi10P / 10-bit H.264: dither down to 8-bit H.264 ────────────
    if codec == "h264" and video.is_10bit:
        reasons.append(f"H.264 10-bit ({profile or 'unknown profile'}) — dithering to 8-bit H.264 yuv420p")
        return (
            Mode.TRANSCODE,
            "libx264",
            "yuv420p",
            None,
            8,
            True,
            False,
            None,
            False,
            tuple(reasons),
        )

    # ── Everything else ────────────────────────────────────────────────────
    label = codec or "unknown"
    if codec == "h264" and profile in H264_10BIT_PROFILES:
        label = f"h264 {profile}"
    if video.pix_fmt and video.pix_fmt.startswith("yuv444"):
        label = f"{codec} {video.pix_fmt}"
    reasons.append(f"{label} is not in the browser-safe copy set — transcoding to H.264 yuv420p 8-bit")
    del hw  # software path; hardware encoders never change the codec decision
    return (
        Mode.TRANSCODE,
        "libx264",
        "yuv420p",
        None,
        8,
        False,
        False,
        None,
        False,
        tuple(reasons),
    )


def _audio_decision(audio: tuple[AudioStream, ...]) -> tuple[AudioAction, bool, str, tuple[str, ...]]:
    """AAC/MP3 copy; everything else becomes AAC 160k stereo."""
    reasons: list[str] = []
    if not audio:
        reasons.append("no audio stream")
        return AudioAction.COPY, False, "160k", tuple(reasons)

    first = audio[0]
    codec = (first.codec or "").lower()
    if len(audio) > 1:
        reasons.append(f"{len(audio)} audio streams; only the first is carried")

    if codec in AUDIO_COPY_CODECS:
        reasons.append(f"audio {codec} — audio copy")
        return AudioAction.COPY, False, "160k", tuple(reasons)

    downmix = not first.is_stereo_or_less
    reasons.append(f"audio {codec or 'unknown'} is not browser-safe — transcoding to AAC 160k")
    if downmix:
        reasons.append(f"downmixing {first.channels} channels to stereo")
    return AudioAction.TRANSCODE, downmix, "160k", tuple(reasons)


def _subtitle_decision(subs: tuple[SubtitleStream, ...]) -> tuple[SubtitleAction, tuple[str, ...]]:
    warnings: list[str] = []
    bitmap = [s for s in subs if s.is_bitmap]
    text = [s for s in subs if s.is_text]

    for s in bitmap:
        warnings.append(
            f"subtitle stream {s.index} ({s.codec}) is a bitmap format (PGS/VobSub) — "
            "skipped, never burned into the picture"
        )
    if bitmap and text:
        warnings.append(
            f"kept {len(text)} text subtitle stream(s) alongside skipped bitmap subtitles; "
            "styled ASS/SSA sources are preserved for client-side rendering"
        )
    elif text and any(s.is_ass for s in text):
        warnings.append("ASS/SSA subtitle kept as-is for client-side styling rather than flattened to SRT")

    if not text:
        return (SubtitleAction.SKIP_BITMAP if bitmap else SubtitleAction.NONE), tuple(warnings)
    return SubtitleAction.KEEP_TEXT, tuple(warnings)


def plan(
    info: MediaInfo,
    client_caps: Optional[ClientCaps] = None,
    hw: HwAccel = NO_HW,
    target_height: Optional[int] = None,
) -> Plan:
    """Decide how to turn ``info`` into something every browser can play.

    Pure: no filesystem, no subprocess, no clock. Same inputs → same Plan.
    """
    warnings: list[str] = []
    reasons: list[str] = []

    if not info.has_video or info.video is None:
        raise ValueError("source has no video stream — nothing to plan")

    video = info.video
    if video.codec is None:
        warnings.append("ffprobe did not report a video codec; assuming it is not browser-safe")

    (mode, v_codec, pix_fmt, tag_v, depth, dither, tonemap, algo,
     hevc_only, v_reasons) = _video_decision(video, target_height, hw, client_caps)
    reasons.extend(v_reasons)

    if not video.width or not video.height:
        warnings.append("source dimensions unknown; the ladder will fall back to its default size")

    # HEVC output is only useful if somebody can decode it. With no client
    # capabilities at all we keep it (the master playlist carries the codec tag
    # and hls.js will simply refuse it) — but if the client told us it cannot,
    # this source needs the compatibility rendition.
    needs_compat = False
    if hevc_only and client_caps is not None:
        if client_caps.hevc is False:
            needs_compat = True
            reasons.append("client reported no HEVC support — a compatibility rendition is required")
        elif client_caps.ten_bit is False and video.is_10bit:
            needs_compat = True
            reasons.append("client reported no 10-bit support — a compatibility rendition is required")

    audio_action, downmix, abr, a_reasons = _audio_decision(info.audio)
    reasons.extend(a_reasons)

    subtitle_action, s_warnings = _subtitle_decision(info.subtitles)
    warnings.extend(s_warnings)

    # Direct play: a faststart MP4 whose video and audio are both browser-safe
    # can bypass HLS entirely. Anything unknown is excluded — serving an
    # unplayable file directly is worse than paying for segments.
    video_safe = (video.codec or "") in BROWSER_SAFE_VIDEO and not video.is_hdr
    audio_safe = all((a.codec or "") in BROWSER_SAFE_AUDIO for a in info.audio) if info.audio else True
    direct_play = bool(
        video_safe and audio_safe and info.has_faststart and not needs_compat
    )
    if direct_play:
        reasons.append("browser-safe codecs with faststart — eligible for direct play without HLS")

    return Plan(
        mode=mode,
        audio_action=audio_action,
        subtitle_action=subtitle_action,
        video_codec=v_codec,
        pix_fmt=pix_fmt,
        tag_v=tag_v,
        target_bit_depth=depth,
        needs_dither=dither,
        needs_tonemap=tonemap,
        tonemap_algorithm=algo,
        target_height=target_height,
        audio_downmix=downmix,
        audio_bitrate=abr,
        hls_fmp4=True,
        direct_play=direct_play,
        hevc_only=hevc_only,
        needs_compat=needs_compat,
        input_primaries=video.color_primaries,
        input_transfer=video.color_transfer,
        input_matrix=video.color_space,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def plan_compat(info: MediaInfo, hw: HwAccel = NO_HW) -> Plan:
    """The on-demand 8-bit H.264 compatibility rendition.

    Always built from the ORIGINAL source (the caller is responsible for
    passing the original file, never another rendition — re-encoding a lossy
    rendition compounds its artefacts and a 10-bit HEVC intermediate can never
    be recovered from an 8-bit one).

    This deliberately ignores ``client_caps``: it exists precisely for clients
    that could not handle the default.
    """
    base = plan(info, client_caps=None, hw=hw, target_height=None)
    reasons = base.reasons + ("compatibility rendition: forced 8-bit H.264 for clients that cannot decode the default",)
    return Plan(
        mode=Mode.TONEMAP if base.needs_tonemap else Mode.TRANSCODE,
        audio_action=AudioAction.TRANSCODE,
        subtitle_action=base.subtitle_action,
        video_codec="libx264",
        pix_fmt="yuv420p",
        tag_v=None,
        target_bit_depth=8,
        needs_dither=True,
        needs_tonemap=base.needs_tonemap,
        tonemap_algorithm=base.tonemap_algorithm,
        target_height=base.target_height,
        audio_downmix=True,
        audio_bitrate="128k",
        hls_fmp4=True,
        direct_play=False,
        hevc_only=False,
        needs_compat=False,
        input_primaries=base.input_primaries,
        input_transfer=base.input_transfer,
        input_matrix=base.input_matrix,
        reasons=reasons,
        warnings=base.warnings,
    )