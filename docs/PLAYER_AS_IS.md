# Player, as-is — S0 characterization (feat/mpv-player)

Written **before any code** for the mpv-player task, on the tree at
`release/w2g-v1` (`2abbe52`). Every claim below is backed by a command run on
that tree; the outputs are quoted inline so the next step can diff against a
known state instead of a memory.

This is the baseline the S7 gate will be judged against: "the native path has
no `<video>`/hls.js" means *no more `<video>` than is in this document*.

---

## 0. Baseline commands (the required first action)

```
$ git status
On branch release/w2g-v1
nothing to commit, working tree clean

$ git branch -a
  feat/cinema-redesign
  imported/cinema-redesign-auth
  main
* release/w2g-v1
  wip/p3-early
  remotes/origin/HEAD -> origin/main
  remotes/origin/feat/cinema-redesign
  remotes/origin/main

$ git log --oneline -n 15
2abbe52 docs: the /w2g desktop package is the one that reaches the server
b04386e docs: session 10 — FlareSolverr mints the clearance, requests replays it
9a639f8 archive: scrapes must carry the UA the clearance is bound to
0bcfe2f archive: point the clearance sidecar at FlareSolverr (it actually clears the wall)
de88373 archive: clearance sidecar client lifts the Cloudflare wall (env-gated)
daf46ea docs: session 9 — the digimoviez 'session expired' was a Cloudflare Turnstile wall
393d6e4 archive: a Cloudflare edge challenge is not an expired session
f1db05d docs: the 2026-10-16 'cannot connect' is a third, distinct cause
eeaf1c2 build(pkg): refuse to ship a dev-shell binary — the localhost:3000 trap
a755a1d docs: record the namcap license fix — the 'advisory' reading was wrong
ec6bd49 build(pkg): make namcap errors fail the run, and stop swallowing rustc advice
91577dc packaging(arch): ship the LI…  (truncated in the session log)

$ ls webapp/src-tauri
build.rs  capabilities/  Cargo.lock  Cargo.toml  crates/  gen/  icons/  src/  target/  tauri.conf.json

$ ls webapp/lib
anim.ts  api.ts  auth.ts  config.ts  format.ts  geometry.ts  notifyText.ts
settings.tsx  tauri.ts  types.ts  useIdle.ts  useLocalStorage.ts
useRoomState.ts  useVoiceRoom.ts  whip.ts

$ wc -l webapp/components/Player.tsx
1788 webapp/components/Player.tsx

$ make test-fast        # in progress at the time of writing; result appended in §7
venv/bin/python -m pytest -m "not integration" -q
```

Branch note: the task says "branch `feat/mpv-player` from current main". The
tree I was handed is `release/w2g-v1`, which contains the whole `/w2g` delivery
(T1–T4, sessions 3–10) and is **not** an ancestor-of-main state — `main`'s
last push predates it. Branching from `main` would silently drop the
multi-instance work the live server depends on. So: `feat/mpv-player` is cut
from `release/w2g-v1` (the actual HEAD of the repo), and this is recorded as a
deviation in the S0 ledger entry.

---

## 1. What mpv-related code already exists

### 1.1 `webapp/src-tauri/crates/mpv_controller` — real, compiles, tested

Introduced by `ec6891e feat(phase 1b): mpv_controller crate — PlayerPort over a spawned mpv`
and documented by `docs/adr/0002-mpv-separate-process.md`.

```
$ cargo test -p mpv_controller
running 18 tests ... test result: ok. 18 passed; 0 failed; finished in 0.30s
running 3 tests  ... test result: ok. 3 passed;  0 failed; finished in 5.17s
EXIT=0
```

**It compiles, and 21 tests pass** — 18 unit (no process) plus 3 against a real
`mpv` binary (`--vo=null --ao=null`), each of which **skips itself** when mpv is
absent (`SKIP: mpv not installed`, then `return`), so the suite is green on a
box with no media stack.

Module map (2 885 lines total incl. the test):

| File | Lines | State |
| --- | --- | --- |
| `src/port.rs` | 147 | **complete.** `PlayerPort` trait, `PlayerEvent`, `PlayerError`, `CacheProfile`. Notably *smaller* than the task's target surface. |
| `src/process.rs` | 235 | **complete.** `locate_mpv()` (`$STREAM_MPV_BIN` → `PATH`), `MpvOptions` (arg builder), `MpvProcess` (spawn/kill/reap, `Drop` kills + waits → no zombies). |
| `src/ipc.rs` | 338 | **complete.** Newline-delimited JSON over a unix socket, `request_id` correlation, per-request channels, 512-slot event queue, timeouts, `is_closed()`. |
| `src/mpv.rs` | 602 | **complete for the port's surface.** `MpvPlayer`: spawn-idle → observe → map events; bounded crash restart (`DEFAULT_MAX_RESTARTS = 3`), resume at last known position, `shutdown()` reaps. |
| `src/fake.rs` | 306 | **complete.** `FakePlayer` with virtual time (`tick`), `stall_for`, `fail_next`, `latency`, call log. Used by its own unit tests; nothing else consumes it yet. |
| `tests/real_mpv.rs` | 510 | **complete.** Local-file roundtrip, crash/restart/give-up without zombies, buffering edges over a throttled HTTP source (with a hand-rolled Range-capable test server). |

Wire behaviours were **measured** against mpv v0.41.0 (not guessed) and are
pinned in `docs/spike/mpv-ipc-observations.json` + ADR 0002 part B:
`observe_property` emits the current value once on subscription; the `seek`
event carries no `reason` field; the `pause` echo races its reply; EOF can
arrive twice; `time-pos` must not be observed (frame-rate flood).

**What it does NOT have (vs. §3.1 of the task):**

| Missing | Consequence |
| --- | --- |
| `add_subtitle` / `set_sub_style` | no ASS/VTT external subs, no per-user styling on the native path |
| `set_fullscreen`, `get_state` (full snapshot), `Loaded{duration,tracks}`, `Position`, `CacheState`, `Seeked`, `TrackChanged`, `ProcessCrashed` events | events are the 6 variants only; no track list, no duration, no cache telemetry |
| `--wid` embedding / any window plumbing | `MpvOptions` only knows `headless` (no window) vs `--force-window=yes` (a *separate* window). Tiers 1 and 2 do not exist. |
| `PlayerPort` → `add_audio` exists, but `set_sub_style`/`sub-add` do not | dub-audio half-done, subtitles absent |

### 1.2 `PlaybackSession` and `SyncEngine` — do not exist

No `Source` (`LocalSource` / `RoomSource`), no session object, no sync engine,
no `Transport` trait anywhere in the tree. The crate is a driver and nothing
has yet been built on top of it.

### 1.3 The Tauri layer — no player commands at all

`webapp/src-tauri/src/lib.rs` (717 lines) is **only** crash/logging
infrastructure: a rotating file logger under `$STREAM_LOG_DIR`, a panic hook,
`install_crash_logging()`, `is_notable_event` filtering, and ~17 tests for the
date maths and log dedup. `run()` builds a default `tauri::Builder` with a
`.setup` that logs the window count and nothing else.

```
$ grep -rn "invoke_handler\|generate_handler" webapp/src-tauri/src
(no output)
$ grep -rn "@tauri-apps/api" webapp/lib webapp/components
webapp/lib/tauri.ts:26:  const mod = await import("@tauri-apps/api/window");
```

There are **zero `invoke()` calls** in the app today; the only Tauri API use is
`webapp/lib/tauri.ts`, which wraps *window* operations (`enterNativeMini`,
`setNativeFullscreen`, `guardNativeClose`) behind `isTauri()` no-ops.

### 1.4 `PlayerBackend` / `HlsBackend` / `MpvBackend` — do not exist

```
$ grep -rn "PlayerBackend|MpvBackend|HlsBackend" --include=*.ts --include=*.tsx .
(no output)
```

`docs/RECONCILIATION.md:79` names "minimal `MpvBackend` behind the existing
`PlayerBackend` interface" as a P1 deliverable and `docs/PROGRESS.md:405` still
has that row as `todo | pending`; `docs/QA_NATIVE_CLIENT.md:8` says the native
playback path is "not built yet". So the task's premise that `PlayerBackend` is
the existing shared seam is **aspirational, not actual** — S7 has to *create*
it (`webapp/lib/player/PlayerBackend.ts`, `HlsBackend` extracted from the
current `Player.tsx` body, `MpvBackend` over the new commands). Recording this
because the task asks for a faithful as-is, and a reviewer comparing S7's diff
against a "there was a seam already" premise would be right to be suspicious.

### 1.5 `GET /api/capabilities` — does not exist

```
$ grep -n "capabilities" app.py
271:    """Resolved capabilities for this request. Cached on flask.g."""
```

The only hit is a docstring on `_perm_snapshot()` (permission capabilities for
a *user*), unrelated to a media-client handshake. There is no client-kind
negotiation of any kind: every client is served `renditions` + HLS.

### 1.6 Media token — does not exist

```
$ grep -rn "media_token|MEDIA_TOKEN" app.py config.py deploy/
(no output)
```

What exists today, instead, in `app.py`:

```python
@app.route(f"{BP}/media/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(Config.UPLOAD_DIR, filename, conditional=True)
```

Four facts that matter for S4:

1. `conditional=True` ⇒ **the dev route already answers `Range`** (206 +
   `Content-Range`). The task's "also provide a Range-capable dev route" is
   mostly a test-and-keep job.
2. The route is **unauthenticated and unauthorised** — the only middleware on
   `/media/*` is a CORS resource entry (`app.py:140`). Knowing the filename is
   the only credential. Same for `/media/subs/`, `/media/hls/`, …
3. In production nginx already **bypasses Flask for media**
   (`deploy/nginx-w2g.conf:55`): `location /w2g/media/ { alias /opt/w2g/media/; }`
   with `Accept-Ranges bytes`, so original-file bytes already never traverse
   Python — but with no token check either.
4. Uploads are stored under `Config.UPLOAD_DIR`; the item's private source is
   the `_src`-prefixed key stripped by `RoomState._sanitize_item` before any
   client sees it. So "the original file" is on disk and has a stable name.

### 1.7 Safety helpers — already present (S4 starts from here)

`docs/PROGRESS.md` records the P1 security slices `d3dfd79`, `e44adf8`,
`92e94a3`: `safe_fetch_url` (8 MiB cap, redirect re-validation, public-IP-only),
`validate_media_source` for ffmpeg/ffprobe (`-protocol_whitelist http,https,tcp,tls,crypto`),
and "no access token from the query string". The task's §3.5 URL/path-safety
row is therefore **already landed**; S4's job is to point the new native-URL
paths at the same helpers and prove it with tests, not to invent them.

### 1.8 Licensing/decision record — already written

`docs/adr/0002-mpv-separate-process.md` is the ADR the task's R7 demands:
process-only, no `libmpv`, GPL aggregation reasoning, spike table.
`docs/DEPENDENCIES.md` already carries the mpv entry. R7 is satisfied at
S0; S7 only has to keep it true.

### 1.9 Test coverage map — what S0's 21 pinned, what S1 added

S1's method is characterization first: pin the existing behaviour, then extend.
This is the inventory the extension commits are measured against.

**S0's 18 unit tests, by module (port.rs had none at all):**

| Module | Tests | What they pin |
| --- | --- | --- |
| `mpv.rs` | 6 | Every `map_event` decision against the *measured* mpv v0.41 wire: on-subscription property emissions are consumed, our own `pause` write is not a `UserPause`, the `seek` event carries no `reason` (own-seek counter), buffering edges flow through, the duplicate EOF signal is deduped, `end-file` reasons map to `Error`/`None`/`Ready` |
| `fake.rs` | 7 | Virtual time only moves when ticked and not paused; stalls bracket with `Buffering(true/false)`; `Ended` fires once; `fail_next` injects one `Rejected` per call; `set_rate` is refused before `load`; `CacheProfile` is stored; `load` reports `Ready` and honours start/pause |
| `ipc.rs` | 2 | Out-of-order replies are correlated by `request_id`; a missing reply is a `Timeout`, not a hang |
| `process.rs` | 3 | `MpvOptions::args()` builds exactly the flags the spike proved; `locate_mpv` returns a real path or `None`; dropping `MpvProcess` kills **and reaps** the child |

**S0's 3 real-mpv integration tests:** a real file under local controls
(load/seek/pause/rate/volume/sub/aid/sid); crash → restart → give-up with the
process table asserted empty afterwards; buffering edges on a throttled HTTP
source. All three self-skip when mpv is absent.

**Gaps that S1's characterization commit fills (45 unit + 3 integration = 48):**

- **`port.rs` had zero tests.** Now pins every `CacheProfile::properties()`
  value (150/50/1.0/yes, 20/10/0.5/no, 8/4/0.2/no) and the `Display` strings
  of `PlayerEvent`/`PlayerError`, which reach logs and the WebView.
- **`fake.rs`:** all 8 pre-load calls (not just `set_rate`), `Ready` delivered
  exactly once *per load* (re-armed by a second `load` — the restart path
  relies on it), `emit()` ordering, seek clamping to `[0, duration]` and EOF
  re-arm, tick-while-unloaded, stall-while-paused, the exact `calls` log
  format later phases assert order on, and `Default`.
- **`ipc.rs`:** malformed lines skipped without disturbing neighbours, events
  routed to the queue in order, a *dropped socket* releasing waiters as
  `NotRunning` (as opposed to waiting out the 3 s timeout) and marking the
  client closed, a closed client refusing further work, an error reply becoming
  `Rejected{command, detail}`, request ids starting at 1 and increasing,
  `connect()` giving up as `Protocol` when the socket never appears.
- **`process.rs`:** a missing binary is `NotRunning`, not a panic; `spawn`
  removes a stale socket; `wait_for_exit` returns `Some` on exit and `None` on
  timeout; `kill()` after the child is already reaped is a no-op that still
  clears the socket path; `STREAM_MPV_BIN` precedence, blank-value fallback and
  missing-file passthrough (behind an `ENV_LOCK`, because the variable is
  process-global and cargo runs tests on parallel threads).
- **`mpv.rs`:** the observed set is exactly `{pause, paused-for-cache,
  eof-reached}` with `max_restarts = 3`, and everything unobserved
  (`time-pos`, `volume`, `mute`, `aid`, `sid`, `fullscreen`,
  `demuxer-cache-duration`, `track-list`, `speed`, `duration`) returns `None` —
  this last test is the deliberate record of what the S1 extension changes, and
  is rewritten in the same commit that changes it.

**Still uncovered after this commit (each is scheduled):**

- `FakePlayer.latency` is a documented field **nothing reads** — there is no
  behaviour to pin, so no test exists. S1's extension makes it real.
- Crash/restart vocabulary: today only the real-mpv crash test, asserting
  `Error("attempt N")` strings. The extension converts it to `ProcessCrashed`
  and adds state restore (commits 5–7).
- The Windows named-pipe transport: the `cfg(not(unix))` stub returns
  `Unsupported` and nothing tests it. Added behind `cfg` in S1, checked by a
  cross `cargo check`.
- `apply_profile`, `respawn` bookkeeping and `poll_events`' blocking loop are
  only exercised indirectly through the real-mpv happy path.

### 1.10 The S1 extension — the vocabulary, filled in

Path §1.8 of `docs/PROGRESS.md` fixed the shape; this is what the code does.

**Events.** The three legacy variants are kept (`Ready`, `UserSeek`,
`UserPause`) and seven are added, so one mpv message can be two events in a
defined order — which is why `map_event` now returns `Vec<PlayerEvent>`
(empty = not interesting) instead of `Option`:

| Event | Source |
| --- | --- |
| `Loaded { duration, tracks }` | `file-loaded`; the metadata is *fetched* (the event carries none). Emitted before `Ready`, and again after a restart. |
| `Position { pos }` | observed `time-pos`, throttled to one per 500 ms; `last_pos` still follows every sample, so a crash-resume is accurate. |
| `Paused { paused }` | **every** `pause` change. A player-originated one also emits `UserPause`. |
| `CacheState { buffered_secs }` | observed `demuxer-cache-duration`, deduped to a 0.25 s change. |
| `Seeked { pos }` | our own seek (matched by the `own_seeks` counter). |
| `TrackChanged { aid, sid }` | observed `aid`/`sid` change; both selections are reported. |
| `ProcessCrashed { restarts }` | a death below the restart budget — replaces the old `Error("mpv exited; restarting…")` string. The fatal give-up is still `Error`. |

The observed set grew from 3 to 7 properties (`OBSERVED`): `pause`,
`paused-for-cache`, `eof-reached`, `time-pos`, `demuxer-cache-duration`,
`aid`, `sid`. Everything else the UI needs (`volume`, `mute`, `fullscreen`,
`duration`, `track-list`, `speed`) is read on demand by `get_state`.

**Port methods.** Added `set_volume` (0–100, clamped), `set_mute`,
`set_fullscreen`, `add_subtitle` (`sub-add … select`), `set_sub_style`
(`sub-scale`/`sub-color`/`sub-border-color`/`sub-back-color`; `None` = leave
alone) and `get_state` (a snapshot: pos, duration, pause, rate, aid, sid,
volume, mute, fullscreen, profile, buffered seconds, tracks, loaded URL).
`audio_add` and `set_cache_profile` already existed and are unchanged. Every
name was checked against the installed mpv v0.41.0 (`--list-properties`).

**Restart state-restore** grew beyond media/position/pause+profile to also
re-apply volume, mute and fullscreen (`respawn`, best-effort).

**`FakePlayer`** implements the whole surface: `latency` is now actually
honoured (a synchronous sleep before a command takes effect, default zero);
`crash()`/`recover()` model the process death and the restart, with state
preserved and `Loaded`+`Ready` re-announced; `load` resets per-file state
(tracks, external subs/audio, queued events) exactly as `MpvPlayer::load`
clears its pending queue.

What this commit's unit tests pin: throttle release, cache epsilon,
both-halves track reporting, `Paused`/`UserPause` pairing, `Seeked` vs
`UserSeek`, `ProcessCrashed`-not-`Error`, the 7-property observed set, and the
fake's new methods, crash/recover, snapshot and latency. Real-mpv coverage of
the new methods and the failure modes is the next commit.

---

## 2. Every feature `Player.tsx` provides today

1 788 lines, one component, no local-file mode. Features as implemented, in
the order they appear in the file:

### 2.1 Media source & lifecycle

| # | Feature | Where |
| --- | --- | --- |
| 1 | Source selection: `hls.js` for `.m3u8`, native `src=` otherwise, Safari native-HLS fallback | L398–495 |
| 2 | `isPlayable` derivation — `ready`/`complete` both playable, so a status tick does **not** tear down the player | L354–356 |
| 3 | Source teardown/attach effect keyed on `(item.id, mediaSrc, isPlayable, sourceEpoch)`; destroys the old `Hls` instance first | L369–501 |
| 4 | Bounded hls.js recovery: 2 network retries, 2 media retries, then a real Persian error message with the hls.js `details` code | L425–486 |
| 5 | `retryPlayback()` — bumps `sourceEpoch` to rebuild the whole pipeline | L362–367 |
| 6 | Rendition (quality) menu from `item.renditions` ∪ live `hls.levels` | L1263–1289 |
| 7 | `quality_ready` / `quality_partial_ready` notify → manifest reload that preserves position, auto-switch **only if I requested** that label | L505–553 |
| 8 | `refreshMenuIfStale()` — lazy manifest reload when the menu opens and a new rendition appeared | L534–553 |
| 9 | `requestQuality()` — `api.requestQuality`, shows a toast | L558–572 |
| 10 | **Encoded-frontier guard**: never seek past what the current rendition has encoded; clamps, warns in Persian ("این بخش هنوز آماده نشده…") | L274–310 |

### 2.2 Transport & sync (the room half)

| # | Feature | Where |
| --- | --- | --- |
| 11 | `sync()` loop every 6 s: nudge `currentTime` toward `expectedPosition()` when drift > 1.2 s; play/pause mirror | L603–665 |
| 12 | Guards: never seek before `loadedmetadata` (`readyState < 1`); clamp expected position to `duration - 0.05` so the shared playhead can't freeze the player at the end | L617–644 |
| 13 | `togglePlay()` starts playback **inside the click gesture** (autoplay policy), then `requestControl("play"/"pause", {at})` | L325–346 |
| 14 | `seek(to)` → local `currentTime` + `requestControl("seek", {to})`, frontier-clamped | L312–323 |
| 15 | `onEnded` → if controller & next allowed, `onNext()`; else `requestControl("pause", {at: duration})` so the shared clock stops at the truth | L718–738 |
| 16 | `onPlaybackState` reporting from the media element's own play/pause (the lounge couch reflects reality) | L685–692 |
| 17 | Rate change via `requestControl("rate")`, applied to `v.playbackRate` and to the dub `<audio>` | L606–607, L1347–1349 |
| 18 | Live-stream special case: no shared transport, restart-only | L330–333, L612–615 |

### 2.3 Playback UI

| # | Feature | Where |
| --- | --- | --- |
| 19 | `PlayerControls` bar: play/pause, ±10 s, prev/next, shuffle, volume, mute, sub menu, dub-audio menu, speed, quality, float, fullscreen, settings | L1292–1368 |
| 20 | Progress + `bufferedEnd` (buffered range containing `curTime`) + transcode-progress bar for the in-flight encode | L1255–1261, L1524–1531 |
| 21 | Scrub: throttled live `currentTime` writes (140 ms), debounced room `seek` on release | L1306–1320 |
| 22 | Volume/mute persisted to `settings.playback` (`useLocalStorage`) | L113–132, L1076–1082 |
| 23 | Buffering spinner from `waiting`/`canplay`/`playing` | L1499–1503, L681–693 |
| 24 | Center play button ("bigPlay") when autoplay was refused | L1379, L1505 |
| 25 | Per-user subtitle style applied as CSS overlay from `activeCue` text (`textTracks[].activeCues`), scaled for fullscreen/mini | L1465–1488 |
| 26 | Subtitle track switching + `<track>` elements from `item.subtitles`; `mode` bookkeeping (hidden in PiP so cues aren't duplicated) | L1448–1452, L796–803 |
| 27 | Dub-audio: separate `<audio>` element muted-video + drift correction every 2 s if > 0.3 s (the "2-second client drift loop" the task names) | L805–837 |
| 28 | Toasts (Persian) for quality, frontier, quota-ish failures | L267–270 |

### 2.4 Fullscreen / floating / PiP

| # | Feature | Where |
| --- | --- | --- |
| 29 | Fullscreen: CSS fixed layout + OS-window fullscreen on Tauri desktop + Fullscreen API in browsers + orientation lock on touch | L848–896 |
| 30 | `rotateScreen()` (forced landscape/portrait flip) | L898–909 |
| 31 | `fullscreenchange` listener keeps state honest when the user presses Esc | L911–925 |
| 32 | PiP: browser `requestPictureInPicture` on web; **Tauri desktop already uses the native mini window** (`enterNativeMini`) instead of PiP | L176–204 |
| 33 | `floatMode: none | dom | native` — in-page draggable float (mobile) vs shrunk OS window (desktop) | L146–265 |
| 34 | Float drag with spring snap-to-edge; mini strip with title, play/pause, restore | L230–265, L1601–1644 |
| 35 | `floatSupported`/`floating`/`onToggleFloat` wiring into the control bar | L1357–1359 |

### 2.5 Input, idle, ambience, states

| # | Feature | Where |
| --- | --- | --- |
| 36 | Rebindable shortcuts (`settings.shortcuts`) + legacy aliases: Space/k, j/l, ←/→ 10 s, ↑/↓ volume, 0–9 seek-to-percent, M, F, P, N, ?, d (debug), Esc | L953–1074 |
| 37 | Mouse click = play/pause, ripple; touch double-tap sides = ±10 s, middle = play/pause, single tap = hide chrome | L1196–1252 |
| 38 | Idle chrome fade after 3 s (`useIdle`), cursor hidden in fullscreen | L1109–1130 |
| 39 | Ambient light: canvas `drawImage(video)` → `--screen-glow` CSS var; honest fallback when the canvas is CORS-tainted | L1138–1193 |
| 40 | Wake Lock while playing | L928–951 |
| 41 | Debug overlay (`d`) with src/status/positions/frontier/readyState/buffered/level/dub | L1672–1712 |
| 42 | Persian empty / processing / encoding-error / no-playable-source / playback-error screens + Retry | L1509–1570 |
| 43 | `player:cmd` window-event bus for the context menu (fullscreen/pip) | L1084–1095 |
| 44 | `onGoToAdd` / `onGoToSubStyle` / `onOpenShortcuts` / `onOpenSettings` navigation hooks | props L63–66 |

**44 distinct features.** The S7 mapping table has to have a row for each.

### 2.6 What is *not* here (i.e. what the task is actually adding)

- No local-file anything: no file picker, no drag-and-drop onto the player
  (the two `dataTransfer` hits in the tree are `AddVideoPanel.tsx` — upload
  into the room — and `SubAudioPanel.tsx` — upload a dub track), no CLI
  argument, no `.desktop` `MimeType` (both `.desktop` files are `Exec=…`,
  `Categories=AudioVideo;Video;Network;`, nothing else).
- No offline mode: `webapp/app/**` always goes through `AuthGate` → the
  backend. There is no "Local player" screen.
- No external subtitle loading (`sub-add`), no ASS anywhere — subtitles are
  `.vtt` `<track>` + a CSS overlay.
- No track *metadata*: the player never knows how many audio/subtitle tracks
  the file has; it only knows what the server put in `item.audio_tracks` /
  `item.subtitles`.

---

## 3. Server-side facts relevant to S4–S5

| Fact | Evidence |
| --- | --- |
| State broadcast: `state_sync` = `{playlist, current_index, playing, position, rate, shuffle, shuffle_order, server_time, online}` — **no `seq`** | `state.py:46–57` |
| Control ingress: socket `control` `{action: play\|pause\|seek\|rate\|select\|shuffle, at/to/rate/index/on}` | `app.py:3947–4005` |
| `play`/`pause` currently **trust the client's `at`** → `rs.set_play(float(data.get("at", ...)))` — this is exactly the stale-position hole §3.5 wants closed, and it is server-side | `app.py:3970–3973` |
| Seek is server-clamped to the encoded frontier, with a `seek_clamped` notify | `app.py:3974–4003` |
| `rate`/`select`/`shuffle` need `_may_control`; `play`/`pause`/`seek` are open to everyone in the room | `app.py:3956–3964` |
| Re-sync path already exists: client `request_sync` → server `state_sync` to the caller | `app.py:4008–4014` |
| Presence/watching: socket `watching` `{watching, item_id}` from the media element's own play/pause. (The `playback_status` / `drift_seconds` event in `docs/QA_NATIVE_CLIENT.md` §2 is **stale documentation** — no such handler exists.) | `app.py:3883–3908`, `grep -n playback_status app.py` → nothing |
| Permissions already resolve `is_site_admin` from `role == "admin"`, `is_root` from configured root emails | `permissions.py:260–269` |
| No clock-sync/ping event exists; `server_time` rides along in every `state_sync` | `state.py:55` |

Implication for §3.5's "if the server cannot do this without breaking the web
client, negotiate a capability flag": it *can* be made additive — the web
client already sends `at`, so a native client that omits `at` must fall back to
`rs.current_position()` (the default in the `float()` expression). That is a
one-line, backward-compatible server change, not a flag. Still needs the
characterisation test that proves the web path is unchanged.

---

## 4. Test-suite state at S0

- `make test-fast` (unit + socket, no real ffmpeg): result in §7 below.
- `cargo test -p mpv_controller`: **21 passed, 0 failed** (§1.1).
- No vitest, deliberately (`docs/QA_NATIVE_CLIENT.md:13–33`): behaviour lives in
  Rust/Python and is tested there; presentation is not worth a runner.

---

## 5. Known rough edges this task will touch

1. `Player.tsx` is 1 788 lines in one function; R5/R6 force the extraction to be
   slice-by-slice, and the seam (`PlayerBackend`) must be created in the same
   branch as the `HlsBackend` it replaces.
2. The 2-second dub-drift loop (`Player.tsx:828–830`) is the client-side
   compensation the task orders deleted on the native path — mpv's `audio-add`
   keeps it synced by construction.
3. The `play`/`pause` `at` trust (§3) is a pre-existing server behaviour, not
   something introduced here; the fix must be additive.
4. `docs/QA_NATIVE_CLIENT.md` §2 documents a `playback_status` event that does
   not exist. Correct it in S8 (it is a doc, not code, so no R4 violation).
5. `tauri.conf.json` CSP `connect-src` already allows `https://vergoboy.ir`
   (needed for ROOM mode), but `media-src` allows `https://vergoboy.ir` and the
   loopback dev ports only. Local file playback does not need `media-src`
   (mpv reads the file directly), but S3 must confirm the webview does not try
   to fetch the local path.

---

## 6. Out of scope, parked (per the task's own list + R4)

Written to `docs/FOLLOWUPS.md` when S0 lands:

- Syncplay-style per-user local files matched across clients.
- Anything observed in `app.py` that this task does not need (listed, not fixed).

---

## 7. Baseline test result

```
$ make test-fast
venv/bin/python -m pytest -m "not integration" -q
...
815 passed, 2 skipped, 20 deselected, 3 warnings in 412.56s (0:06:52)
EXIT=0
```

Both skips are `test_ffmpeg_cmd_golden.py:121` (`direct_play` /
`subtitle_convert` are not HLS commands) — pre-existing, unrelated to the
player. 412.56 s wall. **815/2/20 is the number S1 must not regress from.**

```
$ cargo test -p mpv_controller
running 18 tests ... ok (0.30s)
running 3 tests  ... ok (5.17s)
EXIT=0
```

**21/0 on the Rust side** — the other half of the baseline.
