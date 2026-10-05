# ADR 0002 — Play media in mpv as a separate process (spike + license decision)

- **Status:** Accepted — 2026-10-05 (phase 1, part A)
- **Complements:** ADR 0001 (staging of the migration)
- **Evidence:** `docs/spike/mpv-ipc-observations.json` — regenerate with
  `venv/bin/python scripts/mpv_spike.py` (exit 0 = every check passed)

## Context

Phase 1 must let the native app play the **original** file (any codec/container, 10-bit,
HDR, MKV, multi-track, ASS) in mpv, with local controls and no multi-client sync yet.
Two questions had to be answered with evidence before building the controller:

1. Can we drive a spawned mpv over `--input-ipc-server` — observe time, pause, buffering
   and seeks, and send commands back?
2. Do we link libmpv (LGPL) into the Tauri binary, or spawn the mpv binary (GPL) and talk
   to it?

## Spike result (real mpv v0.41.0, this machine)

| Check | Result | Evidence |
| --- | --- | --- |
| spawn with `--input-ipc-server` (unix socket) and connect | pass | `spawn_and_connect` |
| `file-loaded` event on load | pass | `file_loaded_event` |
| `time-pos` ticks (0.0 → 1.0417 after 1 s) | pass | `time_pos_sample` |
| `pause` set/get round-trip + `property-change` event | pass | `pause_roundtrip`, `pause_observed_event` |
| absolute seek to 12 s (lands 12.5 s) + `seek` event | pass | `seek_absolute`, `seek_event` |
| `speed` set to 2.0 and back | pass | `speed_set` |
| `paused-for-cache` readable **and** flips True/False as an event on a throttled source | pass | `buffering` |
| `core-idle` readable, True while paused | pass | `core_idle_readable`, `core_idle_reflects_pause` |
| `quit` → exit code 0, no zombie | pass | `clean_quit`, `no_zombie` |

Buffering had to be provoked with a deliberately throttled local HTTP server (12 KB/s, below
the fixture's bitrate) — a local file never starves. Two fixture traps are recorded in
`scripts/mpv_spike.py`: mp4 `moov` must be at the front (`-movflags +faststart`) or mpv never
starts demuxing, and `observe_property` emits the current value on subscription (a `False`
for `paused-for-cache` is *not* a "buffering cleared" edge).

**Window check (gate: "real mpv window"):** a windowed spawn on this Wayland session was
listed by `hyprctl -j clients` as `spike.mp4 - mpv` while playing (`time-pos` 3.9 s) and
still answered IPC. A separate window works; no embedding was needed to prove it.

## Decision

**Spawn the mpv binary as a separate process and speak JSON IPC to it. Do not link
libmpv.**

1. **License.** The package declares `GPL-2.0-or-later AND LGPL-2.1-or-later`
   (`pacman -Qi mpv`): the *client binary* is GPL-2.0+, `libmpv.so` is LGPL-2.1+.
   - Spawning the binary is an ordinary aggregation: our code neither links nor
     conveys mpv, so the Tauri app stays under its own license. mpv is a user- or
     distro-installed program, like ffmpeg already is for this project.
   - Linking `libmpv` would put an LGPL-2.1+ dynamic library inside our binary,
     with relinking/disclosure obligations, a build+bundle step for every target
     (Windows/macOS/Android), and Rust FFI over a C API — all to buy an in-window
     surface that does not exist on Wayland or macOS anyway.
2. **Isolation and restart.** The phase requires "restart on crash". A separate
   process gives a real crash boundary: mpv segfaulting, a driver reset, or a
   hang are recoverable by killing and respawning — impossible to do to code
   linked into our own address space.
3. **Interface stability.** The JSON IPC (`--input-ipc-server`) is mpv's documented
   public control surface: request/response with `request_id`, plus an event stream
   (`file-loaded`, `seek`, `property-change`). It is the same protocol the spike
   already drives from Python, so the Rust client is a port, not a redesign.
4. **Display strategy.** Separate mpv window **first** (works on X11, Wayland and
   macOS). Embedding via `--wid` is optional later and X11-only — on Wayland mpv
   cannot be reparented into a foreign surface, and macOS has no `--wid` at all.
   Revisit embedding only if the separate window is rejected in use.

## Consequences

- `mpv_controller` owns: locate/bundle binary, spawn, IPC client (request_id
  correlation, timeouts, reconnect), event stream, restart-on-crash, kill-on-exit —
  all mpv-agnostic above the `PlayerPort` trait.
- mpv becomes a runtime dependency of the native app, detected at startup with a
  clear "install mpv" message when absent (`docs/DEPENDENCIES.md`).
- Controls are limited to what IPC exposes; anything the UI needs later must be a
  property or command, not shared memory.
- The web client keeps the HLS path untouched (ADR 0001 phase boundary).
