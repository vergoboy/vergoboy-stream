# Follow-ups

Out-of-scope findings kept out of the T1 change (AGENTS.md rule 4). Each entry
says what, why it was not done now, and what would be done later.

## From T1 (parametrize base path) — 2026-10-15

### `run.sh` only knows the original instance

The local dev runner hardcodes the original instance: backend port 8801, DB
name `stream` (line 283), and it prints the frontend URL as
`http://localhost:3000/stream/` (line 309). It never sets `STREAM_BASE_PATH`,
`STREAM_INSTANCE` or `NEXT_PUBLIC_BASE_PATH`, so `./run.sh` cannot bring up a
`/w2g` dev instance without hand-editing.

*Why not now:* the T1 acceptance gate and the deploy path both use `make
test-fast` / the production unit, which are already parametrized; `run.sh` is a
developer convenience and touching it risks the daily dev loop the original
instance depends on.

*Later:* derive the four values from the same env vars (`STREAM_BASE_PATH`,
`STREAM_PORT`, `STREAM_INSTANCE`) with the current values as defaults, and emit
`NEXT_PUBLIC_BASE_PATH` into `webapp/.env.local`.

### `get_state.py` pins one admin UUID

`get_state.py` mints a token for a hardcoded admin id
(`cceb5d42-a143-4d08-a634-4fa510507fab`). It is a throwaway diagnostic; the
guid is that instance's admin, so the script fails against any other database.

*Later:* look the id up (`SELECT id FROM users WHERE role='admin' LIMIT 1`),
the way `trigger_add.py` already does.

### `trigger_add.py` is a one-off with a baked-in subject

`trigger_add.py` still carries a specific movie URL and touches the database
directly to find an admin. It also references `urllib.error` without importing
it (it happens to work because `urllib.request` imports that submodule).
Left alone beyond the path/port parametrization.

*Later:* either delete it (it looks single-use) or fold it into a documented
admin CLI.

### `archive_scraper.py` "stream" is unrelated

`archive_scraper.py:327` mentions "download/stream links" — the word, not the
mount point. Only listed here so a future `grep /stream` sweep does not chase
it.

## From T2 (per-instance desktop package) — 2026-10-15

### The desktop client still targets the API at the domain root

`vergoboy-stream`'s bundled export calls `https://vergoboy.ir/api/…` and
`…/socket.io`, not `/stream/api/…`: for the `tauri` target `BASE_PATH` is empty
(the app's own assets load from `tauri://localhost`), and the package predates
`NEXT_PUBLIC_API_BASE_PATH`, so its API base path is empty too. That is only
correct if nginx serves a **root-level** `/api/` — the recon in
`docs/DEPLOY_LOG.md` was filtered to lines containing "stream" and would not
have shown one.

*Why not now:* it is a question about live server state, not about this tree,
and changing it would alter behaviour for the already-installed client. The
`w2g` package does not depend on the answer — it names `/w2g` explicitly.

*Later:* confirm with a read-only `grep -n 'location' .../vergoboy.ir.conf`,
then either keep the root path (there is a proxy) or build the stream package
with `NEXT_PUBLIC_API_BASE_PATH=/stream` and re-release it.

### Live streaming shares one namespace between instances

The RTMP ingest is configured server-side to write a single directory
(`config.py` documents it as `/opt/stream/hls-live`, served by the
instance-global `/hls/` location). `w2g` therefore plays live streams from the
same `/hls/` URL as the original rather than its own `/w2g/hls/`, which would
alias a directory nothing writes.

Consequence: both instances publish into `live/{key}` on the same ingest. Keys
are 10 hex characters (`uuid.uuid4().hex[:10]`) generated independently per
instance, so a collision is negligible — but the namespaces are not isolated:
anyone who can reach `/hls/live/{key}/` can play a stream from either instance,
and a future ingest-level access control would apply to both.

*Later:* give `w2g` its own ingest path prefix (a second RTMP application or a
MediaMTX path namespace writing `/opt/w2g/hls-live`) and restore a `/w2g/hls/`
location plus `STREAM_HLS_PLAYBACK_TEMPLATE`.


### Two `build-arch.sh` runs cannot overlap

`makepkg.conf` leaves `BUILDDIR` unset, so makepkg's `$srcdir` is
`packaging/arch/src` — one directory for every instance — and the script calls
`makepkg -C`, which deletes that whole directory at the start of each run. Two
concurrent runs (different instances, or even the same one) therefore destroy
each other's checkout, and both also race over `webapp/out`, which is shared and
is what gets embedded into the package.

Seen on 2026-10-15: a `./build-arch.sh` for `stream` overlapped a
`--instance w2g` run. The `w2g` run failed in `build()` twice — once with
`npx tsc --noEmit` printing its help text (its `tsconfig.json` had gone with
the shared `$srcdir`), and once mid-Rust with
`error: could not write output to .../target/release/deps/....rcgu.o: No such
file or directory` — and its own `-C` in turn deleted the other run's working
copy. Neither package was corrupted that time (the `stream` artifact was checked afterwards: correct
name, 0 `/w2g`, 0 `/stream/`, 0 `/api/api`), but nothing guarantees that.

The guard added in T2 (`webapp/.out-instance`, five-way reuse verification)
catches a *stale* export from the previous run; it cannot catch a *concurrent*
one.

**Partially fixed:** `build-arch.sh` now takes an exclusive lock
(`flock` on `packaging/arch/.build.lock`) before it touches anything, and a
second run waits for the first instead of corrupting it. That covers runs that
go through the script — the supported path. It does not cover `makepkg` being
invoked by hand in `packaging/arch`.

*Later:* give each instance its own `BUILDDIR`
(`BUILDDIR="$PKGDIR/build/$INSTANCE"` before `makepkg`, which also makes the
`rm -rf "$PKGDIR/src"` lines unnecessary) so that builds of *different*
instances can genuinely run in parallel, and share `webapp/out` by path rather
than by convention.
