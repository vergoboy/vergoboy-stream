# RECONCILIATION — reality vs plan (2026-10-06, session 1)

Command output this is based on is pasted in the session report. Summary of
facts: branch `feat/cinema-redesign` = `origin/feat/cinema-redesign` = `800efad`,
**23 commits ahead of `main`** (`main` = `origin/main` = `96c2fcc`, tag
`pre-mpv-baseline` exists). Tree is **dirty** (5 modified, 1 staged deletion,
8 untracked paths). `docs/PROGRESS.md` did not exist — created this session.

## Branches

| Branch | State | Verdict |
| --- | --- | --- |
| `main` / `origin/main` | `96c2fcc`, behind | baseline; nothing merged back yet |
| `feat/cinema-redesign` (+ origin) | `800efad`, 23 ahead of main, **unmerged** | current work branch — ask user when to merge |
| `imported/cinema-redesign-auth` | fully contained in `feat/cinema-redesign` (merge 274b5cb) | no action, no merge needed |

## Committed work — classification

| Work | Where | Class | Reason |
| --- | --- | --- | --- |
| Phase-0 audit, ADR 0001 (slice-by-slice), ADR 0002 (mpv separate process), DEPENDENCIES | feat branch 655e4c1, 97e9dd7, 0630752 | KEEP | required artifacts of P0; mpv GPL decision documented |
| `media_pipeline/` (probe/planner/ffmpeg_cmd/runner + tests) | main (7afebd9…) | KEEP | predates plan, green tests, P1 will build on it |
| `media_domain/` URL-intake slice (services + adapters, char. tests, boundary test, S1 path-traversal fix) | feat branch 931d924..800efad | KEEP | exactly the rule 2'/5' pattern: tests first → move → delete → boundary test |
| mpv IPC spike + `mpv_controller` crate (PlayerPort, fake, real-mpv tests) | feat branch 0630752, ec6891e | KEEP | P1's mpv-playback core; client-side only |
| Cinema-redesign webapp, archive/auth, packaging, logging, permissions modules | feat branch (merged history) | KEEP | pre-existing, out of master P-scope, not ours to revert |
| `docs/QA_NATIVE_CLIENT.md` | untracked | KEEP | QA plan for native client; commit with next docs commit |

## Uncommitted work — classification (tree dirty; stopped per rule 1)

| Change | Files | Class | Reason |
| --- | --- | --- | --- |
| Staged deletion of `app.py.save` (596 lines) | app.py.save | KEEP → commit | fixes known open issue "committed backup files" |
| Frontier-from-disk recovery + ready-src clamp | app.py (+101/-17), tests/unit/test_frontier_gaps.py, test_frontier_real_encode.py (untracked) | KEEP → commit | tested seek-guard behaviour, 792 tests green; belongs to existing seek/rendition domain |
| Playback-status chips (client-reported, worst-per-person) | state.py, tests/unit/test_playback_status.py, tests/socket/test_playback_status_socket.py (untracked) | KEEP → commit | tested incl. real-DB socket test; feeds P2/P3 control panel |
| `playlist_domain/services/items.py` + endpoint tests | playlist_domain/, tests/api/, tests/unit/test_playlist_items.py, Makefile test-api, pytest.ini `live_api` marker, test_import_boundaries.py grown | KEEP → commit (partial slice) | services-only slice for P3 playlist work, started early; boundary test skips adapters until they exist. Flagged to user: is early start acceptable, or park it? |
| `docs/PROGRESS.md`, `docs/RECONCILIATION.md` | this session | KEEP | required by master instructions |

Nothing reverted, nothing rewritten.

## Discrepancies / decisions needed

1. **Dirty tree** — rule 1 says stop and ask. Recommended default: commit the
   four logical chunks above as separate small commits on
   `feat/cinema-redesign`, then open `phase/1-…` for remaining P1 work.
2. **Phase numbering** — master plan P0–P6 (this doc) supersedes; the audit's
   slice map (P1 media, P2 sync, P3 playlist, P4 rest) maps 1:1 onto it. The
   committed "phase 1a/1b" mpv work = master P1. No rework needed.
3. **Test command** — bare `python -m pytest` fails (`No module named
   'sqlalchemy'`); `venv/bin/python -m pytest -q` → **792 passed, 3 skipped**.
   Makefile already defaults to `$(VENV)/bin/python`. Default: use that.
4. **Unmerged branch** — `feat/cinema-redesign` vs `main`: ask user when to
   merge (gate rule: only after phase gate + approval).

## Session decisions (user, 2026-10-06)

1. Dirty tree → Option A: small commits on `feat/cinema-redesign` in order
   (a) `app.py.save` deletion, (b) frontier clamp + tests (`git add` per hunk).
   The playback-status and playlist chunks are P3 work done early → parked on
   **`wip/p3-early`**, unmerged; when P3 starts that branch is the starting
   point (reuse, not rewrite). It counts toward no gate until then.
2. `feat/cinema-redesign` merges to `main` only after the P1 gate passes and
   the user approves.
3. **`imported/cinema-redesign-auth`** (local branch): a bundle imported from
   the user's other working copies (authors `vergoboy`, `Claude`, 2026-10-04)
   containing archive security-question solving for the Digimoviez login plus
   Arch/Tauri build fixes. It was merged into `feat/cinema-redesign` as
   274b5cb ("Merge archive security-question integration from
   cinema-redesign-auth bundle") and `git merge-base --is-ancestor` confirms it
   is fully contained in `feat/cinema-redesign`. **Nothing on it is needed
   separately**; it is left untouched as a historical pointer.
4. `app.py` grew 3858 (main) → 4059 (dirty tree). From now on it may only
   shrink; the P1 gate must show a net reduction vs the P1 start commit, or a
   written justification (per-commit breakdown goes in the P1 report).
5. Full P1 acceptance: native client plays an item from the real server in a
   real mpv window (any codec/container incl. 10-bit MKV), web/HLS client
   unchanged; deliverables = `/api/capabilities` + scoped short-lived media
   token for native-mpv (original file, no transcode), ffprobe metadata-only
   exposure, S2/token/path security fixes with failing-before/passing-after
   tests, minimal `MpvBackend` behind the existing `PlayerBackend` interface.
   Order: security fixes → capabilities/media token/metadata → MpvBackend →
   gate.
6. Test speed: real-encode tests marked `integration`; `make test-fast` =
   fast suites with no real encodes; `make test` = full run; Makefile uses the
   venv interpreter so the bare-`python` problem cannot recur.
