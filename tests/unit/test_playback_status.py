"""Pins for per-member playback status, as the panel will read it.

Two levels, because the aggregation is a pure function that deserves unit
coverage for its awkward cases, while the fact that the *event* actually
reaches presence needs a real socket -- ``state.py`` could be perfect and the
handler could still drop the report on the floor.
"""
from __future__ import annotations

import pytest

from state import PLAYBACK_STATES, SYNC_DRIFT_THRESHOLD, worst_playback_status


# ── the pure reduction ──────────────────────────────────────────────────────

def test_no_reports_means_ready():
    assert worst_playback_status([]) == {"state": "ready", "drift_seconds": 0.0}


def test_a_clean_client_is_ready():
    assert worst_playback_status(
        [{"state": "ready", "drift_seconds": 0.4}])["state"] == "ready"


def test_buffering_is_reported_as_buffering():
    assert worst_playback_status(
        [{"state": "buffering", "drift_seconds": 0.0}])["state"] == "buffering"


def test_reconnecting_outranks_buffering():
    reports = [{"state": "buffering", "drift_seconds": 0.0},
               {"state": "reconnecting", "drift_seconds": 0.0}]
    assert worst_playback_status(reports)["state"] == "reconnecting"


def test_out_of_sync_outranks_everything():
    reports = [{"state": r} for r in PLAYBACK_STATES if r != "out_of_sync"]
    reports.append({"state": "out_of_sync", "drift_seconds": 0.1})
    assert worst_playback_status(reports)["state"] == "out_of_sync"


def test_the_worst_socket_wins_for_one_person():
    """One person with three tabs is one chip; a stalled tab must not hide
    behind two playing ones."""
    reports = [{"state": "ready"}, {"state": "ready"}, {"state": "buffering"}]
    assert worst_playback_status(reports)["state"] == "buffering"


def test_the_largest_drift_is_kept_alongside_the_state():
    """The chip carries a size, not just a colour, so the panel can say
    "out of sync by 4.25s". The worst *state* still decides the colour."""
    reports = [{"state": "out_of_sync", "drift_seconds": 1.0},
               {"state": "reconnecting", "drift_seconds": 4.25}]
    out = worst_playback_status(reports)
    assert out == {"state": "out_of_sync", "drift_seconds": 4.25}


def test_the_largest_drift_is_reported_even_when_it_is_not_the_worst_state():
    reports = [{"state": "out_of_sync", "drift_seconds": 1.0},
               {"state": "buffering", "drift_seconds": 9.0}]
    out = worst_playback_status(reports)
    assert out == {"state": "out_of_sync", "drift_seconds": 9.0}


def test_drift_over_the_threshold_outranks_a_cheerful_label():
    """A client reporting "ready" while admitting to 5s of drift is out of sync
    whatever it called itself."""
    out = worst_playback_status([{"state": "ready", "drift_seconds": 5.0}])
    assert out["state"] == "out_of_sync"
    assert out["drift_seconds"] == 5.0


def test_drift_below_the_threshold_does_not_invent_an_out_of_sync():
    out = worst_playback_status(
        [{"state": "ready", "drift_seconds": SYNC_DRIFT_THRESHOLD - 0.01}])
    assert out["state"] == "ready"


def test_negative_drift_is_reported_by_magnitude():
    """A client ahead of the room is still drifting; the panel shows the size."""
    out = worst_playback_status(
        [{"state": "ready", "drift_seconds": -3.0}])
    assert out["state"] == "out_of_sync"
    assert out["drift_seconds"] == 3.0


def test_an_unknown_state_is_treated_as_ready_not_as_broken():
    out = worst_playback_status([{"state": "wat", "drift_seconds": 0.0}])
    assert out["state"] == "ready"


def test_junk_reports_are_ignored_rather_than_raising():
    """A malformed report must not take the whole presence broadcast down."""
    out = worst_playback_status(
        [None, "nonsense", {"state": "buffering", "drift_seconds": None},
         {"drift_seconds": "abc"}])
    assert out["state"] == "buffering"
    assert out["drift_seconds"] == 0.0


def test_drift_is_rounded_for_the_wire():
    out = worst_playback_status(
        [{"state": "ready", "drift_seconds": 1.23456}])
    assert out["drift_seconds"] == 1.23