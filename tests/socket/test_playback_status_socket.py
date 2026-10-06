"""The panel's per-member status chips, end to end over a real socket.

The reduction logic is unit-tested in ``tests/unit/test_playback_status.py``.
What only a live socket can prove is the other half: that a client's
``playback_status`` report is actually stored on the session and actually
reaches the presence broadcast the panel renders. A perfect reducer behind a
handler that drops the event would pass every unit test and show nothing.

Presence is asserted on the broadcast payload rather than on
``rs.users[sid]``, because under polling-only transport the sid a client reports
is not the sid the server filed the session under. That is the same reason
``test_live_socket.py`` asserts on the broadcast.
"""
from __future__ import annotations

import pytest

from tests.socket.test_live_socket import Recorder, connect_client, _reset_room

pytestmark = pytest.mark.socket


def _mine(payload, user_id):
    for entry in payload.get("users", []):
        if entry.get("id") == user_id:
            return entry
    pytest.fail(f"no presence entry for {user_id} in {payload}")




def _await_chip(recorder, user_id, predicate, timeout=5.0):
    """Wait for the presence broadcast to show ``user_id`` matching."""
    def found(payload):
        for entry in payload.get("users", []):
            if entry.get("id") == user_id and predicate(entry):
                return True
        return False

    payload = recorder.wait_until("presence", found, timeout=timeout)
    return _mine(payload, user_id)


def _wait_for_state(rec, user_id, state, **kw):
    return _await_chip(rec, user_id,
                       lambda e: e["playback"]["state"] == state, **kw)


def test_a_member_with_no_report_shows_as_ready(live_server, connect_client):
    """The neutral default matters: a client that has not spoken yet must not be
    painted as broken."""
    _client, rec, me = connect_client("owner")
    _reset_room(live_server)

    payload = rec.wait_for("presence")
    assert _mine(payload, me["id"])["playback"] == {
        "state": "ready", "drift_seconds": 0.0,
    }


def test_buffering_reaches_presence(live_server, connect_client):
    client, rec, me = connect_client("owner")
    _reset_room(live_server)
    rec.drain()

    client.emit("playback_status", {"state": "buffering", "drift_seconds": 0.0})

    entry = _wait_for_state(rec, me["id"], "buffering")
    assert entry["playback"]["drift_seconds"] == 0.0


def test_reconnecting_reaches_presence(live_server, connect_client):
    client, rec, me = connect_client("owner")
    _reset_room(live_server)
    rec.drain()

    client.emit("playback_status", {"state": "reconnecting"})

    _wait_for_state(rec, me["id"], "reconnecting")


def test_a_reported_drift_reaches_presence_with_its_size(live_server, connect_client):
    client, rec, me = connect_client("owner")
    _reset_room(live_server)
    rec.drain()

    client.emit("playback_status", {"state": "out_of_sync",
                                    "drift_seconds": 4.25})

    entry = _wait_for_state(rec, me["id"], "out_of_sync")
    assert entry["playback"]["drift_seconds"] == 4.25


def test_a_later_report_replaces_the_earlier_one(live_server, connect_client):
    """Recovery must be visible. A chip stuck on "out of sync" forever would make
    the panel useless for exactly the viewers who fixed themselves."""
    client, rec, me = connect_client("owner")
    _reset_room(live_server)
    rec.drain()

    client.emit("playback_status", {"state": "buffering", "drift_seconds": 0.0})
    _wait_for_state(rec, me["id"], "buffering")

    client.emit("playback_status", {"state": "ready", "drift_seconds": 0.0})
    entry = _wait_for_state(rec, me["id"], "ready")
    assert entry["playback"]["drift_seconds"] == 0.0


def test_drift_is_reported_as_out_of_sync_even_if_the_client_says_ready(
        live_server, connect_client):
    client, rec, me = connect_client("owner")
    _reset_room(live_server)
    rec.drain()

    client.emit("playback_status", {"state": "ready", "drift_seconds": 6.0})

    entry = _wait_for_state(rec, me["id"], "out_of_sync")
    assert entry["playback"]["drift_seconds"] == 6.0


def test_a_junk_report_does_not_break_the_room(live_server, connect_client):
    """The presence broadcast is how everyone learns about everyone; one
    malformed client must not stop it."""
    client, rec, me = connect_client("owner")
    _reset_room(live_server)
    rec.drain()

    client.emit("playback_status", {"state": "nonsense",
                                    "drift_seconds": "not a number"})

    entry = _wait_for_state(rec, me["id"], "ready")
    assert entry["playback"]["drift_seconds"] == 0.0


def test_status_does_not_disturb_watching(live_server, connect_client):
    """The two reports are about different things and both must survive:
    reporting a stalled decoder says nothing about whether the tab is the one
    playing."""
    client, rec, me = connect_client("owner")
    _reset_room(live_server)
    rec.drain()

    client.emit("watching", {"watching": True, "item_id": "x"})
    client.emit("playback_status", {"state": "buffering"})

    entry = _wait_for_state(rec, me["id"], "buffering")
    assert entry["watching"] is True


def test_one_person_with_two_sockets_shows_one_chip(live_server, connect_client):
    """Presence is de-duplicated per person and so is the status: three tabs are
    one member with one chip, showing the worst of them."""
    first, rec_a, me = connect_client("owner")
    second = live_server["client"].Client()
    rec_b = Recorder(second)
    second.connect(live_server["url"],
                   socketio_path=live_server["socketio_path"],
                   auth={"token": me["token"]}, wait_timeout=10)
    assert second.connected
    _reset_room(live_server)
    rec_a.drain()
    rec_b.drain()

    try:
        first.emit("playback_status", {"state": "ready", "drift_seconds": 0.0})
        _wait_for_state(rec_a, me["id"], "ready")
        second.emit("playback_status", {"state": "reconnecting"})
        entry = _wait_for_state(rec_a, me["id"], "reconnecting")
        assert entry["sockets"] >= 2, "both tabs belong to the same person"
    finally:
        second.disconnect()


def test_the_panel_can_see_who_may_control(live_server, connect_client):
    """The other half of what the panel needs from presence: next to a status
    chip it must be able to tell which chips are actionable."""
    _client, rec, me = connect_client("owner")
    _reset_room(live_server)

    entry = _mine(rec.wait_for("presence"), me["id"])
    assert entry["can_control"] is True


def test_the_real_room_owner_is_badged_as_owner(live_server):
    """``live_db(role="owner")`` mints a stand-in -- ``can_control=True`` on a
    plain watcher row -- so it is deliberately *not* flagged ``is_owner``. The
    room's actual owner is a different user, and only that one gets the badge the
    panel draws next to a chip."""
    client = live_server["client"].Client()
    rec = Recorder(client)
    real_owner = live_server["owner"]
    client.connect(live_server["url"],
                   socketio_path=live_server["socketio_path"],
                   auth={"token": real_owner["token"]}, wait_timeout=10)
    assert client.connected
    try:
        entry = _mine(rec.wait_for("presence"), real_owner["id"])
        assert entry["is_owner"] is True
        assert entry["can_control"] is True
    finally:
        client.disconnect()


def test_a_watcher_is_shown_as_not_able_to_control(live_server, connect_client):
    _client, rec, me = connect_client("watcher")
    _reset_room(live_server)

    entry = _mine(rec.wait_for("presence"), me["id"])
    assert entry["can_control"] is False


def test_a_moderator_is_shown_as_able_to_control(live_server, connect_client):
    _client, rec, me = connect_client("moderator")
    _reset_room(live_server)

    entry = _mine(rec.wait_for("presence"), me["id"])
    assert entry["can_control"] is True


def test_every_presence_entry_carries_a_status_chip(live_server, connect_client):
    """A client that never reports must still get a well-formed chip, so the
    panel never has to guard against a missing field."""
    _client, rec, _me = connect_client("owner")
    _reset_room(live_server)

    payload = rec.wait_for("presence")
    for entry in payload.get("users", []):
        assert "playback" in entry, entry
        assert set(entry["playback"]) == {"state", "drift_seconds"}