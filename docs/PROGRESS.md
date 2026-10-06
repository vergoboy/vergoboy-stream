# PROGRESS ledger

Read this first, every session. Update after EVERY step.
Statuses: todo / doing / done / blocked.
Phases in fixed order: P0 → P1 → P2 → P3 → P4 → P5 → P6. No phase starts
before its predecessor's gate passed AND the user approved.

## Session 1 (2026-10-06) — reconciliation

| Phase | Step | Status | Commit | Evidence |
| --- | --- | --- | --- | --- |
| all | Section 1 reconciliation (first action) | done | n/a | see docs/RECONCILIATION.md; test run: 792 passed, 3 skipped (`venv/bin/python -m pytest -q`) |
| all | Tree is dirty — stopped and asked (rule 1) | done | n/a | user decided Option A (see RECONCILIATION "Session decisions") |
| all | docs commit (PROGRESS/RECONCILIATION/QA) | doing | pending | this commit |
| P1-hk | (a) delete app.py.save | doing | pending | staged, 596 lines removed |
| P1-hk | (b) frontier clamp + its tests on feat/cinema-redesign | todo | pending | app.py hunks @@ -560/-587/-621 only |
| P3-early | playback-status + playlist chunks → wip/p3-early | todo | pending | parked, unmerged, counts toward no gate |
| P1 | branch phase/1-media-and-mpv, suite green | todo | pending | full pytest run required |
| P1 | test-speed housekeeping (integration marker, test-fast, venv) | todo | pending | decision 6 |
| P1 | S2 SSRF / safe_local_path / token-in-query (tests first) | todo | pending | url_intake.py:69 defers S2 |
| P1 | capabilities + media token + metadata; MpvBackend; gate | todo | pending | acceptance in RECONCILIATION decision 5 |
| P0 | baseline audit, ADR 0001/0002, slice map | done | 655e4c1, 0630752, 97e9dd7 | docs/ARCHITECTURE_AUDIT.md, docs/adr/ |
| P1 | media URL-intake slice extracted + S1 path traversal | done | 931d924, 99ca8e1, da0d01b, 800efad | 792 tests green incl. media tests |
| P1 | mpv spike + mpv_controller crate (client-side 1a/1b) | done | 0630752, ec6891e | docs/spike/mpv-ipc-observations.json, crate tests |
| P1 | S2 SSRF, token-via-query-string | todo | pending | part of step "security fixes" above |
| P2–P6 | — | todo | — | — |

## ADR log

- ADR-2026-10-06-01: test command is `venv/bin/python -m pytest` (bare `python`
  has no sqlalchemy); decision 6 hardens this into the Makefile.
- User decisions of 2026-10-06 (parked P3 work on `wip/p3-early`, merge
  policy, app.py shrink-only, full P1 acceptance, test speed) are recorded in
  docs/RECONCILIATION.md § "Session decisions".
