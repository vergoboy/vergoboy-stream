# QA: native client, two machines

The manual script for the parts of the native path no automated suite can
assert. Everything here is deliberately *not* covered by vitest — see
"Why there is no vitest" below.

This document covers what exists today. The native playback path itself is
**not built yet** (Phase 1C/2C: `MpvBackend`, Rust `SyncEngine`), so steps
marked *(blocked)* describe the intended procedure and will only become
executable once those land. Everything unmarked is live now and can be run
today.

## Why there is no vitest

The panel's logic splits cleanly in two:

* **Behaviour** — clock correction, drift thresholds, buffering suppression,
  the `pause_at` alignment tolerance. This is Rust (`SyncEngine`) and Python
  (the sync service) logic and is unit-tested there, against a fake player and a
  fake transport. A TS test could only re-assert what those tests already own,
  through a mock of the boundary.
* **Presentation** — reducers and formatters, which decide what a chip looks
  like from a status the server already computed.

Testing the second group in TS would add a test runner, a config file and a CI
step to assert that `drift_seconds > 2` renders as a warning colour. The
behaviour that actually matters — whether a viewer is *really* out of sync — is
decided in Rust, where it is already tested. So: no vitest, and this document
instead.

If a future slice finds real presentation logic that genuinely warrants it, the
ADR belongs with that slice, not before.

## Setup

**Machine A — the server.**

```sh
cd vergoboy-stream
venv/bin/python app.py            # or your usual entrypoint
```

Note the port. Everything below assumes `8801` unless you changed it.

**Machine B — the client.** Native mpv playback is blocked, so for now use the
web build from machine B's browser:

```sh
cd webapp && npm run dev -- --port 3001
```

Open `http://<machine-A-ip>:3001`, log in, and enter the room.

Use the machine's real LAN IP, not `127.0.0.1` — `127.0.0.1` on machine B is
machine B.

## Automated suites that back this up

Run these before blaming the manual script:

```sh
make test-fast     # no database, no server
make test-socket   # boots embedded postgres + a real server, real clients
venv/bin/python -m pytest tests/api -q   # the HTTP entrypoints, real server
```

## Script

### 1. Presence and status chips *(live)*

1. Open the room in two browser profiles on machine B, and one window on a
   third machine or an incognito profile.
2. Confirm each member appears exactly once in the lounge, even with several
   tabs open.
3. Confirm a controller and a watcher are visually distinct — the panel reads
   `can_control` / `is_owner` from presence.

**Checks:** one chip per person; owner badged; watcher not actionable.

### 2. Per-member playback status *(live backend, clients report it)*

The server cannot know whether *your* decoder stalled, so clients report it on
the `playback_status` socket event and the panel renders what comes back on
`presence`.

Drive it from the browser console on machine B:

```js
// healthy
sio.emit("playback_status", { state: "ready", drift_seconds: 0.2 });

// stalled, should show "buffering"
sio.emit("playback_status", { state: "buffering", drift_seconds: 0 });

// behind, should show "out of sync by 4.25s"
sio.emit("playback_status", { state: "out of sync" === 1 ? "" : "out_of_sync", drift_seconds: 4.25 });

// recovering, must go back to ready — a chip stuck on out-of-sync is a bug
sio.emit("playback_status", { state: "ready", drift_seconds: 0 });
```

Replace `sio` with the app's socket handle if it is not exposed globally.

**Checks:**

- [ ] a member who has never reported shows as *ready*, not broken
- [ ] `buffering`, `reconnecting`, `out_of_sync by <N>s` each render
- [ ] one person with three tabs shows **one** chip reflecting the worst tab
- [ ] recovery is visible immediately
- [ ] a junk report (`{"state":"nonsense","drift_seconds":"abc"}`) does not stop
      presence updates for anyone
- [ ] a client reporting `"ready"` with `drift_seconds: 6` is shown as
      *out of sync* — drift outranks the label it was sent with

### 3. Playlist add and remove *(live)*

1. Add a pasted `.m3u8` URL. It should appear immediately as ready and be
   seekable anywhere — no transcode, so no frontier.
2. Add an `.mp4` URL. It should appear as *queued* with a progress indicator.
3. Remove each. Removal requires `can_control`; try it as a watcher and confirm
   it is refused with a clear message rather than silently ignored.

**Checks:**

- [ ] a direct HLS source is playable straight away and seekable past any point
- [ ] a source needing encoding is queued, and shows no `src` until ready
- [ ] a watcher cannot remove an item
- [ ] removing clears the item's HLS directory from disk
- [ ] the title falls back to the last path segment, with any query string
      stripped

### 4. Native + web sync *(blocked)*

Blocked on Phase 1C/2C. When it unblocks, the procedure is:

1. Machine A: start the server. Machine B: run the native client against it.
   Machine C (or a second profile): the web build, same room.
2. Start playback on the native client.
3. Let it run 60s. Compare positions on both screens.
4. Confirm web-only controls behave: the encoded-frontier clamp applies to the
   HLS viewer and **not** to native playback.
5. Change items, and pause/resume from each side in turn.

**Checks:**

- [ ] no drift accumulates over 60s of playback
- [ ] a seek from either side lands both viewers together
- [ ] switching items is reflected on both within one sync round
- [ ] quality/transcode controls are hidden on native and shown on web
- [ ] codec/track info from ffprobe metadata is shown on the native path

### 5. Sync accuracy gate *(blocked)*

The release gate is a measured p95 sync error under 150ms on a clean network.
This cannot be asserted by any unit test, because it is a property of two real
decoders and a real network. Procedure when the engine lands:

1. Wire the Rust `SyncEngine` fake transport to a two-process simulator.
2. Run the named scenarios (pause/resume, seek, rate change, item switch) with
   injected delay and jitter.
3. Record p50/p95/p99 sync error per scenario.
4. **Gate:** p95 < 150ms on a clean network; publish the degradation curve
   against injected delay.

## Notes and known rough edges

- `test-socket` takes ~5 minutes. It boots a real PostgreSQL and a real
  server, and each client teardown is slow under polling-only transport. This is
  deliberate — the logic under test lives in the connect handshake, which
  `socketio.test_client` bypasses entirely.
- `tests/api` runs in seconds and uses the same real server. Prefer it for new
  HTTP-level coverage.
- `i.txt` is **not** stray, despite what the S4 audit finding says: it is the
  captured form `archive_filters.available_options()` reads at import time, and
  removing it breaks collection of nine test modules. `get_state.py` and
  `trigger_add.py` are documented CLIs. `app.py.save` was the only stray file
  and has been removed.