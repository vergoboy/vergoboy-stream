"""Socket.IO tests against a real backend, a real database and a real client.

Everything here goes over an actual WebSocket/HTTP connection to a live server.
That is not ceremony: the interesting logic in this system sits in the connect
handshake, and ``socketio.test_client`` bypasses the transport entirely, so a
suite built on it would happily pass against a backend that rejects every real
client.

The seek-frontier tests are the reason this file exists: the clamp is enforced
in ``on_control``, so a unit test of the helper alone would prove nothing about
whether the *handler* actually applies it.
"""
from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.socket


#: Every event these tests care about. A real socketio.Client has no
#: get_received(): events are delivered to handlers registered up front, so they
#: must be known before the connection opens or the connect burst is lost.
WATCHED_EVENTS = (
    "state_sync", "notify", "chat_history", "presence",
    "transcode_progress", "transcode_error",
)


class Recorder:
    """Collects events a real client receives.

    Handlers are registered before ``connect`` so nothing emitted during the
    handshake (state_sync, chat_history) is dropped.
    """

    def __init__(self, client):
        self.events = []
        for name in WATCHED_EVENTS:
            client.on(name, handler=self._collector(name))

    def _collector(self, name):
        def handler(data=None):
            self.events.append((name, data))
        return handler

    def drain(self):
        out, self.events = self.events, []
        return out

    def names(self):
        return [name for name, _ in self.drain()]

    def wait_until(self, name, predicate, timeout=5.0):
        """Scan events until one named ``name`` satisfies ``predicate``.

        Necessary because a room broadcasts ``presence`` on connect and on every
        membership change, so "the next presence event" is frequently an older
        one that happens to still be queued. Matching on content rather than on
        arrival order is the only way to assert a *transition*.
        """
        import time

        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            for got_name, payload in self.drain():
                if got_name != name:
                    continue
                seen.append(payload)
                if predicate(payload):
                    return payload
            time.sleep(0.02)
        pytest.fail(
            f"no {name!r} event satisfied the predicate within {timeout}s; "
            f"saw {len(seen)} such events, last={seen[-1] if seen else None}"
        )

    def wait_for(self, name, timeout=5.0):
        """Block until ``name`` arrives, failing loudly rather than on a race.

        A bare "drain and assert" would frequently run before the server had
        emitted, producing failures that come and go with machine load.
        """
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for got_name, payload in self.drain():
                if got_name == name:
                    return payload
            time.sleep(0.02)
        pytest.fail(f"timed out waiting for {name!r}")


@pytest.fixture
def connect_client(live_server, live_db):
    """Factory: connect as a fresh user, returning (client, recorder, user).

    ``role`` picks the real permission level the fixture should mint -- see the
    table on :func:`live_db` for how each maps onto ``users.role`` and
    ``users.can_control``.
    """
    created = []

    def _connect(role="owner"):
        if role == "admin":
            user = live_db(can_control=False, name="admin", role="admin")
        elif role == "moderator":
            user = live_db(can_control=True, name="moderator", role="watcher")
        elif role == "watcher":
            user = live_db(can_control=False, name="watcher", role="watcher")
        elif role == "member":
            # Historic name for a plain non-controller viewer.
            user = live_db(can_control=False, name="member", role="watcher")
        elif role == "owner":
            user = live_db(can_control=True, name="owner", role="watcher")
        else:
            raise ValueError(f"unknown role {role!r}")
        client = live_server["client"].Client()
        recorder = Recorder(client)
        client.connect(
            live_server["url"],
            socketio_path=live_server["socketio_path"],
            auth={"token": user["token"]},
            wait_timeout=10,
        )
        assert client.connected, f"client for role {role!r} did not connect"
        created.append(client)
        return client, recorder, user

    yield _connect

    for client in created:
        # Abort rather than disconnect politely. A graceful disconnect waits for
        # the server's acknowledgement over the polling transport, which costs
        # ~25s per client and dominated the suite's runtime. These are throwaway
        # clients and the server tears the session down when the socket closes
        # either way.
        _abort(client)


@pytest.fixture
def owner(connect_client):
    """A connected controlling client, plus its recorder and user record."""
    return connect_client("owner")


def _reset_room(live_server):
    """Return the room to a known-empty playlist.

    Deliberately does *not* take ``app_module.LOCK``. That lock is a gevent lock
    (``gevent.thread.LockType``) owned by the server's hub; acquiring it from the
    pytest thread while a server greenlet holds it blocks forever, because gevent
    locks do not cross hubs. These helpers only ever run against an idle room
    with one test client, so plain assignment is both safe and non-blocking.
    """
    app_module = live_server["module"]
    rs = app_module.rooms.get(live_server["room_code"])
    rs.playlist = []
    rs.current_index = None
    rs.position = 0.0
    rs.playing = False
    rs.rate = 1.0
    # SOCKET_SESSIONS is deliberately NOT cleared here. The client for the
    # current test has already connected by the time this runs, and emptying it
    # would make _socket_session() return None so every handler bailed out
    # silently. Stale presence from earlier clients is handled where it matters:
    # the broadcast presence list is de-duplicated per user.
    app_module.TRANSCODE_PROGRESS.clear()


def _add_item(live_server, item):
    app_module = live_server["module"]
    rs = app_module.rooms.get(live_server["room_code"])
    rs.playlist.append(item)
    rs.current_index = len(rs.playlist) - 1


def _await_position(live_server, expected, timeout=5.0):
    """Wait for the room's position to settle on ``expected``.

    ``emit`` is asynchronous, so asserting straight after it races the server.
    There is no event to wait on either: the handler deliberately broadcasts
    ``state_sync`` with ``include_self=False``, so the client that asked for the
    seek never receives the result. Polling the authoritative room state is the
    only reliable option, and it fails with the real value rather than a bare
    mismatch.
    """
    import time

    rs = live_server["module"].rooms.get(live_server["room_code"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if rs.position == pytest.approx(expected):
            return rs.position
        time.sleep(0.02)
    pytest.fail(
        f"room never settled on {expected}: position is {rs.position}. "
        f"The handler may have rejected the event, or clamped it elsewhere."
    )


def _presence_for(live_server, user_id):
    """The presence entry for a user, found by id rather than by sid.

    Under polling-only transport (no ``websocket-client`` installed) the sid the
    client reports is not reliably the sid the server filed the session under, so
    matching on ``client.sid`` silently looks up nothing. The user id is stable.
    """
    rs = live_server["module"].rooms.get(live_server["room_code"])
    for entry in rs.users.values():
        if entry.get("id") == user_id:
            return entry
    return None


# ── authentication: the connect handshake ────────────────────────────────────


def test_connect_with_a_valid_token_is_accepted(live_server):
    client = live_server["client"].Client()
    client.connect(live_server["url"], socketio_path=live_server["socketio_path"], auth={"token": live_server["owner"]["token"]},
                   wait_timeout=10)
    try:
        assert client.connected
    finally:
        client.disconnect()


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c"])
def test_connect_without_a_usable_token_is_rejected(live_server, token):
    """An unauthenticated socket must never join a room.

    Every subsequent handler trusts SOCKET_SESSIONS, which is only populated
    here — so a connect that succeeds without a token would hand an anonymous
    client the room's full playlist.
    """
    client = live_server["client"].Client()
    with pytest.raises(Exception):
        client.connect(live_server["url"], socketio_path=live_server["socketio_path"], auth={"token": token}, wait_timeout=10)
    assert not client.connected


def test_connect_with_no_auth_at_all_is_rejected(live_server):
    client = live_server["client"].Client()
    with pytest.raises(Exception):
        client.connect(live_server["url"], socketio_path=live_server["socketio_path"], wait_timeout=10)


def test_a_rejected_connect_joins_no_room(live_server):
    """The real risk: a refused handshake that still left the client joined."""
    app_module = live_server["module"]
    before = len(app_module.SOCKET_SESSIONS)
    client = live_server["client"].Client()
    with pytest.raises(Exception):
        client.connect(live_server["url"], socketio_path=live_server["socketio_path"], auth={"token": "bogus"}, wait_timeout=10)
    assert len(app_module.SOCKET_SESSIONS) == before


def test_state_sync_arrives_on_connect_and_hides_internal_fields(live_server, connect_client):
    """``_``-prefixed keys carry the raw source path and must not be sent out."""
    _reset_room(live_server)
    _add_item(live_server, {
        "id": "item-secret", "title": "T", "src": "/x.m3u8",
        "_raw_path": "/srv/media/uploads/private-source.mkv",
        "_aspect": 16 / 9,
    })
    client, rec, _me = connect_client("owner")
    state = rec.wait_for("state_sync")
    assert state is not None
    for item in state.get("playlist", []):
        leaked = [k for k in item if k.startswith("_")]
        assert not leaked, f"internal fields leaked to client: {leaked}"
    assert "private-source" not in json_text(state)


def json_text(obj) -> str:
    import json

    return json.dumps(obj, default=str)


def test_request_sync_returns_current_state(owner, live_server):
    owner, rec, me = owner
    rec.drain()  # discard the connect burst
    owner.emit("request_sync")
    state = rec.wait_for("state_sync")
    assert state is not None
    assert "playlist" in state and "position" in state


# ── permissions ──────────────────────────────────────────────────────────────


def test_a_member_may_seek(live_server, connect_client):
    """Seeking is deliberately open to the whole room — it is not a privileged
    action. (Rate/select/shuffle are; see below.)"""
    member, rec, _me = connect_client("member")
    _reset_room(live_server)
    rec.drain()
    member.emit("control", {"action": "seek", "to": 5.0})
    _await_position(live_server, 5.0)
    assert "control_denied" not in rec.names()


@pytest.mark.parametrize("action,payload", [
    ("rate", {"rate": 2.0}),
    ("shuffle", {"on": True}),
])
def test_a_member_cannot_change_shared_settings(live_server, connect_client, action, payload):
    """These change what *everyone* watches, so they need a manager."""
    member, rec, _me = connect_client("member")
    _reset_room(live_server)
    rec.drain()
    member.emit("control", dict(payload, action=action))
    denied = rec.wait_for("notify")
    assert denied["type"] == "control_denied", denied
    assert denied["extra"]["action"] == action


def test_an_owner_may_change_shared_settings(live_server, owner):
    owner, rec, me = owner
    rec.drain()
    rs = live_server["module"].rooms.get(live_server["room_code"])
    owner.emit("control", {"action": "rate", "rate": 1.5})

    import time

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and rs.rate != pytest.approx(1.5):
        time.sleep(0.02)
    assert rs.rate == pytest.approx(1.5)


def test_watching_updates_presence(live_server, owner):
    """``watching`` must reflect real playback, not mere page presence.

    Asserted on the broadcast ``presence`` payload rather than on
    ``rs.users[client.sid]``: under polling-only transport the sid a client
    reports is not the sid the server filed the session under, and the presence
    list is de-duplicated per user, which is exactly the contract being tested.
    """
    owner, rec, me = owner
    app_module = live_server["module"]
    _reset_room(live_server)
    _add_item(live_server, {"id": "item-1", "title": "T", "src": "/x.m3u8",
                            "type": "movie", "status": "complete", "renditions": []})
    rec.drain()
    owner.emit("watching", {"watching": True, "item_id": "item-1"})

    def is_watching(payload):
        entry = next((u for u in payload["users"] if u.get("id") == me["id"]), None)
        return entry is not None and entry.get("watching") is True

    payload = rec.wait_until("presence", is_watching)
    entry = next(u for u in payload["users"] if u.get("id") == me["id"])
    assert entry.get("watching") is True, entry
    assert entry.get("watching_item") == "item-1", entry


def test_stopping_playback_clears_watching(live_server, owner):
    """The reverse transition matters too: browsing again must clear the flag,
    otherwise a viewer who paused stays marked as watching forever."""
    owner, rec, me = owner
    _reset_room(live_server)
    _add_item(live_server, {"id": "item-1", "title": "T", "src": "/x.m3u8",
                            "type": "movie", "status": "complete", "renditions": []})
    owner.emit("watching", {"watching": True, "item_id": "item-1"})
    rec.wait_until("presence", lambda p: any(
        u.get("id") == me["id"] and u.get("watching") for u in p["users"]))

    rec.drain()
    owner.emit("watching", {"watching": False})

    def stopped(payload):
        entry = next((u for u in payload["users"] if u.get("id") == me["id"]), None)
        return entry is not None and not entry.get("watching")

    payload = rec.wait_until("presence", stopped)
    entry = next(u for u in payload["users"] if u.get("id") == me["id"])
    assert not entry.get("watching"), entry
    assert not entry.get("watching_item"), entry


# ── the seek frontier, through the real handler ──────────────────────────────


def _encoding_item(item_id="enc-1", renditions=None):
    return {
        "id": item_id, "title": "Encoding", "type": "movie", "status": "encoding",
        "renditions": renditions if renditions is not None else [],
    }


def test_seek_past_the_encoded_frontier_is_clamped_on_the_server(live_server, owner):
    owner, rec, me = owner
    """The point of the whole guard.

    The client asks for 5000s while only 120s is encoded. The server must move
    the room to 120 - margin instead, even though nothing about the request
    itself looks suspicious.
    """
    app_module = live_server["module"]
    _reset_room(live_server)
    _add_item(live_server, _encoding_item(renditions=[{"label": "720p", "status": "ready"}]))
    app_module._record_encoded_progress("enc-1", "720p", 120.0)

    rec.drain()
    owner.emit("control", {"action": "seek", "to": 5000.0})

    expected = 120.0 - app_module.SEEK_FRONTIER_MARGIN
    _await_position(live_server, expected)


def test_a_clamped_seek_tells_the_room_why(live_server, owner):
    owner, rec, me = owner
    """Silently landing somewhere else reads as a bug to the viewer."""
    app_module = live_server["module"]
    _reset_room(live_server)
    _add_item(live_server, _encoding_item(renditions=[{"label": "720p", "status": "ready"}]))
    app_module._record_encoded_progress("enc-1", "720p", 120.0)

    rec.drain()
    owner.emit("control", {"action": "seek", "to": 5000.0})

    notify = rec.wait_for("notify")
    assert notify["type"] == "seek_clamped", notify
    assert notify["extra"]["requested"] == pytest.approx(5000.0)
    assert notify["extra"]["position"] == pytest.approx(
        120.0 - app_module.SEEK_FRONTIER_MARGIN
    )


def test_seek_within_the_frontier_is_left_alone(live_server, owner):
    owner, rec, me = owner
    """The guard must not clamp everything; a normal in-range seek is untouched."""
    app_module = live_server["module"]
    _reset_room(live_server)
    _add_item(live_server, _encoding_item(renditions=[{"label": "720p", "status": "ready"}]))
    app_module._record_encoded_progress("enc-1", "720p", 120.0)

    rec.drain()
    owner.emit("control", {"action": "seek", "to": 30.0})
    _await_position(live_server, 30.0)


def test_the_slowest_rendition_governs_the_room(live_server, owner):
    owner, rec, me = owner
    """360p is far ahead, 1080p is behind, so the room is held at 1080p's rate.

    The server cannot know which level a given viewer is watching, so it uses
    the minimum across the ladder — the only value safe for everyone at once.
    """
    app_module = live_server["module"]
    _reset_room(live_server)
    _add_item(live_server, _encoding_item(renditions=[
        {"label": "360p", "status": "ready"},
        {"label": "1080p", "status": "ready"},
    ]))
    app_module._record_encoded_progress("enc-1", "360p", 300.0)
    app_module._record_encoded_progress("enc-1", "1080p", 45.0)

    rec.drain()
    owner.emit("control", {"action": "seek", "to": 250.0})
    _await_position(live_server, 45.0 - app_module.SEEK_FRONTIER_MARGIN)


def test_a_fully_encoded_item_can_be_seeked_freely(live_server, owner):
    owner, rec, me = owner
    """No clamp once the item is complete — this must not feel broken."""
    app_module = live_server["module"]
    _reset_room(live_server)
    _add_item(live_server, {
        "id": "done-1", "title": "Done", "type": "movie", "status": "complete",
        "renditions": [{"label": "720p", "status": "complete"}],
    })
    rec.drain()
    owner.emit("control", {"action": "seek", "to": 7200.0})
    _await_position(live_server, 7200.0)


def test_a_live_item_can_be_seeked_freely(live_server, owner):
    owner, rec, me = owner
    app_module = live_server["module"]
    _reset_room(live_server)
    _add_item(live_server, {"id": "live-1", "title": "Live", "type": "live",
                            "status": "live", "renditions": []})
    rec.drain()
    owner.emit("control", {"action": "seek", "to": 99999.0})
    _await_position(live_server, 99999.0)


def test_a_negative_seek_is_floored_at_zero(live_server, owner):
    owner, rec, me = owner
    """Pre-existing RoomState behaviour, asserted here because the clamp now
    shares the code path and must not reintroduce a negative position."""
    app_module = live_server["module"]
    _reset_room(live_server)
    _add_item(live_server, {"id": "neg-1", "title": "N", "type": "movie",
                            "status": "complete", "renditions": []})
    rec.drain()
    owner.emit("control", {"action": "seek", "to": -50.0})
    _await_position(live_server, 0.0)


def test_an_unknown_action_is_ignored(live_server, owner):
    owner, rec, me = owner
    app_module = live_server["module"]
    _reset_room(live_server)
    rec.drain()
    owner.emit("control", {"action": "selfdestruct"})
    import time

    time.sleep(0.4)
    rs = app_module.rooms.get(live_server["room_code"])
    assert rs.position == 0.0


def test_a_non_numeric_seek_does_not_take_the_room_down(live_server, owner):
    """Malformed input from one client must not raise inside the handler and
    leave the room wedged."""
    owner, rec, me = owner
    app_module = live_server["module"]
    _reset_room(live_server)
    _add_item(live_server, {"id": "num-1", "title": "N", "type": "movie",
                            "status": "complete", "renditions": []})
    rec.drain()
    owner.emit("control", {"action": "seek", "to": "not-a-number"})
    import time

    time.sleep(0.5)
    # The room must still be usable afterwards.
    owner.emit("control", {"action": "seek", "to": 12.0})
    _await_position(live_server, 12.0)


# ── the clamp as other people see it ──────────────────────────────────────────


def test_the_clamped_position_reaches_other_viewers_in_the_broadcast(
    live_server, connect_client
):
    """The guard has to be visible where it actually matters.

    ``on_control`` broadcasts ``state_sync`` with ``include_self=False``, so the
    client that asked for the seek never learns the outcome from it -- that
    viewer gets a ``notify`` instead. Everybody else learns the position only
    from the broadcast. If the clamp were computed after the broadcast, or
    applied to the room state but not the payload, this is the assertion that
    fails: the requesting client would look fine while every other viewer
    silently sits at the requested position.
    """
    app_module = live_server["module"]
    seeker, seeker_rec, _me = connect_client("owner")
    watcher, watcher_rec, watcher_user = connect_client("watcher")

    _reset_room(live_server)
    _add_item(live_server, _encoding_item(
        renditions=[{"label": "720p", "status": "ready"}]))
    app_module._record_encoded_progress("enc-1", "720p", 120.0)

    watcher_rec.drain()
    seeker_rec.drain()
    seeker.emit("control", {"action": "seek", "to": 5000.0})

    expected = 120.0 - app_module.SEEK_FRONTIER_MARGIN
    payload = watcher_rec.wait_until(
        "state_sync",
        lambda p: p.get("position") == pytest.approx(expected),
    )
    assert payload["position"] == pytest.approx(expected)
    assert payload["position"] != pytest.approx(5000.0)
    # ...and the passive viewer is a real user in the room, not a bystander.
    assert watcher_user["id"] in [
        entry.get("id") for entry in (payload.get("online_ids") or [])
    ] or _presence_for(live_server, watcher_user["id"]) is not None


def test_a_seek_inside_the_frontier_broadcasts_the_requested_position(
    live_server, connect_client
):
    """The clamp must not become a blanket cap.

    Same two-client shape as above: a legal seek reaches the broadcast untouched.
    """
    app_module = live_server["module"]
    seeker, _seeker_rec, _me = connect_client("owner")
    watcher, watcher_rec, _watcher_user = connect_client("watcher")

    _reset_room(live_server)
    _add_item(live_server, _encoding_item(
        renditions=[{"label": "720p", "status": "ready"}]))
    app_module._record_encoded_progress("enc-1", "720p", 120.0)

    watcher_rec.drain()
    seeker.emit("control", {"action": "seek", "to": 42.0})

    payload = watcher_rec.wait_until(
        "state_sync", lambda p: p.get("position") == pytest.approx(42.0)
    )
    assert payload["position"] == pytest.approx(42.0)


def test_a_completed_item_lets_any_viewer_seek_past_the_old_frontier(
    live_server, connect_client
):
    """Once the ladder is finished there is no frontier, so a stale clamp left
    behind must not keep capping the room at the old number."""
    app_module = live_server["module"]
    owner_client, _rec, _me = connect_client("owner")
    _reset_room(live_server)
    _add_item(live_server, {
        "id": "done-1", "title": "Done", "type": "movie", "status": "complete",
        "renditions": [{"label": "720p", "status": "complete"},
                       {"label": "1080p", "status": "complete"}],
    })
    # Progress recorded, then the item finished: _clear_encoded_progress should
    # have run, and even if it did not, "all rungs complete" means no clamp.
    app_module._record_encoded_progress("done-1", "720p", 120.0)

    owner_client.emit("control", {"action": "seek", "to": 4000.0})
    _await_position(live_server, 4000.0)


def test_one_dead_rung_does_not_freeze_the_whole_room(live_server, connect_client):
    """End-to-end version of the policy: a failed 1080p rung must not pin every
    viewer at 0s for the rest of the item's life."""
    app_module = live_server["module"]
    owner_client, _rec, _me = connect_client("owner")
    _reset_room(live_server)
    _add_item(live_server, _encoding_item(renditions=[
        {"label": "360p", "status": "ready"},
        {"label": "720p", "status": "ready"},
        {"label": "1080p", "status": "error"},
    ]))
    app_module._record_encoded_progress("enc-1", "360p", 600.0)
    app_module._record_encoded_progress("enc-1", "720p", 480.0)
    app_module._record_encoded_progress("enc-1", "1080p", 3.0)

    owner_client.emit("control", {"action": "seek", "to": 5000.0})
    _await_position(live_server, 480.0 - app_module.SEEK_FRONTIER_MARGIN)


def test_an_item_with_nothing_playable_yet_is_not_freely_seekable(
    live_server, connect_client
):
    """The first seconds of every encode, over the wire.

    Every rung is still queued, so the master playlist is empty and not one
    segment exists. If skipping unadvertised rungs were mistaken for "no
    limit", this is where a viewer would seek to 5000s and the room would stare
    at a black screen -- on every single item, for the whole first encode.
    """
    app_module = live_server["module"]
    owner_client, rec, _me = connect_client("owner")
    _reset_room(live_server)
    _add_item(live_server, _encoding_item(renditions=[
        {"label": "360p", "status": "queued"},
        {"label": "720p", "status": "queued"},
        {"label": "1080p", "status": "queued"},
    ]))
    rec.drain()

    owner_client.emit("control", {"action": "seek", "to": 5000.0})
    _await_position(live_server, 0.0)

    notify = rec.wait_for("notify")
    assert notify["type"] == "seek_clamped", notify
    assert notify["extra"]["requested"] == pytest.approx(5000.0)
    assert notify["extra"]["position"] == pytest.approx(0.0)


# ── the permission matrix, over the real wire ────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_db(reset_live_db):
    """Autouse: every test in this module truncates the mutable tables on exit.

    PostgreSQL itself is started once per session and never restarted; without
    this, a ban inserted by one test would still be in the table when the next
    one connects.
    """


@pytest.fixture
def resettable(live_server):
    """``live_server`` under a name that reads as "already isolated".

    Every test in this module is covered by ``_isolate_db``; this alias keeps
    the new permission tests readable without repeating the fixture pair.
    """
    return live_server


def _patch_user(live_server, user_id, **fields):
    """Write real columns on a real user row.

    Done with the ORM rather than raw SQL so the tests cannot drift away from
    the model, and rather than by adding db.py helpers that would exist only for
    the suite.
    """
    session = live_server["db"].SessionLocal()
    try:
        user = session.get(live_server["db"].User, user_id)
        assert user is not None, f"no user row {user_id}"
        for key, value in fields.items():
            setattr(user, key, value)
        session.commit()
    finally:
        session.close()


def _abort(client):
    """Tear a throwaway client down without the ~25s polite disconnect."""
    try:
        client.eio.disconnect(abort=True)
        client.disconnect_abort()
    except Exception:  # noqa: BLE001 - teardown must not mask a failure
        pass


def _assert_connect_refused(live_server, token):
    """The handshake must refuse this token.

    When ``on_connect`` returns False the server rejects the namespace, and a
    real client surfaces that as ``ConnectionError`` rather than a quietly
    disconnected socket. Both outcomes mean "refused"; what must never happen is
    a successful connect, so either way is accepted and only success fails.
    """
    client = live_server["client"].Client()
    try:
        with pytest.raises(Exception):
            client.connect(
                live_server["url"],
                socketio_path=live_server["socketio_path"],
                auth={"token": token},
                wait_timeout=10,
            )
        assert not client.connected, "the handshake was refused but the client connected"
    finally:
        _abort(client)


def test_a_watcher_cannot_change_shared_settings(resettable, connect_client):
    """role=watcher, can_control=False: the room's shared state is off limits.

    These are the actions that change what *everybody* watches -- speed,
    switching the item, reordering the queue. A watcher asking for one must be
    refused with a reason, and the shared state must not move.
    """
    live_server = resettable
    app_module = live_server["module"]
    watcher, rec, me = connect_client("watcher")
    assert me["can_control"] is False and me["role"] == "watcher"

    _reset_room(live_server)
    rec.drain()

    watcher.emit("control", {"action": "rate", "rate": 2.0})
    denied = rec.wait_for("notify")
    assert denied["type"] == "control_denied", denied
    assert denied["extra"]["action"] == "rate"

    rs = app_module.rooms.get(live_server["room_code"])
    assert rs.rate == 1.0, "a refused action still changed the shared rate"


def test_a_watcher_may_still_do_what_everyone_may_do(resettable, connect_client):
    """Refusing `rate` must not turn into refusing everything.

    play/pause/seek are open to the room; a watcher who cannot scrub at all
    would make the room unwatchable for them.
    """
    live_server = resettable
    watcher, rec, _me = connect_client("watcher")
    _reset_room(live_server)
    _add_item(live_server, {"id": "open-1", "title": "Open", "type": "movie",
                            "status": "complete", "renditions": []})
    rec.drain()

    watcher.emit("control", {"action": "seek", "to": 25.0})
    _await_position(live_server, 25.0)
    assert [n for n, _ in rec.drain() if n == "notify"
            and _.get("type") == "control_denied"] == []


def test_a_moderator_can_change_shared_settings(resettable, connect_client):
    """A promoted controller (can_control=True on a watcher row) can.

    This is the closest thing the schema has to a moderator: `role` only
    distinguishes admin, so room management is the `can_control` flag.
    """
    live_server = resettable
    moderator, rec, me = connect_client("moderator")
    assert me["can_control"] is True and me["role"] == "watcher"

    _reset_room(live_server)
    rec.drain()

    moderator.emit("control", {"action": "rate", "rate": 1.5})
    import time

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if live_server["module"].rooms.get(live_server["room_code"]).rate == 1.5:
            break
        time.sleep(0.02)
    else:
        pytest.fail("moderator's rate change was not applied")

    denials = [p for n, p in rec.drain()
               if n == "notify" and p.get("type") == "control_denied"]
    assert denials == [], denials


def test_an_admin_can_change_shared_settings(resettable, connect_client):
    """role=admin passes `_may_control` without any promotion at all."""
    live_server = resettable
    admin, rec, me = connect_client("admin")
    assert me["role"] == "admin"

    _reset_room(live_server)
    rec.drain()

    admin.emit("control", {"action": "rate", "rate": 2.0})
    import time

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if live_server["module"].rooms.get(live_server["room_code"]).rate == 2.0:
            break
        time.sleep(0.02)
    else:
        pytest.fail("admin's rate change was not applied")

    denials = [p for n, p in rec.drain()
               if n == "notify" and p.get("type") == "control_denied"]
    assert denials == [], denials


def test_the_room_owner_can_change_shared_settings_without_a_flag(resettable, connect_client):
    """`_may_control` also accepts the room's own owner.

    The owner row the session fixture creates has no promotion of its own; the
    ownership relationship is what grants the permission.
    """
    live_server = resettable
    _reset_room(live_server)
    client = live_server["client"].Client()
    recorder = Recorder(client)
    try:
        client.connect(
            live_server["url"],
            socketio_path=live_server["socketio_path"],
            auth={"token": live_server["owner"]["token"]},
            wait_timeout=10,
        )
        assert client.connected
        recorder.drain()
        client.emit("control", {"action": "rate", "rate": 1.25})
        import time

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if live_server["module"].rooms.get(live_server["room_code"]).rate == 1.25:
                break
            time.sleep(0.02)
        else:
            pytest.fail("room owner's rate change was not applied")
    finally:
        _abort(client)


def test_a_banned_member_is_rejected_at_the_handshake(resettable, live_server, live_db):
    """A real ``room_bans`` row must stop the connection.

    The ban is checked in ``on_connect`` against the database, so this exercises
    the query rather than a cached flag.
    """
    banned = live_db(can_control=True, name="banned", banned=True)

    _assert_connect_refused(live_server, banned["token"])


def test_the_ban_is_scoped_to_the_one_room(resettable, live_server, live_db):
    """A ban must not follow the user into a different room.

    Same user, banned from the session room, pointed at the other room: allowed.
    Otherwise one room's moderation decision becomes a site-wide lockout.
    """
    user = live_db(can_control=True, name="crossroom",
                   banned=True, room=live_server["room_code"])
    _patch_user(live_server, user["id"],
                current_room_id=live_server["other_room_code"])

    client = live_server["client"].Client()
    try:
        client.connect(
            live_server["url"],
            socketio_path=live_server["socketio_path"],
            auth={"token": user["token"]},
            wait_timeout=10,
        )
        assert client.connected, "a room-scoped ban leaked into another room"
    finally:
        _abort(client)


def test_a_user_assigned_to_another_room_never_sees_this_rooms_state(
    resettable, live_server, live_db, connect_client
):
    """Room membership is `users.current_room_id`, and nothing else.

    There is no "private room" flag in the schema, so the enforceable boundary
    is assignment: a user whose ``current_room_id`` points elsewhere lands in
    that room and must not receive this room's playlist or position.
    """
    outsider = live_db(can_control=True, name="outsider",
                       room=live_server["other_room_code"])
    _reset_room(live_server)
    _add_item(live_server, {
        "id": "secret-1", "title": "Should stay here", "type": "movie",
        "status": "complete", "renditions": [],
    })

    client = live_server["client"].Client()
    recorder = Recorder(client)
    try:
        client.connect(
            live_server["url"],
            socketio_path=live_server["socketio_path"],
            auth={"token": outsider["token"]},
            wait_timeout=10,
        )
        assert client.connected, "an outsider must still connect, to their own room"
        import time

        time.sleep(0.6)
        leaked = [
            payload for name, payload in recorder.drain()
            if name == "state_sync"
            and any(it.get("id") == "secret-1" for it in payload.get("playlist") or [])
        ]
        assert leaked == [], "this room's playlist reached a user assigned elsewhere"
    finally:
        _abort(client)


def test_a_user_with_no_room_cannot_connect(resettable, live_server, live_db):
    """No ``current_room_id`` means there is nothing to join, so the handshake
    is refused rather than dropping the client into an arbitrary room."""
    user = live_db(can_control=False, name="homeless")
    _patch_user(live_server, user["id"], current_room_id=None)

    _assert_connect_refused(live_server, user["token"])


def test_a_deactivated_user_cannot_connect(resettable, live_server, live_db):
    """`is_active=False` is refused alongside a bad token.

    Worth pinning separately from the ban: deactivation is the account-level
    kill switch, and it is checked with the same `if not user or not
    user.is_active` guard as a missing row.
    """
    user = live_db(can_control=True, name="disabled")
    _patch_user(live_server, user["id"], is_active=False)

    _assert_connect_refused(live_server, user["token"])


def test_an_expired_token_is_rejected(resettable, live_server, live_db):
    """A correctly signed token whose ``exp`` has passed must not authenticate.

    Signed with the real secret and the real claims, so only the expiry can be
    what rejects it -- this is the case a "just check the signature" check would
    wave through.
    """
    from datetime import timedelta

    user = live_db(can_control=True, name="expired")
    dbmod = live_server["db"]
    # The production signer, given a TTL that has already elapsed. Correct
    # secret, correct claims, correct type -- only `exp` is in the past.
    token = dbmod._sign(
        {"sub": user["id"], "type": "access"}, timedelta(seconds=-30)
    )

    _assert_connect_refused(live_server, token)


def test_a_token_signed_with_another_secret_is_rejected(resettable, live_server, live_db):
    """Correct claims, correct expiry, wrong key: still refused."""
    from datetime import datetime, timedelta, timezone

    import jwt

    user = live_db(can_control=True, name="forged")
    token = jwt.encode(
        {"sub": user["id"], "type": "access",
         "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
         "jti": "forged"},
        "not-the-servers-secret",
        algorithm=live_server["db"].Config.JWT_ALGORITHM,
    )

    _assert_connect_refused(live_server, token)


def test_a_refresh_token_cannot_be_used_to_connect(resettable, live_server, live_db):
    """The handshake insists on ``type == "access"``.

    A refresh token is a real, validly signed token for the same user; without
    the type check it would be a second, long-lived way in.
    """
    user = live_db(can_control=True, name="refresher")
    token = live_server["db"].create_refresh_token(user["id"])

    _assert_connect_refused(live_server, token)
