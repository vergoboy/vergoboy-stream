"""Google/GitHub sign-in: the generated username must be sane, and the person must choose their own
username + password before the account counts as complete. Runs against the live test backend (real PostgreSQL)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

import pytest


def _call(live_server, method, path, body=None, token=None):
    url = live_server["url"] + live_server["module"].BP + path
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.fixture
def make_user(live_server):
    dbmod = live_server["db"]
    made = []

    def _make(*, complete, username=None, password="old-random-password"):
        uid = str(uuid.uuid4())
        name = username or f"gen_{uuid.uuid4().hex[:8]}"
        s = dbmod.SessionLocal()
        try:
            s.add(dbmod.User(id=uid, username=name, display_name=name, password_hash=dbmod.hash_password(password),
                             is_active=True, email_verified=True, role="watcher", can_control=False,
                             profile_complete=complete, current_room_id=live_server["room_code"]))
            s.commit()
        finally:
            s.close()
        made.append(uid)
        return {"id": uid, "username": name, "token": dbmod.create_access_token(uid)}

    yield _make
    s = dbmod.SessionLocal()
    try:
        for uid in made:
            u = s.get(dbmod.User, uid)
            if u is not None:
                u.current_room_id = None            # /me creates the person's own room and joins it
                s.flush()
            s.execute(dbmod.delete(dbmod.Room).where(dbmod.Room.owner_id == uid))
            if u is not None:
                s.delete(u)
        s.commit()
    finally:
        s.close()


def test_a_new_google_user_gets_a_sane_username_and_is_marked_incomplete(live_server):
    app_module, dbmod = live_server["module"], live_server["db"]
    email = f"very.good.{uuid.uuid4().hex[:6]}@gmail.com"
    s = dbmod.SessionLocal()
    try:
        info = {"provider": "google", "oauth_id": f"g-{uuid.uuid4().hex}", "email": email, "verified": True, "name": "Very Good"}
        with app_module.app.test_request_context("/"):
            app_module._oauth_finish_session(s, info)
        user = dbmod.get_user_by_email(s, email)
        local = email.split("@")[0]
        assert user.username == local or user.username.startswith(local[:20])
        assert "gmail" not in user.username and "@" not in user.username
        assert user.profile_complete is False and user.public_dict()["profile_complete"] is False
        # the sign-in also created the person's own room; remove it before the user (owner FK)
        user.current_room_id = None
        s.flush()
        s.execute(dbmod.delete(dbmod.Room).where(dbmod.Room.owner_id == user.id))
        s.delete(user)
        s.commit()
    finally:
        s.close()


def test_choosing_username_and_password_completes_the_profile(live_server, make_user):
    u = make_user(complete=False)
    status, me = _call(live_server, "GET", "/api/auth/me", token=u["token"])
    assert status == 200 and me["user"]["profile_complete"] is False

    chosen = f"chosen_{uuid.uuid4().hex[:6]}"
    status, out = _call(live_server, "POST", "/api/auth/complete-profile",
                        {"username": chosen, "password": "my-new-password"}, token=u["token"])
    assert status == 200 and out["user"]["username"] == chosen and out["user"]["profile_complete"] is True
    assert out["user"]["display_name"] == chosen                  # was still the generated name

    status, me = _call(live_server, "GET", "/api/auth/me", token=u["token"])
    assert me["user"]["profile_complete"] is True
    # the chosen credentials really work, and the generated password no longer does
    assert _call(live_server, "POST", "/api/auth/login", {"username": chosen, "password": "my-new-password"})[0] == 200
    assert _call(live_server, "POST", "/api/auth/login", {"username": chosen, "password": "old-random-password"})[0] == 401


def test_a_provider_display_name_is_kept(live_server, make_user):
    u = make_user(complete=False)
    dbmod = live_server["db"]
    s = dbmod.SessionLocal()
    try:
        s.get(dbmod.User, u["id"]).display_name = "Very Good Boy"
        s.commit()
    finally:
        s.close()
    chosen = f"c_{uuid.uuid4().hex[:6]}"
    status, out = _call(live_server, "POST", "/api/auth/complete-profile", {"username": chosen, "password": "abcdef"}, token=u["token"])
    assert status == 200 and out["user"]["display_name"] == "Very Good Boy"


def test_it_can_only_be_done_once(live_server, make_user):
    u = make_user(complete=False)
    ok = {"username": f"once_{uuid.uuid4().hex[:6]}", "password": "abcdef"}
    assert _call(live_server, "POST", "/api/auth/complete-profile", ok, token=u["token"])[0] == 200
    again = {"username": f"twice_{uuid.uuid4().hex[:6]}", "password": "abcdef"}
    status, out = _call(live_server, "POST", "/api/auth/complete-profile", again, token=u["token"])
    assert status == 409 and "قبلاً" in out["error"]


def test_a_normal_account_cannot_use_it_to_change_username_or_password(live_server, make_user):
    u = make_user(complete=True, password="keep-this-password")
    status, _ = _call(live_server, "POST", "/api/auth/complete-profile",
                      {"username": f"hijack_{uuid.uuid4().hex[:6]}", "password": "attacker-pass"}, token=u["token"])
    assert status == 409
    assert _call(live_server, "POST", "/api/auth/login", {"username": u["username"], "password": "keep-this-password"})[0] == 200


def test_taken_and_invalid_names_and_weak_passwords_are_refused(live_server, make_user):
    taken = make_user(complete=True)
    u = make_user(complete=False)
    call = lambda body, token=u["token"]: _call(live_server, "POST", "/api/auth/complete-profile", body, token=token)  # noqa: E731
    assert call({"username": taken["username"], "password": "abcdef"})[0] == 409
    assert call({"username": "ab", "password": "abcdef"})[0] == 400
    assert call({"username": "has space", "password": "abcdef"})[0] == 400
    assert call({"username": "علی_فارسی", "password": "abcdef"})[0] == 400
    assert call({"username": f"ok_{uuid.uuid4().hex[:6]}", "password": "123"})[0] == 400
    assert call({})[0] == 400
    # nothing above changed the account
    status, me = _call(live_server, "GET", "/api/auth/me", token=u["token"])
    assert me["user"]["profile_complete"] is False and me["user"]["username"] == u["username"]


def test_it_needs_a_valid_login(live_server):
    assert _call(live_server, "POST", "/api/auth/complete-profile", {"username": "abc", "password": "abcdef"})[0] == 401
    assert _call(live_server, "POST", "/api/auth/complete-profile", {"username": "abc", "password": "abcdef"}, token="not-a-token")[0] == 401


def test_existing_accounts_are_complete_by_default(live_server):
    dbmod = live_server["db"]
    uid = str(uuid.uuid4())
    s = dbmod.SessionLocal()
    try:
        s.add(dbmod.User(id=uid, username=f"legacy_{uuid.uuid4().hex[:6]}", display_name="x", password_hash="x"))  # flag not given
        s.commit()
        u = s.get(dbmod.User, uid)
        assert u.profile_complete is True and u.public_dict()["profile_complete"] is True
        s.delete(u)
        s.commit()
    finally:
        s.close()
