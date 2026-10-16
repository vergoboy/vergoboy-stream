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
