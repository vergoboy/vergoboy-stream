import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    SECRET_KEY = os.environ.get("STREAM_SECRET_KEY", "change-this-secret-please")

    HOST = os.environ.get("STREAM_HOST", "127.0.0.1")
    PORT = int(os.environ.get("STREAM_PORT", "8801"))

    MEDIA_DIR = os.path.join(BASE_DIR, "media")
    UPLOAD_DIR = os.path.join(MEDIA_DIR, "uploads")   # raw/original uploads before encode
    SUBS_DIR = os.path.join(MEDIA_DIR, "subs")
    HLS_DIR = os.path.join(MEDIA_DIR, "hls")          # per-item progressive multi-bitrate HLS output
    CHAT_IMG_DIR = os.path.join(MEDIA_DIR, "chat")    # chat-uploaded images
    AVATAR_DIR = os.path.join(MEDIA_DIR, "avatars")   # user-uploaded avatars
    DATA_DIR = os.path.join(BASE_DIR, "data")
    DATA_FILE = os.path.join(DATA_DIR, "state.json")

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
    HLS_PRESET = os.environ.get("STREAM_HLS_PRESET", "ultrafast")  # was "ultrafast"

    # Max number of ffmpeg transcode jobs allowed to run at the same time.
    # Extra items go to status "queued" until a slot frees up. Protects the
    # server's CPU from being overwhelmed by simultaneous heavy encodes.
    MAX_CONCURRENT_ENCODES = int(os.environ.get("STREAM_MAX_CONCURRENT_ENCODES", "2"))

    # Once this many seconds of every rendition are encoded, the item flips
    # to status "ready" and playback can start, even though encoding of the
    # rest of the file continues in the background. Kept low so first
    # playback is available well inside the ~2 minute target.
    HLS_READY_AFTER_SECONDS = int(os.environ.get("STREAM_HLS_READY_AFTER_SECONDS", "20"))

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
        "STREAM_RTMP_PUSH_TEMPLATE", "rtmp://vergoboy.ir:1935/live/{key}"
    )
    HLS_PLAYBACK_URL_TEMPLATE = os.environ.get(
        "STREAM_HLS_PLAYBACK_TEMPLATE", "https://vergoboy.ir/hls/{key}/index.m3u8"
    )

    # yt-dlp settings for YouTube and other supported sites.
    YTDLP_PROXY = os.environ.get("STREAM_YTDLP_PROXY", "socks5://127.0.0.1:1080")
    YTDLP_COOKIES = os.environ.get("STREAM_YTDLP_COOKIES", "/opt/stream/cookies.txt")
    YTDLP_FORMAT = os.environ.get(
        "STREAM_YTDLP_FORMAT",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    )


os.makedirs(Config.UPLOAD_DIR, exist_ok=True)
os.makedirs(Config.SUBS_DIR, exist_ok=True)
os.makedirs(Config.HLS_DIR, exist_ok=True)
os.makedirs(Config.CHAT_IMG_DIR, exist_ok=True)
os.makedirs(Config.AVATAR_DIR, exist_ok=True)
os.makedirs(Config.DATA_DIR, exist_ok=True)