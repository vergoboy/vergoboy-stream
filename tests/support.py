"""Shared test helper *functions*.

Two rules this whole suite is built on:

1. **Never touch the real data directory.** The developer's server keeps its
   uploads and room state under ``media/`` and ``data/``. A test that writes
   there corrupts a working install and is very hard to notice, so every
   fixture that needs media builds it under ``tmp_path``.

2. **Never assume ffmpeg exists.** The unit suites must run on a machine with
   no ffmpeg at all (a CI lint job, a contributor's laptop before they install
   it). Anything that spawns a real process is marked ``integration`` and skips
   itself via :func:`require_ffmpeg`.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"

REPO_ROOT = TESTS_DIR.parent


def require_ffmpeg(*encoders: str) -> None:
    """Skip unless ffmpeg is present *and* exposes every named encoder.

    Checking the encoder too matters: a perfectly good ffmpeg without libx265
    would otherwise fail the HEVC fixtures with a confusing encoder error
    instead of an honest skip saying which encoder is missing.
    """
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not on PATH")
    if not encoders:
        return
    listed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True, check=False,
    ).stdout
    missing = [e for e in encoders if not _encoder_listed(listed, e)]
    if missing:
        pytest.skip(f"ffmpeg lacks required encoder(s): {', '.join(missing)}")


def _encoder_listed(encoders_output: str, name: str) -> bool:
    for line in encoders_output.splitlines():
        parts = line.split()
        # Format: " V....D libx264   H.264 ..." — flags then name then description.
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False
