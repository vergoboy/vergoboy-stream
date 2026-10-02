# vergoboy.ir / stream — Synchronized Watch Party Player

A real-time synchronized video streaming platform for `vergoboy.ir/stream`. Users gather in a watch room where playback state (play/pause/seek/rate/shuffle) stays in sync across all participants, with per-user independent subtitle language and audio track selection.

---

## Quick Start

Get from a fresh clone to a running dev server in about five minutes.

### 0. Prerequisites

| Requirement | Version | Needed for |
|---|---|---|
| Python | 3.10+ (3.12 tested) | backend |
| Node.js | 18+ (22 tested) | Next.js build |
| PostgreSQL | 13+ | accounts, rooms, admin panel |
| `ffmpeg` + `ffprobe` | any recent build | HLS transcoding, subtitle extraction |
| `yt-dlp` | latest, on `$PATH` | YouTube / series imports |

```bash
sudo apt install -y postgresql ffmpeg python3-venv
pipx install yt-dlp   # or: sudo apt install yt-dlp
```

### 1. Clone and install

```bash
git clone https://github.com/vergoboy/vergoboy-stream.git
cd vergoboy-stream

# Backend
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# Frontend (static export, built and served by Nginx)
cd webapp && npm ci && cd ..
```

### 2. Create the database

Tables are created automatically on first boot (`db.init_db()`), so only the
role and database need to exist:

```bash
sudo -u postgres psql -c "CREATE USER stream WITH PASSWORD 'stream';"
sudo -u postgres psql -c "CREATE DATABASE stream OWNER stream;"
```

### 3. Configure

Put your secrets in `.stream_db.env` — it is git-ignored, and it is what
`deploy/vergoboy-stream.service` loads via `EnvironmentFile=`:

```bash
cat > .stream_db.env <<'EOF'
STREAM_DATABASE_URL=postgresql+psycopg2://stream:stream@127.0.0.1:5432/stream
STREAM_SECRET_KEY=<paste: openssl rand -hex 32>
STREAM_JWT_SECRET=<paste: openssl rand -hex 32>
EOF
```

Everything else has a working default in `config.py` — bind host/port, upload
cap, HLS preset, RTMP/HLS URL templates, yt-dlp proxy. See
[Configuration](#configuration) for the full list.

### 4. Run it (two terminals)

**Backend** (gevent WSGI on `127.0.0.1:8801`):

```bash
cd vergoboy-stream
set -a && . ./.stream_db.env && set +a
./venv/bin/python3 app.py
```

**Frontend** (Next.js dev server with hot reload on `:3000`):

```bash
cd vergoboy-stream/webapp
cp .env.local.example .env.local   # points NEXT_PUBLIC_API_ORIGIN at :8801
npm run dev
```

Open **http://localhost:3000** for the dev server, or
**http://127.0.0.1:8801/stream/** for the Flask-served page.

Create the first account with the address in `STREAM_ADMIN_EMAIL`
(default `very.good.booyy@gmail.com`) — that account skips email verification
and gets the `admin` role, which unlocks `/stream/admin` (user list, delete).

### 5. Run it for production

```bash
# Build the static export into webapp/out/, then publish it
cd webapp && npm run build
mkdir -p webapp-out && rsync -a --delete webapp/out/ webapp-out/ && cd ..

# systemd units (template placeholders — edit the two CHANGE_ME secrets first)
sudo cp deploy/vergoboy-stream.service deploy/vergoboy-frontend-build.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vergoboy-frontend-build vergoboy-stream

# Nginx: paste deploy/nginx-stream.conf into your server{} block
sudo nginx -t && sudo systemctl reload nginx
```

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

Everything is environment-driven with sensible defaults in `config.py`. The
Quick Start only requires the three values marked **required**; the rest are
tuned per deployment.

| Variable | Default | Description |
|---|---|---|
| STREAM_HOST | 127.0.0.1 | Local bind host |
| STREAM_PORT | 8801 | Local bind port |
| STREAM_SECRET_KEY | **required** | Flask session secret |
| STREAM_DATABASE_URL | **required** | `postgresql+psycopg2://stream:stream@127.0.0.1:5432/stream` |
| STREAM_JWT_SECRET | falls back to SECRET_KEY | JWT access/refresh signing key |
| STREAM_SITE_URL | https://vergoboy.ir/stream | Public base URL (verification links, OAuth redirects) |
| STREAM_ADMIN_EMAIL | very.good.booyy@gmail.com | Skips email verification, gets the `admin` role |
| STREAM_EXEMPT_EMAILS | (see config.py) | Comma-separated auto-verified addresses |
| STREAM_MAIL_FROM | info@vergoboy.ir | Envelope/From for outgoing mail |
| STREAM_GOOGLE_OAUTH_CLIENT_ID / _SECRET | (empty) | Google login |
| STREAM_GITHUB_OAUTH_CLIENT_ID / _SECRET | (empty) | GitHub login |
| STREAM_DEFAULT_UPLOAD_QUOTA | 50 | Per-user upload quota for new signups |
| STREAM_MAX_UPLOAD_MB | 8192 | Max video upload size (MB) — mirror in nginx `client_max_body_size` |
| STREAM_HLS_PRESET | ultrafast | ffmpeg preset for transcoding |
| STREAM_HLS_READY_AFTER_SECONDS | 20 | Seconds encoded before an item is playable |
| STREAM_MAX_CONCURRENT_ENCODES | 2 | Max simultaneous ffmpeg processes |
| STREAM_RTMP_PUSH_TEMPLATE | rtmpps://vergoboy.ir:8443/live/{key} | RTMP push URL template for live streaming |
| STREAM_RTMP_ALT_PORTS | 1935 | Extra ingest ports advertised to the user |
| STREAM_HLS_PLAYBACK_TEMPLATE | https://vergoboy.ir/hls/live/{key}/index.m3u8 | HLS playback URL template |
| STREAM_YTDLP_PROXY | socks5://127.0.0.1:1080 | Proxy for yt-dlp (YouTube imports) |
| STREAM_YTDLP_COOKIES | /opt/stream/cookies.txt | Cookies file for yt-dlp |
| STREAM_YTDLP_FORMAT | bestvideo[ext=mp4]+bestaudio[ext=m4a]/… | yt-dlp format selector |
| STREAM_LIVEKIT_URL / _ROOM / _TOKEN_TTL | wss://vergoboy.ir/livekit | Voice-room connection |
| STREAM_LIVEKIT_API_KEY / _API_SECRET | parsed from /etc/livekit/config.yaml | LiveKit token minting |

Notes:

- `RTMP_PUSH_TEMPLATE` and `HLS_PLAYBACK_TEMPLATE` must match your existing
  RTMP/HLS server's path structure. The app only substitutes `{key}` with a
  randomly generated stream key — it does not run an RTMP or HLS server itself.
- LiveKit credentials are never committed: `config.py` falls back to parsing
  `/etc/livekit/config.yaml` when the env vars are unset.
- `STREAM_YTDLP_COOKIES` is optional; drop it entirely for sources that do not
  need a logged-in session.

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
  stops the backend.
- **Nginx** — see the comments in `deploy/nginx-stream.conf`. Three things are
  easy to get wrong:
  - `/stream/` serves the static Next.js export from disk, not through Flask;
    only `/stream/api/` and `/stream/socket.io/` are proxied to `127.0.0.1:8801`
    (the latter needs the `Upgrade`/`Connection` headers).
  - `/stream/media/` is served with `alias` and Range support so video bytes
    never traverse Flask.
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
| `[CRITICAL] Database init failed` on boot | Wrong `STREAM_DATABASE_URL`, or the role/database does not exist. Check step 2 of the Quick Start. |
| Backend exits immediately | Missing deps — `gevent` is required; `app.py` calls `monkey.patch_all()` before anything else. Reinstall with `./venv/bin/pip install -r requirements.txt`. |
| Page loads but nothing updates | Socket.IO blocked — the `/stream/socket.io` location must forward `Upgrade`/`Connection` headers. |
| Video stalls at ~10s while encoding | A cached `.m3u8`. Confirm the `no-cache` regex locations from `deploy/nginx-stream.conf` are in place. |
| Import fails with a yt-dlp timeout | `STREAM_YTDLP_PROXY` unreachable, or `yt-dlp` is not on the server's `$PATH`. |
| Login e-mail never arrives | Expected for addresses in `STREAM_EXEMPT_EMAILS` (auto-verified). Otherwise check the mail relay used by `mail.py`. |
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
