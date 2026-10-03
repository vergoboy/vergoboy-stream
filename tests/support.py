"""Shared test helper *functions*.

Two rules this whole suite is built on:

1. **Never touch the real data directory.** The developer's server keeps its
   uploads, HLS output and room state under ``media/`` and ``data/``. A test
   that writes there corrupts a working install and is very hard to notice, so
   every fixture that needs media builds it under ``tmp_path``.

2. **Never assume ffmpeg exists.** The unit suites must run on a machine with
   no ffmpeg at all (a CI lint job, a contributor's laptop before they install
   it). Anything that spawns a real process is marked ``integration`` and skips
   itself via :func:`require_ffmpeg`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
PROBE_FIXTURES_DIR = FIXTURES_DIR / "probe"
STDERR_FIXTURES_DIR = FIXTURES_DIR / "stderr"

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


def load_probe_fixture(name: str) -> tuple[dict, dict]:
    """Return ``(ffprobe_payload, fixture_meta)`` for a committed probe case.

    The payload has the ``_fixture`` bookkeeping key removed so it can be fed to
    :func:`media_pipeline.probe.parse_probe` exactly as ffprobe would have
    returned it.
    """
    path = PROBE_FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        pytest.fail(
            f"missing probe fixture {path.name}. Regenerate with:\n"
            f"    ./venv/bin/python scripts/capture_probe_fixtures.py"
        )
    data = json.loads(path.read_text())
    meta = data.pop("_fixture", {})
    return data, meta


def probe_info(name: str):
    """Load a fixture and parse it into a :class:`MediaInfo`.

    ``has_faststart`` is passed explicitly because it is the one answer that
    needs the original file on disk, and a committed JSON cannot carry it. The
    capture script records it alongside the payload.
    """
    from media_pipeline.probe import parse_probe

    payload, meta = load_probe_fixture(name)
    return parse_probe(payload, has_faststart=meta.get("has_faststart"))


def probe_fixture_ids() -> list[str]:
    """Every committed probe fixture, for parametrisation."""
    if not PROBE_FIXTURES_DIR.exists():
        return []
    return sorted(p.stem for p in PROBE_FIXTURES_DIR.glob("*.json"))


def load_stderr_fixture(name: str) -> str:
    """Read a real captured ffmpeg stderr sample.

    These are verbatim captures, not invented strings. Classification bugs live
    in the gap between what ffmpeg actually prints and what the patterns expect,
    and a hand-written "Invalid data found..." tests only the hand-writing.
    """
    path = STDERR_FIXTURES_DIR / f"{name}.txt"
    if not path.exists():
        pytest.fail(f"missing stderr fixture {path.name}")
    return path.read_text(errors="replace")
