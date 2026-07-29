# vergoboy.ir / stream — Synchronized Watch Party Player

A real-time synchronized video streaming platform for `vergoboy.ir/stream`. Multiple users share a single watch room where playback state (play/pause/seek/rate) stays in sync across all participants, with per-user independent subtitle language and audio track selection.

---

## Features

- **Shared watch room** — every visitor is in the same room; play, pause, seek, and speed changes are synchronized across all clients (with automatic drift correction).
- **No sign-up** — users identify themselves by entering a name (no password).
- **Real-time notifications** — streamed to all clients via Socket.IO (play/pause/seek events, users joining/leaving, new subtitles or audio tracks becoming available).
- **Shared playlist** — add videos via file upload or direct URL (HTTP/HTTPS/HLS). Items are persisted across server restarts.
- **Progressive multi-bitrate HLS transcoding** — uploaded/imported videos are transcoded into a ladder of HLS renditions (480p, 720p, 1080p where the source supports it). The default rendition starts encoding immediately; viewers can request additional qualities on demand. Playback starts as soon as the first ~8 seconds are encoded, while the rest continues in the background.
- **Dubbed audio tracks** — each audio track is a separate file synced with the video client-side. Selection is per-user and independent of the shared playback position.
- **Subtitles** — embedded text subtitle streams in the source file are automatically extracted and served as WebVTT. Subtitle style customization (font, size, color, background opacity, outline, bold, bottom offset) is local to each user.
- **Controls** — play/pause, 10s skip forward/back, previous/next item, volume, playback speed (0.5x-2x), fullscreen (portal-based for reliable fixed-position coverage), Picture-in-Picture.
- **Live streaming panel** — generate an RTMP stream key and get the push URL (for OBS/mobile apps) and HLS playback URL, using the existing RTMP/HLS server on the host. Manual HLS URLs can also be added as playlist items.
- **Keyboard shortcuts** — Space/k (play/pause), f (fullscreen), left/right arrow (seek 10s), up/down arrow (volume), 0-9 (seek to 10%-90%), ? (shortcuts help).
- **Visual identity** — matches vergoboy.ir design (colors, fonts, header/footer).

---

## Project Structure

```
/opt/stream/
├── app.py                           # Flask + Socket.IO backend
├── config.py                        # Environment-driven configuration
├── state.py                         # Shared room state (in-memory + persisted)
├── srt_to_vtt.py                    # SRT -> WebVTT converter
├── requirements.txt
├── webapp/                          # Next.js frontend (static export)
│   ├── app/                         #   Page components (Next.js App Router)
│   ├── components/                  #   React components (Player, Chat, Sofa, etc.)
│   ├── lib/                         #   Client library (API client, state hooks, types)
│   ├── public/                      #   Static assets
│   ├── next.config.ts
│   ├── package.json
│   └── tsconfig.json
├── webapp-out/                      #   Built static export (served by Nginx)
├── media/
│   ├── uploads/                     #   Raw uploaded video files
│   ├── hls/{item_id}/              #   Per-item HLS renditions + master playlists
│   ├── subs/                        #   Extracted WebVTT subtitles
│   ├── chat/                        #   Chat-uploaded images
│   └── avatars/                     #   User-uploaded avatars
├── data/
│   └── state.json                   #   Playlist/position persistence
├── deploy/
│   ├── vergoboy-stream.service      #   systemd unit for the Flask backend
│   └── nginx-stream.conf            #   Nginx location snippets
└── .gitignore
```

---

## Architecture

### Backend (app.py, state.py, config.py)

The backend is a single-process Flask application using **gevent** for async I/O and **python-socketio** for real-time communication. It runs on `127.0.0.1:8801`.

- **Room state** is held in a global in-memory `RoomState` object, protected by a `threading.Lock`. The playlist and current playback position are persisted to `data/state.json` so they survive server restarts.
- **Socket.IO events** — every connected client receives `state_sync` broadcasts with the full room state. Control actions (play/pause/seek/rate/select) are sent via the `control` event and immediately broadcast to all clients.
- **HLS transcoding** is progressive: ffmpeg encodes one rendition at a time to an EVENT-type HLS playlist, emitting `transcode_progress` events as segments are written. The item becomes playable (status: "ready") after `HLS_READY_AFTER_SECONDS` (default: 8s) of timeline are encoded.
- **Subtitle extraction** runs as a concurrent greenlet: embedded text subtitle streams are extracted with ffmpeg, converted to WebVTT, and added to the item's `subtitles` array.
- **Concurrency limit** — at most `MAX_CONCURRENT_ENCODES` (default: 2) ffmpeg processes run at once. Excess items wait in "queued" status.

### Frontend (webapp/)

The frontend is a **Next.js** application configured for **static export** (output: "export"). It uses:

- **Next.js App Router** with a single root layout + page.
- **Tailwind CSS v4** for styling (the `@tailwindcss/postcss` plugin).
- **socket.io-client** for real-time bidirectional communication with the backend.
- **hls.js** for HLS playback (progressive download of EVENT-type playlists, native HLS on Safari).
- **framer-motion** for animated transitions (sofa seating area, menus, etc.).
- **Vazirmatn** (Persian) and **JetBrains Mono** fonts.

### Client-Server Synchronization

1. The server maintains `position`, `playing`, and `updated_at`.
2. `current_position()` estimates the real-time position: if playing, it adds elapsed wall-clock time since `updated_at`.
3. Clients run a sync interval every 6 seconds: if the local video position drifts more than 1.2 seconds from the server's expected position, the client seeks to correct it.
4. The seek target is clamped to the **encoded frontier** (the number of seconds the HLS encoder has produced so far) so the sync loop never tries to jump into un-encoded territory.
5. An optimistic local update in `requestControl()` makes the UI feel instant while the server round-trip completes.

### Fullscreen

Fullscreen uses a **React Portal** pattern: the fullscreen overlay is rendered as a `fixed` child of `document.body`, escaping any ancestor transform/stacking contexts (e.g., framer-motion wrappers). The native `requestFullscreen()` API is called on the portal root to hide browser chrome on desktop. The video uses `object-cover` in fullscreen mode to fill the entire screen.

---

## Setup

### Dependencies

```bash
# Python backend
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# Next.js frontend (requires Node.js 18+)
cd webapp
npm ci
```

### Configuration

All configuration is environment-driven with sensible defaults in `config.py`:

| Variable | Default | Description |
|---|---|---|
| STREAM_HOST | 127.0.0.1 | Local bind host |
| STREAM_PORT | 8801 | Local bind port |
| STREAM_SECRET_KEY | (required) | Flask session secret |
| STREAM_MAX_UPLOAD_MB | 8192 | Max video upload size (MB) |
| STREAM_HLS_PRESET | veryfast | ffmpeg preset for transcoding |
| STREAM_HLS_READY_AFTER_SECONDS | 8 | Seconds encoded before item is playable |
| STREAM_MAX_CONCURRENT_ENCODES | 2 | Max simultaneous ffmpeg processes |
| STREAM_RTMP_PUSH_TEMPLATE | rtmp://vergoboy.ir:1935/live/{key} | RTMP push URL template for live streaming |
| STREAM_HLS_PLAYBACK_TEMPLATE | https://vergoboy.ir/hls/{key}/index.m3u8 | HLS playback URL template for live streaming |
| STREAM_YTDLP_PROXY | socks5://127.0.0.1:1080 | Proxy for yt-dlp (YouTube imports) |
| STREAM_YTDLP_COOKIES | /opt/stream/cookies.txt | Cookies file for yt-dlp |

Note: RTMP_PUSH_TEMPLATE and HLS_PLAYBACK_TEMPLATE must match your existing RTMP/HLS server's path structure. The app only substitutes {key} with a randomly generated stream key — it does not run an RTMP or HLS server itself.

### Fonts

The page uses the same local fonts as the main vergoboy.ir site, referenced at /register/static/fonts/.... Since both apps are on the same domain, no font files need to be copied — just ensure the main app (which serves /register) is running.

---

## Running

### Development

```bash
# Backend
cd /opt/stream
STREAM_PORT=8801 ./venv/bin/python3 app.py

# Frontend (separate terminal)
cd /opt/stream/webapp
npm run dev          # runs on port 3000 with hot reload
```

Visit http://127.0.0.1:8801/stream/ for the Flask-served version, or http://localhost:3000 for the Next.js dev server.

### Production (systemd)

```bash
# Backend service
sudo cp deploy/vergoboy-stream.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vergoboy-stream

# Frontend build (runs npm ci + npm run build + rsync to webapp-out/)
sudo systemctl restart vergoboy-frontend-build.service
```

The backend service (vergoboy-stream.service) uses Wants= (soft dependency) on the frontend build service to ensure ordering during startup, without forcing the stream service to stop when the build re-runs.

Logs: journalctl -u vergoboy-stream -f

### Nginx

Append the contents of deploy/nginx-stream.conf into your site's server block (the one serving vergoboy.ir), then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Key details in the Nginx config:
- location /stream/ proxies page/API requests and WebSocket upgrades to 127.0.0.1:8801 (with necessary Upgrade/Connection headers).
- location /stream/media/ serves uploaded video files and HLS segments directly from disk (faster than proxying through Flask, with proper Range request support).
- client_max_body_size allows large file uploads.
- The static Next.js export at webapp-out/ is served as the root of the /stream subpath.

---

## Design Notes & Limitations

- **Single room** — by design, there is exactly one shared room. All visitors are in the same room.
- **Subtitle and audio selection are local** — each user independently chooses their subtitle language and audio track. Only the existence of a new subtitle/audio track is broadcast.
- **Dubbed audio is a separate file** — because browsers do not support multi-track audio switching within a single video file reliably, each dubbed audio track is a separate file synced client-side (drift checked every 2 seconds). This works uniformly across all browsers.
- **HLS progressive encoding** — the item becomes playable after ~8 seconds of content are encoded. The sync loop clamps seeks to the encoded frontier to prevent stalling. hls.js is configured with startLevel: 0 for predictable initial quality selection.
- **Video formats** — only formats the browser can play natively (MP4, WebM, OGG, MOV) are supported for direct upload. MKV/AVI sources are transcoded via yt-dlp + ffmpeg HLS pipeline.
- **Playlist persistence** — the playlist and last-known position are saved to data/state.json. After a restart, playback is always paused at the last known position.
- **Chat is in-memory only** — chat messages are never written to disk. They are automatically purged every 24 hours and on server restart.

---

## License

Proprietary — all rights reserved.
