"""Characterisation of the playlist entrypoints, against a real live server.

The playlist domain has no unit-level coverage at all today, so these tests
exist to pin the *current* behaviour of `add-url` and the remove route before
either is lifted out of `app.py`. They are deliberately written as black-box
HTTP calls with a real bearer token and the real PostgreSQL behind it: the
behaviour that matters (who is allowed to do what, what shape an item comes
back as, whether a room's playlist actually changed) is decided in the route,
in the permission helpers and in `RoomState`, not in any single function that
could be called directly.

Two things are pinned here that a refactor is most likely to break by accident:

* the shape of the item `add-url` returns, which today still carries internal
  fields such as `_room_id` and `added_by_user_id` that `to_public_dict` strips
  on the way out via Socket.IO;
* the fact that removing an unknown id is a success, not a 404.
"""
from __future__ import annotations

import pytest
import requests

pytestmark = pytest.mark.live_api

HLS_URL = "https://example.com/stream/master.m3u8"
MP4_URL = "https://example.com/movie.mp4"


@pytest.fixture
def room(live_server, tmp_path, monkeypatch):
    """The session room, with its playlist emptied before and after the test,
    and every path the remove route can delete pointed at a tmp dir.

    The media directories are redirected because ``DELETE /playlist/<id>`` does
    real damage: it rmtree's the item's HLS output and, by default, spawns
    ``_cleanup_orphans``, which deletes anything in the upload/sub directories
    that no room references. Against the default config that is the user's real
    ``media/`` tree. A characterization suite must not be able to remove real
    files, so the orphan sweep is stubbed out and the paths are per-test.

    ``check_link_ok`` is stubbed reachable because ``add-url`` probes every
    http(s) URL for real, including a pasted .m3u8. The dead-link test patches
    it back to False, which wins because this runs first.
    """
    mod = live_server["module"]
    monkeypatch.setattr(mod, "check_link_ok", lambda *a, **k: True)
    monkeypatch.setattr(mod, "_cleanup_orphans",
                        lambda: {"uploads": [], "subs": [], "hls": []})
    for attr in ("HLS_DIR", "UPLOAD_DIR", "SUBS_DIR"):
        path = tmp_path / attr.lower()
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(mod.Config, attr, str(path))
    rs = mod.rooms.get(live_server["room_code"])
    _empty(mod, rs)
    yield live_server["room_code"]
    _empty(mod, rs)


def _empty(mod, rs):
    with mod.LOCK:
        for item in list(rs.playlist):
            rs.remove_item(item.get("id"))
        mod.safe_save(rs)


def _api(live_server, method, path, token=None, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return requests.request(method, f"{live_server['url']}{path}",
                            headers=headers, timeout=30, **kw)


def _items(live_server, room_code):
    mod = live_server["module"]
    return list(mod.rooms.get(room_code).playlist)


# ── add-url: shape of what comes back ──────────────────────────────────────

def test_add_url_of_a_direct_hls_source_is_ready_immediately(
        live_server, live_db, room, monkeypatch):
    """A pasted .m3u8 has nothing to transcode, so it is playable at once and
    keeps its original src. This is the same shape ``_add_url_item`` derives from
    ``is_hls``, and it is why such items carry no renditions."""
    monkeypatch.setattr(live_server["module"], "start_item_encoding",
                        lambda *a, **k: None)
    user = live_db(can_control=True, name="adder")
    mod = live_server["module"]

    r = _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
             json={"url": HLS_URL, "title": "live channel", "name": "adder"})

    assert r.status_code == 200, r.text
    item = r.json()
    assert item["status"] == "ready"
    assert item["src"] == HLS_URL
    assert item["type"] == "url"
    assert item["title"] == "live channel"
    assert item["subtitles"] == []
    assert item["audio_tracks"] == []
    assert item["added_by"] == "adder"


def test_add_url_that_needs_encoding_is_queued_with_no_src(
        live_server, live_db, room, monkeypatch):
    mod = live_server["module"]
    monkeypatch.setattr(mod, "start_item_encoding", lambda *a, **k: None)
    monkeypatch.setattr(mod, "check_link_ok", lambda *a, **k: True)
    user = live_db(can_control=True, name="adder")

    r = _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
             json={"url": MP4_URL, "title": "a film", "name": "adder"})

    assert r.status_code == 200, r.text
    item = r.json()
    assert item["status"] == "queued"
    assert item["src"] is None, "an item that will be encoded has no src yet"
    assert item["title"] == "a film"


def test_add_url_starts_an_encode_only_when_one_is_needed(
        live_server, live_db, room, monkeypatch):
    mod = live_server["module"]
    started: list = []
    monkeypatch.setattr(mod, "start_item_encoding",
                        lambda item_id, url, name: started.append(item_id))
    monkeypatch.setattr(mod, "check_link_ok", lambda *a, **k: True)
    user = live_db(can_control=True, name="adder")

    _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
         json={"url": HLS_URL, "name": "adder"})
    assert started == [], "a direct HLS source must not spawn an encoder"

    r = _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
             json={"url": MP4_URL, "name": "adder"})
    assert len(started) == 1
    assert started[0] == r.json()["id"]


def test_add_url_derives_a_title_from_the_url_when_none_is_given(
        live_server, live_db, room, monkeypatch):
    monkeypatch.setattr(live_server["module"], "start_item_encoding",
                        lambda *a, **k: None)
    user = live_db(can_control=True, name="adder")

    r = _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
             json={"url": HLS_URL, "name": "adder"})

    assert r.status_code == 200
    assert r.json()["title"] == "master.m3u8"


def test_add_url_strips_a_query_string_from_a_derived_title(
        live_server, live_db, room, monkeypatch):
    monkeypatch.setattr(live_server["module"], "start_item_encoding",
                        lambda *a, **k: None)
    user = live_db(can_control=True, name="adder")

    r = _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
             json={"url": "https://example.com/a/b/clip.m3u8?token=zzz",
                   "name": "adder"})

    assert r.json()["title"] == "clip.m3u8"


def test_add_url_returns_the_internal_fields_todays_socket_state_hides(
        live_server, live_db, room, monkeypatch):
    """``add-url`` hands back the raw item dict, so it still carries ``_room_id``
    and ``added_by_user_id``. Socket.IO strips these in ``to_public_dict``; the
    REST route does not. Pinned so the extraction cannot quietly change what this
    endpoint leaks, in either direction.
    """
    monkeypatch.setattr(live_server["module"], "start_item_encoding",
                        lambda *a, **k: None)
    user = live_db(can_control=True, name="adder")

    r = _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
             json={"url": HLS_URL, "name": "adder"})
    item = r.json()

    assert item["_room_id"] == room
    assert item["added_by_user_id"] == user["id"]
    assert isinstance(item["added_at"], (int, float))


def test_add_url_puts_the_item_in_the_room_playlist(
        live_server, live_db, room, monkeypatch):
    monkeypatch.setattr(live_server["module"], "start_item_encoding",
                        lambda *a, **k: None)
    user = live_db(can_control=True, name="adder")

    before = len(_items(live_server, room))
    r = _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
             json={"url": HLS_URL, "name": "adder"})

    items = _items(live_server, room)
    assert len(items) == before + 1
    assert items[-1]["id"] == r.json()["id"]


# ── add-url: rejections ─────────────────────────────────────────────────────

def test_add_url_without_a_token_is_rejected(live_server, room):
    r = _api(live_server, "POST", "/stream/api/add-url",
             json={"url": HLS_URL, "name": "nobody"})
    assert r.status_code in (401, 403), r.text
    assert _items(live_server, room) == []


def test_add_url_with_a_bogus_token_is_rejected(live_server, room):
    r = _api(live_server, "POST", "/stream/api/add-url", token="not-a-jwt",
             json={"url": HLS_URL, "name": "nobody"})
    assert r.status_code in (401, 403), r.text
    assert _items(live_server, room) == []


def test_add_url_of_an_empty_url_is_a_400(live_server, live_db, room):
    user = live_db(can_control=True, name="adder")
    for bad in ("", "   "):
        r = _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
                 json={"url": bad, "name": "adder"})
        assert r.status_code == 400, r.text
    assert _items(live_server, room) == []


def test_add_url_of_a_dead_link_is_a_422_and_adds_nothing(
        live_server, live_db, room, monkeypatch):
    monkeypatch.setattr(live_server["module"], "check_link_ok",
                        lambda *a, **k: False)
    user = live_db(can_control=True, name="adder")

    r = _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
             json={"url": MP4_URL, "name": "adder"})

    assert r.status_code == 422, r.text
    assert _items(live_server, room) == []


def test_add_url_is_refused_when_the_user_may_not_add(
        live_server, live_db, room, monkeypatch):
    mod = live_server["module"]
    monkeypatch.setattr(mod, "_may_add", lambda user: False)
    monkeypatch.setattr(mod, "start_item_encoding", lambda *a, **k: None)
    user = live_db(can_control=True, name="adder")

    r = _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
             json={"url": HLS_URL, "name": "adder"})

    assert r.status_code == 403, r.text
    assert _items(live_server, room) == []


def test_add_url_outside_the_active_room_is_refused(
        live_server, live_db, room, monkeypatch):
    """The route reads the *current* room, not the one in the token, so a user
    with no current room gets a 400 rather than a silent success."""
    mod = live_server["module"]
    monkeypatch.setattr(mod, "_current_room", lambda: None)
    user = live_db(can_control=True, name="adder")

    r = _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
             json={"url": HLS_URL, "name": "adder"})

    assert r.status_code == 400, r.text


# ── remove ──────────────────────────────────────────────────────────────────

def _add_one(live_server, live_db, monkeypatch, url=HLS_URL):
    monkeypatch.setattr(live_server["module"], "start_item_encoding",
                        lambda *a, **k: None)
    user = live_db(can_control=True, name="adder")
    r = _api(live_server, "POST", "/stream/api/add-url", token=user["token"],
             json={"url": url, "title": "doomed", "name": "adder"})
    assert r.status_code == 200, r.text
    return user, r.json()["id"]


def test_remove_deletes_the_item_from_the_playlist(
        live_server, live_db, room, monkeypatch):
    _, item_id = _add_one(live_server, live_db, monkeypatch)
    user = live_db(can_control=True, name="ctl")

    r = _api(live_server, "DELETE", f"/stream/api/playlist/{item_id}",
             token=user["token"])

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert item_id not in [i["id"] for i in _items(live_server, room)]


def test_remove_of_an_unknown_id_still_succeeds(
        live_server, live_db, room):
    """Removing something that is not there is a success today, not a 404. The
    panel fires this on a stale click, so the shape is worth pinning rather than
    quietly improving mid-extraction."""
    user = live_db(can_control=True, name="ctl")

    r = _api(live_server, "DELETE", "/stream/api/playlist/does-not-exist",
             token=user["token"])

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}


def test_remove_is_refused_for_a_watcher(live_server, live_db, room, monkeypatch):
    _, item_id = _add_one(live_server, live_db, monkeypatch)
    watcher = live_db(can_control=False, name="watcher")

    r = _api(live_server, "DELETE", f"/stream/api/playlist/{item_id}",
             token=watcher["token"])

    assert r.status_code == 403, r.text
    assert item_id in [i["id"] for i in _items(live_server, room)]


def test_remove_requires_a_token(live_server, live_db, room, monkeypatch):
    _, item_id = _add_one(live_server, live_db, monkeypatch)

    r = _api(live_server, "DELETE", f"/stream/api/playlist/{item_id}")

    assert r.status_code in (401, 403), r.text
    assert item_id in [i["id"] for i in _items(live_server, room)]


def test_remove_clears_the_item_hls_directory(
        live_server, live_db, room, monkeypatch):
    mod = live_server["module"]
    user, item_id = _add_one(live_server, live_db, monkeypatch)
    hls = mod.Config.HLS_DIR
    import os
    os.makedirs(os.path.join(hls, item_id, "720p"), exist_ok=True)
    assert os.path.isdir(os.path.join(hls, item_id))

    r = _api(live_server, "DELETE", f"/stream/api/playlist/{item_id}",
             token=user["token"])

    assert r.status_code == 200, r.text
    assert not os.path.isdir(os.path.join(hls, item_id))


def test_remove_accepts_the_cleanup_flag(
        live_server, live_db, room, monkeypatch):
    mod = live_server["module"]
    user, item_id = _add_one(live_server, live_db, monkeypatch)
    calls: list = []
    monkeypatch.setattr(mod, "_cleanup_orphans", lambda: calls.append(1))

    r = _api(live_server, "DELETE",
             f"/stream/api/playlist/{item_id}?cleanup=0",
             token=user["token"])

    assert r.status_code == 200, r.text
    assert calls == [], "cleanup=0 must not trigger an orphan sweep"


def test_remove_of_the_playing_item_clears_the_current_selection(
        live_server, live_db, room, monkeypatch):
    mod = live_server["module"]
    user, item_id = _add_one(live_server, live_db, monkeypatch)
    rs = mod.rooms.get(room)
    with mod.LOCK:
        rs.select_item(0)
        assert rs.current_index == 0

    r = _api(live_server, "DELETE", f"/stream/api/playlist/{item_id}",
             token=user["token"])

    assert r.status_code == 200, r.text
    assert rs.playlist == []