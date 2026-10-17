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

**Update 2026-10-15 — answered by live probe, and this is the "cannot
connect" bug.** The root path does **not** reach this app. The installed
`vergoboy-stream` client calls the domain root, where `GET /api/auth/me`
returns 404 with `Access-Control-Allow-Origin: *` (a different co-located
service) and the CORS preflight `OPTIONS /api/auth/me` returns **404** — a
non-2xx preflight makes `fetch()` reject, which is precisely the sentence the
app shows. Our backend answers only under `/stream/api/*`:
`GET /stream/api/auth/me` → 401 JSON, and
`/stream/api/auth/oauth/google` → 302 to Google with
`redirect_uri=https://vergoboy.ir/stream/api/auth/oauth/google/callback`.

Second, independent defect on the same path: the running `/stream` build sends
no CORS headers at all (`OPTIONS /stream/api/auth/me` → 404, no
`Access-Control-Allow-Origin`, not even for `https://vergoboy.ir`), while the
tree's current code registers flask-cors on `{BP}/api/*` with
`tauri://localhost` in production. So repointing the package is necessary but
not sufficient: the live instance must also run code that answers the preflight.
Details in `docs/PROGRESS.md` § "Pre-T4".

*Later:* (a) build the stream package with `NEXT_PUBLIC_API_BASE_PATH=/stream`
(one line in `build-arch.sh:97`) and re-release it; (b) refresh the live
`/stream` deployment to code that answers CORS, which needs the user's approval
since it touches the existing instance; (c) still read the vhost's `location`
inventory at S0 in case a root-level proxy is meant to exist.

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

## From T4 (deploy `/w2g`) — 2026-10-16

- **Wheelhouse upkeep.** The host resolves `pypi.org` but **cannot reach
  `files.pythonhosted.org`** (read timeouts even at `--timeout 120`), so a
  release can only be installed offline: cross-download on a machine with
  PyPI access (`pip download --python-version 3.12 --platform manylinux_2_34_x86_64
  … --only-binary=:all:`), scp, then `pip install --no-index
  --find-links=…`. The wheels used for release `20261015T204954Z` are kept at
  **`/opt/w2g/wheelhouse/`** (32 wheels, 18 M) — refresh it whenever
  `requirements.lock.txt` changes, or the next release will stall the same way.
  Alternative worth testing someday: a PyPI mirror the host *can* reach.
- **`location = /livekit/` is instance-global but lives in the w2g snippet.**
  `_probe_livekit()` GETs `{STREAM_LIVEKIT_URL}/` (→ `https://vergoboy.ir/livekit/`),
  which had no route at all (only `/livekit/rtc/…`), so health answered 503.
  The fix shipped inside `/etc/nginx/snippets/vergoboy-w2g.conf` because D6
  allowed exactly one include line. If the vhost or `/stream`'s snippet is ever
  regenerated, hoist this exact-match location into a shared snippet — and note
  a *future* `/stream` build with the health endpoint needs it too (its current
  old build has no `/stream/api/health`).
- **`config.py` never creates `DATA_DIR/rooms/`.** It makes `DATA_DIR` itself
  (line ~345) and `state.py`'s *save* path makedirs on demand, but every
  *reader* (`state.py` load, `app.py:1428/4047` listdir, the cleanup greenlet)
  assumes it exists — a fresh instance crashes a greenlet with
  `FileNotFoundError …/data/rooms` until someone mkdirs it (done manually at
  deploy: `/opt/w2g/data/rooms`). Likely also bites a fresh desktop install.
  One-line fix: `os.makedirs(os.path.join(Config.DATA_DIR, "rooms"), exist_ok=True)`
  next to the other makedirs in config.py.
- **`STREAM_ADMIN_EMAIL` is required in production but carried nowhere.** The
  old `/opt/stream/config.py` hardcodes `very.good.booyy@gmail.com` as its
  default, the new `config.py` has no default and `validate()` refuses to boot
  without it, and `/opt/stream/.stream_db.env` doesn't contain the key — so S6's
  "carry the whitelist" step silently missed it. Deployed by appending it to
  `/opt/w2g/.stream_db.env`. Add it to whatever sample/template env file is
  documented, otherwise the next instance repeats the crash loop.
- **`docs/DEPLOY_PLAN.md` S9's insertion snippet is superseded.** It assumes a
  single `server{}` (`rfind("}")`); the vhost has four, and even a
  brace-matching rewrite desynced once. The executed, verified procedure is the
  one in `docs/DEPLOY_LOG.md` Session 3 (anchor on `location /stream/_next/ {`,
  `nginx -t` gates the reload, `.bak.<ts>` restore).

## From Session 4 (mail + OAuth) — 2026-10-16

- **postfix has no supervision: the next global OOM kills mail silently again.**
  Evidence: `postfix@-.service` started at the Sep 26 boot (the
  `postfix-instance-generator` re-queues it into `postfix.service.wants` every
  boot — so *boots* are covered), then the Sep 28 global OOM (a docker `java`)
  killed proxymap/master; the unit has no `Restart=` and sat `failed` for
  **2.5 weeks** — every password-reset and roundcube submission silently failed
  until found here. A plain `Restart=` on a postmulti forking unit is not a
  reliable fix (systemd tracks no main pid: `GuessMainPID=no`). Cheap options,
  in order of preference: (a) an `OOMScoreAdjust=-1000` drop-in for
  `postfix@.service` so the OOM killer spares it, (b) a 5-minute timer that
  `systemctl start postfix@-` when `ss -ltn` shows no :25, (c) fixing the real
  memory hog (docker without limits — the Sep 28 kill was `java`, 10 GB RSS).
- **`/stream` and roundcube were fixed as a side effect** of bringing postfix
  back — both depend on the same local :25/:587. Worth knowing when someone
  reports "webmail can't send" again: check `systemctl status postfix@-`
  first.
- **New OAuth apps for `/w2g`**: the server now sends
  `…/w2g/api/auth/oauth/{google,github}/callback` redirect URIs with the fresh
  client ids, but each provider console must still *allow-list* them
  (Google: Cloud console → credentials; GitHub: OAuth app callback URL —
  remember the pre-Aug-2026 subdirectory-matching caveat from G2, a `/stream`
  callback will not accept `/w2g`).

## From Session 5 (spam placement) — 2026-10-16

- **If vergoboy mail still lands in Gmail Spam after the Message-ID fix**, the
  server side is provably clean (SPF pass incl. IP, DKIM signed+matching,
  DMARC pass, rDNS `mail.vergoboy.ir`, RBLs clean) — remaining levers, in
  order: (1) open Gmail Postmaster Tools for `vergoboy.ir` and read the
  **IP reputation + spam rate** panels; (2) user marks the test mails "Not
  spam" (Gmail per-sender engagement); (3) consider a transactional smarthost
  (Brevo/SendGrid/Mailgun) as `relayhost` — best inbox rates, small cost;
  (4) review the DMARC aggregate reports that land in the admin mailbox
  (`rua=mailto:` — sender XMLs from googlemail-dmarc-reports) for per-receiver
  auth results.
- **`/stream`'s deployed (old) app still sends Message-ID-less mail** — same
  spam risk on its password resets; heals on its next deploy, nothing to do
  now while it stays on the old build.
- **opendkim private key perms warning** (`opendkim-testkey` from root says
  "unsafe permissions"): cosmetic — file is `opendkim:opendkim 0640`, signing
  works; only noticed because the check ran as root.

## From Session 6 (VPN proxy) — 2026-10-16

- **Scope boundary the operator chose:** only archive HTTP + yt-dlp go through
  the new proxy. `ffmpeg`/`ffprobe` still run with `direct_media_environment()`
  (proxy env stripped on purpose), so **server-side probes/fetches of blocked
  hosts still fail**: adding a `digimoviez.com` media URL for playback, or any
  googlevideo fetch by ffmpeg, will time out direct. Widening this = a small
  URL-aware proxy option in the media env builder + tests — do it when
  archive/YouTube *playback* is actually needed, not before.
- **`/stream` left alone** (frozen until its own deploy): its config.py
  already reads one of the two vars, but no proxy vars were added to its env
  and its service was not restarted. Wire + restart in its next deploy.
- **Cross-dependencies (accepted, loud):** `build_config.py` imports
  `/opt/noisy/egress/build_configs.py` and reads
  `/opt/noisy/secrets/subscription.txt` — if noisy moves or rekeys, the import
  fails loudly (never silently wrong). After a subscription change: re-run the
  builder, gate with `sing-box check`, `compose up -d`.
- **No route switcher exposed:** the `urltest` group failovers at runtime
  (hysteria2 ↔ grpc); the watchdog asserts exit-IP ≠ host IP, not a pinned
  value. If a pinned exit is ever needed (allowlists), record it in
  `proxy-status` first — don't guess.
- **`digimoviez.ir` is dead** (SERVFAIL delegation from clean resolvers,
  poisoned to `10.10.34.35` locally); the app targets `.com` — nothing to do,
  but don't "fix" probes back to `.ir`.
- The two loopback listeners have **no auth**: any local user/account can use
  the proxy. Acceptable on this single-operator box; revisit if multi-user.

## From Session 7 (archive endpoint drift) — 2026-10-16

- Digimoviez is a moving target: its WP/theme updates rename AJAX actions
  and card markup (this was the second markup generation the parser knows).
  If search ever returns 0 cards or «درخواست را رد کرد» again, first replay
  `action=ajaxsearchheader` through the proxy and re-capture the fixture —
  compare `CARD_CLASSES`/heading/rating against the live body before
  touching anything else.
- The site's other actions in `main.js` (`loginAjax`, `favoriteAjax`,
  `commentsAjax`, …) are not used by the collector; nothing to do unless a
  feature starts using them.
- The old comment "the `?s=` page does not filter by the query" was verified
  *against the old site*; the current `?s=` page looks query-specific but
  the collector still goes through ajax (single shot, no pagination) —
  revisit only if `ajaxsearchheader` dies too.

## From Session 9 (Cloudflare wall on digimoviez) — 2026-10-16

### Programmatic bypass for the Turnstile wall — only if it persists

Since 2026-10-16 ~15:30 UTC digimoviez.com serves a Turnstile challenge to
every non-headed client on every available network (proxy exit and
residential alike). FlareSolverr 3.5.2 cannot solve it (120 s timeout; the
widget never clears in headless Chromium), `curl_cffi` fingerprints do not
help, and the sing-box proxy's second outbound exits from the same IP.

*Why not now:* the wall may be the site's temporary anti-attack mode, and
every solver path is an arms-race dependency (nodriver/camoufox-class
automation that actually clicks the widget, ~1 GB image, per-request
brittleness). The classification fix means the honest 503 shows meanwhile
and autologin resumes by itself the moment the edge lifts the challenge.

*Later, if the wall stays up for days:* trial a Turnstile-clicking solver
(camoufox or a nodriver-based FlareSolverr fork) as a container on
`vergoboy-proxy_default` with `PROXY=http://vergoboy-proxy:10808`, feeding
the minted cookies into the archive session. The upstream flaresolverr
image (1.07 GB) is already on the server for such a trial; drop it with
`docker rmi ghcr.io/flaresolverr/flaresolverr:latest` if not wanted.

**Resolved 2026-10-17 (Session 10).** The upstream FlareSolverr *did* solve
it once it was run on the proxy's network with the proxy passed per request;
the wall was cleared and archive search works again. See DEPLOY_LOG Session 10.

## From Session 10 (Cloudflare wall lifted) — 2026-10-17

- **A restart costs one ~2-minute solve.** The minted `cf_clearance` and the
  User-Agent it is bound to are adopted into the running session but not
  persisted with it, so a process start re-solves. Cheap fix when it starts to
  annoy: store `{"cookies": {...}, "user_agent": "..."}` in the session file
  (it is currently a flat `{cookie: value}` map) and reload the UA with the
  cookies; tests in `tests/unit/test_archive_clearance.py` pin the current
  shape and would move with it.
- **The year-long clearance is a single point of invalidation.** Cloudflare can
  revoke `cf_clearance` before its stated expiry (zone secret rotation, policy
  change). The signal is exactly the Session-10 pattern —
  `AUTH_CHECK_SITE_CHALLENGE` back in the journal — and the recovery is
  automatic (the next wall triggers one solver round-trip). If it ever stops
  recovering, check `docker logs vergoboy-archive-solver` first:
  `Challenge detected` with no `solved!` means the edge changed again.
- **`digi-solver:probe` (5.14 GB) is dead weight.** It was the browser
  microsolver that never cleared the wall; FlareSolverr replaced it. The disk
  sat at **93 % (6.4 G free)** on 2026-10-17. Remove with
  `docker rmi digi-solver:probe` — reversible only by rebuilding, and the
  Dockerfile lived in `/tmp` and is gone, so decide deliberately.
- **Do not set a global proxy env on FlareSolverr.** `HTTP(S)_PROXY` makes its
  own startup User-Agent check hang for 120 s (observed twice) and the
  container reports "Error getting browser User-Agent" instead of serving.
  The target proxy belongs in the per-request `proxy` field only.
