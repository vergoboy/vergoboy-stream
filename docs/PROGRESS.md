# PROGRESS ledger

Read this first, every session. Update after EVERY step.
Statuses: todo / doing / done / blocked.
Phases in fixed order: P0 → P1 → P2 → P3 → P4 → P5 → P6. No phase starts
before its predecessor's gate passed AND the user approved.

## Session 11 (2026-10-17) — mpv player task (LOCAL + ROOM), steps S0→S8

Mission (task doc): native client plays the **original** file in a real mpv
process — LOCAL mode (offline, no login) and ROOM mode (original bytes over
Range, no transcode, synchronized) — driven through Tauri commands; the web
build keeps its HLS player untouched. Deployment/packaging/server work is out
of scope (→ `docs/FOLLOWUPS.md`). Fixed order S0→S8, GATE D1 after S2, GATE D2
after S6, both stop the run for user review. Documentation for S0 lives in
**`docs/PLAYER_AS_IS.md`** (baseline: 815 py / 21 rust, and the exact feature
inventory S7 must reproduce).

| Step | Status | Commit | Evidence |
| --- | --- | --- | --- |
| S0 recon: baseline commands (`git status/branch/log`, `ls`, `wc -l`) | done | n/a | tree clean on `release/w2g-v1` @ `2abbe52`; `Player.tsx` = 1788 lines; full output quoted in docs/PLAYER_AS_IS.md §0 |
| S0: `docs/PLAYER_AS_IS.md` written (mpv state + full player inventory) | done | c17a155 | 44 features enumerated (§2), crate state (§1), server/sync contract (§3), 5 rough edges (§5); out-of-scope notes → `docs/FOLLOWUPS.md` §S0 (incl. the `.desktop` MimeType gap the "Open with" DoD hits at S6) |
| S0: `make test-fast` baseline | done | n/a | **815 passed, 2 skipped, 20 deselected, 412.56s**, EXIT=0 (2 skips = pre-existing `test_ffmpeg_cmd_golden` non-HLS cases) |
| S0: `cargo test -p mpv_controller` baseline | done | n/a | **18 unit + 3 real-mpv = 21 passed, 0 failed**, EXIT=0 (real tests self-skip without mpv) |

S0 findings that correct the task's premises (recorded, no code changed):

- `mpv_controller` **already exists and is green** (2885 lines, `ec6891e`,
  ADR 0002) — `PlayerPort`/`FakePlayer`/`MpvPlayer`/`MpvProcess`/IPC + real-mpv
  integration tests. S1 *characterizes and extends* it, it does not start it.
  Missing vs the task's §3.1: subtitles/`sub-add`, sub styles, `set_fullscreen`,
  `get_state`, richer events (`Loaded/Position/Seeked/TrackChanged/
  ProcessCrashed`), and all `--wid` window plumbing (tiers 1–2 don't exist).
- `PlayerBackend`/`HlsBackend`/`MpvBackend` **do not exist** — the seam the task
  calls "the only shared one" is aspirational (`RECONCILIATION.md:79`,
  `PROGRESS.md` P1 row still `todo`). S7 creates it.
- `GET /api/capabilities` **does not exist** (only an unrelated docstring at
  `app.py:271`); no media-token code anywhere. nginx already serves
  `/w2g/media/` from disk with `Accept-Ranges` and **no auth** — S4's token is a
  real gap, not a refactor.
- The Tauri layer has **zero player commands**: `src-tauri/src/lib.rs` (717
  lines) is crash logging only; no `invoke_handler`, no `invoke()` in the app.
- Server trust hole already present: `play`/`pause` accept the client's `at`
  (`app.py:3970`) — S3.5's fix is a backward-compatible one-liner plus a
  characterization test.
- `docs/QA_NATIVE_CLIENT.md` §2 documents a `playback_status` socket event that
  **does not exist** (handler is `watching`, `app.py:3883`) — stale doc, fix in
  S8.

**R8 resolved (user decision, 2026-10-17):** task R3 says branch
`feat/mpv-player` from `main`, but HEAD was `release/w2g-v1` (`2abbe52`,
unpushed, ahead of `main` by the whole `/w2g` delivery). Asked with three
options; the user chose **cut from `release/w2g-v1`**, so branching from
`main` (and dropping the multi-instance work) was rejected. Branch created:
`feat/mpv-player` @ `c17a155` (S0 docs commit).

### User decisions for S1–S4 (2026-10-17, verbatim intent)

1. **Branch base** confirmed; the R3 deviation stays in the ledger; nothing is
   merged or pushed to `main` from this branch.
2. **`.desktop` `MimeType=` + `main.rs` argv/single-instance are IN SCOPE and
   belong to S3**, not S6 — local-mode entry points (CLI argument,
   file-manager "Open with", single-instance forwarding of a second file to
   the running window) are required by the task's section 3.4. Allowed edits,
   verbatim: the `MimeType=` lines in the two `.desktop` files, argv handling
   and single-instance in `main.rs`. PKGBUILD and everything under
   server/deploy stay untouched. FOLLOWUPS entry updated; DoD verification at
   S3 must use `vergoboy-w2g /path/file.mkv` **and** `xdg-open`/`gio open` if a
   display is available (Xvfb acceptable), naming whatever could not be
   verified.
3. **The unauthenticated `/media/*` paths are NOT a followup** — they overlap
   section 3.5, so they move into **S4 scope**, with required tests: no token →
   401, wrong item → 403, expired → 401, valid → 206 + `Content-Range`, and the
   HLS/web path unregressed (a different mechanism is acceptable if documented
   and gated behind the client kind).
4. **Test-time side task:** the 20 slowest tests and their cause go into
   `docs/FOLLOWUPS.md`; fixes only if a one-line marker change (own commit).
5. **S1 scope:** characterization tests for the existing `mpv_controller`
   first (published as pinned/not-covered, own commit), then exactly the listed
   extension (port methods `set_volume`/`set_mute`, `add_audio`,
   `add_subtitle(url|path)`, `set_sub_style`, `set_cache_profile`,
   `set_fullscreen`, `get_state`; richer events `Loaded{duration,tracks}`,
   `Position`, `Paused`, `Buffering`, `CacheState`, `Seeked`, `Ended`,
   `TrackChanged`, `Error`, `ProcessCrashed`; `FakePlayer` implementing all of
   it with virtual time and injectable stalls/latency/failures), the required
   failure-mode tests (crash mid-play → `ProcessCrashed` then clean restart
   with state restored; kill during seek; IPC timeout; socket/pipe reconnect;
   no zombie asserted via the process table; real-mpv `--vo=null --ao=null`
   integration that self-skips loudly; Windows named-pipe path behind `cfg`
   with a cross `cargo check` if the target is available, otherwise declared
   unverified), `cargo tree` proving no Tauri dependency, and a gate report
   before S2. `make test-fast` green after every step; no push/merge.

S1 rows are below; the plan was printed to the user before the first commit
(their explicit condition).

## Session 3 (2026-10-15) — w2g delivery: T0 reconcile, T1 parametrize

Mission (from the master instructions): a second instance at
https://vergoboy.ir/w2g served from /opt/w2g, leaving the existing
https://vergoboy.ir/stream, /opt/stream, its unit, DB and port untouched.
Branch `release/w2g-v1` cut from `main` at 8cb0873. Three hard gates: G1 local
readiness, G2 deploy plan, G3 final report. The old P2–P6 roadmap is PAUSED.

Order agreed with the user: T1 → T2 → G1 → T3 → G2 **STOP** → T4 → G3.

| Track | Step | Status | Commit | Evidence |
| --- | --- | --- | --- | --- |
| T0 | reconcile: HEAD, tree, branches, tags | done | n/a | HEAD 8cb0873; tree clean; `wc -l app.py` = 4093; only tag `pre-mpv-baseline` |
| T0 | branch release/w2g-v1 cut from main | done | n/a | `git branch --show-current` |
| T0 | dev env: venv rebuilt with uv, py3.12 | done | n/a | `venv/bin/python -V` = Python 3.12.15 |
| T0 | dev env: dev lock regains pytest/pytest-cov | done | 4157d1f | additive 155/0; now pins pytest 8.4.2, pytest-cov 6.3.0 |
| T0 | dev env: `make test-fast` guards the interpreter | done | 11a5f11 | `make test-fast` = 768 passed, 2 skipped, 20 deselected (405s) |
| T1 | parametrize base path + instance identity | done | dfc2c7a, 30fc78e | ADR 0003; `/w2g` build has 0 `/stream/` refs (229 `/w2g`); full suite green under both `STREAM_BASE_PATH` values — see T1 block |
| T2 | unblock the Arch package build | done | 12676ed | dropped unused `patchelf` makedepend + dropped stale `$SRCDEST` mirror; `./build-arch.sh` → `vergoboy-stream-0.1.0-1-x86_64.pkg.tar.zst` (3.25 MiB) |
| T2 | native client: per-instance Arch package | done | 7bdc32f, 1bce226, dc520d9 | `./build-arch.sh --instance w2g` → `vergoboy-w2g-0.1.0-1-x86_64.pkg.tar.zst`; both install side by side — see T2 block |
| T3 | server read-only recon (S1/S2) | done | — | docs/DEPLOY_LOG.md; port 8802 free, `/opt/w2g` absent, `/stream` baseline HTTP 200 |
| T3 | deploy plan, read-only (S3) | done | — | docs/DEPLOY_PLAN.md — S0–S11, abort conditions, verification, rollback; **G2 approved 2026-10-15** |
| T4 | provision /opt/w2g, deploy, verify | blocked | — | G2 approved 2026-10-15; blocked on the SSH password — read-only S0 is the first step |
| G1 | local readiness gate (before T3) | done | — | see G1 below |
| G2 | deploy plan approved by the user | todo | — | presented, awaiting answer |
| G3 | final report | todo | — | — |

### T1 — parametrize base path + instance identity

Decision record: **docs/adr/0003-multi-instance-base-path.md**. Three env vars,
all defaulting to the original instance (unset environment ⇒ nothing changes):
`STREAM_BASE_PATH` (`/stream`), `STREAM_INSTANCE` (`stream`),
`STREAM_INSTANCE_ROOT` (the checkout dir). Full mechanism and the alternatives
rejected are in the ADR; the four default choices the user approved were:
route prefix as a constant + f-strings, per-instance `STREAM_JWT_SECRET` with
`iss`/`aud` from `STREAM_INSTANCE`, `INSTANCE_ROOT` defaulting to `BASE_DIR`,
and package name `vergoboy-w2g`.

| Area | Change | Evidence |
| --- | --- | --- |
| `config.py` | `INSTANCE`/`BASE_PATH`/`INSTANCE_ROOT`, `JWT_ISSUER`/`JWT_AUDIENCE`, `SITE_BASE_URL` derived; `media/`,`data/` under `INSTANCE_ROOT`; `LIVEKIT_ROOM` default `<instance>-voice` | `_normalise_base_path` handles `""`, `/x`, `x`, `/x/` |
| `app.py` | `BP = Config.BASE_PATH`; 59 `@app.route(f"{BP}/…")` + the bare `{BP}` via `add_url_rule` (60 rules, was 60); `static_url_path`, CORS resources, `SocketIO(path=…)` all follow `BP` | url_map under `/w2g`: `['/w2g','/w2g/']`, 61 rules, **no** `/stream` rule |
| `db.py` | `_sign` stamps `iss`/`aud`; `decode_token` rejects a mismatch but still accepts a token carrying neither (no forced logout on upgrade) | lenient path exercised by the auth-placement tests |
| `state.py` | HLS URL test uses `f"{Config.BASE_PATH}/media/hls/"` | tests/unit/test_state_missing_media.py |
| `templates/index.html`, `static/js/app.js` | legacy UI receives the prefix at render time (`window.__STREAM_BASE_PATH__`), fallback `/stream` | `node --check static/js/app.js` |
| `webapp/lib/config.ts`, `next.config.ts` | one exported `BASE_PATH`/`API_PREFIX`/`SOCKET_PATH`; `next.config.ts` imports it so build and runtime cannot disagree | `npx tsc --noEmit` exit 0 |
| `webapp/{app,components,lib}` | 4 page files + `api.ts`/`auth.ts`/`ClientErrorReporter.tsx` use the prefix | `npx eslint app components lib` = 0 errors (14 pre-existing warnings) |
| `tests/` | `conftest.py` derives `socketio_path` from `Config.BASE_PATH` (was hardcoded `stream/socket.io`); 5 more files parametrized | was the cause of 30 `/w2g` failures — now 42 passed |
| `get_state.py`, `trigger_add.py` | derive root from the script's own dir (override `STREAM_INSTANCE_ROOT`), port/prefix from `config` — no `/opt/stream` left | `py_compile` clean |
| `build-arch.sh` | web-export guard is now prefix-agnostic (any non-empty `basePath` is wrong for the tauri bundle) | `grep -qE '"/[^/"]+/_next/'` |
| `deploy/` | new `nginx-w2g.conf` + `vergoboy-w2g.service` (port 8802, `STREAM_BASE_PATH=/w2g`, `STREAM_INSTANCE_ROOT=/opt/w2g`); `nginx-stream.conf` notes it is the original instance | both snippets paste into the same `server{}`; global `/hls/`,`/whip/`,`/livekit/` kept once |
| `README.md` | documents `STREAM_BASE_PATH`/`_INSTANCE`/`_INSTANCE_ROOT`, the per-instance LiveKit room, and the three-way build | — |

Acceptance (T1): grep of `/stream` across code/build/config files shows only the
documented default in config files (`config.py`, `deploy/*`), prose in docs and
in the two operator scripts, and unrelated words (`archive_scraper.py` "stream
links", `auth.ts` "media/streams"). The web export built twice from one tree:
`out-stream` = 229 `/stream` refs / 0 `/w2g`; `out-w2g`
(`NEXT_PUBLIC_BASE_PATH=/w2g`) = 0 `/stream` refs / 229 `/w2g`.

Backend suite green under both mount points: `make test-fast` = 768 passed, 2
skipped, 20 deselected (406s) by default, and the same with
`STREAM_BASE_PATH=/w2g STREAM_INSTANCE=w2g`. `npx tsc --noEmit` exit 0; eslint
over `app components lib` = 0 errors (14 pre-existing warnings).

Out-of-scope findings are in **docs/FOLLOWUPS.md** (`run.sh` knows only the
original instance; `get_state.py` pins an admin UUID; `trigger_add.py` is a
one-off).

### T2 — native client + per-instance Arch package

One Rust binary, one flag, two packages that install side by side:

| Artifact | Instance | API origin | API base path | Size |
| --- | --- | --- | --- | --- |
| `vergoboy-stream-0.1.0-1-x86_64.pkg.tar.zst` | stream | `https://vergoboy.ir` | *(empty → root `/api`)* | 3.25 MiB |
| `vergoboy-w2g-0.1.0-1-x86_64.pkg.tar.zst` | w2g | `https://vergoboy.ir` | `/w2g` | 3.25 MiB |

The instance selects only what the embedded static export carries and what the
package installs as: `NEXT_PUBLIC_API_BASE_PATH`, the package name, and the
binary/desktop-entry/icon name (`vergoboy-<instance>`). The Rust source is the
same either way.

| Area | Change | Evidence |
| --- | --- | --- |
| `webapp/lib/config.ts` | new `API_BASE_PATH = NEXT_PUBLIC_API_BASE_PATH ?? BASE_PATH`; `API_PREFIX`/`SOCKET_PATH` derive from it. Needed because the desktop target's `BASE_PATH` is empty by design | `npx tsc --noEmit` exit 0; eslint 0 errors |
| `webapp/app/page.tsx`, `components/RoomBar.tsx` | invite links are absolute URLs a human opens → built from `API_BASE_PATH`; in-app navigation keeps `BASE_PATH` | was `origin + "" + "/?join="` for the desktop target |
| `build-arch.sh` | `--instance NAME` (default `stream`); instance-keyed artifacts, source mirror and cleanups; `webapp/.out-instance` stamp; reuse check grew from three ways to five | `bash -n` clean; `--help` documents the instance |
| `packaging/arch/PKGBUILD` | reads `_instance` from `${startdir}/.instance`; `pkgname=vergoboy-$_instance`, installs under `_pkgbin` | both builds produced the right `Name:` metadata |
| `packaging/arch/vergoboy-w2g.desktop` | second desktop entry (`Name=vergoboy W2G`) | installed by the w2g package |
| `.gitignore` | `packaging/arch/vergoboy-*/`, `packaging/arch/.instance`, `.build.lock`, `webapp/.out-instance` | `git status` clean after both builds |
| `docs/FOLLOWUPS.md` | the root-`/api` question for the *shipped stream* client; the shared live namespace; the shared build tree | carried to the deploy plan |
| `packaging/arch/PKGBUILD` (2) | `_inject_instance`: any instance but `stream` rewrites `identifier` in the cloned `tauri.conf.json` to `ir.vergoboy.<instance>`, reusing the CSP patcher | one line differs on a copy of the real conf, still valid JSON; and confirmed in the compiled binary by disassembly (below) |
| `build-arch.sh` (2) | exclusive `flock` on `packaging/arch/.build.lock` for the whole run; a second run waits instead of corrupting | see the incident below |
| `packaging/arch/PKGBUILD` (3) | installs a repo-root `LICENSE` under `/usr/share/licenses/$pkgname/` — clears namcap's `LicenseRef-*` file requirement, which printed as `E:` on every run since `b8fbb31` | rebuild 2026-10-16: namcap output has no ` E: ` line; `pacman -Qlp` lists `usr/share/licenses/vergoboy-stream/LICENSE` |
| `build-arch.sh` (3) | verification captures namcap output and `die`s on ` E: ` (namcap exits 0 anyway, and the pipeline's status was sed's — errors scrolled by green); merged the unreachable second `die` after "no usable rustc", whose advice never printed | `bash -n` clean; gate logic checked both ways (E → fail, W-only → pass); verify run `EXIT=0` |
| `build-arch.sh` (4) | dev-shell gate: extract `usr/bin/vergoboy-<instance>` from the artifact and require embedded frontend assets (`_next/static`); a binary built by raw `cargo build --release` (no `custom-protocol` feature) embeds none and boots WebKit's "Could not connect to localhost: Connection refused" page | gate checked both ways: hand-built binary 0 hits → would die; both packaged artifacts 28/30 hits → pass |

Three things the build cannot get wrong any more, all verified in the logs:

- **Wrong-instance export** — `webapp/out` is shared and gitignored, so it
  could silently carry the other instance's API path. The export is stamped in
  `webapp/.out-instance` and the script refused to reuse it in *both*
  directions during the two builds: `exported for instance '?', not 'w2g'`,
  then `exported for instance 'w2g', not 'stream'`.
- **Clobbered artifact** — artifacts, `$SRCDEST` mirror and `--clean-first` are
  all keyed by instance; both `.pkg.tar.zst` files coexist (the w2g build did
  not delete or shadow the stream one).
- **Dev-shell binary** — Tauri chooses between its embedded assets and
  `build.devUrl` at *compile* time (`cfg(dev)` in tauri's build script is
  `!custom_protocol`). `npm run tauri build` passes the feature; a raw
  `cargo build --release` does not, and such a binary boots
  `http://localhost:3000/` → "Could not connect to localhost: Connection
  refused". That is exactly what a hand-built package shipped on 2026-10-16
  (the LD_PRELOAD connect log showed `::1:3000`/`127.0.0.1:3000` from
  WebKitNetworkProcess at launch, and a screenshot of the window caught the
  error page; the packaged artifact instead dials `45.135.242.135:443` and
  renders the login UI). The verification step now extracts the binary from
  the `.pkg.tar.zst` and refuses one without embedded `_next/static` assets.

What is baked into each export (grep of the module the package bundles):

```
stream: let o="https://vergoboy.ir", a="", n="", s=`${n}/api`, i=`${n}/socket.io`
w2g:    let t="https://vergoboy.ir", a="", o="/w2g", i=`${o}/api`, l=`${o}/socket.io`
```

i.e. `w2g` calls `/w2g/api/…` and `/w2g/socket.io`, `stream` keeps the domain
root it has always used; 0 `/stream/` refs and 0 `/api/api` in either.

Why the two packages also needed **distinct app identifiers**: `webapp/lib/auth.ts`
keeps the session in `localStorage`, and Tauri derives the WebView data
directory from the identifier. Both packages shipped `ir.vergoboy.stream`, so
with both installed they would have shared one store — signing in to `/stream`
would hand the `/w2g` window a token minted with the other instance's
`STREAM_JWT_SECRET`, which is correctly refused, so each app would quietly log
the other out. `stream` is deliberately left byte-for-byte unchanged, or the
already-installed client would lose its storage.

**Incident — two builds at once (fixed here).** The first two attempts at the
w2g package after the identifier change both failed, and the change was not the
cause: a `./build-arch.sh` with no `--instance` (so `stream`) was running at the
same time. `makepkg.conf` leaves `BUILDDIR` unset, so every run's `$srcdir` is
the same `packaging/arch/src`, and `makepkg -C` deletes it at the start of each
build. The w2g run lost `tsconfig.json` first (`npx tsc --noEmit` printed its
help text and "failed"), then lost the Rust output directory mid-compile:

```
error: could not write output to .../target/release/deps/...rcgu.o: No such file or directory
```

Confirmed from the process table, not guessed: a `bash ./build-arch.sh` under a
`fish` shell in `kitty`, started 18:18:44 while the w2g run was compiling, and a
fresh `vergoboy-stream-0.1.0-1-x86_64.pkg.tar.zst` at 18:21:37. `build-arch.sh`
now takes an exclusive lock before touching any shared state. The stream
artifact was then checked for cross-contamination: correct `pkgname`, and 0
`/w2g`, 0 `/stream/`, 0 `/api/api` in its binary.

**Rebuilt alone, and verified** (`./build-arch.sh --instance w2g`, exit 0):

- `packaging/arch/vergoboy-w2g-0.1.0-1-x86_64.pkg.tar.zst`, 3.24 MiB,
  `Name: vergoboy-w2g`, installing `usr/bin/vergoboy-w2g` plus its desktop entry
  and three icon sizes.
- The embedded export is the w2g one: `webapp/.out-instance` = `w2g`, and the
  minified config in `webapp/out/_next` reads
  ``n="/w2g",s=`${n}/api`,i=`${n}/socket.io` `` — 0 `/stream` refs, 0 `/api/api`.
- The identifier really is `ir.vergoboy.w2g`. Worth writing down, because it is
  *not* greppable the obvious way: a 15-byte constant is short enough for LLVM
  to materialise as immediates instead of a `.rodata` literal, so
  `strings … | grep ir.vergoboy` finds **nothing** even though the string is
  there. What is actually emitted:

  ```
  $ objdump -d --start-address=0x551bf0 --stop-address=0x551c30 usr/bin/vergoboy-w2g
  551bf0: 48 b9 6f 62 6f 79 2e 77 32 67   movabs $0x6732772e796f626f,%rcx  ; "oboy.w2g"
  551bfa: 48 89 48 07                       mov    %rcx,0x7(%rax)           ; store at +7
  551bfe: 48 b9 69 72 2e 76 65 72 67 6f   movabs $0x6f677265762e7269,%rcx  ; "ir.vergo"
  551c0c: 48 89 08                          mov    %rcx,(%rax)              ; store at +0
  ```

  Two overlapping stores — offsets `+0` and `+7`, sharing the `o` — spell
  exactly `ir.vergoboy.w2g`. The 18-byte `ir.vergoboy.stream` is *over* the
  inline threshold, so it stays a `.rodata` string and greps first try; that
  asymmetry is why the two binaries do not look alike. Cross-checked both ways:
  the w2g binary holds `oboy.w2g` once and `vergoboy.stream` zero times, the
  stream binary the exact reverse. Neither has `/api/api`; the w2g one has no
  `/stream/`.

### G1 — local readiness gate (passed)

Measured on the tree that will be deployed, not on an earlier state:

| Check | Command | Result |
| --- | --- | --- |
| Backend suite, original mount point | `make test-fast` | **780 passed, 2 skipped, 20 deselected, 3 warnings (405 s)**, exit 0 |
| Backend suite, second mount point | `make test-fast` with `STREAM_BASE_PATH=/w2g STREAM_INSTANCE=w2g` | 768 passed, 2 skipped, 20 deselected (406 s) — T1 evidence; the 12 tests added since read source files, not routes |
| Type check | `npx tsc --noEmit` | exit 0 |
| Lint | `npx eslint app components lib` | 0 errors, 14 pre-existing warnings |
| Legacy UI syntax | `node --check static/js/app.js` | OK |
| Web export, `/stream` | `NEXT_PUBLIC_BASE_PATH=/stream` | 229 `/stream` refs, 0 `/w2g` |
| Web export, `/w2g` | `NEXT_PUBLIC_BASE_PATH=/w2g` | **0** `/stream` refs, 229 `/w2g`, 0 `/api/api` |
| Desktop package, `stream` | `./build-arch.sh --instance stream` | `vergoboy-stream-0.1.0-1-x86_64.pkg.tar.zst`, 3.25 MiB |
| Desktop package, `w2g` | `./build-arch.sh --instance w2g` | `vergoboy-w2g-0.1.0-1-x86_64.pkg.tar.zst`, 3.25 MiB |
| `/stream` in shipped code | grep across code/build/config | only the documented default (`config.py`), the original instance's own `deploy/*` templates, comments/prose, `run.sh` (FOLLOWUPS) and unrelated words |

The 780 is 768 + the 12 prefix/invite guards in
`tests/unit/test_client_api_prefix.py`; that guard is the only new backend
test, and it fails on `30fc78e` by naming all 38 offending call sites.

### T3 — read-only deploy plan

`docs/DEPLOY_PLAN.md`. Nothing in it has been executed. It carries the recon
facts, the three open questions from `docs/DEPLOY_LOG.md` resolved (own
`w2g` DB/role; room `w2g-voice`; shared live namespace), and steps S0–S11 with
the exact command for each: read-only preflight with abort conditions and a
20 GB disk floor, a release under `/opt/w2g/releases/<UTC-ts>` with an atomically
repointed `current`, a `0600` secrets file written server-side, `nginx -t`
before any reload, a one-line nginx `include` whose insertion and idempotency
were both exercised on a stand-in vhost, a verification pass that re-checks
`/stream` is untouched, and a rollback.

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
Contract for the T4 deploy (stated here, not yet implemented — there is no
`deploy.sh` in the tree): it will never read or delete `/opt/w2g/media` or
`/opt/stream/media`. It writes only under `/opt/w2g/releases/<ts>/`, repoints the
`/opt/w2g/current` symlink, and puts the static export at
`/opt/w2g/current/webapp-out/`. That mirrors the stream layout with one
deliberate difference: `/opt/stream/webapp-out` sits *outside* its releases and
is refreshed by `vergoboy-frontend-build.service`, whereas w2g's export lives
*inside* the release, so flipping `current` swaps the backend and the UI
together and a rollback moves both back. `media` and `data` live outside the
release tree and are referenced by no `rm`, `rsync --delete`, or cleanup call.

### Pre-T4 — desktop client reports "cannot connect" (2026-10-15)

Reported from the installed app, not from a test. Diagnosed by probing the live
site from the workstation; no server access was used.

* The app in question is **`vergoboy-stream`** (the only instance installed; its
  data lives in `~/.local/share/ir.vergoboy.stream`). `vergoboy-w2g` is not
  installed at all.
* The sentence is `friendlyError()` in `webapp/lib/auth.ts`, which prints it only
  when `fetch()` itself rejects — a webview-level networking refusal, never an
  HTTP status.
* **Cause 1 (ours, packaging):** `build-arch.sh:97` bakes an *empty* API base
  path for the `stream` instance, so `webapp/lib/config.ts` yields
  `API_PREFIX="/api"` and the shipped client calls `https://vergoboy.ir/api/…`.
* **Cause 2 (live state, pre-existing):** the domain root `/api/` is **not**
  this app. `GET /api/auth/me` → 404 carrying `Access-Control-Allow-Origin: *`
  (a different co-located service), and the CORS preflight
  `OPTIONS /api/auth/me` → **404**: a non-2xx preflight rejects the fetch, which
  is precisely the reported sentence. Our backend answers only under
  `/stream/api/*` — `GET /stream/api/auth/me` → 401 JSON, and
  `/stream/api/auth/oauth/google` → 302 to Google with
  `redirect_uri=https://vergoboy.ir/stream/api/auth/oauth/google/callback`.
  (That resolves the root-`/api/` question in `docs/FOLLOWUPS.md` and
  independently confirms the redirect-URI shape D7 predicts for
  `/w2g`. The nginx `location` inventory at S0 still has to confirm there is no
  root proxy that *should* exist.)
* **Cause 2b (server, pre-existing):** the running `/stream` build emits no CORS
  at all — `OPTIONS /stream/api/auth/me` → 404 with no
  `Access-Control-Allow-Origin`, not even for `https://vergoboy.ir`. The tree's
  current code does both that (`app.py:131`: flask-cors on `{BP}/api/*`, origins
  `SITE_ORIGIN` + `tauri://localhost` in production) and has
  `{BP}/api/health` (app.py:1584), so the live deployment predates both. The
  browser client never needed CORS because it is same-origin.

Consequence for the plan: the `w2g` client has neither defect (`/w2g` is baked in
explicitly) and the code T4 deploys *is* the current code, so **T4 is the route to
a working desktop client**. The `stream` client would need its baked base path
repointed to `/stream` **and** its live instance refreshed — neither is part of
T4.

User decisions (2026-10-15): deploy `/w2g` now; repoint the `stream` package is
*not* part of this round; namcap license fix done (`LicenseRef-proprietary`,
commit `b8fbb31`, both packages rebuilt).

*Third cause, 2026-10-16 — distinct from both above:* a **dev-shell binary**.
The error sentence this time came from WebKit's own error *page* (the window
showed nothing else), not from `friendlyError()`: a package built with raw
`cargo build --release` lacks tauri's `custom-protocol` feature, so the crate
compiles with `cfg(dev)` and the window loads `build.devUrl`
(`http://localhost:3000/`) instead of the embedded UI — "Could not connect to
localhost: Connection refused" on every launch, before any click. Diagnosed
with an `LD_PRELOAD` `connect()` shim (WebKitNetworkProcess dialing `::1:3000`
and `127.0.0.1:3000` at launch; the correctly built artifact dials
`45.135.242.135:443` and renders the login screen). `build-arch.sh`'s
verification now refuses such a binary — see the (4) row in the T2 table.

### Packaging — both artifacts rebuilt (2026-10-15)

namcap's E-class (`custom is not a valid SPDX license identifier`) was fixed in
`packaging/arch/PKGBUILD` (`LicenseRef-proprietary`, commit `b8fbb31`), and both
packages were rebuilt from the committed `release/w2g-v1` tree:

| artifact | size | pkgname | license | namcap | baked API path |
| --- | --- | --- | --- | --- | --- |
| `vergoboy-stream-0.1.0-1-x86_64.pkg.tar.zst` | 3.4 MiB | vergoboy-stream | LicenseRef-proprietary | W-class only | `/api` (domain root) |
| `vergoboy-w2g-0.1.0-1-x86_64.pkg.tar.zst` | 3.4 MiB | vergoboy-w2g | LicenseRef-proprietary | W-class only | `/w2g/api` |

Both artifacts pass `bsdtar` integrity. namcap's remaining line is W-class
("LicenseRef-* requires a license file under /usr/share/licenses/") — the repo
ships no `LICENSE`, so that needs a `LICENSE` file added to fully clear it; it is
advisory, not a build failure, and does not block T4.

*Resolved 2026-10-16:* the license line was in fact E-class — namcap prints
`E: Uncommon license identifiers ... require license files below
/usr/share/licenses/…` — and it appeared on every run while `build-arch.sh`
stayed green, because namcap exits 0 and the pipeline status was sed's. The
repo now ships `LICENSE`, `package()` installs it, and the script fails on
` E: ` output (commits `91577dc`, `ec6bd49`); verified rebuild prints only
W-class lines and exits 0. The "advisory, not a build failure" reading above
was wrong on both counts and is superseded by this note.

A side-effect worth noting: `build-arch.sh`'s lock (`packaging/arch/.build.lock`)
and the 5-way export verification worked as designed — the `w2g` run refused to
reuse the `stream` export and rebuilt it. There is still unexplained build
churn (a `stream` build finished after the `w2g` run, leaving `webapp/out` as
the `stream` export); nothing server-side depends on it, since T4 ships a
`git archive` release, not a `.pkg.tar.zst`.

*User decisions (2026-10-15):* deploy `/w2g` now; repoint the `stream` package's
baked base path is *not* part of this round.

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

### T4 — deploy `/w2g` to vergoboy.ir (2026-10-16) — DONE

Release `20261015T204954Z` from commit `9b80c22`, live at
**https://vergoboy.ir/w2g** behind `vergoboy-w2g.service` on `127.0.0.1:8802`,
own `w2g` DB/role, own `STREAM_SECRET_KEY`/`STREAM_JWT_SECRET`, `0600`
secrets file `/opt/w2g/.stream_db.env`. `/stream` untouched (200/301/404
baseline unchanged, both units active).

| Step | Result | Deviation worth remembering |
| --- | --- | --- |
| S1–S2 | dirs + `git archive` release, www-data | — |
| S3 venv | deps at exact lockfile versions | **host cannot reach `files.pythonhosted.org`** → wheels cross-downloaded locally and installed `--no-index` from `/opt/w2g/wheelhouse/` (32 wheels, 18 M) |
| S4 export | 229 `/w2g`, 0 `/stream`, 0 `/api/api` | copied `node_modules` insufficient (`animejs`, `@tauri-apps/api`); real `npm ci` (396 pkgs) |
| S5–S6 | `w2g` role+DB, secrets file, all 4 OAuth key names carried | `STREAM_ADMIN_EMAIL` not in stream's env (old code hardcodes it) → appended explicitly for the new `validate()` |
| S7 | atomic `current` symlink | — |
| S8 | active, `env=production` | unit needed `STREAM_LOG_DIR=/opt/w2g/data/logs` (else `/var/www/.local` EACCES); `data/rooms/` had to be created (config.py never makes it) |
| S9 | +1 line, `nginx -t` OK, reloaded | plan's `rfind("}")` targets the wrong block (4 server blocks) — inserted before `location /stream/_next/ {` instead; first brace-scan attempt landed in a `/music` location, `nginx -t` caught it, restored from `.bak.20261016T104610Z`; snippet gained `location = /livekit/` (probe URL had no route → 503) |
| S10 | **all green** | health `{"status":"healthy"}` all 4 checks true; OAuth redirect_uri at `/w2g` (D7); socket handshake; hashed asset 200; `/stream` unchanged |

Follow-ups now on the user: register the two OAuth redirect URIs
(`…/w2g/api/auth/oauth/{google,github}/callback`) — the server already sends
them, the provider consoles must allow them. GitHub may need a second OAuth
app (callback-subdirectory matching, see G2 note).

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
