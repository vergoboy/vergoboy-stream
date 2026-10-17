# DEPLOY_LOG

Every command run against the production host, in order, with what it printed.
This is the audit trail for the `/w2g` rollout; it is written as commands are
run, not reconstructed afterwards.

Rules for this log (from the master instructions):

- **Secrets are redacted as `***`.** The SSH password, JWT secrets, DB
  passwords and any OAuth material never appear here.
- **Read-only first.** T3 records inspection only. Nothing under `/opt/stream`,
  its database, port, unit or nginx snippet may be read-modify-written here.
- Server commands are logged with the exact text that was executed (the
  `sshpass -e` wrapper carries the password in `$SSHPASS`, shown as `***`).

Host: `ubuntu@45.135.242.135` (SSH). The password is `***`.

---

## Session 1 (2026-10-15) — T3 read-only recon

Purpose: establish the baseline and the facts the deploy plan (G2) depends on.
No write, no restart, no `systemctl`, no `nginx -s`. Read-only commands only.

```sh
SSHPASS='***' sshpass -e ssh ubuntu@45.135.242.135 'bash -s' <<'REMOTE'
# identity / capability
hostname; whoami; id -nG; uname -r
sudo -n true && echo "passwordless sudo: YES"
head -3 /etc/os-release
df -h /
free -h
# listeners
ss -ltn
systemctl list-units --type=service --no-pager --no-legend | grep -iE 'stream|nginx|postgres|livekit|mediamtx|rtmp'
systemctl status vergoboy-stream --no-pager
# toolchain
nginx -v; python3 --version; ffmpeg -version | head -1; node --version; psql --version
# layout
ls -la /opt/
ls -la /etc/nginx/
# nginx routing + reversibility hashes
sudo -n grep -rn "stream" /etc/nginx/ | head
sudo -n cat /etc/nginx/sites-enabled/vergoboy.ir.conf
sudo -n sha256sum /etc/nginx/nginx.conf /etc/nginx/sites-enabled/*
curl -sI https://vergoboy.ir/stream/ | head -12
# database inventory (read-only)
sudo -n -u postgres psql -tAc "select datname from pg_database order by 1"
sudo -n -u postgres psql -tAc "select rolname from pg_roles order by 1"
# free port probe
for p in 8801 8802 8803; do ss -ltn | grep -q ":$p " && echo "$p BUSY" || echo "$p FREE"; done
ls -la /opt/w2g
REMOTE
```

### Findings

| Fact | Value |
| --- | --- |
| Host / OS | `bolbolestan`, Ubuntu 24.04.4 LTS, kernel 6.8.0-142-generic |
| SSH user | `ubuntu`; groups `ubuntu adm cdrom sudo dip lxd`; **passwordless sudo: yes** |
| Disk `/` | 87 G total, 61 G used, **26 G free (71 %)** — above the 20 GB floor |
| RAM | 15 Gi, 9.2 Gi used |
| Toolchain | nginx 1.24.0; Python 3.12.3; ffmpeg 6.1.1; Node v22.13.0; PostgreSQL 16 |
| Existing `/stream` app | `vergoboy-stream.service`, `127.0.0.1:8801` (python3) |
| Target port | **8802 free** (8803 also free) |
| `/opt/stream` | present, owned by `www-data` |
| `/opt/w2g` | **absent** (`No such file or directory`) |
| `/stream` baseline | `HTTP/2 200`, `content-type: text/html`, `content-length: 8436` |

### How `/stream` is wired (the thing we must not disturb)

`/etc/nginx/sites-enabled/vergoboy.ir.conf` holds the 443 vhost for
`vergoboy.ir` / `www.vergoboy.ir`. The Stream app is these locations:

- `/stream/_next/` → `alias /opt/stream/webapp-out/_next/` (immutable)
- `/stream/` → `alias /opt/stream/webapp-out/`, `try_files ... /index.html`
- `= /stream` → `301 /stream/`
- `/stream/media/` → `alias /opt/stream/media/`
- `~* ^/stream/media/hls/*.m3u8` and `*.ts` → same, split cache rules
- `/stream/api/` → `proxy_pass http://127.0.0.1:8801/stream/api/`
- `/stream/socket.io` → `proxy_pass http://127.0.0.1:8801/stream/socket.io` (upgrade)

Some locations are **instance-global, not per-app**, and a second instance must
not assume it can reuse them:

- `/hls/` → `alias /opt/stream/hls-live/` (RTMP ingest remux output)
- `/whip/` → `proxy_pass http://127.0.0.1:8889` (MediaMTX WHIP ingest)
- `/livekit/rtc/` → `proxy_pass http://127.0.0.1:7880` (LiveKit SFU signal)

### Reversibility baseline (sha256, before any edit)

```
4967f7ca9a70f41d051a2e6074a6bd109ada98674fef9910ef50c747756cb1e3  /etc/nginx/nginx.conf
2dc0998653e87ce50599efbb20de87d56d6d39de84d61028d922846bc0f78372  /etc/nginx/sites-enabled/arman-hosseini.ir.conf
351714456912b3f6f5fe0906f37c8bf3624da45dfb44bbea43146e8e3afb6986  /etc/nginx/sites-enabled/matrix
96dc30d91845eddca7df2dc4df4e300ed2b2d9c3253dba842d92c0648bdac756  /etc/nginx/sites-enabled/nikatormc.ir
bbd2d225876810b0b79e87c5452cfaff5879e8e649eed70a7898e9ec053d8a6b  /etc/nginx/sites-enabled/pterodactyl.conf
cc367f408035a980245fcb9c79e3a6cff2d0782b36adfa636e836dd51ce94471  /etc/nginx/sites-enabled/roundcube
48b7da0c08d8b541c2ede269ba97708a77d41015b4a05fc14b9e73a85eca3e6e  /etc/nginx/sites-enabled/rtc-matrix
b219168ef215708461a9cfb66815b368d23fa7a826b91ddf8ed5f8d697ff2e9f  /etc/nginx/sites-enabled/vergoboy.ir.conf
```

### Existing database inventory (read-only)

Databases: `arman_music`, `postgres`, `roundcube`, `roundcubedb`, `stream`,
`synapse`, `template0`, `template1`.
Roles: `arman_music`, `postgres`, `roundcube`, `stream`, `synapse_user`, plus
the built-in `pg_*` roles.

> The existing `stream` database and `stream` role must never be touched. The
> `/w2g` instance will need its own database and role (decided at G2).

### Open questions carried to the deploy plan (G2)

1. Does `/w2g` get its **own** LiveKit room/token config, or share
   `/livekit/rtc/`? Sharing the nginx location is fine; the room namespace is
   not.
2. Does `/w2g` get its own `/w2g/hls/` (RTMP ingest) or reuse the global
   `/hls/`? Global `/hls/` already points at `/opt/stream/hls-live/`.
3. Which DB/role for `/w2g` — new `w2g` database+role, or reuse the `stream`
   one? (Default: new, to keep the two instances independent.)

---

## Session 2 (2026-10-16) — T4 S0 read-only preflight

```sh
SSHPASS='***' sshpass -e ssh ubuntu@45.135.242.135 'bash -s' <<'REMOTE'
# S0 preflight (read-only; abort conditions: disk<20G, 8802 busy, nginx -t fail,
# vergoboy-stream not active, vhost sha256 mismatch)
df -BG --output=avail /opt | tail -1
ss -ltn | grep -E ':8802\b' && { echo "OCCUPIED — abort"; exit 1; } || echo free
ls -ld /opt/w2g 2>/dev/null || echo absent
sudo nginx -t
CONF=$(sudo readlink -f /etc/nginx/sites-enabled/vergoboy.ir.conf); echo "CONF=$CONF"
sudo sha256sum "$CONF"
sudo grep -c '^server' "$CONF"
sudo grep -n 'location' "$CONF"
/usr/bin/python3 -V; /usr/bin/node -v; /usr/bin/npm -v
test -x /opt/stream/venv/bin/python3 && echo "python3-venv works here"
sudo systemctl show vergoboy-stream -p EnvironmentFiles
sudo grep -oE '^[A-Z_]+=' /opt/stream/.stream_db.env | sort || true
sudo grep -oE '^STREAM_(GOOGLE|GITHUB)_OAUTH_CLIENT_(ID|SECRET)=' /opt/stream/.stream_db.env | sort || true
systemctl is-active vergoboy-stream vergoboy-frontend-build
curl -sS -o /dev/null -w 'stream HTTP %{http_code}\n' https://vergoboy.ir/stream/
REMOTE
```

### S0 findings

| Check | Result | Verdict |
| --- | --- | --- |
| Disk `/opt` free | **26G** | ≥ 20 GB floor ✓ |
| Port 8802 | **free** | ✓ |
| `/opt/w2g` | **absent** | ✓ |
| `nginx -t` | **syntax OK / test successful** | ✓ (proxy_headers_hash warnings are pre-existing) |
| vhost real path | `/etc/nginx/sites-available/vergoboy.ir.conf` | symlink resolves |
| vhost sha256 | `b219168ef215708461a9cfb66815b368d23fa7a826b91ddf8ed5f8d697ff2e9f` | **matches T3 baseline** ✓ |
| `server{}` count | **4** (ports 80, 443×2, 9000) | **deviates from plan's assumption of 1** — the /stream locations live in the line-15 443 block; S9 must insert the include into *that* block, not `rfind("}")` of the whole file |
| root `/api` location | → `proxy_pass http://127.0.0.1:5000` | **resolves open question 1**: root `/api` is another app (port 5000), not vergoboy-stream (8801); no root `/socket.io` |
| root `/socket.io` | absent | ✓ (only `/stream/socket.io`) |
| toolchain | Python 3.12.3, Node v18.19.1, npm 9.2.0 | ✓; stream venv exists at /opt/stream/venv |
| original env file | `/opt/stream/.stream_db.env`, `0600 www-data` | path confirmed for S6 |
| env key names | STREAM_DATABASE_URL, STREAM_GITHUB_OAUTH_CLIENT_{ID,SECRET}, STREAM_GOOGLE_OAUTH_CLIENT_{ID,SECRET}, STREAM_JWT_SECRET | OAuth credential names present (values never logged) |
| stream baseline | both units `active`; `GET /stream/` → **HTTP 200** | ✓ unchanged |

### Deviations handled at S9

The plan's S9 python inserts the include before `rfind("}")` (the last brace of the file), which with 4 server blocks would land in the wrong block. I will instead insert `include /etc/nginx/snippets/vergoboy-w2g.conf;` into the server block that contains `location /stream/api/` (the line-15 443 vhost), immediately before that block's closing brace — verified by re-reading the file after insertion and by `nginx -t`.

---

## Session 3 (2026-10-16) — T4 S1…S10 (the rollout itself)

Release: `20261015T204954Z`, source commit `9b80c22d2eeebe31ea0f4643006cba3c31461bd3`
(`git archive HEAD` piped to the host; ownership `www-data` throughout).

### S1–S2 — directories and release identity

```sh
sudo install -d -o www-data -g www-data -m 0755 /opt/w2g /opt/w2g/releases \
     /opt/w2g/media /opt/w2g/data "$REL"
git archive HEAD | sshpass -e ssh ubuntu@45.135.242.135 \
     "tar -xf - -C $REL && chown -R www-data:www-data $REL"
```

Verified: `app.py`, `config.py`, `requirements.lock.txt`, `webapp/` present;
`webapp/out` and `.git` correctly absent.

### S3 — virtualenv (deviation: server cannot reach the PyPI wheel CDN)

`python3 -m venv $REL/venv` then `pip install -r requirements.lock.txt` failed
repeatedly: `pypi.org` answers (HTTP 200, 1.7 s) but
**`files.pythonhosted.org` — the wheel CDN — times out** from this host
(`curl --max-time 25` to it never returns). Read timeouts persisted at
`--timeout 120 --retries 10`. npm's registry was unaffected (S4 needs it).

Workaround (recorded because every future deploy on this host needs it):

```sh
# on the deploy machine (has PyPI access), cross-download for the target:
pip download -r requirements.lock.txt -d w2g-wheelhouse \
  --python-version 3.12 --platform manylinux_2_34_x86_64 … \
  --platform manylinux2014_x86_64 --only-binary=:all:
scp -r w2g-wheelhouse ubuntu@45.135.242.135:/tmp/
# on the host:
pip install --no-index --find-links=/tmp/w2g-wheelhouse -r requirements.lock.txt
```

31 of 33 pins came down (`cffi`, `pycparser` are win32-only markers in the
lockfile — correctly skipped on Linux). The install upgraded the copies taken
from `/opt/stream/venv` to the exact lockfile versions. The wheelhouse now
lives at **`/opt/w2g/wheelhouse/`** (32 wheels, 18 M) for the next release.

Why the venv was seeded from `/opt/stream/venv` at all: that venv predates the
current code — it has **no `flask_cors`** (which is exactly why the live
`/stream` sends no CORS headers) — so `import flask_cors` failed at first boot.

Verify step:

```sh
$REL/venv/bin/python3 -c "import flask, flask_cors, flask_socketio, gevent, \
  psycopg2, jwt, yaml, sqlalchemy, requests, socketio, yt_dlp; print('ALL KEY DEPS OK')"
```

### S4 — static export

`rsync $REL/webapp/ → /opt/w2g/webapp/`, then as `www-data`:

```sh
NEXT_PUBLIC_BUILD_TARGET=web NEXT_PUBLIC_BASE_PATH=/w2g npm ci     # 396 packages
NEXT_PUBLIC_BUILD_TARGET=web NEXT_PUBLIC_BASE_PATH=/w2g npm run build
rsync -a --delete /opt/w2g/webapp/out/ "$REL/webapp-out/"
```

Post-conditions: `grep -rhoF /w2g → 229`, `/stream → 0`, `/api/api → 0`.
(A first attempt with a copied `node_modules` failed on `animejs` /
`@tauri-apps/api`; `npm ci` from the lockfile is the real path.)

### S5 + S6 — database and secrets (one shell, `$PGPASS` never printed)

- role `w2g` + database `w2g` created (`CREATE ROLE … LOGIN PASSWORD`, idempotent
  `DO $$` block; `stream` untouched);
- `/opt/w2g/.stream_db.env` written `0600 www-data`: own `STREAM_DATABASE_URL`,
  freshly minted `STREAM_SECRET_KEY` / `STREAM_JWT_SECRET`, plus the carried
  whitelist — all four `STREAM_{GOOGLE,GITHUB}_OAUTH_CLIENT_{ID,SECRET}` names
  landed.

### S7 — `current` symlink

`ln -sfn $REL /opt/w2g/current.new && mv -Tf …` (atomic), prune keeps 3 — only
one release exists so nothing was pruned.

### S8 — backend start (three fixes needed)

```sh
sudo install -m 0644 deploy/vergoboy-w2g.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now vergoboy-w2g
```

Boot failed three times, each fixed at the source rather than silenced:

| # | Failure | Fix |
| - | --- | --- |
| 1 | `ModuleNotFoundError: flask_cors` | S3 wheelhouse install (above) |
| 2 | `PermissionError: /var/www/.local` — `app_logging` falls back to `$XDG_STATE_HOME` when `STREAM_LOG_DIR` unset | unit now sets `Environment="STREAM_LOG_DIR=/opt/w2g/data/logs"` (inside `ReadWritePaths`) |
| 3 | `Production requires explicit … STREAM_ADMIN_EMAIL` — the **old** `/opt/stream/config.py` hardcodes `very.good.booyy@gmail.com` as a default, the new `config.py` requires it explicitly in production, and the key is not in `/opt/stream/.stream_db.env` to carry | appended `STREAM_ADMIN_EMAIL=very.good.booyy@gmail.com` to `/opt/w2g/.stream_db.env` |

Also created `/opt/w2g/data/rooms/`: `config.py` makes `DATA_DIR` but never
`DATA_DIR/rooms`, and readers (`state.py` load path, cleanup greenlet) assume it
— a greenlet crashed with `FileNotFoundError … /opt/w2g/data/rooms`.

After the fixes: `env=production`, `Database tables ready`,
`Running on http://127.0.0.1:8802 (gevent)`.

### S9 — nginx (deviation: plan's brace position + one extra location)

1. Backup: `vergoboy.ir.conf.bak.20261016T104610Z`; snippet installed to
   `/etc/nginx/snippets/vergoboy-w2g.conf`.
2. **First insertion attempt failed and was reverted.** A brace-matching
   scanner (written because `rfind("}")` targets the wrong block) desynced and
   dropped the include inside a `/music/` location →
   `nginx -t: location "/w2g/_next/" is outside location "/music/api/"`.
   Because `nginx -t` gates the reload, **nginx never loaded it** (live traffic
   untouched); the conf was restored from the backup and verified byte-identical.
3. Final insertion: immediately **before the `location /stream/_next/ {` line**.
   nginx forbids nested `location`, so any `location` line in a sites file is a
   server-level child — this provably lands in the `/stream` server block without
   parsing braces. `diff` showed exactly `+1` line; `nginx -t` OK; reloaded.
4. Snippet carries one addition beyond the plan: **`location = /livekit/`** →
   `proxy_pass http://127.0.0.1:7880/`. `_probe_livekit()` derives
   `https://vergoboy.ir/livekit/` from `STREAM_LIVEKIT_URL`, but only
   `/livekit/rtc/…` was routed — the probe got the SPA `404` →
   `livekit:false` → **HTTP 503**. Exact match cannot collide with the prefix
   `location /livekit/rtc/`; the SFU answers plain `OK` on its root.

### S10 — verification (all green)

```
GET /w2g/              -> 200
GET /w2g (no slash)    -> 301
GET /w2g/api/health    -> 200   {"checks":{"database":true,"livekit":true,
                                          "storage":true,"transcoder":true},
                                 "status":"healthy"}
socket.io polling      -> 0{"sid":"…","upgrades":["websocket"],…}   (Engine.IO open)
GET /w2g/_next/static/chunks/webpack-014f8389997a9eac.js -> 200
GET /livekit/          -> 200   (health passthrough)
google oauth           -> 302, redirect_uri=https%3A%2F%2Fvergoboy.ir%2Fw2g%2Fapi%2Fauth%2Foauth%2Fgoogle%2Fcallback   (D7 ✓)
github oauth           -> 302, redirect_uri=…%2Fw2g%2Fapi%2Fauth%2Foauth%2Fgithub%2Fcallback   (D7 ✓)
GET /stream/           -> 200   (untouched)
GET /stream/api/health -> 404   (old build — unchanged baseline)
GET /stream (no slash) -> 301
services: vergoboy-w2g enabled+active, vergoboy-stream active, nginx active, postgresql active
nginx -t               -> successful (only the pre-existing proxy_headers_hash warnings)
```

Disk after rollout: 25 G free (still above the 20 G floor).

### Follow-ups this session created

- User-side OAuth redirect URIs still to add (see G3): Google
  `https://vergoboy.ir/w2g/api/auth/oauth/google/callback`, and the same for
  GitHub — the server now *sends* `/w2g` callbacks but the apps must allow them.
- `/opt/w2g/wheelhouse/` must be refreshed whenever `requirements.lock.txt`
  changes (host cannot reach `files.pythonhosted.org`).

---

## Session 4 (2026-10-16) — OAuth credentials swapped, mail fixed

### OAuth (operator-supplied credentials)

The operator supplied fresh OAuth credentials for the `/w2g` instance (values
never logged — written directly into the secrets file by key name):

```sh
sudo python3 - <<'PY'   # replaces the four STREAM_{GOOGLE,GITHUB}_OAUTH_* values in place
… update keys: STREAM_GOOGLE_OAUTH_CLIENT_ID STREAM_GOOGLE_OAUTH_CLIENT_SECRET
               STREAM_GITHUB_OAUTH_CLIENT_ID STREAM_GITHUB_OAUTH_CLIENT_SECRET …
PY
sudo chown www-data:www-data /opt/w2g/.stream_db.env && sudo chmod 600 …
sudo systemctl restart vergoboy-w2g
```

Verified: both authorise endpoints 302 with the **new** client ids
(`774272794013-23rotbe0…` for Google, `Ov23li2gVBfhIf1Ngvwf…` for GitHub) and
`redirect_uri=…%2Fw2g%2Fapi%2Fauth%2Foauth%2F…callback`. The redirect URIs must
still be registered on the two provider consoles (they are app-side allow-lists,
not something the server can set).

### Mail — root cause and fix

Symptom: "sending email is not working". Diagnosis chain, all read-only first:

| Check | Finding |
| --- | --- |
| listener on :25 | **none** (`ss -ltn`) — `smtplib.SMTP(127.0.0.1,25)` in `mail.py` → connection refused |
| `systemctl status postfix` | `active (exited)` — the stock unit is a **stub** (`ExecStart=/bin/true`) |
| actual owner of master | `postfix@-.service` (postmulti instance `-`) — **failed** |
| journal for `postfix@-` | ran Sep 26 16:12 (boot, via `postfix-instance-generator`) → **died 2026-09-28 02:08** after the global OOM (dmesg: `Out of memory: Killed process … java`, docker) took proxymap/master down → unit stuck `failed`, no restart policy |
| consequence | **mail dead box-wide since Sep 28** — `/stream` password-reset, roundcube submission (:587), everything; last successful delivery Sep 25 |
| outbound :25 | fine historically (`status=sent` to gmail through Sep 25) — not a provider block |
| `dpkg -V postfix` | clean — stub unit is stock, nothing tampered |

Fix (no config edits, fully reversible):

```sh
sudo postfix stop                  # hand back the manually-started master
sudo systemctl reset-failed postfix@-.service
sudo systemctl start postfix@-.service
```

Verified: `postfix@-` **active (running)**, master pid present, listeners on
**:25 and :587** (the latter is roundcube's submission path — also restored).

### Test script

`scripts/test_email.py` (committed; deployed to `/opt/w2g/scripts/`) sends a
real message through `mail.send_email` — the exact path password-reset uses —
loading the instance env file and the unit defaults:

```sh
sudo -u www-data python3 /opt/w2g/scripts/test_email.py
# app dir /opt/w2g/current · from info@vergoboy.ir · to very.good.booyy@gmail.com
# RESULT: accepted by postfix (mail.send_email -> True).  exit 0
```

Delivery confirmed independently in the log:

```
postfix/smtp[…]: … to=<very.good.booyy@gmail.com>,
  relay=gmail-smtp-in.l.google.com[…] :25, dsn=2.0.0, status=sent (250 2.0.0 OK … gsmtp)
Mail queue is empty
```

Recipient note: the registered/admin address is `very.good.booyy@gmail.com`
(confirmed in the `stream` DB users table); the request text showed an extra
"o" (`goood`) — a typo, the real address was used.

> **Boot/robustness:** the generator re-queues `postfix@-.service` at every
> boot, so a reboot starts mail again; but a repeat global OOM will kill master
> and leave the unit `failed` with nothing restarting it — same silent failure
> as Sep 28. See docs/FOLLOWUPS.md.


---

## Session 5 (2026-10-16) — test email landed in Spam: diagnosis and fix

Read-only diagnosis first:

| Check | Result |
| --- | --- |
| NS | Cloudflare (alina/tadeo) — DNS records not editable from the server |
| SPF | `v=spf1 ip4:37.32.43.212 ip4:45.135.242.135 -all` — **includes the sending IP** ✓ |
| DMARC | `v=DMARC1; p=none; rua=mailto:very.good.booyy@gmail.com` ✓ (monitoring) |
| DKIM | `default._domainkey.vergoboy.ir` TXT published ✓; private key present, `opendkim-testkey` matches ✓ |
| opendkim | **active**; socket `local:/var/spool/postfix/opendkim/opendkim.sock`; log shows `DKIM-Signature field added (s=default, d=vergoboy.ir)` on the actual test sends ✓ |
| postfix identity | `myhostname=mail.vergoboy.ir`, `mydomain=vergoboy.ir`, banner `$myhostname ESMTP` ✓ |
| rDNS | PTR `45.135.242.135 → mail.vergoboy.ir` ✓; `mail.vergoboy.ir A → 45.135.242.135` ✓ (HELO resolves) |
| RBLs (via Google DoH) | spamhaus zen, spamcop, barracuda, CBL — **all clean** (the direct-resolver `127.255.255.254` was query-policy refusal, not a listing) |
| wire headers (probe to root@localhost) | DKIM-Signature ✓ Return-Path `<info@vergoboy.ir>` ✓ Date ✓ — **`Message-ID` MISSING** |

Fix: `mail.py` now sets `Date` and `Message-ID` itself (RFC 5322 requires both;
Gmail scores header-less mail as malformed/spammy — a delivered copy provably
had neither; the earlier `Date` had been MTA-added). Release copy patched with
backup `mail.py.bak.20261016T115510Z`, `vergoboy-w2g` restarted.

Re-probe verified **exactly one `Date` and one `Message-ID`**
(`<…@vergoboy.ir>`) plus the DKIM signature on the wire; new test delivered to
the admin Gmail at 11:56:54 (`status=sent`, 250 gsmtp).

If placement is still Spam after this, everything server-side is passing
(SPF/DKIM/DMARC/rDNS/RBL) and the remaining lever is **sending-IP reputation**
— cold hosting IP, plus Gmail's engagement history with this sender (earlier
header-less mail taught it "spam"): warm-up, "Not spam" training, Gmail
Postmaster Tools, or an external transactional relay — operator decision.

### Session 5 addendum — objective deliverability verdict (mail-tester.com)

Sent the fixed message to `test-w2g7f3k9qz@srv1.mail-tester.com` (12:02:44,
`status=sent`) and fetched `/check?id=…`:

- **Score: 10/10** ("Wow! Perfect, you can send"), SpamAssassin **0.2** (spam
  threshold 5.0)
- `Received-SPF: Pass` — `ip4:45.135.242.135` matched for `info@vergoboy.ir`
- `dkim=pass (2048-bit key)` — `header.d=vergoboy.ir [at] gmail` aligned
- `dmarc=pass (p=none)` — From/DKIM/SPF alignment confirmed
- rDNS/HELO: all three = `mail.vergoboy.ir` ✓; MX + A present ✓
- **Not listed on all 20 checked blocklists** (Spamhaus SBL/CSS/XBL/PBL,
  Barracuda, SpamCop, PSBL, Backscatterer, …)
- Content: no short URLs, no dangerous HTML, no broken links; only advisory
  notes are `SPF_HELO_NONE` (+0.001, HELO publishes no SPF — cosmetic) and
  missing `List-Unsubscribe` (only required for bulk senders).

Conclusion: every server-side and content signal passes an independent
receiver. Gmail Spam placement is therefore **Gmail-side reputation and
engagement** (cold IP + earlier header-less mail history), not configuration.
Levers remain the ones in FOLLOWUPS: Postmaster Tools, "Not spam" warm-up,
or a smarthost relay.

---

## Session 6 (2026-10-16) — VPN proxy for the apps, built like /opt/noisy

**Request:** read `/opt/noisy`, create a proxy/VPN like it for this project.
**Decisions (operator):** scope = **archive scraper + yt-dlp only** (the media
pipeline stays direct by the app's own design); credentials = **reuse noisy's
subscription**.

### Why it was needed (measured, read-only first)

| Path from this host | Result |
| --- | --- |
| `youtube.com`, `redirector.googlevideo.com`, `digimoviez.com`, `animex.click` direct | timeout / connection reset |
| host DNS answers | censor infra: `2001:4188:2:600::/64` AAAA, A `10.10.34.35` (private) |
| existing `xray.service` socks5 `127.0.0.1:1080` → YouTube | TLS reset |
| noisy's tunnel (`docker exec noisy-restreamer`) | exit `194.5.192.35`, YouTube `204` ✓ |

The app already had the hook points: `STREAM_ARCHIVE_HTTP_PROXY` defaulted to
`http://127.0.0.1:10808` **with nothing listening**, `STREAM_YTDLP_PROXY`
empty, and `app.py` already passes `--proxy` to yt-dlp when set.

### What was built — `/opt/vergoboy-proxy/`

`build_config.py` **imports** `/opt/noisy/egress/build_configs.py` in place
(never copied) and reads `/opt/noisy/secrets/subscription.txt`, rendering a
**userspace** sing-box config — no TUN, host routing untouched:

- inbounds: HTTP `:10808`, SOCKS `:10809`, published **127.0.0.1-only** in
  compose (loopback boundary = the port publish, see gotcha below)
- outbounds: `urltest` failover over the subscription's sing-box-capable
  entries — currently `vpn-hysteria2` + `vpn-grpc` (the rest are XHTTP/ML-KEM,
  xray-only); YouTube needed **no** xray special-case (noisy's TUN does, ours
  tested green through the native pair)
- DNS: bootstrap via `dns-local`; everything else via `dns-tunnel`
  (`udp 1.1.1.1 detour auto`) so poisoned censor answers never reach routing
- private/multicast CIDR → direct; `cap_drop: ALL`, `no-new-privileges`,
  mem/cpu/pids limits, pinned image `ghcr.io/sagernet/sing-box:v1.14.2`

**Gotcha (measured):** first start, every probe died with `RST` and **zero**
sing-box logs — the config bound `127.0.0.1` *inside the container*, but
docker-proxy forwards to the container's **eth0**, never its loopback. In-
container listen is `0.0.0.0`; host-loopback-only is guaranteed by the
`127.0.0.1:` publish. Documented at the exact spot in `build_config.py`.

**Watchdog** (`vergoboy-proxy-watchdog.timer`, every 60s, root): probes the
exit IP **through the proxy** (never DNS — DNS answers whether or not the
tunnel carries traffic). Host-IP answer = restart immediately; two empty
probes = restart (wedge); container absent = report only, does not fight an
operator. Exit IP is deliberately **not** pinned: urltest failover changes it
legitimately; the invariant asserted is "≠ host IP".

**Wrong-domain detour:** initial probes used `digimoviez.ir` — that domain is
dead (clean resolvers: `SERVFAIL … No Reachable Authority`; host: poisoned to
`10.10.34.35`). The app's real targets are `digimoviez.com` and
`animex.click` (`ARCHIVE_BASE_URL`, `AX_BASE`).

### Wiring and verification

`/opt/w2g/.stream_db.env` → backup `.bak.20261016T125652Z`, appended
`STREAM_ARCHIVE_HTTP_PROXY=http://127.0.0.1:10808` and
`STREAM_YTDLP_PROXY=http://127.0.0.1:10808` (0600 `www-data` kept),
`vergoboy-w2g` restarted.

| Check | Result |
| --- | --- |
| vars in running process (`/proc/<MainPID>/environ`) | both present |
| exit IP via HTTP + SOCKS inbound | `194.5.192.35` (host `45.135.242.135`) |
| `youtube.com/generate_204` / `www.youtube.com` via proxy | 204 / 200 |
| `redirector.googlevideo.com` via proxy | answered (404 on `/` = live) |
| `digimoviez.com` / `animex.click` via proxy | 200 / 301 |
| yt-dlp as `www-data`, `--proxy …10808`, same venv the app uses | extracted `jNQXAC9IVRw \| Me at the zoo` |
| `https://vergoboy.ir/w2g/api/health` after restart | 200 |
| `/stream/` untouched | 200 |
| watchdog: success / stopped-container / recovery branches | all as designed |
| `sing-box check` on the generated config | OK (gated before `up`) |

Ops entry points: `sudo /opt/vergoboy-proxy/proxy-status`, unit docs in
`deploy/vergoboy-proxy/README.md`.

---

## Session 7 (2026-10-16) — archive search broken: site retired `ajaxsearch`

**Symptom:** every archive search showed «دیجی‌موویز درخواست را رد کرد».

### Diagnosis chain (each step measured)

1. The string maps to `KIND_HTTP_ERROR` (`app.py`) — a 4xx from digimoviez.
2. Journal: post-proxy searches reach the site (642 ms) and get
   **`HTTP 400`** on `POST /wp-admin/admin-ajax.php` (earlier 11:53 entries
   were the pre-proxy `proxy_unavailable` — a different, already-fixed error).
3. Replay of the exact request: **400, body `0`, WP headers** = WordPress
   "no handler for this action" — not Cloudflare (its ray/header show pass-
   through) and not the VPN: **identical 400 from a completely unrelated
   network direct**.
4. `action=heartbeat` (core, guest-capable) → **200** — admin-ajax itself is
   fine; only the action is gone.
5. The site updated to WordPress 7.1.2 + theme `digimoviez-2`; its homepage
   has no ajax search form anymore (plain `GET /?s=`), but
   `themes/digimoviez-2/assets/main.js` still posts two actions —
   `ajaxsearch` (dead for guests) and **`ajaxsearchheader`** (the header's).
6. `action=ajaxsearchheader&s=interstellar` → **200**, JSON string of HTML,
   real cards. Extra payload fields (our `type`) ignored — verified.

### Fix (tests first, commit `876d303`)

Fixture captured from the live response
(`tests/fixtures/archive/ajax_search_header_interstellar.json`) + two tests
that failed on the old code, then four minimal edits in `archive_scraper.py`:

| Was | Now |
| --- | --- |
| `SEARCH_AJAX_ACTION = "ajaxsearch"` | `"ajaxsearchheader"` |
| `CARD_CLASSES` (3 legacy names) | + `text_adv_se` (new `<li>` card; marker slicing spans the li) |
| title from `<h2>` only | `<h2>` priority, `<h3 class="title">` fallback |
| rating `rate_num`/`<strong>` only | + `rate_container`/`<span>` |

Suite: **590 passed** (2 pre-existing skips).

### Deploy + verification

Release patched with backup `archive_scraper.py.bak.20261016T131859Z`,
`vergoboy-w2g` restarted (active, health 200). End-to-end as `www-data`
against the **deployed** code with the production env (proxy wiring):

```
results: 3
 - Interstellar 2014 | https://digimoviez.com/interstellar-2014/ | rating 8.7 | year 2014
 - Tenet 2020        | https://digimoviez.com/tenet-2020/        | rating 7.3 | year 2020
 - Black Beauty 2020 | https://digimoviez.com/black-beauty-2020/ | rating 6.4 | year 2020
```

### Session 7 addendum (2026-10-16) — archive autologin enabled

**Symptom:** every gated detail page answered 401 «برای دیدن لینک‌های دانلود
باید وارد حساب دیجی‌موویز شوید» — `KIND_AUTH_REQUIRED`, because
`STREAM_ARCHIVE_AUTH_ENABLED` was absent (default `false`) and no credentials
were configured. The auth machinery itself was never the problem.

**Compatibility proof before touching the server** (offline, against the live
login page fetched through the proxy):

- `parse_login_form` recognizes the site's `dashboard_form`: fields
  `username`/`password`, hidden `login_security_str` + `_wp_http_referer`,
  challenge `secureq_key`/`secureq_ans`, same-origin action → `/account/login/`.
- `solve_question_text` answers the served Persian arithmetic
  («۸ ضرب در ۵ چند می شود؟» → 40).

**Wiring** — env append on `/opt/w2g/.stream_db.env` (backup
`.stream_db.env.bak.20261016T132717Z`, re-chmod 0600 www-data, no value ever
echoed):

| Key | Value |
| --- | --- |
| `STREAM_ARCHIVE_AUTH_ENABLED` | `true` |
| `STREAM_ARCHIVE_LOGIN_USERNAME_FIELD` | `username` |
| `STREAM_ARCHIVE_LOGIN_PASSWORD_FIELD` | `password` |
| `DIGIMOVIEZ_USERNAME` / `DIGIMOVIEZ_PASSWORD` | user's account (server-side only) |

**Verification (all measured):**

1. Standalone as `www-data` with the production env: `ensure_authenticated()`
   → `AUTHENTICATED` (4 cookies, 9.3 s); the previously gated
   `/batman-knightfall-part-1-knightfall-2026/` → **1 group, 106 download
   links** (`dl8.deserver.top`).
2. Service watchdog (started at boot via `auth.start()`) did the same by
   itself at 13:29:19 UTC: guest check → expired → login form parsed →
   arithmetic challenge solved → 1 POST → **`AUTH_LOGIN_SUCCESS` (15 s)** →
   `AUTH_SESSION_PERSISTED`.
3. Session file: `/opt/w2g/data/digimoviez-session.json`, 433 bytes,
   `-rw------- www-data`.
4. Next tick (13:31:40): **`AUTH_OK` in 1.7 s with no new POST** — session
   reuse, no relogin loop.

No code changed — configuration only. If the site password changes, update
`DIGIMOVIEZ_PASSWORD` in the env and restart; the failed-login cooldown
(300 s) and `max_login_posts=3` bound any lockout risk.

---

## Session 8 (2026-10-16) — playback broken: atomic runner vs progressive HLS

**Symptom:** clicking play on an archive movie showed «ارتباط با سرور برقرار
نشد؛ اتصال اینترنت را بررسی کن» (a `friendlyError` TypeError — hls.js reports
a manifest `networkError` through the same funnel).

**Measured diagnosis:**

1. Server healthy (health 200 local+public, disk 24 G free).
2. Archive flow worked end to end: detail (106 links) → playlist created →
   ffprobe OK (0.4 s, `dl8.deserver.top` reachable directly — no proxy
   needed) → ffmpeg encoding happily.
3. nginx access log (`vergoboy_access.log`): the player polled
   `/w2g/media/hls/<id>/1080p/index.m3u8` → **404, 30+ times**, while ffmpeg
   wrote into `1080p.partial/` right next to it.
4. Root cause: **regression from the atomic-output runner.** `/stream`'s old
   code wrote HLS straight into the final dir (playlists grow in place →
   `on_ready`'s "ready after 20 s" was truly playable). The new `EncodeRunner`
   builds in `<label>.partial` and renames only on success, but `on_ready`
   still flipped the item to `ready` and pointed the player at a final path
   that stayed **empty for the whole encode**.

**Fix (commit `8a65e25`, tests first — 5 `ProgressiveOutputTests`, suite 640
passed):**

- `expose_partial_output()` in `media_pipeline/runner.py`: when no finished
  output exists, `final_dir -> final_dir.partial` symlink makes the growing
  playlists/segments servable at the URL the player already uses; a real dir
  (re-encode) is left alone; a leftover symlink from a failed attempt is
  reused. `_promote` unlinks the symlink before the atomic swap (rmtree
  refuses symlinks); `fail()` in `app.py` drops it with the partial.

**Deploy + verification:** release patched (backups `.bak.20261016T140030Z`),
restarted. The interrupted Batman item was stale ("ready", output deleted by
boot cleanup, resume only re-kicks "queued") → dropped its garbage dir,
requeued in `rooms/4JE9D9.json`, restarted: resume re-kicked the encode with
the new code and within 90 s:

```
1080p -> .../1080p.partial          (symlink)
master.m3u8          200   130 B
1080p/index.m3u8     200  3115 B   (was 404 — playlist already at seg_102)
1080p/seg_000.ts     200   3.3 MB
both via https://vergoboy.ir       200/200
```

Early playback during encode now works on /w2g exactly like /stream, with
the atomic promote still guaranteeing a clean final artifact at completion.

## Session 9 (2026-10-16) — archive walled: Cloudflare Turnstile, not an expired session

**Symptom:** every archive search on /w2g returned «نشست دیجی‌موویز منقضی
شده؛ دوباره وارد شوید» — and relogging could not help.

**Measured diagnosis:**

1. Journal: `AUTH_EXPIRED ... status: 403` on the session check +
   `AUTH_LOGIN_FAILED reason: HTTPError` on every login attempt. Last
   success 15:28 UTC; first failure 15:30 UTC.
2. Live curl through the archive proxy (exit 194.5.192.35): every endpoint
   (`/`, `/account/login/`, `wp-admin/admin-ajax.php`) answers **403** with
   `cf-mitigated: challenge` and a "Just a moment…" page. Same from a
   residential path. The server cannot reach digimoviez at all without the
   proxy (status 000), so the proxy stays mandatory.
3. `curl_cffi` with Chrome TLS fingerprints: still 403 — not a JA3 gate.
   Headless Chromium through the proxy: the page loads, but the
   **Turnstile widget never clears** (explicit-render `cf-turnstile-response`
   present in the DOM). FlareSolverr 3.5.2 (probe container on the proxy's
   own network, exit verified 194.5.192.35): `Error solving the challenge`
   after 120 s.
4. The sing-box proxy's second outbound (vless-grpc) exits from the **same**
   194.5.192.35 — no alternate egress exists.
5. Headless Chromium from the workstation (residential path) also stays on
   «Just a moment…»: the wall defeats every programmatic client on every
   network available; only real headed browsers pass it.

**Classification bug (fixed, commit `393d6e4`, tests first):** `check()`
read the 403 as "redirected to login" → AUTH_EXPIRED → the misleading
message, then hammered the equally walled login form on every request.
`tests/unit/test_archive_site_challenge.py` pins the correction:

* `_is_cloudflare_challenge()` — the `cf-mitigated` header, with the page's
  `challenges.cloudflare.com` CSP as the header-stripped fallback.
* `check()` → AUTH_FAILED/`site_challenge` returning `None` (transport
  failure): `ensure_authenticated()` never opens the walled login form.
* `KIND_SITE_CHALLENGE` → **503** «دیجی‌موویز پشت محافظ کلادفلر است؛ چند
  دقیقه بعد دوباره تلاش کنید», deliberately not in
  `ARCHIVE_MANUAL_SESSION_KINDS` — importing a browser session cannot help
  when the edge blocks before WordPress.
* A genuine expiry still auto-relogs, pinned by test. Suite: 813 passed.

**Deploy + verification:** backups `.bak.20261016T190521Z`, installed
checksums match the commit, restart clean, health 200. Journal after
restart: one `AUTH_CHECK_SITE_CHALLENGE` per watchdog cycle (609/676 ms),
**zero** login attempts — the opposite of the pre-fix pattern.

**Bypass status:** no standard tool passes this wall (probe containers
removed; the 1.07 GB flaresolverr image is kept for a future trial — see
FOLLOWUPS). The state machine recovers by itself the moment the edge lifts
the challenge; no manual step is possible or needed.

## Session 10 (2026-10-17) — the wall is lifted: FlareSolverr mints, requests replays

**Symptom:** the Session-9 state held all through the night — every archive
search answered the honest 503 «دیجی‌موویز پشت محافظ کلادفلر است؛ چند دقیقه
بعد دوباره تلاش کنید», and the journal logged one `AUTH_CHECK_SITE_CHALLENGE`
per watchdog cycle.

**Dead end first (recorded so it is not retried):** a browser we drove
ourselves — headed Camoufox on Xvfb through the archive proxy — never got
past the interactive step from the server. Probe versions (`solve.py` v1–v6)
showed the page going silent for 120 s right after the `ci` beacon, a
shadow-rooted checkbox no synthetic click ever landed on, and a provisional
`cf_clearance` that the edge refused on replay. A locally-run *headed*
Camoufox on the real workstation display cleared the same wall in ~6 s from
the same exit IP (194.5.192.35) — i.e. the wall was **environment detection**,
not IP reputation — but that solver depended on the operator's desktop being
on and was abandoned with the probe image. The user-supplied
`cloudflare-turnstile-bypass` repo was also evaluated and rejected: it is a
~7 KB wrapper around a **paid** API (`peak.fo`) that returns a
`cf-turnstile-response` token for a widget sitekey, which does not apply to an
edge interstitial and was out of scope by the user's own decision.

**What actually worked.** The FlareSolverr 3.5.2 image that Session 9 left on
the server — previously written off — solves this wall cleanly. Run on the
proxy's own network with the proxy passed **per request** (a global
`HTTP_PROXY` env breaks its own startup check):

```
POST http://127.0.0.1:8191/v1
  {"cmd":"request.get","url":"https://digimoviez.com/","maxTimeout":180000,
   "proxy":{"url":"http://vergoboy-proxy:10808"}}
→ {"status":"ok", "solution":{"userAgent":"…Chrome/152… Linux x86_64",
                              "cookies":[{"name":"cf_clearance", …}]}}
```

`solution.cookies[cf_clearance]` has **expiry ≈ 525600 min = one year**, and a
plain `curl` replays it — 200, 371 KB real page — while the same cookie with a
different User-Agent is 403 again. So the edge binds `cf_clearance` to IP +
User-Agent, not to TLS/JA3, and the app only has to hold the pair.

**Code (commits `0bcfe2f`, `9a639f8`; tests first, 835 passed):**

* `archive_solver.fetch_clearance()` now speaks FlareSolverr's `POST /v1`
  instead of the home-grown `/solve` contract, requires a `cf_clearance` in
  the answer (a "solved" page without one is a failure, never a retry), and
  forwards the archive proxy as a per-request field.
* `archive_auth._adopt_clearance()` passes `settings.http_proxy` to the
  sidecar (the *sidecar's* Chrome reaches the site; this process only talks
  loopback) and still adopts cookies + User-Agent atomically.
* `archive_scraper` **seeds the session User-Agent once and stops stamping its
  own on every scrape.** This was the second bug: `cf_clearance` is bound to
  the UA that minted it, so with the clearance adopted the session *check*
  passed (session UA) while every *search* was walled again (hardcoded
  Chrome/125 UA). Live proof: adopted UA → 200, hardcoded UA → 403.

**Deployed (all backed up `.bak.<ts>`, instance untouched otherwise):**

* `vergoboy-archive-solver` — `ghcr.io/flaresolverr/flaresolverr:latest`,
  `--network host --shm-size=1g`, `HOST=127.0.0.1 PORT=8895`, `RestartPolicy
  unless-stopped`. Bound to **127.0.0.1 only** (verified: no `0.0.0.0:8895`),
  so it is not reachable from the internet.
* `/opt/w2g/.stream_db.env` (0600) + `.bak.20261017T115820Z`:
  `STREAM_ARCHIVE_SOLVER_URL=http://127.0.0.1:8895`. Empty = off; the wall
  degrades to the Session-9 503 exactly as before.
* `/opt/w2g/current/`: `archive_solver.py` (new), `archive_auth.py`,
  `config.py`, `archive_scraper.py` (installed checksums match the commits).

**Verification.**

* Solver: `Challenge detected … solved!` in ~116 s; `ss -ltn` shows only
  `127.0.0.1:8895`.
* Service journal after restart: `AUTH_CHECK` → `AUTH_CLEARANCE_ADOPTED`
  (`cookie_names:["cf_clearance"]`, UA Chrome/152 Linux) →
  `AUTH_CHECK_RETRY_AFTER_CLEARANCE` → `AUTH_OK marker=logout_link status=200`,
  then steady-state `AUTH_OK` every 120 s in 3–6 s.
* End-to-end with the live config and the app's own venv:
  `get_collector().search(SearchFilters.from_mapping({"query":"dune"}))`
  returned **3 results**, first `Dune: Part Two 2024` (2024, 8.4,
  `https://digimoviez.com/dune-part-two-2024/`) — real content, not a wall.

**Cost / recovery:** one ~2-minute solve is spent per process start (the
clearance is not yet persisted with its UA) and again only if Cloudflare
invalidates the year-long cookie. Every solve failure (unreachable, timeout,
non-JSON, no `cf_clearance`) collapses to the honest `site_challenge` 503, so
the state machine can never do worse than Session 9.

**Rollback:** stop/remove `vergoboy-archive-solver`, drop the
`STREAM_ARCHIVE_SOLVER_URL` line from `.stream_db.env` (restore
`.bak.20261017T115820Z`), restore the four files from their `.bak.<ts>`
siblings, `systemctl restart vergoboy-w2g`. No nginx change, no reload.
