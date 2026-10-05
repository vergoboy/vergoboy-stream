# ADR 0001 — Slice-by-slice migration of `app.py` and target layering

- **Status:** Accepted — 2026-10-05 (phase 0)
- **Deciders:** project owner
- **Context:** `app.py` is 4 156 lines holding every domain (media/encode, rooms, sync,
  playlist, auth, admin, archive, YouTube, chat). A native-mpv direction needs room to move in
  it. Baseline: tag `pre-mpv-baseline` → `0ccf192`, suite **608 passed / 2 skipped in 410 s**.

## Decision

**1. Vertical slices, not big-bang refactors.**

- A phase touches one domain. Code outside that domain is never reorganized "while we're
  here".
- When a phase touches a domain of `app.py`, that domain is extracted **in the same phase**:
  1. characterization tests first — pin the current behavior;
  2. move the code into its module;
  3. **delete the old code from `app.py` in the same branch** — no `app.py.bak`, no `v2`
     copy, no dead alias left behind.
- Untouched domains stay in `app.py` until their own phase.
- No speculative modules, no empty packages, no modules created before the phase that needs
  them.

**2. Layering applies to every module created or extracted:**

```
handlers (thin)  →  services (no Flask / SocketIO imports)  →  adapters (DB, filesystem,
                                                              ffmpeg/ffprobe, network)
```

- Handlers parse/validate input, call one service, shape the response. No business logic.
- Services own decisions and orchestration; they must not import `flask`, `flask_socketio`,
  or `request`/`g`/`emit`.
- Adapters wrap the outside world: SQLAlchemy, disk, `ffmpeg`/`ffprobe`, HTTP.
- Dependencies point **downward only** (handler → service → adapter; never the reverse, never
  adapter → service).
- Enforcement is an **import-boundary test** that starts small and is **grown slice by
  slice**: each phase adds its module(s) and their allowed edges to the boundary spec, so a
  violation fails CI from that phase onward. It never asserts about modules that do not
  exist yet.

**3. Documentation follows the code.**

- `BACKEND_RULES.md` is **drafted at the end of phase 1 from what was actually done** and
  **finalized in phase 4**.
- Rules are only written for modules that exist. Nothing is specified for a future layout.

**4. Phases (from the slice map in `docs/ARCHITECTURE_AUDIT.md` §4):**

| Phase | Domain | Gate |
| --- | --- | --- |
| 0 | baseline, exports, audit, this ADR — **no production code changed** | tag + bundle + audit exist; suite green |
| 1 | media (URL intake, probe, ladder, encode, progress/seek, HLS serving) | suite green, old code deleted, import-boundary test introduced |
| 2 | rooms / sync / sockets | suite green, boundary extended |
| 3 | playlist | suite green, boundary extended |
| 4 | the rest (auth, admin, archive, YouTube, chat, bootstrap) | suite green, boundary complete, `BACKEND_RULES.md` finalized |

## Consequences

- **Positive:** every phase is independently reviewable and revertible to
  `pre-mpv-baseline`; behavior stays pinned by characterization tests before each move; the
  layering rule is proven against real modules instead of guessed; `app.py` shrinks
  monotonically and ends phase 4 as a composition root.
- **Negative / accepted cost:** each extraction pays for tests first; cross-domain calls made
  from a not-yet-extracted `app.py` keep working through the existing names until their own
  phase, so the boundary test is deliberately incomplete until phase 4; fixes to the security
  findings (S1–S3) are staged into their owning phases rather than shipped immediately.
- **Out of scope for all phases:** multi-worker deployment, persistence format changes,
  frontend refactors, and deleting anything the native-mpv design might obsolete (see audit
  §5 — that needs its own ADR first).
