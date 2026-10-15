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

