# ADR 0004 — Telling our own seeks from everything else (seek-origin matching)

- **Status:** Accepted — 2026-10-17 (mpv player task, S1/S2)
- **Complements:** ADR 0002 (mpv as a separate process), ADR 0003 (instance identity)
- **Evidence:** the measurement table below (`/tmp/opencode/mpv-seek-probe*.py`
  against mpv v0.41.0, headless, one file / a chaptered file / a 2-item
  playlist), `cargo test -p mpv_controller` (unit + real-mpv suites)

## Context

ROOM mode needs a synchronisation engine (S6) that **drives** playback for a
room and must know when *its own* seek has landed. Before it is written, the
distinction between the two seek events has to be pinned, because everything
S6 does rests on it.

mpv reports every seek the same way. Measured, verbatim from mpv v0.41:

```json
{ "event": "seek" }
```

There is **no `reason` field, no `request_id`, no target position**. A seek
issued by our own `seek` command, a seek from a keypress inside mpv, a chapter
jump and a loop restart all arrive as byte-identical messages. `playback-restart`
follows every one of them and is equally bare.

## Decision

Classify on a **single pending-credit counter, `own_seeks: u32`** — no ids, and
**no time window**:

- `MpvPlayer::seek()` increments the counter *before* sending the command and
  decrements it again if mpv rejects the command (no `seek` event will arrive).
- `load_inner(start > 0)` does the same, so loading at a position counts as our
  seek.
- On a `seek` event: a credit outstanding → consume one and emit
  `PlayerEvent::Seeked { pos }`; no credit → emit `PlayerEvent::UserSeek { pos }`.
- `respawn()` zeroes the counter, so a connection lost between our command and
  its event drops the credit instead of leaving it to be spent later.

**Why no time window.** Every own seek produces exactly one `seek` event —
*including a seek to the position already current* — so credits are consumed
1:1 and there is no unmatched credit to age out. The only path that could leave
a credit stranded is a process death in flight, which `respawn()` already
clears. A window would therefore be a second clock guarding a path that does
not exist.

## Measurements (mpv v0.41.0)

| what triggered it | `seek` events | classification today |
| --- | --- | --- |
| `seek` `absolute+exact` | 1 | `Seeked` |
| `seek` `absolute+exact` **to the current position** (no-op) | 1 | `Seeked` |
| `seek` `absolute` (keyframe) | 1 | `Seeked` |
| `seek` `relative` | 1 | `Seeked` |
| `seek 0` | 1 | `Seeked` |
| `set_property time-pos` (the port never does this) | 1 | `Seeked` *only if counted* — it is not, so `UserSeek` |
| chapter jump — `set_property chapter`, `add chapter` | 1 | **`UserSeek`** |
| mpv default key binding — `keypress right` | 1 | **`UserSeek`** |
| `--loop-file=inf` restart at EOF | 1 | **`UserSeek`** |
| playlist advance — `set playlist-pos 1` | 0 | not a seek: `end-file`/`start-file`/`file-loaded`, handled by the load path |

## Consequences — what S6 may and may not rely on

1. **`Seeked` is ours, unconditionally.** It can only be produced while a credit
   is outstanding. Safe to treat as "our seek landed" and to use as
   confirmation.
2. **`UserSeek` means "not ours", not "the local user".** Every mpv-originated
   seek above lands here: key bindings, chapter jumps, loop/repeat restarts,
   and — not measured, but the same bare message — hr-seek correction and
   post-buffer resync. **S6 must treat `UserSeek` as advisory** and never as
   authority to re-sync a room.
3. **Making (2) narrow requires mpv's own input off.** The measured `keypress
   right` and `keypress q` cases mean that with default bindings enabled a
   focused mpv window can seek the player *or quit it*, and the port cannot
   tell a keypress from anything else. `MpvOptions` does **not** currently pass
   `--input-default-bindings=no`. Enabling it is a production behaviour change
   that is not part of this commit: it is recorded in `docs/FOLLOWUPS.md` and
   needs approval before it lands.
4. **Repeating must not be `--loop-file`.** Its restart is a `UserSeek` every
   cycle. Either drive repeats through the port or have S6 ignore those seeks.
5. **The one race, stated rather than papered over:** a user seek landing
   between our command and our event swaps the two labels — one event each,
   order preserved. Because S6 issues one seek at a time, the window is a
   single event wide and self-limiting. No mitigation exists short of mpv
   growing a correlation id.

## Test coverage for the above

- `mpv_controller/src/mpv.rs` (unit, no process): one credit consumed 1:1, two
  outstanding credits in order, an internal seek with no credit → `UserSeek`,
  an internal seek stealing a credit (case 5's sibling), and a respawn dropping
  a stranded credit.
- `mpv_controller/src/fake.rs` (unit): the fake has no socket, so it now emits
  `Seeked` from its own `seek()` — it previously emitted nothing, which meant a
  SyncEngine built on the port could never see a `Seeked` against the fake.
  `FakePlayer::load(start > 0)` still emits **no** `Seeked` (the real player
  does, via the same credit); that asymmetry is deliberate and documented at
  the call site, not hidden.
- `mpv_controller/tests/real_mpv.rs`: our seek yields exactly one `Seeked` and
  zero `UserSeek`; an mpv-initiated seek (`--loop-file=inf` restart) yields
  `UserSeek`, pinning limitation (2) as measured behaviour rather than a
  comment.
