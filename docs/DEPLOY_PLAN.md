# Deploy plan — second instance at `https://vergoboy.ir/w2g`

**Status: draft for approval (G2). Nothing in this file has been executed.**
Every server step below is listed with the exact command that will run, so the
plan can be reviewed rather than trusted.

Scope: stand up the `/w2g` instance and expose it publicly, **leaving
`https://vergoboy.ir/stream` and everything that serves it untouched** — its
process, port, database, files, unit, nginx locations and systemd state.

---

## 1. Non-negotiables

| Rule | How this plan honours it |
| --- | --- |
| Never disturb `/stream` | No `systemctl` action on `vergoboy-stream` or `vergoboy-frontend-build`; no write under `/opt/stream`; the DB role `stream` and database `stream` are only ever *read* (S0) |
| Read-only until approved | S0 makes no change; the first write is S1 |
| Idempotent | Every create is guarded (`install -d`, `IF NOT EXISTS`, "already present" checks); re-running converges instead of failing or duplicating |
| Reversible | Every edited file is copied to `<file>.bak.<UTC-ts>` first, with a sha256 before/after; S11 restores them |
| Config checked before it is loaded | `nginx -t` must pass before `systemctl reload nginx`; the unit is `daemon-reload`ed before it is started |
| Disk floor | S0 aborts if `/opt` has less than 20 GB free (last measured: 26 GB) |
| Keep last 3 releases | S7 prunes `releases/*` to the 3 newest |
| No secrets printed | Secrets are generated server-side into a `0600`/`0640` file; nothing is echoed, logged, or written to the repo. Placeholders below are `***` |

## 2. Facts this plan relies on

From the read-only recon in `docs/DEPLOY_LOG.md` (host `bolbolestan`, Ubuntu
24.04.4, nginx 1.24.0, PostgreSQL 16, passwordless sudo as `ubuntu`):

- port **8802 is free**; `/opt/w2g` **does not exist**; 26 GB free on `/`
- `/opt/stream` exists and is owned by `www-data`; `/stream` answers `HTTP/2 200`
- `/etc/nginx/sites-enabled/vergoboy.ir.conf` holds the 443 vhost; instance-global
  locations `/hls/`, `/whip/`, `/livekit/rtc/` are already declared there and
  **must not be declared twice** (a duplicate `location` fails `nginx -t`)
- sha256 of every nginx file was recorded before any change (recon §Reversibility)

### Still unverified (checked read-only in S0, no change)

1. **Is there a root-level `/api/` or `/socket.io` location?** The recon grepped
   `/etc/nginx/` for `"stream"` only, so a root path would not have shown. This
   decides whether the **already-shipped** `vergoboy-stream` desktop package
   (which calls `https://vergoboy.ir/api/…`) actually works. It does **not**
   affect the `w2g` client, which names `/w2g` explicitly — see
   `docs/FOLLOWUPS.md`.
2. **Is `sites-enabled/vergoboy.ir.conf` a symlink?** The edit must land on the
   real file (`readlink -f`), not the symlink.
3. **How many `server{}` blocks** does that file hold? The include helper below
   assumes one.

## 3. Decisions

| # | Decision | Value | Note |
| --- | --- | --- | --- |
| D1 | Database + role | **new** `w2g` DB and `w2g` role | recon open question 3; keeps the instances independent; the `stream` DB is never written |
| D2 | LiveKit room | `STREAM_LIVEKIT_ROOM=w2g-voice` | recon open question 1; the `/livekit/rtc/` nginx location is shared, the room namespace is not |
| D3 | Live (RTMP→HLS) | **share the global `/hls/`** | recon open question 2; the ingest writes one server-side directory, so a `/w2g/hls/` location would serve an empty dir. Keys are 10 hex chars generated per instance → collision negligible. Isolation is a follow-up |
| D4 | Static export location | **inside** the release (`$REL/webapp-out`) | unlike `/opt/stream/webapp-out`, which sits outside its releases; flipping `current` then swaps backend and UI together, so a rollback moves both |
| D5 | Frontend build | on the server, in a reused workspace `/opt/w2g/webapp`, by the same `npm ci` + `NEXT_PUBLIC_BASE_PATH=/w2g npm run build` recipe the stream build unit uses | fallback: build locally and upload `out/`, if the server's npm is unavailable |
| D6 | nginx integration | one `include` line + a snippet file, not a paste | revert is "remove the line and the file" instead of "un-paste 90 lines" |
| D7 | OAuth | **off** for v1 | Google/GitHub redirect URIs are registered per app; email+password login works without it. A second OAuth app can be added later without touching this plan |
| D8 | Release identity | the commit sha of the release candidate, recorded at S2 | `git archive HEAD` — only tracked files, exactly that commit |

## 4. Target layout

```
/opt/w2g/
├── releases/
│   ├── 20261015T180000Z/
│   │   ├── <repo tree, tracked files only>      (app.py, config.py, templates/, static/ …)
│   │   ├── venv/                                 its own virtualenv
│   │   └── webapp-out/                           the /w2g static export (nginx aliases this)
│   └── <older releases, last 3 kept>
├── current -> releases/20261015T180000Z           atomic rename (S7)
├── webapp/                                        frontend build workspace (source + node_modules)
├── media/                                         mutable, survives upgrades   (INSTANCE_ROOT/media)
├── data/                                          mutable, survives upgrades   (INSTANCE_ROOT/data)
├── .npm-cache/
└── .stream_db.env                                 0600, www-data:www-data, never committed
```

`STREAM_INSTANCE_ROOT=/opt/w2g`, and `config.py` derives `media/`, `data/` and
their subdirectories from it — all writable paths are inside those two, which is
what the unit's `ReadWritePaths=/opt/w2g/media /opt/w2g/data` grants.

nginx will serve:

| Location | Backing path |
| --- | --- |
| `/w2g/_next/` | `alias /opt/w2g/current/webapp-out/_next/` |
| `/w2g/` | `alias /opt/w2g/current/webapp-out/`, `try_files … /index.html` |
| `= /w2g` | `301 /w2g/` |
| `/w2g/media/` | `alias /opt/w2g/media/` |
| `/w2g/api/` | `proxy_pass http://127.0.0.1:8802/w2g/api/` |
| `/w2g/socket.io` | `proxy_pass http://127.0.0.1:8802/w2g/socket.io` (upgrade) |

Source of truth for both files: `deploy/nginx-w2g.conf`, `deploy/vergoboy-w2g.service`.

---

## 5. Steps

Convention: `HOST=ubuntu@45.135.242.135`. S0–S9 run **from this repo, in one
local shell**, talking to the server as `SSHPASS='***' sshpass -e ssh "$HOST" …`.
`TS`/`REL` are chosen once and locally (S1, using the server's clock), then
handed to the server as `$1` of each remote script:

```bash
SSHPASS='***' sshpass -e ssh "$HOST" 'bash -s' -- "$REL" <<'REMOTE'
REL=$1
...                      # server-side lines from the step below
REMOTE
```

Short one-liners are the same thing inline:
`SSHPASS='***' sshpass -e ssh "$HOST" "… $REL …"`. So every block below is
*server-side* unless it says otherwise (S2's `git archive` and S1's `date` run
here). The heredocs are quoted, so remote expansions (`$PGPASS`, `$CONF`) happen
on the server and never on this machine. **S0 makes no change**; S1–S9 must run
in order, each step checked before the next.

### S0 — preflight, read-only

```bash
SSHPASS='***' sshpass -e ssh "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
echo "--- disk floor (need >= 20G free) ---"
df -BG --output=avail /opt | tail -1
echo "--- port 8802 (must be free) ---"
ss -ltn | grep -E ':8802\b' && { echo "OCCUPIED — abort"; exit 1; } || echo free
echo "--- /opt/w2g (must not already exist as a foreign install) ---"
ls -ld /opt/w2g 2>/dev/null || echo absent
echo "--- nginx baseline ---"
sudo nginx -t
CONF=$(sudo readlink -f /etc/nginx/sites-enabled/vergoboy.ir.conf); echo "CONF=$CONF"
sudo sha256sum "$CONF"          # must match bytes 2dc0… / b219… recorded in DEPLOY_LOG
echo "--- server{} block count (include helper assumes 1) ---"
sudo grep -c '^server' "$CONF"
echo "--- location inventory (answers open question 1) ---"
sudo grep -n 'location' "$CONF"
echo "--- toolchain ---"
/usr/bin/python3 -V; /usr/bin/node -v; /usr/bin/npm -v
test -x /opt/stream/venv/bin/python3 && echo "python3-venv works here (stream has one)"
echo "--- stream baseline (must stay like this) ---"
systemctl is-active vergoboy-stream vergoboy-frontend-build
curl -sS -o /dev/null -w 'stream HTTP %{http_code}\n' https://vergoboy.ir/stream/
REMOTE
```

Abort conditions: `/opt` free < 20 GB; 8802 occupied; `nginx -t` failing;
`vergoboy-stream` not active; sha256 of the vhost not matching the recorded
baseline. **Re-measure and append the fresh numbers to `docs/DEPLOY_LOG.md`.**

### S1 — directories

```bash
TS=$(SSHPASS='***' sshpass -e ssh "$HOST" 'date -u +%Y%m%dT%H%M%SZ')  # the server's clock
REL=/opt/w2g/releases/$TS
echo "release $REL    # note this down for DEPLOY_LOG.md"
SSHPASS='***' sshpass -e ssh "$HOST" "
  sudo install -d -o www-data -g www-data -m 0755 \
       /opt/w2g /opt/w2g/releases /opt/w2g/media /opt/w2g/data '$REL'"
```

`REL` is a *local* variable from here on: S2 uses it to pick the extraction
directory, every later step passes it as `$1`. The release directory is created
empty and owned by `www-data` now, so S2's `tar` and S3's `venv` do not need
root beyond the `sudo` they already use.

### S2 — release content (local → server)

```bash
git rev-parse HEAD                      # <sha> — the release identity, recorded in DEPLOY_LOG
git archive --format=tar HEAD | SSHPASS='***' sshpass -e ssh "$HOST" \
  "sudo tar -xf - -C $REL && sudo chown -R www-data:www-data $REL"
```

The release carries no `.git`, by design: what is deployed is the tree of that
one commit, and the sha is recorded in the log rather than inside the release.

`git archive` ships tracked files only, so `webapp/out`, `node_modules` and the
local venv stay behind; the export is produced in S4 and the venv in S3.

### S3 — virtualenv

```bash
sudo -u www-data env HOME=/opt/w2g /usr/bin/python3 -m venv "$REL/venv"
sudo -u www-data env HOME=/opt/w2g "$REL/venv/bin/python" -m pip install \
     --upgrade --no-cache-dir pip wheel
sudo -u www-data env HOME=/opt/w2g "$REL/venv/bin/pip" install \
     --no-cache-dir -r "$REL/requirements.lock.txt"
sudo -u www-data "$REL/venv/bin/python" -c "import flask, gevent, psycopg2; print('deps ok')"
```

### S4 — static export (web target, mounted at `/w2g`)

Reused workspace, mirroring `/opt/stream/webapp`:

```bash
sudo install -d -o www-data -g www-data -m 0755 /opt/w2g/webapp /opt/w2g/.npm-cache
sudo rsync -a --delete --exclude node_modules --exclude out --exclude .next \
     "$REL/webapp/" /opt/w2g/webapp/
cd /opt/w2g/webapp
sudo -u www-data env HOME=/opt/w2g npm_config_cache=/opt/w2g/.npm-cache \
     NEXT_PUBLIC_BUILD_TARGET=web NEXT_PUBLIC_BASE_PATH=/w2g \
     /usr/bin/npm ci --no-audit --no-fund
sudo -u www-data env HOME=/opt/w2g npm_config_cache=/opt/w2g/.npm-cache \
     NEXT_PUBLIC_BUILD_TARGET=web NEXT_PUBLIC_BASE_PATH=/w2g \
     /usr/bin/npm run build
sudo -u www-data rsync -a --delete /opt/w2g/webapp/out/ "$REL/webapp-out/"
```

Post-condition (must be proven, not assumed):

```bash
grep -rho '/w2g' "$REL/webapp-out" | wc -l   # > 0
grep -rho '/stream'  "$REL/webapp-out" | wc -l   # == 0
grep -rho '/api/api' "$REL/webapp-out" | wc -l   # == 0
```

The same three checks passed on the locally built export at T1 (0 `/stream`,
0 `/api/api`) and on the desktop export at T2.

### S5 — database and role (never touches `stream`)

```bash
# S5 and S6 must run in the same shell, because $PGPASS is generated here and
# consumed there. The SQL goes in over stdin so the password never appears in
# this host's process list.
PGPASS=$(openssl rand -hex 24)        # generated on the server, never printed
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='w2g')
    THEN CREATE ROLE w2g LOGIN PASSWORD '$PGPASS';
    ELSE ALTER ROLE w2g WITH LOGIN PASSWORD '$PGPASS';
  END IF;
END \$\$;
SQL
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='w2g'" | grep -q 1 \
  || sudo -u postgres createdb -O w2g -E UTF8 w2g
sudo -u postgres psql -tAc "SELECT rolname FROM pg_roles WHERE rolname='w2g'"
```

`ALTER ROLE … PASSWORD` on re-run is what keeps the DB and the env file in step.

### S6 — secrets file

Written with a heredoc on the server, mode `0600`, owner `www-data`, never
echoed. Non-secret production values are copied from the stream instance's env
file **without printing it** — a named-key read, e.g.
`sudo grep -E '^(STREAM_ADMIN_EMAIL|STREAM_ARCHIVE_HTTP_PROXY)=' /opt/stream/.stream_db.env`
— while the two secrets are generated fresh. `w2g` gets its **own**
`STREAM_JWT_SECRET` so a token minted by one instance is rejected by the other.
**This step must share the shell with S5** (it uses `$PGPASS`).

```bash
sudo -u www-data env HOME=/opt/w2g /usr/bin/python3 - <<PY
import os, secrets
path = "/opt/w2g/.stream_db.env"
lines = [
  "STREAM_ENV=production",
  "STREAM_DATABASE_URL=postgresql+psycopg2://w2g:$PGPASS@127.0.0.1:5432/w2g",
  "STREAM_SECRET_KEY=" + secrets.token_hex(32),
  "STREAM_JWT_SECRET=" + secrets.token_hex(32),
  "STREAM_ADMIN_EMAIL=*** (same admin as the original instance)",
  "STREAM_ARCHIVE_HTTP_PROXY=http://127.0.0.1:10808",
]
if os.path.exists(path) and os.path.getsize(path) > 0:
    print("exists — leaving untouched (idempotent)"); raise SystemExit
open(path, "w").write("\\n".join(lines) + "\\n")
print("wrote", path)
PY
sudo chown www-data:www-data /opt/w2g/.stream_db.env
sudo chmod 600 /opt/w2g/.stream_db.env
sudo grep -c . /opt/w2g/.stream_db.env     # count lines only — never the values
```

`STREAM_JWT_SECRET` differs per instance, so a token minted by one instance is
rejected by the other (`iss`/`aud` carry the instance name — see ADR 0003).

### S7 — `current` symlink, prune to 3

The symlink must exist before the unit starts: `WorkingDirectory=/opt/w2g/current`
and `ExecStart=/opt/w2g/current/venv/bin/python3` both dereference it.

```bash
sudo ln -sfn "$REL" /opt/w2g/current.new
sudo mv -Tf /opt/w2g/current.new /opt/w2g/current     # atomic rename, no window
ls -l /opt/w2g/current
# keep the 3 newest, delete the rest (never touches media/ or data/)
sudo bash -c 'ls -1dt /opt/w2g/releases/*/ | tail -n +4 | xargs -r rm -rf'
ls -1dt /opt/w2g/releases/*/
```

### S8 — start the backend (before nginx points at it)

```bash
sudo install -m 0644 deploy/vergoboy-w2g.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vergoboy-w2g.service
sleep 2
systemctl is-active vergoboy-w2g.service
sudo journalctl -u vergoboy-w2g.service -n 40 --no-pager
curl -sS http://127.0.0.1:8802/w2g/api/health | head -c 400; echo
```

The unit starts before the public location exists, so a broken backend cannot
take nginx down with it.

### S9 — public location (reversible, one line + one snippet)

```bash
CONF=$(sudo readlink -f /etc/nginx/sites-enabled/vergoboy.ir.conf)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
sudo cp -a "$CONF" "$CONF.bak.$STAMP"
sudo install -d -m 0755 /etc/nginx/snippets
sudo install -o root -g root -m 0644 deploy/nginx-w2g.conf /etc/nginx/snippets/vergoboy-w2g.conf

sudo python3 - "$CONF" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); src = p.read_text()
line = "    include /etc/nginx/snippets/vergoboy-w2g.conf;\n"
if "vergoboy-w2g.conf" in src:
    print("include already present — no change"); raise SystemExit
i = src.rstrip().rfind("}")           # the closing brace of the single server{}
p.write_text(src[:i].rstrip("\n") + "\n" + line + "\n" + src[i:])
print("inserted include before the final brace")
PY

sudo diff -u "$CONF.bak.$STAMP" "$CONF" | head -20   # must show exactly +1 line
sudo nginx -t || { sudo cp -a "$CONF.bak.$STAMP" "$CONF"; echo "reverted; nginx -t failed"; exit 1; }
sudo systemctl reload nginx
```

Note the snippet deliberately does **not** re-declare `/hls/`, `/whip/` or
`/livekit/rtc/` — they are already in the same `server{}` block and a duplicate
`location` would fail `nginx -t`.

### S10 — verification (must pass before this is called done)

```bash
echo "--- new instance ---"
curl -sS -o /dev/null -w 'GET /w2g/              -> %{http_code}\n' https://vergoboy.ir/w2g/
curl -sS -o /dev/null -w 'GET /w2g (no slash)    -> %{http_code}\n' https://vergoboy.ir/w2g
curl -sS -o /dev/null -w 'GET /w2g/api/health    -> %{http_code}\n' https://vergoboy.ir/w2g/api/health
curl -sS https://vergoboy.ir/w2g/api/health | head -c 300; echo
curl -sS 'https://vergoboy.ir/w2g/socket.io/?EIO=4&transport=polling' | head -c 80; echo
ASSET=$(curl -sS https://vergoboy.ir/w2g/ | grep -o '/w2g/_next/[^"]*\.js' | head -1)
curl -sS -o /dev/null -w "GET $ASSET -> %{http_code}\n" "https://vergoboy.ir$ASSET"

echo "--- the original instance is untouched ---"
curl -sS -o /dev/null -w 'GET /stream/           -> %{http_code}\n' https://vergoboy.ir/stream/
curl -sS -o /dev/null -w 'GET /stream/api/health -> %{http_code}\n' https://vergoboy.ir/stream/api/health
systemctl is-active vergoboy-stream vergoboy-frontend-build nginx postgresql
sudo nginx -t
```

Pass criteria: `/w2g/` and `/w2g/api/health` = 200 (health body
`{"status":"healthy"}`), socket handshake returns an Engine.IO open packet,
a hashed asset loads, and **every** `/stream` line is unchanged from S0.

### S11 — rollback (used only if S10 fails)

```bash
CONF=$(sudo readlink -f /etc/nginx/sites-enabled/vergoboy.ir.conf)
sudo cp -a "$CONF.bak.<stamp>" "$CONF"          # remove the include line
sudo rm -f /etc/nginx/snippets/vergoboy-w2g.conf
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl disable --now vergoboy-w2g.service
# optional, and only ever after the above:
#   sudo rm -rf /opt/w2g/releases/<ts>     (media/ and data/ are left alone)
```

Recovery of the release itself is `sudo mv -Tf` of `current` back to the
previous `releases/<ts>`; the DB can stay (it costs nothing and dropping it
loses nothing either way during the first deploy).

---

## 6. Why this is safe for `/stream`

- No step writes under `/opt/stream`; `ReadWritePaths` is scoped to `/opt/w2g`.
- The `stream` **database and role are never modified** (S0 reads their names
  only; S5 creates `w2g`).
- No unit belonging to `/stream` is restarted, disabled or edited.
- The only change to shared nginx state is one `include` line, backed up with a
  timestamped copy and a recorded sha256, and removed first thing in rollback.
- `nginx -t` gates the reload, so a bad edit leaves the running config alone.
- Duplicate instance-global locations are impossible: the snippet declares none
  of `/hls/`, `/whip/`, `/livekit/rtc/`.

## 7. What is still open (for approval)

1. **SSH password** — key auth is rejected (`Permission denied
   (publickey,password)`), so T4 needs the password supplied inline. It is
   never written to a file, log or commit.
2. **D7 (OAuth off for v1)** — confirm that email+password login is acceptable
   for the first public day at `/w2g`.
3. **D5 (server-side `npm ci`)** — confirm, or switch to building the export
   locally and uploading `webapp/out` (faster, but the deploy then depends on
   the operator's machine).
4. **`/opt/w2g` state ownership** — `www-data`, matching `/opt/stream`. The
   database role is `w2g`; the files are `www-data`.
