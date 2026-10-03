"""Pytest fixtures shared by every suite.

The helper *functions* live in :mod:`tests.support`; this module only wraps the
ones that need to be injected. Keeping them apart matters: importing ``conftest``
directly is not supported by pytest, so a test module that wants a helper must
import :mod:`tests.support`, not this file.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text as sql_text

from tests.support import require_ffmpeg


# ── import-time environment ──────────────────────────────────────────────────
# These are set at *module* level, not in a fixture, on purpose: pytest imports
# conftest before it collects test modules, so a test module may legitimately do
# `import app` at the top. A fixture would run too late.
#
# They are assigned rather than defaulted. `setdefault` would quietly adopt the
# developer's real .stream_db.env if it happened to be exported, and a test run
# that silently talks to the production database is far worse than one that
# cannot connect at all. Nothing here connects: create_engine() is lazy, and the
# suites that need a live database start their own pgserver (see the `pgserver`
# fixture below).
os.environ["STREAM_SECRET_KEY"] = "pytest-secret-key-not-a-real-credential"
os.environ["STREAM_JWT_SECRET"] = "pytest-jwt-secret-not-a-real-credential"
# psycopg2 explicitly: a bare postgresql:// resolves to the psycopg *v3* driver
# under SQLAlchemy 2.1, which this project does not install (requirements.txt
# pins psycopg2-binary).
os.environ["STREAM_DATABASE_URL"] = (
    "postgresql+psycopg2://pytest:pytest@127.0.0.1:1/pytest_not_used"
)
os.environ["STREAM_ENV"] = "development"


@pytest.fixture(scope="session")
def ffmpeg_bin() -> str:
    """Absolute path to ffmpeg, or skip.

    Session-scoped so the "is it installed?" question is asked once instead of
    per test — and so the answer is consistent across a whole run rather than
    depending on collection order.
    """
    path = shutil.which("ffmpeg")
    if not path:
        pytest.skip("ffmpeg not on PATH")
    return path


@pytest.fixture
def require_encoders():
    """Factory fixture: ``require_encoders("libx265", "ac3")`` skips if absent."""

    def _require(*encoders: str) -> None:
        require_ffmpeg(*encoders)

    return _require


@pytest.fixture
def isolated_hls_root(tmp_path, monkeypatch):
    """A throwaway Config.HLS_DIR for tests that would otherwise touch media/.

    Pointed at tmp_path so a cleanup bug cannot reach the developer's real HLS
    output. Restored automatically by monkeypatch.
    """
    root = tmp_path / "hls"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STREAM_HLS_DIR", str(root))
    return root


@pytest.fixture
def clean_env(monkeypatch):
    """Neutralise the developer's real credentials for the duration of a test.

    Tests must not read the developer's .stream_db.env by accident. Clearing the
    handful of variables config.py insists on keeps an import inside a test from
    silently depending on a database that happens to be running locally.
    """
    for var in ("STREAM_SECRET_KEY", "STREAM_DATABASE_URL", "STREAM_JWT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# ── live server (socket suite) ───────────────────────────────────────────────


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def live_db(live_server):
    """Factory that mints a fresh, unique, real user per connection.

    A fresh user per test is not incidental. ``users_public_list`` de-duplicates
    presence per user id, so if two tests shared a user while the first
    connection was still being torn down (disconnect is slow under polling-only
    transport), the stale entry would win the de-duplication and the second test
    would assert against the first test's leftovers.

    ``role`` maps onto the two real permission axes this codebase has:

    ==============  ==========================  ==========================
    role argument   ``users.role``              ``users.can_control``
    ==============  ==========================  ==========================
    ``admin``       ``admin``                   True
    ``moderator``   ``watcher``                 True
    ``watcher``     ``watcher``                 False
    ``owner``       ``watcher``                 True
    ==============  ==========================  ==========================

    There is no separate "moderator" role in the schema. ``_may_control`` is
    ``admin or can_control or room owner``, so a promoted room manager -- the
    closest thing this system has to a moderator -- is ``can_control=True`` on a
    plain watcher row.
    """
    import uuid as _uuid

    dbmod = live_server["db"]
    created_ids: list = []

    def _make_user(*, can_control: bool, name: str = "user", role: str = "watcher",
                   banned: bool = False, room: str | None = None):
        user_id = str(_uuid.uuid4())
        username = f"{name}_{_uuid.uuid4().hex[:8]}"
        room_id = room if room is not None else live_server["room_code"]
        is_admin = role == "admin"
        user = dbmod.User(
            id=user_id, username=username, display_name=name, password_hash="x",
            is_active=True,
            can_control=True if is_admin else can_control,
            role="admin" if is_admin else role,
            current_room_id=room_id,
        )
        session = dbmod.SessionLocal()
        try:
            session.add(user)
            session.flush()
            if banned:
                # A real ban row, not a flag: `on_connect` asks the database.
                session.add(dbmod.RoomBan(
                    id=str(_uuid.uuid4()), room_id=room_id, user_id=user_id,
                    banned_by=None,
                ))
            session.commit()
        finally:
            session.close()
        created_ids.append(user_id)
        return {"id": user_id, "username": username, "can_control": can_control,
                "role": role, "room": room_id,
                "token": dbmod.create_access_token(user_id)}

    live_server["_created_user_ids"] = created_ids
    return _make_user


@pytest.fixture
def reset_live_db(live_server, live_db):
    """Truncate the mutable tables between socket tests.

    PostgreSQL is started once per session and never restarted; what changes per
    test is the data. Truncating is faster than a restart and, more importantly,
    a restart would drop the connections the in-memory room registry and the
    JWT-signing config depend on.

    ``users`` and ``rooms`` are deliberately *not* truncated: the session room
    and its owner are referenced by the server's own in-memory state, so
    deleting them would break every subsequent test rather than isolate them.
    Only the tables a test can actually dirty are cleared -- bans and refresh
    tokens -- plus the throwaway users minted by the factory.

    Depends on ``live_db`` so the factory's bookkeeping exists before teardown
    reads it, and is requested explicitly by the socket suite.
    """
    yield

    dbmod = live_server["db"]
    session = dbmod.SessionLocal()
    try:
        session.execute(sql_text("TRUNCATE room_bans, refresh_tokens"))
        for user_id in live_server.get("_created_user_ids") or []:
            session.execute(
                sql_text("DELETE FROM users WHERE id = :uid"), {"uid": user_id}
            )
        session.commit()
    finally:
        session.close()
    live_server["_created_user_ids"].clear()


@pytest.fixture(scope="session")
def live_server(request):
    """A real backend on a real ephemeral port, backed by a real PostgreSQL.

    Deliberately *not* ``socketio.test_client``. That helper bypasses the
    transport, so it cannot catch an auth or handshake regression — and the
    handshake is precisely where the security-relevant logic lives
    (``on_connect`` rejects anything without a valid access token). These tests
    connect with an actual ``socketio.Client`` over HTTP so the connect handler
    really runs.

    Session-scoped: starting PostgreSQL and a gevent server per test would add
    minutes to the suite for no extra coverage. Between-test isolation is the
    ``reset_live_db`` fixture's job.
    """
    import pathlib
    import shutil
    import tempfile
    import threading
    import time
    import uuid

    pgserver = pytest.importorskip("pgserver", reason="pgserver not installed")
    socketio_client = pytest.importorskip("socketio", reason="python-socketio not installed")

    import db as dbmod

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="pytest-live-"))
    # Teardown is registered as finalizers on the *session* request rather than
    # left to a `finally` around the yield: if anything below raises before the
    # yield, or pytest is interrupted (Ctrl-C, an internal error), the data
    # directory and the postgres process still get cleaned up instead of leaking
    # a live cluster per aborted run. Registered before the cluster starts so a
    # failure inside get_server() cannot leak the directory either.
    request.addfinalizer(lambda: shutil.rmtree(tmpdir, ignore_errors=True))

    server = pgserver.get_server(tmpdir / "pgdata")
    request.addfinalizer(lambda: server.cleanup())

    # Explicit psycopg2 driver: a bare postgresql:// resolves to psycopg v3
    # under SQLAlchemy 2.1, which this project does not install.
    uri = server.get_uri().replace("postgresql://", "postgresql+psycopg2://", 1)

    # db.py builds its engine from Config at import time, so the URL has to
    # be in place before anything imports it.
    os.environ["STREAM_DATABASE_URL"] = uri
    import config as config_mod

    config_mod.Config.DATABASE_URL = uri
    dbmod.engine = dbmod.create_engine(
        uri, pool_size=5, max_overflow=5, pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )
    dbmod.SessionLocal = dbmod.sessionmaker(
        bind=dbmod.engine, autoflush=False, expire_on_commit=False
    )
    dbmod.init_db()

    import app as app_module

    port = _free_port()
    thread = threading.Thread(
        target=lambda: app_module.socketio.run(
            app_module.app, host="127.0.0.1", port=port,
            allow_unsafe_werkzeug=True, log_output=False,
        ),
        daemon=True,
    )
    thread.start()

    # Wait for the port to accept rather than sleeping a fixed amount: a fixed
    # sleep is either flaky or needlessly slow. Bounded at 15s and reported as a
    # failure with the reason, so a missing system library surfaces as one clear
    # error instead of the whole suite hanging.
    import socket as _socket

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with _socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.1)
    else:
        pytest.fail(
            "live socket server never accepted a connection on 127.0.0.1:"
            f"{port} within 15s. Common causes: the port was taken between "
            "_free_port() and bind(), or werkzeug refused to start. The server "
            "thread is a daemon, so the rest of the suite is unaffected."
        )

    # Two real rooms in a real database. The second exists so a test can prove
    # that a user assigned elsewhere never lands in this room's state.
    room_code = "TST" + uuid.uuid4().hex[:4].upper()[:5]
    other_room_code = "OTH" + uuid.uuid4().hex[:4].upper()[:5]
    owner = dbmod.User(
        id=str(uuid.uuid4()), username=f"owner_{uuid.uuid4().hex[:6]}",
        display_name="Owner", password_hash="x", is_active=True,
        can_control=True, role="watcher",
    )
    member = dbmod.User(
        id=str(uuid.uuid4()), username=f"member_{uuid.uuid4().hex[:6]}",
        display_name="Member", password_hash="x", is_active=True,
        can_control=False, role="watcher",
    )
    # The second room gets its own owner on purpose. `User.own_room` is a
    # uselist=False relationship, so one user owning two rooms makes SQLAlchemy
    # pick one arbitrarily (and warn) -- which would quietly break _is_room_owner
    # for the room under test.
    other_owner = dbmod.User(
        id=str(uuid.uuid4()), username=f"other_{uuid.uuid4().hex[:6]}",
        display_name="OtherOwner", password_hash="x", is_active=True,
        can_control=True, role="watcher",
    )
    session = dbmod.SessionLocal()
    try:
        # Circular foreign keys: users.current_room_id -> rooms.id, while
        # rooms.owner_id -> users.id. Neither can be inserted first, so a room is
        # created with its owner's room pointer still unset and the pointer is
        # filled in afterwards.
        session.add(owner)
        session.add(member)
        session.add(other_owner)
        session.flush()
        session.add(dbmod.Room(
            id=room_code, name="pytest room", owner_id=owner.id,
        ))
        session.add(dbmod.Room(
            id=other_room_code, name="other room", owner_id=other_owner.id,
        ))
        session.flush()
        owner.current_room_id = room_code
        member.current_room_id = room_code
        other_owner.current_room_id = other_room_code
        session.commit()
    finally:
        session.close()


    app_module.rooms.get(room_code)  # materialise the in-memory room
    app_module.rooms.get(other_room_code)

    yield {
        "url": f"http://127.0.0.1:{port}",
        # The app mounts Socket.IO at "stream/socket.io", not the default
        # "/socket.io"; clients must be told or the handshake 404s.
        "socketio_path": "stream/socket.io",
        "module": app_module,
        "db": dbmod,
        "client": socketio_client,
        "room_code": room_code,
        "other_room_code": other_room_code,
        "owner": {"id": owner.id, "token": dbmod.create_access_token(owner.id)},
        "member": {"id": member.id, "token": dbmod.create_access_token(member.id)},
    }
