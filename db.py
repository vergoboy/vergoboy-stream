"""
Accounts, rooms and admin data, persisted in PostgreSQL (same stack as the
arman-music backend: SQLAlchemy + psycopg2 + JWT + bcrypt).

Only used for auth/rooms/permissions — the live watch-party state (playlist,
chat, encodes) stays in the in-memory RoomState objects in state.py.
"""
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from jwt.exceptions import PyJWTError
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, delete, func, select, text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from config import Config

_ddl_lock = threading.Lock()

engine = create_engine(
    Config.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 10},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    password_hash: Mapped[str] = mapped_column(String(128))
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Email verification (null when already verified or when the user signed
    # in via OAuth, whose emails are verified by the provider).
    verification_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verification_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Password reset (one-time tokens, cleared after use).
    reset_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reset_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # OAuth identity (provider + provider-side user id), used to find/link
    # accounts when the same person signs in again via Google/GitHub.
    oauth_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    oauth_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Role decides what a user is allowed to do. Admin = dashboard access;
    # controller = can drive playback + add media; watcher = watch only.
    role: Mapped[str] = mapped_column(String(16), default="watcher", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Per-user "custom" permissions, editable from the admin dashboard.
    can_control: Mapped[bool] = mapped_column(Boolean, default=False)  # drive play/pause/seek
    youtube_allowed: Mapped[bool] = mapped_column(Boolean, default=False)  # may add YouTube links
    upload_quota: Mapped[int] = mapped_column(Integer, default=Config.DEFAULT_UPLOAD_QUOTA)  # -1 = unlimited
    uploads_used: Mapped[int] = mapped_column(Integer, default=0)  # media added by this user

    # A user is "active" in exactly ONE room at a time.
    current_room_id: Mapped[str | None] = mapped_column(ForeignKey("rooms.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    own_room: Mapped["Room | None"] = relationship(
        back_populates="owner", uselist=False, foreign_keys="Room.owner_id"
    )

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "email": self.email,
            "email_verified": self.email_verified,
            "role": self.role,
            "can_control": self.can_control,
            "youtube_allowed": self.youtube_allowed,
            "upload_quota": self.upload_quota,
            "uploads_used": self.uploads_used,
            "current_room_id": self.current_room_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String(8), primary_key=True)  # short invite code
    name: Mapped[str] = mapped_column(String(64), default="")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped["User"] = relationship(back_populates="own_room", foreign_keys="Room.owner_id")

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "owner_id": self.owner_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RoomBan(Base):
    """Room-level ban: a user kicked out of a room cannot rejoin it until
    the room's owner/promoted user unbans them (managed from the room's
    member panel)."""

    __tablename__ = "room_bans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    room_id: Mapped[str] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    banned_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ── Password / token helpers (mirror arman-music's app/core/security.py) ─────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _sign(payload: dict, ttl: timedelta) -> str:
    payload = dict(payload)
    payload["exp"] = datetime.now(timezone.utc) + ttl
    # A JWT's `exp` is a whole number of SECONDS, so two tokens minted for the
    # same user within the same second were byte-identical. That made a second
    # login (a second tab, another device, or simply retrying) collide on the
    # unique refresh-token index and fail with a 500. A random `jti` makes every
    # token unique without changing any of the claims we actually rely on.
    payload["jti"] = uuid.uuid4().hex
    return jwt.encode(payload, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)


def create_access_token(user_id: str) -> str:
    return _sign({"sub": user_id, "type": "access"}, timedelta(minutes=Config.JWT_ACCESS_TTL_MINUTES))


def create_refresh_token(user_id: str) -> str:
    return _sign({"sub": user_id, "type": "refresh"}, timedelta(days=Config.JWT_REFRESH_TTL_DAYS))


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, Config.JWT_SECRET, algorithms=[Config.JWT_ALGORITHM])
    except PyJWTError:
        return None


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db():
    """Yields a session, committing on success / rolling back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_user_by_id(session, user_id: str) -> User | None:
    return session.get(User, user_id)


def get_user_by_username_or_email(session, login: str) -> User | None:
    login = (login or "").strip()
    if not login:
        return None
    if "@" in login:
        return session.execute(
            select(User).where((User.email == login) | (User.username == login))
        ).scalar_one_or_none()
    return session.execute(select(User).where(User.username == login)).scalar_one_or_none()


def get_user_by_email(session, email: str) -> User | None:
    email = (email or "").strip().lower()
    if not email:
        return None
    return session.execute(select(User).where(User.email == email)).scalar_one_or_none()


def get_user_by_oauth(session, provider: str, oauth_id: str) -> User | None:
    return session.execute(
        select(User).where(User.oauth_provider == provider, User.oauth_id == oauth_id)
    ).scalar_one_or_none()


def new_verification_token() -> str:
    return secrets.token_urlsafe(40)


def is_special_account(email: str | None) -> bool:
    """Whether this verified address is the explicitly configured admin."""
    return bool(Config.ADMIN_EMAIL and email and email.strip().lower() == Config.ADMIN_EMAIL)


def create_room_code(session) -> str:
    """Random unambiguously-readable room code, unique across the table."""
    alpha = Config.ROOM_CODE_ALPHABET
    for _ in range(50):
        code = "".join(secrets.choice(alpha) for _ in range(Config.ROOM_CODE_LEN))
        if session.get(Room, code) is None:
            return code
    return f"R{int(time.time()) % 1000000:06d}"


def ensure_own_room(session, user: User) -> Room:
    """Every user owns exactly one room (created lazily on first login)."""
    if user.own_room:
        return user.own_room
    room = Room(id=create_room_code(session), name=f"{user.username}",
                owner_id=user.id)
    session.add(room)
    session.flush()
    return room


def is_banned(session, room_id: str, user_id: str) -> bool:
    if not room_id or not user_id:
        return False
    return (
        session.execute(
            select(RoomBan).where(
                RoomBan.room_id == room_id, RoomBan.user_id == user_id
            )
        ).first()
        is not None
    )


def banned_room_ids(session, user_id: str) -> set[str]:
    if not user_id:
        return set()
    return {
        r.room_id
        for r in session.execute(
            select(RoomBan).where(RoomBan.user_id == user_id)
        ).scalars()
    }


def ban_user(session, room_id: str, user_id: str, banned_by: str | None = None) -> None:
    if not room_id or not user_id:
        return
    if is_banned(session, room_id, user_id):
        return
    session.add(RoomBan(room_id=room_id, user_id=user_id, banned_by=banned_by))


def unban_user(session, room_id: str, user_id: str) -> None:
    if not room_id or not user_id:
        return
    session.execute(
        delete(RoomBan).where(
            RoomBan.room_id == room_id, RoomBan.user_id == user_id
        )
    )


def init_db():
    """Creates tables if missing. Called once at startup."""
    with _ddl_lock:
        Base.metadata.create_all(engine)
        _migrate_columns()


def _migrate_columns():
    """Adds columns introduced after the original schema was deployed.
    create_all() only creates missing tables, so new columns on existing
    tables must be added explicitly (idempotent, safe to re-run)."""
    with engine.begin() as conn:
        existing = {
            (r.table_name, r.column_name)
            for r in conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema()"
                )
            )
        }
    adds = [
        ("users", "reset_token", "VARCHAR(128)"),
        ("users", "reset_expires", "TIMESTAMPTZ"),
    ]
    missing = [(t, c, d) for (t, c, d) in adds if (t, c) not in existing]
    if not missing:
        return
    with engine.begin() as conn:
        for table, col, dtype in missing:
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {dtype}'))
