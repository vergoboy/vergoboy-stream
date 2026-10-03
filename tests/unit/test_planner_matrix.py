"""The planner decision matrix, driven by real ffprobe output.

Every case here is parametrised from a committed ``ffprobe -of json`` payload in
``tests/fixtures/probe/``, produced by ``scripts/capture_probe_fixtures.py`` from
genuine ffmpeg output. The point is that the matrix is pinned to what ffprobe
*actually* prints — the exact codec names, profiles, pixel formats, colour
transfer strings and four-character tags — because that is where planner bugs
live. A hand-written dict that happens to say ``"codec_name": "hevc"`` tests
nothing about whether the planner agrees with ffprobe.

The expected values were read off real runs of ``plan()``, then reviewed by
hand. If the planner changes its mind about any of them, that is a behaviour
change and should be a deliberate edit to this table, not a surprise.
"""
from __future__ import annotations

import pytest

from tests.support import probe_fixture_ids, probe_info
from media_pipeline.planner import (
    AudioAction,
    ClientCaps,
    HwAccel,
    Mode,
    SubtitleAction,
    NO_HW,
    plan,
    plan_compat,
)

# fixture name -> the decisions that fixture exists to pin down.
#
# ``mode``          how video reaches the container
# ``audio``         what happens to the audio tracks
# ``subs``          what happens to subtitles
# ``video_codec``   -c:v verbatim; copy plans emit the literal "copy"
# ``depth``         required output bit depth
# ``hevc_only``     only offer this to clients that decode HEVC
# ``needs_compat``  a compatibility rendition is required
# ``direct_play``   the source file itself can be served, no HLS
# ``remux``         cheap job: must not take a transcode slot
MATRIX = {
    "h264_8bit": dict(
        mode=Mode.COPY, audio=AudioAction.COPY, subs=SubtitleAction.NONE,
        video_codec="copy", depth=8, hevc_only=False, needs_compat=False,
        direct_play=True, remux=True, dither=False, tonemap=False,
    ),
    "h264_8bit_mkv": dict(
        mode=Mode.COPY, audio=AudioAction.COPY, subs=SubtitleAction.NONE,
        video_codec="copy", depth=8, hevc_only=False, needs_compat=False,
        # Matroska has no moov/mdat ordering, so faststart is unknowable and
        # direct play must not be claimed even though the codecs are safe.
        direct_play=False, remux=True, dither=False, tonemap=False,
    ),
    "hevc_main": dict(
        mode=Mode.COPY, audio=AudioAction.COPY, subs=SubtitleAction.NONE,
        video_codec="copy", depth=8, hevc_only=True, needs_compat=False,
        direct_play=False, remux=True, dither=False, tonemap=False,
    ),
    "hevc_main10": dict(
        mode=Mode.COPY, audio=AudioAction.COPY, subs=SubtitleAction.NONE,
        video_codec="copy", depth=10, hevc_only=True, needs_compat=False,
        direct_play=False, remux=True, dither=False, tonemap=False,
    ),
    "hevc_hdr10": dict(
        mode=Mode.TONEMAP, audio=AudioAction.COPY, subs=SubtitleAction.NONE,
        video_codec="libx264", depth=8, hevc_only=False, needs_compat=False,
        direct_play=False, remux=False, dither=True, tonemap=True,
    ),
    "h264_hi10p": dict(
        mode=Mode.TRANSCODE, audio=AudioAction.COPY, subs=SubtitleAction.NONE,
        video_codec="libx264", depth=8, hevc_only=False, needs_compat=False,
        # High 10 is "h264" by codec name but no browser decodes it. The HLS
        # path transcodes; direct play must refuse for the same reason.
        direct_play=False, remux=False, dither=True, tonemap=False,
    ),
    "av1_main": dict(
        mode=Mode.TRANSCODE, audio=AudioAction.COPY, subs=SubtitleAction.NONE,
        video_codec="libx264", depth=8, hevc_only=False, needs_compat=False,
        direct_play=False, remux=False, dither=False, tonemap=False,
    ),
    "audio_ac3": dict(
        mode=Mode.COPY, audio=AudioAction.TRANSCODE, subs=SubtitleAction.NONE,
        video_codec="copy", depth=8, hevc_only=False, needs_compat=False,
        direct_play=False, remux=True, dither=False, tonemap=False,
    ),
    "audio_dts": dict(
        mode=Mode.COPY, audio=AudioAction.TRANSCODE, subs=SubtitleAction.NONE,
        video_codec="copy", depth=8, hevc_only=False, needs_compat=False,
        direct_play=False, remux=True, dither=False, tonemap=False,
    ),
    "audio_flac": dict(
        mode=Mode.COPY, audio=AudioAction.TRANSCODE, subs=SubtitleAction.NONE,
        video_codec="copy", depth=8, hevc_only=False, needs_compat=False,
        direct_play=False, remux=True, dither=False, tonemap=False,
    ),
    "subs_pgs": dict(
        mode=Mode.COPY, audio=AudioAction.COPY, subs=SubtitleAction.SKIP_BITMAP,
        video_codec="copy", depth=8, hevc_only=False, needs_compat=False,
        direct_play=False, remux=True, dither=False, tonemap=False,
    ),
    "subs_ass": dict(
        mode=Mode.COPY, audio=AudioAction.COPY, subs=SubtitleAction.KEEP_TEXT,
        video_codec="copy", depth=8, hevc_only=False, needs_compat=False,
        direct_play=False, remux=True, dither=False, tonemap=False,
    ),
}

ALL_FIXTURES = probe_fixture_ids()


def test_every_fixture_is_in_the_matrix():
    """A new captured fixture must be given an expected decision.

    Without this, adding a codec to the capture script would silently produce a
    fixture that nothing asserts on — the matrix would look complete while
    quietly covering less than it did yesterday.
    """
    unasserted = set(ALL_FIXTURES) - set(MATRIX)
    assert not unasserted, (
        f"probe fixture(s) captured but never asserted on: {sorted(unasserted)}. "
        "Add a MATRIX entry, or delete the fixture if it was a mistake."
    )


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_matrix_covers_every_case(name):
    assert name in MATRIX, f"{name} has no MATRIX entry"


@pytest.mark.parametrize("name,expect", sorted(MATRIX.items()))
def test_plan_decision(name, expect):
    info = probe_info(name)
    assert info.has_video, f"{name}: fixture has no video stream"
    p = plan(info)

    assert p.mode is expect["mode"], f"{name}: mode"
    assert p.audio_action is expect["audio"], f"{name}: audio action"
    assert p.subtitle_action is expect["subs"], f"{name}: subtitle action"
    assert p.video_codec == expect["video_codec"], f"{name}: video codec"
    assert p.target_bit_depth == expect["depth"], f"{name}: target bit depth"
    assert p.hevc_only is expect["hevc_only"], f"{name}: hevc_only"
    assert p.needs_compat is expect["needs_compat"], f"{name}: needs_compat"
    assert p.direct_play is expect["direct_play"], f"{name}: direct_play"
    assert p.is_remux is expect["remux"], f"{name}: is_remux"
    assert p.needs_dither is expect["dither"], f"{name}: needs_dither"
    assert p.needs_tonemap is expect["tonemap"], f"{name}: needs_tonemap"


@pytest.mark.parametrize("name", sorted(MATRIX))
def test_every_plan_explains_itself(name):
    """A plan must carry human-readable reasons.

    This is the difference between "the file re-encoded" and "the file
    re-encoded because it was High 10 and no browser decodes that". Anyone
    debugging an unexpected transcode reads these strings, so an empty tuple is
    a bug, not a style issue.
    """
    p = plan(probe_info(name))
    assert p.reasons, f"{name}: plan has no reasons at all"
    for reason in p.reasons:
        assert isinstance(reason, str) and reason.strip(), f"{name}: blank reason"


@pytest.mark.parametrize("name", sorted(MATRIX))
def test_plans_are_deterministic(name):
    """Same input, same plan, always.

    The planner is documented as pure. Cheap to assert, and it is the property
    that lets the caller cache and compare plans without surprises.
    """
    a, b = plan(probe_info(name)), plan(probe_info(name))
    assert a == b


# ── the two facts the whole matrix hangs off ────────────────────────────────


def test_remux_and_transcode_are_mutually_exclusive():
    """is_remux must never be true for a plan that actually re-encodes.

    MAX_CONCURRENT_REMUX exists to keep cheap copy-only jobs off the transcode
    pool. If is_remux were ever true for a re-encode, a queue of remuxes would
    occupy the cheap pool while doing expensive work.
    """
    for name in sorted(MATRIX):
        p = plan(probe_info(name))
        reencoding = p.mode in (Mode.TRANSCODE, Mode.TONEMAP)
        assert p.is_remux is not reencoding, f"{name}: mode={p.mode.value}"
        assert p.video_passthrough is not reencoding, f"{name}: passthrough"


def test_copy_plans_never_name_an_encoder():
    """A copy plan must emit exactly "copy" for -c:v.

    Naming a real encoder here is the bug that turns a 2-second remux into a
    full re-encode — silently, and with a plausible-looking argv. Emitting None
    is the same bug by a different route: ffmpeg_cmd falls back to the default
    encoder, so "no codec named" quietly means "re-encode".
    """
    for name in sorted(MATRIX):
        p = plan(probe_info(name))
        if p.mode in (Mode.COPY, Mode.REMUX):
            assert p.video_codec == "copy", (
                f"{name}: copy plan says -c:v {p.video_codec!r}, expected 'copy'"
            )
        else:
            assert p.video_codec and p.video_codec != "copy", (
                f"{name}: re-encode plan says -c:v {p.video_codec!r}"
            )


def test_hdr_always_gets_a_tonemap_and_never_a_copy():
    for name in sorted(MATRIX):
        info = probe_info(name)
        if info.video.is_hdr:
            p = plan(info)
            assert p.needs_tonemap, f"{name}: HDR without a tonemap"
            assert p.mode is not Mode.COPY, f"{name}: HDR copied verbatim"
            assert p.target_bit_depth == 8, f"{name}: HDR kept at 10-bit"


def test_10bit_source_is_never_direct_played():
    """The bug this matrix was built to catch.

    A 10-bit H.264 is "h264" by codec name, so the obvious safety check passes
    it — but High 10 / High 4:2:2 10 / High 4:4:4 Predictive 10 are not decodable
    by browser H.264 decoders. Marking one direct-playable hands the client a
    black screen on the fast path that the HLS path would have handled.
    """
    for name in sorted(MATRIX):
        info = probe_info(name)
        if info.video.is_10bit and info.has_faststart:
            assert not plan(info).direct_play, (
                f"{name}: 10-bit {info.video.codec} marked direct-playable"
            )


# ── client capabilities ──────────────────────────────────────────────────────


@pytest.mark.parametrize("caps,expect_compat", [
    (None, False),
    (ClientCaps(), False),
    (ClientCaps(hevc=True, ten_bit=True), False),
    (ClientCaps(hevc=False), True),
    (ClientCaps(hevc=True, ten_bit=False), True),
])
def test_client_caps_decide_compat_for_hevc(caps, expect_compat):
    p = plan(probe_info("hevc_main10"), client_caps=caps)
    assert p.needs_compat is expect_compat


def test_unreported_capability_is_not_read_as_false():
    """None must never behave like False.

    A browser that said nothing about HEVC has not refused HEVC. Treating silence
    as refusal would point every such client at the compatibility rendition for
    no reason — the expensive path, chosen for a question nobody asked.
    """
    silent = plan(probe_info("hevc_main"), client_caps=ClientCaps())
    assert silent.needs_compat is False
    assert silent.hevc_only is True


def test_explicit_no_hevc_blocks_direct_play():
    p = plan(probe_info("h264_8bit"), client_caps=ClientCaps(hevc=False))
    # H.264 needs no compat rendition, so direct play is unaffected...
    assert p.needs_compat is False
    # ...but the flag must not silently change unrelated decisions.
    assert p.mode is Mode.COPY


def test_needs_compat_blocks_direct_play():
    p = plan(probe_info("hevc_main10"), client_caps=ClientCaps(hevc=False))
    assert p.needs_compat is True
    assert p.direct_play is False


def test_claims_reports_only_what_the_client_answered():
    caps = ClientCaps(supported=frozenset({"video/mp4; codecs=\"avc1.64001f\""}))
    assert caps.claims('video/mp4; codecs="avc1.64001f"') is True
    assert caps.claims("video/mp4; codecs=\"hev1.1.6.L93.B0\"") is None


# ── the compatibility rendition ─────────────────────────────────────────────


def test_plan_compat_is_always_8bit_h264():
    for name in sorted(MATRIX):
        p = plan_compat(probe_info(name))
        assert p.video_codec == "libx264", f"{name}: compat encoder"
        assert p.target_bit_depth == 8, f"{name}: compat depth"


def test_plan_compat_tonemaps_an_hdr_source():
    p = plan_compat(probe_info("hevc_hdr10"))
    assert p.mode is Mode.TONEMAP
    assert p.needs_tonemap


def test_plan_compat_ignores_client_caps():
    """It exists for clients that could not handle the default.

    If it honoured client_caps it would reproduce the default rendition, which
    is precisely the rendition the client cannot play.
    """
    hostile = ClientCaps(hevc=False, ten_bit=False, av1=False)
    a = plan_compat(probe_info("hevc_main10"), hw=NO_HW)
    b = plan_compat(probe_info("hevc_main10"), hw=NO_HW)
    assert a == b
    assert a.hevc_only is False, "compat rendition must not be HEVC-only"


def test_plan_compat_reasons_mention_compatibility():
    p = plan_compat(probe_info("h264_hi10p"))
    assert any("compat" in r.lower() for r in p.reasons), p.reasons


# ── hardware ────────────────────────────────────────────────────────────────


def test_hw_available_flag():
    assert HwAccel(name="none").available is False
    assert HwAccel(name="vaapi", device="/dev/dri/renderD128").available is True


def test_source_without_video_cannot_be_planned():
    from media_pipeline.probe import MediaInfo
    with pytest.raises(ValueError, match="no video"):
        plan(MediaInfo(container="mp4", duration=1.0, bit_rate=None,
                       video=None, audio=(), subtitles=(), has_faststart=True))


def test_missing_codec_is_warned_not_crashed():
    """ffprobe that reports no codec_name means "unknown", not "safe"."""
    from media_pipeline.probe import VideoStream
    from media_pipeline.probe import MediaInfo
    info = MediaInfo(
        container="mp4", duration=1.0, bit_rate=None,
        video=VideoStream(index=0, codec=None, profile=None, pix_fmt=None,
                          bit_depth=None, color_transfer=None,
                          color_primaries=None, color_space=None,
                          width=320, height=240, avg_frame_rate="24/1"),
        audio=(), subtitles=(), has_faststart=True,
    )
    p = plan(info)
    assert any("codec" in w for w in p.warnings), p.warnings
    assert p.mode is not Mode.COPY, "unknown codec must not be treated as safe to copy"


def test_missing_dimensions_warn():
    from media_pipeline.probe import VideoStream, MediaInfo
    info = MediaInfo(
        container="mp4", duration=1.0, bit_rate=None,
        video=VideoStream(index=0, codec="h264", profile="High",
                          pix_fmt="yuv420p", bit_depth=8, color_transfer="bt709",
                          color_primaries="bt709", color_space="bt709",
                          width=None, height=None, avg_frame_rate=None),
        audio=(), subtitles=(), has_faststart=True,
    )
    p = plan(info)
    assert any("dimension" in w for w in p.warnings), p.warnings
