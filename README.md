# vergoboy.ir / stream — Synchronized Watch Party Player

A real-time synchronized video streaming platform for `vergoboy.ir/stream`. Users gather in a watch room where playback state (play/pause/seek/rate/shuffle) stays in sync across all participants, with per-user independent subtitle language and audio track selection.

---

## Quick Start

Get from a fresh clone to a running dev server in about five minutes.

### 0. Prerequisites

| Requirement | Version | Needed for |
|---|---|---|
| Python | 3.12 | backend and pinned Python lock |
| Node.js | 20.9+ | Next.js 16 build |
| PostgreSQL | 13+ for production | accounts, rooms, admin panel; the dev runner provisions a persistent local instance |
| `uv` (optional) | current stable | preferred Python lockfile installer; `run.sh` falls back to pip |
| `ffmpeg` + `ffprobe` | installed on `PATH` | HLS transcoding, subtitle extraction |

```bash
sudo apt install -y ffmpeg python3-venv
```

### 1. Clone and install

```bash
git clone https://github.com/vergoboy/vergoboy-stream.git
cd vergoboy-stream

./run.sh setup
```

### 2. Create the database

For local development, `./run.sh setup` installs the Python and npm lockfiles,
initializes an isolated persistent PostgreSQL cluster, and generates a random
database password in the ignored local environment file. For production,
provision a persistent PostgreSQL database and use its real connection URI;
there is no SQLite or in-memory fallback.

Tables are currently created and incrementally adjusted at startup by
`db.init_db()`; this project does not yet have versioned Alembic migrations.

### 3. Configure

Put real, independent secrets in `.stream_db.env` — it is git-ignored, and it
is what `deploy/vergoboy-stream.service` loads via `EnvironmentFile=`. Set
`STREAM_ADMIN_EMAIL` to the operator's verified address; first signup is not
automatically privileged.

The local runner starts a real, checksum-verified LiveKit server and generates
a random local keypair outside the repository. Production must use a separate
LiveKit config and a reachable WSS URL.

Everything else has a local default in `config.py` — bind host/port, upload
cap, HLS preset, and RTMP/HLS URL templates. The yt-dlp proxy and cookies are
optional and unset unless configured. See
[Configuration](#configuration) for the full list.

### 4. Run it

```bash
./run.sh start
```

The runner starts PostgreSQL, the real local LiveKit SFU, Flask, and Next.js.
Open **http://localhost:3000/stream/**.

The configured admin account receives the `admin` role only after its email is
verified (or after a verified OAuth provider authenticates that address).

### 5. Run it for production

```bash
# Build the static export into webapp/out/, then publish it
cd webapp && npm run build
mkdir -p webapp-out && rsync -a --delete webapp/out/ webapp-out/ && cd ..

# Backend and LiveKit units; create the protected LiveKit config as described below.
sudo cp deploy/vergoboy-stream.service deploy/vergoboy-frontend-build.service /etc/systemd/system/
sudo cp deploy/livekit-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now livekit-server vergoboy-frontend-build vergoboy-stream

# Nginx: paste deploy/nginx-stream.conf into your server{} block
sudo nginx -t && sudo systemctl reload nginx
```

### Self-hosted LiveKit

The existing production architecture already routes `wss://vergoboy.ir/livekit`
through Nginx to a local SFU on port `7880`. There was no LiveKit service in
the repository or on this development host. The deployment unit pins the
official LiveKit Server `v1.13.7` binary at
`/usr/local/lib/livekit/v1.13.7/livekit-server`; install that exact release
for the server architecture and verify its published checksum before enabling
the unit. The Linux amd64 asset SHA-256 is
`6634aeeb2fb1366b6723708ae4320b9d5408106a4c63457c5e845ae3979c90e2`.

Create the service account and a root-managed config containing generated
credentials. Do not commit this file. The `www-data` group is used deliberately
so the backend can read the same key pair without copying secrets into the app
environment file:

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin livekit
sudo install -d -o root -g www-data -m 0750 /etc/livekit
sudo sh -c 'umask 027
key=$(openssl rand -hex 12)
secret=$(openssl rand -hex 32)
cat > /etc/livekit/config.yaml <<EOF
port: 7880
log_level: info
rtc:
  tcp_port: 7881
  port_range_start: 50000
  port_range_end: 60000
  use_external_ip: true
keys:
  ${key}: ${secret}
EOF
chown root:www-data /etc/livekit/config.yaml
chmod 0640 /etc/livekit/config.yaml'
sudo systemctl enable --now livekit-server
```

Keep port `7880` private behind Nginx/WSS. Allow TCP `7881` and UDP
`50000-60000` through the host and cloud firewall for ICE media; the configured
public-IP discovery requires working outbound STUN. TURN is not configured:
restrictive client networks may additionally require a TURN hostname, valid
certificate, and reachable relay port, which must be provisioned on the real
domain rather than guessed here. The backend's `/stream/api/health` endpoint
probes PostgreSQL, writable storage, FFmpeg tools, and the configured LiveKit
endpoint.

Follow logs with `journalctl -u vergoboy-stream -f`. Redeploying the frontend
is just `sudo systemctl restart vergoboy-frontend-build`.

---

## Features

- **Shared watch rooms** — everyone in a room shares one playback clock; play, pause, seek, speed and shuffle changes are synchronized across all clients (with automatic drift correction). Rooms are invite codes; the default room is open to all.
- **Accounts** — email + password with email verification, Google/GitHub OAuth, forgot-password reset, and per-user upload quotas. Roles are `watcher` / `moderator` / `admin`; only moderators and admins may drive playback. The `admin` role also unlocks an admin dashboard (user list, delete user).
- **Room membership** — join codes, member lists, promote/demote, ban/unban.
- **LiveKit voice room** — audio-only push-to-talk voice channel alongside the video.
- **Real-time notifications** — streamed to all clients via Socket.IO (play/pause/seek events, users joining/leaving, new subtitles or audio tracks becoming available).
- **Shared playlist** — add videos via file upload or direct URL (HTTP/HTTPS/HLS), or import from YouTube / the series scraper. Items are persisted across server restarts.
- **Progressive multi-bitrate HLS transcoding** — uploaded/imported videos are transcoded into a ladder of HLS renditions (360p, 480p, 720p, 1080p where the source supports it). The default rendition starts encoding immediately; viewers can request additional qualities on demand. Playback starts as soon as the first ~20 seconds are encoded, while the rest continues in the background.
- **Dubbed audio tracks** — each audio track is a separate file synced with the video client-side. Selection is per-user and independent of the shared playback position.
- **Subtitles** — embedded text subtitle streams in the source file are automatically extracted and served as WebVTT. Subtitle style customization (font, size, color, background opacity, outline, bold, bottom offset) is local to each user.
- **Controls** — play/pause, 10s skip forward/back, previous/next item, shuffle (follows the room's shuffled order, wrapping around), volume, playback speed (0.5x-2x), fullscreen (portal-based for reliable fixed-position coverage), Picture-in-Picture.
- **Live streaming panel** — generate an RTMP stream key and get the push URL (for OBS/mobile apps) and HLS playback URL, using the existing RTMP/HLS server on the host. Manual HLS URLs can also be added as playlist items.
- **Browser screen-share streaming («استریم مستقیم با مرورگر»)** — Google-Meet-style presenting straight from the browser: pick a desktop/window/tab in the «استریم خارجی» tab and it is captured with `getDisplayMedia` and pushed over WebRTC (WHIP → MediaMTX → HLS), then auto-added to the playlist for everyone. Audio policy: only the picked surface's audio is streamed — tab shares carry that tab's sound; desktop/window shares are video-only (`systemAudio` is excluded) so other tabs/windows never leak into the stream, and if a vergoboy/stream page itself is captured its audio is dropped via the CaptureHandle API to prevent echo.
- **Keyboard shortcuts** — Space/k (play/pause), f (fullscreen), left/right arrow (seek 10s), up/down arrow (volume), 0-9 (seek to 10%-90%), ? (shortcuts help).
- **Visual identity** — matches vergoboy.ir design (colors, fonts, header/footer).

---

## Project Structure

```
/opt/stream/
├── app.py                           # Flask + Socket.IO backend (all routes)
├── config.py                        # Environment-driven configuration
├── state.py                         # Per-room state (in-memory + persisted)
├── db.py                            # SQLAlchemy models: users, rooms, invites
├── mail.py                          # Verification / password-reset email (SMTP)
├── livekit_auth.py                  # LiveKit access-token minting
├── archive_scraper.py               # Series/movie search + episode listing
├── srt_to_vtt.py                    # SRT -> WebVTT converter
├── trigger_add.py                   # CLI: add an item to a room from a URL
├── get_state.py                     # CLI: dump a room's current state
├── requirements.txt
├── webapp/                          # Next.js frontend (static export)
│   ├── app/                         #   Page components (Next.js App Router)
│   ├── components/                  #   React components (Player, Chat, Sofa, etc.)
│   ├── lib/                         #   Client library (API client, state hooks, types)
│   ├── public/                      #   Static assets
│   ├── src-tauri/                   #   Desktop/Android shell
│   ├── next.config.ts
│   ├── package.json
│   └── tsconfig.json
├── webapp-out/                      #   Built static export (served by Nginx)
├── static/  templates/              # Legacy Flask-served page (dev fallback)
├── media/
│   ├── uploads/                     #   Raw uploaded video files
│   ├── hls/{item_id}/              #   Per-item HLS renditions + master playlists
│   ├── subs/                        #   Extracted WebVTT subtitles
│   ├── chat/                        #   Chat-uploaded images
│   └── avatars/                     #   User-uploaded avatars
├── data/
│   ├── state.json                   #   Legacy single-room persistence
│   └── rooms/{code}.json            #   Per-room playlist/position
├── hls-live/                        #   Live RTMP/WHIP -> HLS output (runtime)
├── deploy/
│   ├── vergoboy-stream.service      #   systemd unit for the Flask backend
│   ├── vergoboy-frontend-build.service  # systemd unit for the Next.js build
│   └── nginx-stream.conf            #   Nginx location snippets
└── .gitignore
```

---

## Architecture

### Backend (app.py, state.py, db.py, config.py)

The backend is a single-process Flask application using **gevent** for async I/O and **python-socketio** for real-time communication. It runs on `127.0.0.1:8801` and is served through Nginx, which terminates TLS and static assets.

- **Room state** is held in in-memory `RoomState` objects (one per invite code, created lazily and cached in `state.rooms`), each protected by a `threading.Lock`. The playlist and current playback position are persisted to `data/rooms/<code>.json` so they survive server restarts.
- **Accounts, rooms and membership** live in PostgreSQL via SQLAlchemy (`db.py`) — users, credentials, roles, room records and bans. Passwords are bcrypt-hashed; sessions are JWT access + refresh tokens. Playback state is deliberately *not* in the database.
- **Socket.IO events** — every connected client receives `state_sync` broadcasts with the full room state. Control actions (play/pause/seek/rate/select/shuffle) are sent via the `control` event, permission-checked, and immediately broadcast to that room's channel.
- **HLS transcoding** is progressive: ffmpeg encodes one rendition at a time to an EVENT-type HLS playlist, emitting `transcode_progress` events as segments are written. The item becomes playable (status: "ready") after `HLS_READY_AFTER_SECONDS` (default: 20s) of timeline are encoded.
- **Subtitle extraction** runs as a concurrent greenlet: embedded text subtitle streams are extracted with ffmpeg, converted to WebVTT, and added to the item's `subtitles` array.
- **Concurrency limit** — at most `MAX_CONCURRENT_ENCODES` (default: 2) ffmpeg processes run at once. Excess items wait in "queued" status, and interrupted queued encodes are re-kicked on boot.

### Frontend (webapp/)

The frontend is a **Next.js** application configured for **static export** (output: "export"). It uses:

- **Next.js App Router** with a single root layout + page.
- **Tailwind CSS v4** for styling (the `@tailwindcss/postcss` plugin).
- **socket.io-client** for real-time bidirectional communication with the backend.
- **hls.js** for HLS playback (progressive download of EVENT-type playlists, native HLS on Safari).
- **livekit-client** for the audio-only voice room.
- **framer-motion** for animated transitions (sofa seating area, menus, etc.).
- **Vazirmatn** (Persian) and **JetBrains Mono** fonts, loaded from the main vergoboy.ir site.

The export is built twice-over from one config: `NEXT_PUBLIC_BUILD_TARGET=web`
(the default, `basePath: /stream`, same-origin API) and `=tauri` (no basePath,
absolute `NEXT_PUBLIC_API_ORIGIN`) for the desktop/Android shell in `src-tauri/`.

### Client-Server Synchronization

1. The server maintains `position`, `playing`, and `updated_at`.
2. `current_position()` estimates the real-time position: if playing, it adds elapsed wall-clock time since `updated_at`.
3. Clients run a sync interval every 6 seconds: if the local video position drifts more than 1.2 seconds from the server's expected position, the client seeks to correct it.
4. The seek target is clamped to the **encoded frontier** (the number of seconds the HLS encoder has produced so far) so the sync loop never tries to jump into un-encoded territory.
5. An optimistic local update in `requestControl()` makes the UI feel instant while the server round-trip completes.

### Fullscreen

Fullscreen uses a **React Portal** pattern: the fullscreen overlay is rendered as a `fixed` child of `document.body`, escaping any ancestor transform/stacking contexts (e.g., framer-motion wrappers). The native `requestFullscreen()` API is called on the portal root to hide browser chrome on desktop. The video uses `object-cover` in fullscreen mode to fill the entire screen.

---

## Configuration

Runtime secrets and the PostgreSQL URL are mandatory. Local-only defaults are
used for bind addresses and processing limits; production startup validates
HTTPS/WSS domains and refuses missing secrets or LiveKit configuration.

| Variable | Default | Description |
|---|---|---|
| STREAM_ENV | development | `development` or `production`; systemd sets `production` explicitly |
| STREAM_HOST | 127.0.0.1 | Local bind host |
| STREAM_PORT | 8801 | Local bind port |
| STREAM_SECRET_KEY | required | Flask/session signing key, at least 32 characters |
| STREAM_DATABASE_URL | required | Persistent PostgreSQL SQLAlchemy URI; no fallback |
| STREAM_JWT_SECRET | required | Independent JWT access/refresh signing key, at least 32 characters |
| STREAM_SITE_URL | https://vergoboy.ir/stream | Public HTTPS base URL (verification links, OAuth redirects); explicit in production |
| STREAM_ADMIN_EMAIL | unset | Verified address promoted to admin; explicit in production |
| STREAM_CORS_ORIGINS | environment-specific | Comma-separated exact browser origins |
| STREAM_MAIL_FROM | info@vergoboy.ir | Envelope/From for outgoing mail |
| STREAM_GOOGLE_OAUTH_CLIENT_ID / _SECRET | (empty) | Google login |
| STREAM_GITHUB_OAUTH_CLIENT_ID / _SECRET | (empty) | GitHub login |
| STREAM_DEFAULT_UPLOAD_QUOTA | 50 | Per-user upload quota for new signups |
| STREAM_MAX_UPLOAD_MB | 8192 | Max video upload size (MB) — mirror in nginx `client_max_body_size` |
| STREAM_HLS_PRESET | veryfast | x264 speed preset for transcoding (ignored by hardware encodes) |
| STREAM_HLS_READY_AFTER_SECONDS | 20 | Seconds encoded before an item is playable |
| STREAM_MAX_CONCURRENT_ENCODES | 2 | Max simultaneous ffmpeg processes |
| STREAM_ENCODE_STALL_SECONDS | 120 | Kill an encode after this long with no progress |
| STREAM_ENCODE_MAX_RETRIES | 2 | Retries for the last fallback rung, jittered backoff |
| STREAM_ENCODE_HW_FAILURES | 3 | Hardware failures before the encoder is disabled |
| STREAM_ENCODE_HW_DISABLE_SECONDS | 600 | How long the hardware encoder stays disabled |
| STREAM_MAX_CONCURRENT_REMUX | 4 | Concurrent copy-only jobs (own pool — these are cheap) |
| STREAM_RTMP_PUSH_TEMPLATE | rtmpps://vergoboy.ir:8443/live/{key} | RTMP push URL template for live streaming |
| STREAM_RTMP_ALT_PORTS | 1935 | Extra ingest ports advertised to the user |
| STREAM_HLS_PLAYBACK_TEMPLATE | https://vergoboy.ir/hls/live/{key}/index.m3u8 | HLS playback URL template |
| STREAM_YTDLP_PROXY | socks5://127.0.0.1:1080 | Proxy for yt-dlp (YouTube imports) |
| STREAM_YTDLP_COOKIES | /opt/stream/cookies.txt | Cookies file for yt-dlp |
| STREAM_YTDLP_FORMAT | bestvideo[ext=mp4]+bestaudio[ext=m4a]/… | yt-dlp format selector |
| STREAM_ARCHIVE_MOVIES_ENABLED | true | Enables the sole Digimoviez movie archive collector; series/anime/animation collectors remain disabled |
| STREAM_ARCHIVE_AUTH_ENABLED | false | Enables the optional persistent Digimoviez session manager |
| STREAM_ARCHIVE_HTTP_PROXY | http://127.0.0.1:10808 | Mandatory proxy for Digimoviez website/auth/search/detail traffic only; extracted media URLs explicitly bypass all proxy environment variables |
| DIGIMOVIEZ_USERNAME / DIGIMOVIEZ_PASSWORD | unset | Archive credentials; never commit them |
| STREAM_ARCHIVE_LOGIN_USERNAME_FIELD / _PASSWORD_FIELD | unset | Target login-form field names; explicitly configure after an authorized form inspection |
| STREAM_ARCHIVE_AUTH_CHECK_INTERVAL | 120 | Seconds between archive session health checks |
| STREAM_ARCHIVE_REQUEST_TIMEOUT | 30 | Authentication request timeout in seconds |
| STREAM_ARCHIVE_LOGIN_RETRY_COUNT | 3 | Login retries with exponential backoff |
| STREAM_LIVEKIT_URL / _ROOM / _TOKEN_TTL | URL unset in development | Voice-room connection; production requires an explicit reachable WSS URL |
| STREAM_LIVEKIT_API_KEY / _API_SECRET | parsed from `/etc/livekit/config.yaml` | LiveKit token minting; never sent to clients |

Notes:

- `RTMP_PUSH_TEMPLATE` and `HLS_PLAYBACK_TEMPLATE` must match your existing
  RTMP/HLS server's path structure. The app only substitutes `{key}` with a
  randomly generated stream key — it does not run an RTMP or HLS server itself.
- LiveKit credentials are never committed: the backend reads environment
  variables first, then the structured `keys` mapping in
  `/etc/livekit/config.yaml`. Production fails startup if credentials or a
  non-local WSS URL are missing.
- `STREAM_YTDLP_COOKIES` is optional; drop it entirely for sources that do not
  need a logged-in session.
- `STREAM_PG_PASSWORD` is used only by the local `run.sh` PostgreSQL bootstrap;
  production database credentials are supplied in `STREAM_DATABASE_URL`.

### Fonts

The page reuses the local fonts served by the main vergoboy.ir app at
`/register/static/fonts/...`. Both apps share the domain, so no font files need
to be copied — just make sure the main app is running.

---

## Deployment notes

- **Units** — `deploy/vergoboy-stream.service` runs the backend as `www-data`
  with `ProtectSystem=full`; only `media/` and `data/` are writable.
  `deploy/vergoboy-frontend-build.service` is a `oneshot` build that the
  backend `Wants=`/`After=` for startup ordering, so re-running the build never
  stops the backend. `deploy/livekit-server.service` runs the pinned SFU as the
  `livekit` user and reads its protected config from `/etc/livekit/config.yaml`.
- **Nginx** — see the comments in `deploy/nginx-stream.conf`. Three things are
  easy to get wrong:
  - `/stream/` serves the static Next.js export from disk, not through Flask;
    only `/stream/api/` and `/stream/socket.io/` are proxied to `127.0.0.1:8801`
    (the latter needs the `Upgrade`/`Connection` headers).
  - `/stream/media/` is served with `alias` and Range support so video bytes
    never traverse Flask.
  - `/livekit/` proxies signaling and the HTTP health endpoint over TLS; actual
    WebRTC media uses direct ICE over the configured TCP/UDP port ranges.
  - `.m3u8` playlists under `/stream/media/hls/` must be `no-cache`: they grow
    while ffmpeg encodes, and a cached one makes hls.js re-read a stale
    2-3 segment playlist and stall.
- **Redeploying the frontend** is just `sudo systemctl restart vergoboy-frontend-build`.
- **Logs** — `journalctl -u vergoboy-stream -f`. The backend logs a
  `[DEBUG_STREAM]` line for every encode, control action, and error.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Backend exits immediately | Check required environment variables, PostgreSQL connectivity, the gevent dependency, and `journalctl -u vergoboy-stream`. Install locked dependencies with `uv pip sync --python /opt/stream/venv/bin/python /opt/stream/requirements.lock.txt`. |
| `/stream/api/health` returns 503 | Inspect its component checks; a missing/unreachable LiveKit endpoint intentionally keeps full health degraded. |
| Page loads but nothing updates | Socket.IO blocked — the `/stream/socket.io` location must forward `Upgrade`/`Connection` headers. |
| Video stalls at ~10s while encoding | A cached `.m3u8`. Confirm the `no-cache` regex locations from `deploy/nginx-stream.conf` are in place. |
| Import fails with a yt-dlp timeout | `STREAM_YTDLP_PROXY` unreachable, or `yt-dlp` is not on the server's `$PATH`. |
| Login e-mail never arrives | Check the mail relay used by `mail.py`; all password registrations now require email verification. |
| Upload rejected at 413 | Raise nginx `client_max_body_size` to match `STREAM_MAX_UPLOAD_MB`. |

---

## Design Notes & Limitations

- **Multi-room, not single-room** — rooms are 6-char invite codes from an unambiguous alphabet, materialized lazily. The playlist/position of each room is persisted separately under `data/rooms/`.
- **Playback is permission-gated** — only moderators and admins can issue control events (play/pause/seek/next/shuffle); watchers follow the shared clock. Upload quota is enforced per user at signup time via `STREAM_DEFAULT_UPLOAD_QUOTA`.
- **Subtitle and audio selection are local** — each user independently chooses their subtitle language and audio track. Only the existence of a new subtitle/audio track is broadcast.
- **Dubbed audio is a separate file** — because browsers do not support multi-track audio switching within a single video file reliably, each dubbed audio track is a separate file synced client-side (drift checked every 2 seconds). This works uniformly across all browsers.
- **HLS progressive encoding** — the item becomes playable after ~20 seconds of content are encoded. The sync loop clamps seeks to the encoded frontier to prevent stalling. hls.js is configured with startLevel: 0 for predictable initial quality selection.
- **Autoplay policy** — play/pause start local playback inside the click gesture rather than waiting for the server round-trip, because browsers block `play()` from timers and effects.
- **Video formats** — only formats the browser can play natively (MP4, WebM, OGG, MOV) are supported for direct upload. MKV/AVI sources are transcoded via yt-dlp + ffmpeg HLS pipeline.
- **Playlist persistence** — the playlist and last-known position are saved per room. After a restart, playback is always paused at the last known position.
- **Chat is in-memory only** — chat messages are never written to disk. They are automatically purged every 24 hours and on server restart.
- **Scraper fragility** — `archive_scraper.py` targets third-party sites whose markup can change without notice; search and episode listing can break independently of the player.

---

## License

Proprietary — all rights reserved.
