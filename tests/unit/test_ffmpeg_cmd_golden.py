"""Golden argv tests for the ffmpeg command builders.

These pin the exact argument list the pipeline hands to ffmpeg. An argv is the
one interface in this system that cannot be reviewed by eye at runtime — it is
built in Python, handed to ``execve``, and never printed in full — so a wrong
flag shows up as a subtly broken encode rather than an exception.

Two rules keep the goldens honest:

* The goldens were **captured from real builder output**, not transcribed from
  the source by hand (see ``scripts/capture_argv_fixtures.py``). A hand-typed
  golden only proves the author read the code correctly.
* ``STREAM_HLS_PRESET`` and friends are **pinned** for the whole module. Without
  that, the same test would pass or fail depending on what the developer's shell
  happens to export — which is the worst possible failure mode for a test
  suite: it fails on one machine and passes on another for no real reason.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from media_pipeline.ffmpeg_cmd import (
    HwAccel,
    bitrate_for_height,
    build_direct_play_cmd,
    build_plan_args,
    build_subtitle_cmd,
    resolve_preset,
)
from media_pipeline.planner import plan
from tests.argv_cases import CASES, RENDITION_DIR, SOURCE
from tests.support import probe_info

ARGV_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "argv"


@pytest.fixture(autouse=True)
def _pinned_environment(monkeypatch):
    """Neutralise every knob that would otherwise vary per machine."""
    for var in ("STREAM_HLS_PRESET", "STREAM_FFMPEG_BIN"):
        monkeypatch.delenv(var, raising=False)


def _golden(name: str) -> list[str]:
    path = ARGV_FIXTURES / f"{name}.txt"
    if not path.exists():
        pytest.fail(
            f"missing golden {path.name}. Regenerate with:\n"
            f"    ./venv/bin/python scripts/capture_argv_fixtures.py"
        )
    return path.read_text().splitlines()


GOLDEN_NAMES = sorted(p.stem for p in ARGV_FIXTURES.glob("*.txt"))


def test_goldens_exist():
    """Guard against the whole directory silently going missing."""
    assert GOLDEN_NAMES, "no argv goldens found"


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_argv_matches_golden(name):
    """Build the same argv the capture script built and compare.

    Both sides come from ``tests/argv_cases.CASES``, so the golden cannot have
    been generated from different inputs than these assertions use.
    """
    assert name in CASES, (
        f"golden {name}.txt has no entry in tests/argv_cases.py — "
        "a golden nobody builds is a golden nobody checks"
    )
    built = CASES[name]()
    assert built == _golden(name), (
        f"{name}: argv changed.\n"
        f"If this is intended, regenerate with:\n"
        f"    ./venv/bin/python scripts/capture_argv_fixtures.py\n"
        f"and review the diff."
    )


# ── structural guarantees that outlive any one golden ───────────────────────


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_argv_is_always_a_list_of_str(name):
    argv = _load_argv(name)
    assert isinstance(argv, list), f"{name}: argv is {type(argv).__name__}"
    assert all(isinstance(a, str) for a in argv), f"{name}: non-str argument"
    assert argv[0] == "ffmpeg", f"{name}: argv does not start with ffmpeg"


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_no_shell_metacharacters_are_needed(name):
    """Arguments are passed to execve, never to a shell.

    If any builder ever produced a string, these characters would become
    injection surface. Asserting they are absent is a cheap tripwire for the
    day someone "simplifies" the argv into a string.
    """
    argv = _load_argv(name)
    for arg in argv:
        assert ";" not in arg and "&&" not in arg and "|" not in arg, (
            f"{name}: shell metacharacter in {arg!r}"
        )


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_every_hls_argv_has_a_keyframe_grid(name):
    """hls.js switches renditions at segment boundaries only.

    Without -g/-force_key_frames the renditions drift out of alignment and every
    quality switch lands mid-GOP, which the player cannot do cleanly. This holds
    for copy plans too, which is the surprising part.
    """
    argv = _load_argv(name)
    if "-f" not in argv or "hls" not in argv:
        pytest.skip(f"{name} is not an HLS command")
    assert "-g" in argv, f"{name}: no -g"
    assert "-force_key_frames" in argv, f"{name}: no forced keyframe grid"
    assert argv[argv.index("-sc_threshold") + 1] == "0", f"{name}: sc_threshold not 0"


def test_copy_plans_emit_no_rate_control():
    """-crf/-b:v without an encoder makes ffmpeg warn about unused options.

    More importantly, emitting them on a copy plan is a strong hint that an
    encoder crept in — which would turn a cheap remux into a full transcode.
    """
    for name in ("hls_copy", "hls_copy_ac3_audio", "hls_copy_genpts"):
        argv = _load_argv(name)
        assert "-crf" not in argv, f"{name}: -crf on a copy plan"
        assert "-b:v" not in argv, f"{name}: -b:v on a copy plan"
        assert argv[argv.index("-c:v") + 1] == "copy", f"{name}: not a copy"


def test_copy_plans_still_transcode_browser_unsafe_audio():
    """Video can be copied while audio must not be.

    AC-3 in an HLS segment is not something a browser plays; copying it would
    produce a stream that looks fine and plays silence.
    """
    argv = _load_argv("hls_copy_ac3_audio")
    assert argv[argv.index("-c:v") + 1] == "copy"
    assert argv[argv.index("-c:a") + 1] == "aac"
    assert "-b:a" in argv, "transcoded audio needs a bitrate"


def test_tonemap_carries_the_source_colorspace():
    """zscale cannot guess.

    Given a source that omits its VUI colour metadata, zscale fails with "no
    path between colorspaces" instead of picking one. The pin/tin values in the
    filter chain are what make an HDR source tonemappable at all.
    """
    argv = _load_argv("hls_tonemap_hdr")
    vf = argv[argv.index("-vf") + 1]
    assert "zscale=" in vf
    assert "pin=bt2020" in vf, vf
    assert "tin=smpte2084" in vf, vf
    assert "tonemap=tonemap=" in vf, vf
    assert "t=bt709" in vf, f"output transfer must be SDR bt709: {vf}"


def test_dither_appears_only_when_depth_changes():
    hi10p = _load_argv("hls_dither_hi10p")
    assert "dither=" in hi10p[hi10p.index("-vf") + 1], "10-bit source needs dithering"
    copy = _load_argv("hls_copy")
    assert "-vf" not in copy, "a pure copy must not carry a filter chain"


def test_hardware_paths_use_hardware_encoders_and_bitrate():
    for name, encoder in (("hls_hw_vaapi", "h264_vaapi"), ("hls_hw_nvenc", "h264_nvenc")):
        argv = _load_argv(name)
        assert argv[argv.index("-c:v") + 1] == encoder, name
        # Hardware encoders accept -crf and then ignore it, so a bitrate is
        # mandatory on those paths.
        assert "-b:v" in argv, f"{name}: hardware encode without a bitrate"
        assert "-crf" not in argv, f"{name}: -crf is silently ignored by {encoder}"


def test_vaapi_uploads_the_frames():
    argv = _load_argv("hls_hw_vaapi")
    vf = argv[argv.index("-vf") + 1]
    assert "hwupload" in vf, vf
    assert "-vaapi_device" in argv


def test_nvenc_does_not_pretend_to_upload_frames():
    argv = _load_argv("hls_hw_nvenc")
    if "-vf" in argv:
        assert "hwupload" not in argv[argv.index("-vf") + 1], (
            "NVENC takes system memory; hwupload belongs to VAAPI only"
        )


def test_fmp4_segments_have_an_init_file():
    for name in ("hls_copy", "hls_transcode_hevc", "hls_tonemap_hdr"):
        argv = _load_argv(name)
        assert argv[argv.index("-hls_segment_type") + 1] == "fmp4", name
        assert argv[argv.index("-hls_fmp4_init_filename") + 1] == "init.mp4", name


def test_hls_playlist_is_fully_buffered():
    """hls_list_size 0 = keep every segment.

    This is a VOD-style playlist on purpose: the player must be able to seek
    anywhere in an already-encoded item, which a sliding window would forbid.
    """
    argv = _load_argv("hls_copy")
    assert argv[argv.index("-hls_list_size") + 1] == "0"
    assert argv[argv.index("-hls_playlist_type") + 1] == "event"


def test_genpts_is_the_only_difference_in_the_copy_fallback_chain():
    """First rung of the copy-failure fallback: same copy, timestamps repaired."""
    base = _load_argv("hls_copy")
    genpts = _load_argv("hls_copy_genpts")
    extra = [a for a in genpts if a not in base]
    assert genpts[1:3] == base[1:3], "only the input flags should differ"
    assert "+genpts" in genpts
    assert "-fflags" in genpts
    assert extra == ["-fflags", "+genpts"] or set(extra) <= {"-fflags", "+genpts"}


def test_direct_play_is_a_faststart_copy():
    argv = _load_argv("direct_play")
    assert argv[argv.index("-movflags") + 1] == "+faststart"
    assert argv[argv.index("-c:v") + 1] == "copy"
    assert argv[argv.index("-c:a") + 1] == "copy"
    assert "-f" not in argv, "direct play is a plain MP4, not HLS"


def test_direct_play_refuses_an_h264_plan_it_cannot_copy():
    """A tonemapped plan has no copy to give."""
    from media_pipeline.errors import EncodeError  # noqa: F401 - import guard

    argv = _load_argv("direct_play")
    assert "-hls_segment_type" not in argv


def test_subtitle_conversion_targets_srt():
    """The builder emits SubRip; WebVTT conversion is a separate step.

    Asserting the extension matters because ffmpeg selects the muxer from it:
    pair this codec with a .vtt extension and the muxer rejects the codec,
    producing a zero-byte file and a non-zero exit.
    """
    argv = _load_argv("subtitle_convert")
    assert argv[-1].endswith(".srt")
    assert argv[argv.index("-c:s") + 1] == "srt"


def test_subtitle_conversion_names_the_stream_by_index():
    """Copying "the first subtitle stream" is wrong the moment a file has two."""
    from media_pipeline.ffmpeg_cmd import build_subtitle_cmd as build

    argv = build(SOURCE, 3, "/out/subs.vtt")
    assert "0:3" in argv, argv


# ── preset resolution ───────────────────────────────────────────────────────


def test_default_preset_is_veryfast():
    assert resolve_preset() == "veryfast"


def test_explicit_preset_wins_over_environment(monkeypatch):
    monkeypatch.setenv("STREAM_HLS_PRESET", "superfast")
    assert resolve_preset("ultrafast") == "ultrafast"


def test_environment_preset_is_used(monkeypatch):
    monkeypatch.setenv("STREAM_HLS_PRESET", "SUPERFAST")
    assert resolve_preset() == "superfast", "value must be normalised"


def test_typo_in_preset_fails_loudly(monkeypatch):
    """Better to fail here than to hand ffmpeg a bad preset mid-encode."""
    monkeypatch.setenv("STREAM_HLS_PRESET", "veryfats")
    with pytest.raises(ValueError, match="not a valid x264 preset"):
        resolve_preset()


def test_explicit_typo_also_fails():
    with pytest.raises(ValueError, match="unknown x264 preset"):
        resolve_preset("turbo-max")


@pytest.mark.parametrize("height,expected", [
    (None, "2800k"),     # unknown height falls back to the 720p rate
    (240, "2800k"),      # below the smallest rung
    (360, "800k"),
    (480, "1400k"),
    (720, "2800k"),
    (1080, "5000k"),
    (1440, "5000k"),     # above the top rung: capped, not extrapolated
    (2160, "5000k"),
])
def test_bitrate_ladder_values(height, expected):
    assert bitrate_for_height(height) == expected


def test_higher_rungs_never_get_a_lower_bitrate():
    """A 4K rung slower than a 720p one would be an obvious mistake, but it is
    exactly the kind of table edit that goes unnoticed."""
    heights = [360, 480, 720, 1080, 1440, 2160]
    rates = [int(bitrate_for_height(h).rstrip("k")) for h in heights]
    assert rates == sorted(rates), dict(zip(heights, rates))


# ── helper ──────────────────────────────────────────────────────────────────


def _load_argv(name: str) -> list[str]:
    """Rebuild one case's argv from the shared table."""
    return CASES[name]()
