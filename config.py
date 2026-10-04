import os
import ipaddress
from urllib.parse import urlsplit

from media_pipeline.runner import RunnerConfig

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _livekit_creds():
    """Read LiveKit credentials from the environment or its root-managed config."""
    key = os.environ.get("STREAM_LIVEKIT_API_KEY") or ""
    secret = os.environ.get("STREAM_LIVEKIT_API_SECRET") or ""
    if key and secret:
        return key, secret
    default_cfg = (
        "/etc/livekit/config.yaml"
        if os.environ.get("STREAM_ENV", "development").strip().lower() == "production"
        else os.path.expanduser("~/.local/share/vergoboy-stream-dev/livekit/config.yaml")
    )
    cfg = os.environ.get("STREAM_LIVEKIT_CONFIG", default_cfg)
    try:
        import yaml

        with open(cfg, encoding="utf-8") as f:
            keys = (yaml.safe_load(f) or {}).get("keys", {})
        if isinstance(keys, dict) and keys:
            key, secret = next(iter(keys.items()))
            return str(key), str(secret)
    except Exception:
        pass
    return None, None


def _is_dummy_livekit_value(value: str | None) -> bool:
    if not value:
        return True
    text = value.strip().lower()
    return any(token in text for token in ("dummy", "placeholder", "example", "not-used", "dev-key", "dev-secret"))


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is missing")
    return value


class Config:
    ENVIRONMENT = os.environ.get("STREAM_ENV", "development").strip().lower()
    SECRET_KEY = _required_env("STREAM_SECRET_KEY")

    HOST = os.environ.get("STREAM_HOST", "127.0.0.1")
    PORT = int(os.environ.get("STREAM_PORT", "8801"))

    # ---------------------------------------------------------------
    # Accounts, rooms & admin (PostgreSQL, same stack as arman-music)
    # ---------------------------------------------------------------
    DATABASE_URL = _required_env("STREAM_DATABASE_URL")
    JWT_SECRET = _required_env("STREAM_JWT_SECRET")
    JWT_ALGORITHM = "HS256"
    JWT_ACCESS_TTL_MINUTES = int(os.environ.get("STREAM_JWT_ACCESS_TTL_MINUTES", "4320"))  # 3 days
    JWT_REFRESH_TTL_DAYS = int(os.environ.get("STREAM_JWT_REFRESH_TTL_DAYS", "30"))

    # Room codes are 6 chars from an unambiguous alphabet (no 0/O/1/I/L).
    ROOM_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    ROOM_CODE_LEN = 6

    # ---------------------------------------------------------------
    # Email verification & OAuth login
    # ---------------------------------------------------------------
    # Public base URL of the frontend (used in verification links and
    # OAuth redirect URIs).
    SITE_BASE_URL = os.environ.get("STREAM_SITE_URL", "https://vergoboy.ir/stream")
    SITE_ORIGIN = f"{urlsplit(SITE_BASE_URL).scheme}://{urlsplit(SITE_BASE_URL).netloc}"
    CORS_ORIGINS = tuple(
        origin.strip()
        for origin in os.environ.get(
            "STREAM_CORS_ORIGINS",
            ",".join(
                (SITE_ORIGIN, "tauri://localhost")
                if ENVIRONMENT == "production"
                else ("http://localhost:3000", "http://127.0.0.1:3000", "tauri://localhost")
            ),
        ).split(",")
        if origin.strip()
    )
    # Envelope/From address for outgoing verification mail (the server's
    # own mailbox on the local postfix).
    MAIL_FROM = os.environ.get("STREAM_MAIL_FROM", "info@vergoboy.ir")
    # The site owner's account — the only one that skips email verification.
    ADMIN_EMAIL = os.environ.get("STREAM_ADMIN_EMAIL", "").strip().lower()
    VERIFY_TOKEN_TTL_HOURS = int(os.environ.get("STREAM_VERIFY_TTL_HOURS", "72"))
    RESET_TOKEN_TTL_MINUTES = int(os.environ.get("STREAM_RESET_TTL_MINUTES", "30"))

    # OAuth2 providers (reuses the same Google/GitHub apps as arman-music).
    GOOGLE_OAUTH_CLIENT_ID = os.environ.get("STREAM_GOOGLE_OAUTH_CLIENT_ID", "")
    GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("STREAM_GOOGLE_OAUTH_CLIENT_SECRET", "")
    GITHUB_OAUTH_CLIENT_ID = os.environ.get("STREAM_GITHUB_OAUTH_CLIENT_ID", "")
    GITHUB_OAUTH_CLIENT_SECRET = os.environ.get("STREAM_GITHUB_OAUTH_CLIENT_SECRET", "")

    # Default per-user limits for a fresh signup (watcher).
    DEFAULT_UPLOAD_QUOTA = int(os.environ.get("STREAM_DEFAULT_UPLOAD_QUOTA", "50"))

    # ---------------------------------------------------------------
    # Digimoviez movie archive. Credentials remain environment-only.
    # The archive is intentionally movie-only; legacy series/anime parsers are
    # retained for compatibility but are never registered as collectors.
    # ---------------------------------------------------------------
    ARCHIVE_MOVIES_ENABLED = os.environ.get("STREAM_ARCHIVE_MOVIES_ENABLED", "true").lower() == "true"
    ARCHIVE_SERIES_ENABLED = False
    ARCHIVE_ANIME_ENABLED = False
    ARCHIVE_ANIMATION_ENABLED = False
    ARCHIVE_BASE_URL = os.environ.get("STREAM_ARCHIVE_BASE_URL", "https://digimoviez.com").rstrip("/")
    ARCHIVE_HTTP_PROXY = os.environ.get("STREAM_ARCHIVE_HTTP_PROXY", "http://127.0.0.1:10808").strip()
    ARCHIVE_LOG_LEVEL = os.environ.get("STREAM_ARCHIVE_LOG_LEVEL", "INFO").upper()
    ARCHIVE_AUTH_ENABLED = os.environ.get("STREAM_ARCHIVE_AUTH_ENABLED", "false").lower() == "true"
    ARCHIVE_LOGIN_URL = os.environ.get("STREAM_ARCHIVE_LOGIN_URL", f"{ARCHIVE_BASE_URL}/account/login/")
    ARCHIVE_AUTH_CHECK_URL = os.environ.get("STREAM_ARCHIVE_AUTH_CHECK_URL", f"{ARCHIVE_BASE_URL}/account/")
    ARCHIVE_USERNAME = os.environ.get("DIGIMOVIEZ_USERNAME", "")
    ARCHIVE_PASSWORD = os.environ.get("DIGIMOVIEZ_PASSWORD", "")
    # Target form names are explicitly configured rather than guessed.
    ARCHIVE_LOGIN_USERNAME_FIELD = os.environ.get("STREAM_ARCHIVE_LOGIN_USERNAME_FIELD", "")
    ARCHIVE_LOGIN_PASSWORD_FIELD = os.environ.get("STREAM_ARCHIVE_LOGIN_PASSWORD_FIELD", "")
    ARCHIVE_SESSION_FILE = os.environ.get("STREAM_ARCHIVE_SESSION_FILE", os.path.join(BASE_DIR, "data", "digimoviez-session.json"))
    ARCHIVE_AUTH_CHECK_INTERVAL = float(os.environ.get("STREAM_ARCHIVE_AUTH_CHECK_INTERVAL", "120"))
    ARCHIVE_REQUEST_TIMEOUT = float(os.environ.get("STREAM_ARCHIVE_REQUEST_TIMEOUT", "30"))
    ARCHIVE_LOGIN_RETRY_COUNT = int(os.environ.get("STREAM_ARCHIVE_LOGIN_RETRY_COUNT", "3"))
    ARCHIVE_CONNECT_TIMEOUT = float(os.environ.get("STREAM_ARCHIVE_CONNECT_TIMEOUT", "10"))
    ARCHIVE_READ_TIMEOUT = float(os.environ.get("STREAM_ARCHIVE_READ_TIMEOUT", "30"))
    ARCHIVE_TOTAL_TIMEOUT = float(os.environ.get("STREAM_ARCHIVE_TOTAL_TIMEOUT", "60"))
    ARCHIVE_REQUEST_RETRY_COUNT = int(os.environ.get("STREAM_ARCHIVE_REQUEST_RETRY_COUNT", "3"))

    MEDIA_DIR = os.path.join(BASE_DIR, "media")
    UPLOAD_DIR = os.path.join(MEDIA_DIR, "uploads")   # raw/original uploads before encode
    SUBS_DIR = os.path.join(MEDIA_DIR, "subs")
    HLS_DIR = os.path.join(MEDIA_DIR, "hls")          # per-item progressive multi-bitrate HLS output
    CHAT_IMG_DIR = os.path.join(MEDIA_DIR, "chat")    # chat-uploaded images
    AVATAR_DIR = os.path.join(MEDIA_DIR, "avatars")   # user-uploaded avatars
    DATA_DIR = os.path.join(BASE_DIR, "data")
    DATA_FILE = os.path.join(DATA_DIR, "state.json")
    # name(lowercased) -> avatar_url map, persisted so uploaded avatars survive
    # server restarts (chat messages themselves stay in-memory only).
    AVATAR_MAP_FILE = os.path.join(DATA_DIR, "avatars.json")

    # Max upload size (MB) - the real limit is also enforced by Nginx's
    # client_max_body_size, since Flask only checks this after the request
    # body starts arriving.
    MAX_CONTENT_LENGTH = int(os.environ.get("STREAM_MAX_UPLOAD_MB", "8192")) * 1024 * 1024

    ALLOWED_VIDEO_EXT = {"mp4", "webm", "ogg", "ogv", "mov", "m4v", "mkv"}
    ALLOWED_SUB_EXT = {"vtt", "srt"}
    ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif"}

    # ---------------------------------------------------------------
    # Progressive multi-bitrate HLS transcoding
    # ---------------------------------------------------------------
    # Rendition ladder — every added video is transcoded into up to 3
    # renditions, picked from this table based on the SOURCE height
    # (never upscaled past the source's own resolution).
    HLS_LADDER = [
        # (label, target_height, video_bitrate, audio_bitrate)
        ("1080p", 1080, "5000k", "160k"),
        ("720p",  720,  "2800k", "128k"),
        ("480p",  480,  "1400k", "128k"),
        ("360p",  360,  "800k",  "96k"),
    ]
    HLS_MAX_RENDITIONS = 3
    HLS_SEGMENT_SECONDS = 4

    # NOTE: the x264 speed preset is NOT configured here. It is resolved by
    # media_pipeline.ffmpeg_cmd.resolve_preset() from STREAM_HLS_PRESET, falling
    # back to "veryfast". It used to live here with a default of "ultrafast",
    # which nothing read — a setting that looks real but changes nothing, and
    # that disagrees with what the encoder actually does.

    # Max number of ffmpeg transcode jobs allowed to run at the same time.
    # Extra items go to status "queued" until a slot frees up. Protects the
    # server's CPU from being overwhelmed by simultaneous heavy encodes.
    MAX_CONCURRENT_ENCODES = int(os.environ.get("STREAM_MAX_CONCURRENT_ENCODES", "2"))

    # Copy-only jobs ("remuxes") cost almost no CPU, so they get their own,
    # larger pool. Sharing the transcode pool would let a queue of trivial jobs
    # block a long transcode, while a long transcode would in turn stall every
    # remux behind it.
    MAX_CONCURRENT_REMUX = int(os.environ.get("STREAM_MAX_CONCURRENT_REMUX", "4"))

    # Once this many seconds of every rendition are encoded, the item flips
    # to status "ready" and playback can start, even though encoding of the
    # rest of the file continues in the background. Kept low so first
    # playback is available well inside the ~2 minute target.
    HLS_READY_AFTER_SECONDS = int(os.environ.get("STREAM_HLS_READY_AFTER_SECONDS", "20"))

    # ---------------------------------------------------------------
    # Encode supervision
    #
    # These are re-exported from media_pipeline.runner.RunnerConfig rather
    # than re-declared, because that class is the code that actually reads
    # them and it already validates them. Declaring a second copy here is how
    # a deployment ends up disagreeing with itself about how many retries a
    # job gets.
    # ---------------------------------------------------------------
    ENCODE_STALL_SECONDS = RunnerConfig.STALL_SECONDS
    ENCODE_MAX_RETRIES = RunnerConfig.MAX_RETRIES
    ENCODE_RETRY_BASE_DELAY = RunnerConfig.RETRY_BASE_DELAY
    ENCODE_TIMEOUT_BASE_SECONDS = RunnerConfig.TIMEOUT_BASE
    ENCODE_TIMEOUT_PER_MEDIA_SECOND = RunnerConfig.TIMEOUT_PER_SECOND
    ENCODE_MAX_TIMEOUT_SECONDS = RunnerConfig.MAX_TIMEOUT
    ENCODE_HW_FAILURE_THRESHOLD = RunnerConfig.HW_FAILURES_BEFORE_DISABLE
    ENCODE_HW_DISABLE_SECONDS = RunnerConfig.HW_DISABLE_SECONDS

    # ---------------------------------------------------------------
    # Chat (in-memory only, never written to state.json / disk)
    # ---------------------------------------------------------------
    CHAT_RETENTION_SECONDS = int(os.environ.get("STREAM_CHAT_RETENTION_SECONDS", str(24 * 3600)))
    CHAT_PURGE_INTERVAL_SECONDS = 600
    CHAT_MAX_MESSAGES = 500
    CHAT_MAX_TEXT_LEN = 500

    # ---------------------------------------------------------------
    # Live streaming from an external device (OBS, phone app, etc.)
    # An RTMP server is assumed to already be running on this host; this
    # app only generates a random stream key and the push/playback URLs
    # from these templates. If your RTMP server layout differs, override
    # via env vars. {key} is substituted with the generated key.
    # ---------------------------------------------------------------
    RTMP_PUSH_URL_TEMPLATE = os.environ.get(
        "STREAM_RTMP_PUSH_TEMPLATE", "rtmps://vergoboy.ir:8443/live/{key}"
    )
    # Extra RTMP ingest ports (all served by the same nginx-rtmp application).
    # Iranian ISPs commonly block the default RTMP port 1935, so we advertise
    # alternates — the user picks whichever one their connection allows.
    RTMP_ALTERNATE_PORTS = os.environ.get("STREAM_RTMP_ALT_PORTS", "1935").split(",")
    # MediaMTX writes HLS per-path under {hlsDirectory}/{path}/index.m3u8, served
    # by nginx at /hls/ (alias /opt/stream/hls-live). {key} is the stream key.
    HLS_PLAYBACK_URL_TEMPLATE = os.environ.get(
        "STREAM_HLS_PLAYBACK_TEMPLATE", "https://vergoboy.ir/hls/live/{key}/index.m3u8"
    )

    # yt-dlp settings for YouTube and other supported sites.
    YTDLP_PROXY = os.environ.get("STREAM_YTDLP_PROXY", "").strip()
    YTDLP_COOKIES = os.environ.get("STREAM_YTDLP_COOKIES", "").strip()
    YTDLP_FORMAT = os.environ.get(
        "STREAM_YTDLP_FORMAT",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    )

    # ---------------------------------------------------------------
    # LiveKit voice room (audio-only watch-party voice channel)
    # ---------------------------------------------------------------
    _LK_KEY, _LK_SECRET = _livekit_creds()
    LIVEKIT_API_KEY = _LK_KEY or ""
    LIVEKIT_API_SECRET = _LK_SECRET or ""
    LIVEKIT_URL = os.environ.get("STREAM_LIVEKIT_URL", "").strip()
    LIVEKIT_CONFIGURED = bool(
        LIVEKIT_API_KEY
        and LIVEKIT_API_SECRET
        and LIVEKIT_URL
        and not _is_dummy_livekit_value(LIVEKIT_API_KEY)
        and not _is_dummy_livekit_value(LIVEKIT_API_SECRET)
    )
    LIVEKIT_ROOM = os.environ.get("STREAM_LIVEKIT_ROOM", "stream-voice")
    LIVEKIT_TOKEN_TTL = int(os.environ.get("STREAM_LIVEKIT_TOKEN_TTL", "14400"))

    @classmethod
    def validate(cls):
        if cls.ENVIRONMENT not in {"development", "production"}:
            raise RuntimeError("STREAM_ENV must be 'development' or 'production'")
        if len(cls.SECRET_KEY) < 32 or len(cls.JWT_SECRET) < 32:
            raise RuntimeError("STREAM_SECRET_KEY and STREAM_JWT_SECRET must each contain at least 32 characters")
        if not cls.DATABASE_URL.startswith("postgresql"):
            raise RuntimeError("STREAM_DATABASE_URL must point to a persistent PostgreSQL database")
        if cls.ENVIRONMENT == "production":
            if not os.environ.get("STREAM_SITE_URL") or not cls.ADMIN_EMAIL:
                raise RuntimeError("Production requires explicit STREAM_SITE_URL and STREAM_ADMIN_EMAIL values")
            if not cls.LIVEKIT_CONFIGURED:
                raise RuntimeError("Production requires real LiveKit credentials and STREAM_LIVEKIT_URL")
            site = urlsplit(cls.SITE_BASE_URL)
            voice = urlsplit(cls.LIVEKIT_URL)
            try:
                loopback_voice = bool(voice.hostname and ipaddress.ip_address(voice.hostname).is_loopback)
            except ValueError:
                loopback_voice = bool(voice.hostname and voice.hostname.endswith(".localhost"))
            if site.scheme != "https" or not site.hostname:
                raise RuntimeError("Production STREAM_SITE_URL must use HTTPS")
            if voice.scheme != "wss" or not voice.hostname or voice.hostname == "localhost" or loopback_voice:
                raise RuntimeError("Production STREAM_LIVEKIT_URL must be a reachable WSS endpoint")


os.makedirs(Config.UPLOAD_DIR, exist_ok=True)
os.makedirs(Config.SUBS_DIR, exist_ok=True)
os.makedirs(Config.HLS_DIR, exist_ok=True)
os.makedirs(Config.CHAT_IMG_DIR, exist_ok=True)
os.makedirs(Config.AVATAR_DIR, exist_ok=True)
os.makedirs(Config.DATA_DIR, exist_ok=True)
