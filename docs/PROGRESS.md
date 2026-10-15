# PROGRESS ledger

Read this first, every session. Update after EVERY step.
Statuses: todo / doing / done / blocked.
Phases in fixed order: P0 → P1 → P2 → P3 → P4 → P5 → P6. No phase starts
before its predecessor's gate passed AND the user approved.

## Session 3 (2026-10-15) — w2g delivery: T0 reconcile

Mission (from the master instructions): a second instance at
https://vergoboy.ir/w2g served from /opt/w2g, leaving the existing
https://vergoboy.ir/stream, /opt/stream, its unit, DB and port untouched.
Branch `release/w2g-v1` cut from `main` at 8cb0873. Three hard gates: G1 local
readiness, G2 deploy plan, G3 final report. The old P2–P6 roadmap is PAUSED.

| Track | Step | Status | Commit | Evidence |
| --- | --- | --- | --- | --- |
| T0 | reconcile: HEAD, tree, branches, tags | done | n/a | HEAD 8cb0873; tree clean; `wc -l app.py` = 4093; only tag `pre-mpv-baseline` |
| T0 | branch release/w2g-v1 cut from main | done | n/a | `git branch --show-current` |
| T0 | dev env: venv rebuilt with uv, py3.12 | done | n/a | `venv/bin/python -V` = Python 3.12.15 |
| T0 | dev env: dev lock regains pytest/pytest-cov | done | 4157d1f | additive 155/0; now pins pytest 8.4.2, pytest-cov 6.3.0 |
| T0 | dev env: `make test-fast` guards the interpreter | done | 11a5f11 | `make test-fast` = 768 passed, 2 skipped, 20 deselected (405s) |
| T1 | parametrize base path + instance identity | doing | — | plan proposed, awaiting user go |

Media-wipe post-mortem (requested at T0). The wipe was self-inflicted by our own
cleanup routine — nginx aliases and paths were never involved.
`_cleanup_orphans()` (app.py) deletes every file under UPLOAD_DIR/SUBS_DIR and
every dir under HLS_DIR that is not "referenced", and `_collect_referenced_files()`
built that reference set only from rooms open *in-process* (`rooms._rooms`) plus
the single `room`. On a fresh boot the registry is empty, so the first
`POST /stream/api/cleanup` — or any `/stream/api/delete?...cleanup=1` — classified
every cold room's uploads, subtitles and HLS dirs as orphans and removed them,
even though those rooms still existed as JSON in `data/rooms/`. The files were
simply gone, so clients got a 404 on `master.m3u8`: the endless-spinner shape
that 09231c1 now turns into a visible "this video's file was deleted" error.
3ec15ab fixed the root cause (the collector also scans `data/rooms/*.json`, so a
cold room owns its media) and e8bcf20 stopped the socket suite from writing into
the developer's real `data/rooms` and triggering cleanups against real media.
Confirmation for T4: the deploy scripts will never read or delete `/opt/w2g/media`
or `/opt/stream/media` — `deploy.sh` writes only under `/opt/w2g/releases/<ts>/`,
repoints the `/opt/w2g/current` symlink, and rsyncs the static export into
`/opt/w2g/webapp-out`; `media`, `data` and `hls-live` live outside the release
tree and are referenced by no `rm`, `rsync --delete`, or cleanup call.

## Session 2 (2026-10-15) — main-branch fix batch

Five fixes off the phase plan, one commit each. No phase gate is involved and
none of the P0–P6 rows move. Evidence: `make test-fast` = 768 passed, 2
skipped, 20 deselected (405s); `npx tsc --noEmit` clean; eslint over
`app/ components/ lib/` = 0 errors (14 pre-existing warnings).

| Area | Step | Status | Commit | Evidence |
| --- | --- | --- | --- | --- |
| ui | Latin numerals throughout the Persian UI | done | 9cf68c3 | 11 files; fa-IR → fa-IR-u-nu-latn for date/time stamps |
| ui | avatar falls back to the name initial on a failed image | done | 2afc0c2 | Avatar.tsx tracks the failed src, draws the initial |
| web | stale session healed (mount `/me` + socket `connect_error`) | done | 158d199 | page.tsx +20, useRoomState.ts +11 |
| state | vanished media flagged as an error on load | done | 09231c1 | state.py +32; tests/unit/test_state_missing_media.py (6 tests) |
| build | PKGBUILD pins rustc 1.97.1, caps CARGO_BUILD_JOBS=4 | done | 95da02d | 1.99.0 SIGSEGV / 1.97.1 GPF workaround |

Notes:
- 09231c1 is the follow-through on the media wipe: 3ec15ab stopped *new*
  deletions, this surfaces items the wipe already blighted as a friendly error
  card instead of an endless spinner on a 404 master.m3u8.
- `_source` semantics are reused from app.py:1350 — an absolute path means
  "check the file exists"; anything else with an http(s) scheme is treated as
  re-fetchable and left "complete" for the on-demand encoder.
- The 768 (was 762 after e8bcf20) is the 6 new tests in
  tests/unit/test_state_missing_media.py.

## Session 1 (2026-10-06) — reconciliation + commit housekeeping

| Phase | Step | Status | Commit | Evidence |
| --- | --- | --- | --- | --- |
| all | Section 1 reconciliation (first action) | done | n/a | docs/RECONCILIATION.md; 792 passed, 3 skipped on the dirty tree |
| all | docs commit (PROGRESS/RECONCILIATION/QA) | done | af57656 | git show --stat |
| P1-hk | (a) delete app.py.save | done | 5fda377 | 596 lines removed, own commit as ordered |
| P1-hk | (b) frontier clamp + its tests (3 hunks only) | done | b620294 | app.py +65/-2; tests/unit/test_frontier_{gaps,real_encode}.py |
| P3-early | playback-status chunk → wip/p3-early | done | 5d32425 | app.py handler + state.py + unit/socket tests, staged hunk-by-hunk |
| P3-early | playlist slice → wip/p3-early | done | c389d78 | playlist_domain/, tests/api/, boundary-growth, Makefile/pytest.ini live_api |
| P1 | branch phase/1-media-and-mpv from feat/cinema-redesign | done | (branch at b620294) | clean tree, app.py = 4038 lines at P1 start |
| P1 | test-speed housekeeping (decision 6) | done | c8eea6d | `make test-fast` = 704 passed, 2 skipped, 20 deselected (405s); `make test` = 724 passed, 2 skipped (410s); bare `python -m pytest` → actionable venv message, exit 1 |
| P1 | S2 SSRF / safe_local_path / token-in-query (tests first) | done | d3dfd79, e44adf8, 92e94a3 | 28+18+5 new tests; negative controls 11/7, 3/5, 3/5 vs pre-change code; `make test-fast` = 758 passed (see 2b/2c notes) |
| P1 | capabilities + media token + metadata; MpvBackend; gate | todo | pending | acceptance in RECONCILIATION decision 5 |
| P0 | baseline audit, ADR 0001/0002, slice map | done | 655e4c1, 0630752, 97e9dd7 | docs/ARCHITECTURE_AUDIT.md, docs/adr/ |
| P1 | media URL-intake slice extracted + S1 path traversal | done | 931d924, 99ca8e1, da0d01b, 800efad | tests green |
| P1 | mpv spike + mpv_controller crate (client-side 1a/1b) | done | 0630752, ec6891e | docs/spike/mpv-ipc-observations.json, crate tests |
| P2–P6 | — | todo | — | — |

Notes:
- Decision-6 venv guard: a true re-exec was attempted first and rejected —
  pytest's fd capture already replaced fd 1/2 by conftest-import time and
  `/proc/<ppid>/fd` cannot reopen pipes (ENXIO), so the child's output was
  silently lost. The honest fix: conftest raises SystemExit with the exact
  command to run; every Makefile target pins $(VENV)/bin/python.
- `-m integration` marks are honoured on unittest.TestCase classes
  (verified: 20/726 collected with `-m integration`, RealEncode +
  RuntimePipeline both in it).
- S2 security slices (all failing-before/passing-after):
  - d3dfd79: every user-URL fetch (check_link_ok / resolve_media_url_remote /
    files-API) goes through safe_fetch_url (8 MiB cap, redirect-loop re-
    validation, DNS→public-IP-only policy). tests/unit/test_safe_fetch_url.py.
  - e44adf8: ffprobe/ffmpeg inputs gated by
    media_domain/services/source_policy.validate_media_source (http→host/IP,
    other schemes refused, local paths realpath-contained in the media roots);
    remote inputs get `-protocol_whitelist http,https,tcp,tls,crypto`.
    app.py +31; log_debug 52->53 (one subtitle-refusal trace, the rest flat).
  - 92e94a3: access token no longer accepted from the query string (header /
    form / JSON body / socket auth-dict only). verify-email/reset query
    tokens kept deliberately (single-purpose email tokens).
- Media-wipe bug (reported 2026-10-06 "image and video is not loading"):
  - Diagnosis: the whole media/ tree (uploads/hls/subs/chat/avatars) was
    empty while rooms still referenced it → 404s. Non-returnable deletions.
  - 3ec15ab: `_cleanup_orphans` counted only in-process-open rooms; a cold
    room on disk (not yet `rooms.get()`-opened) had its media deleted as
    "orphan" by any /stream/api/delete or /stream/api/cleanup. Now scans
    data/rooms/*.json too; genuinely orphaned files still removed.
  - e8bcf20: live_server (socket suite) runs the real app in-process with
    real Config, so it wrote TST room JSONs into the dev's real data/rooms
    and could trigger cleanups against real media/. It now points every
    Config media/data attr at the throwaway pgserver dir, restored at
    session teardown; a test pins that persistence stays out of repo data/.
  - Neg. controls: 2/3 and 1/1 failed against pre-fix code; fast suite 762.

## ADR log

- ADR-2026-10-06-01: test command is `venv/bin/python -m pytest` (bare `python`
  has no sqlalchemy); decision 6 hardens this into the Makefile.
- User decisions of 2026-10-06 (parked P3 work on `wip/p3-early`, merge
  policy, app.py shrink-only, full P1 acceptance, test speed) are recorded in
  docs/RECONCILIATION.md § "Session decisions".
