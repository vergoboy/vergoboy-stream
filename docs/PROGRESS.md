# PROGRESS ledger

Read this first, every session. Update after EVERY step.
Statuses: todo / doing / done / blocked.
Phases in fixed order: P0 → P1 → P2 → P3 → P4 → P5 → P6. No phase starts
before its predecessor's gate passed AND the user approved.

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
