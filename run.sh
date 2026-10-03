#!/usr/bin/env bash
# vergoboy-stream — local development runner (no systemd, no nginx).
#
#   ./run.sh setup     first-time setup: venv, deps, .stream_db.env, database
#   ./run.sh start     postgres + backend (:8801) + frontend (:3000)
#   ./run.sh stop      stop everything started by this script
#   ./run.sh restart   stop, then start
#   ./run.sh status    what is running
#   ./run.sh logs [backend|frontend|postgres]   tail -f a log (default: backend)
#
# Everything lives under $DEVDIR except the venv and node_modules in the repo.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

DEVDIR="$HOME/.local/share/vergoboy-stream-dev"
RUNDIR="$DEVDIR/run"
PGDATA="$DEVDIR/pgdata"
PGSTASH="$DEVDIR/pgserver"
VENV="$ROOT/venv"
PY="$VENV/bin/python3"
PGBIN=""

BACKEND_PID="$RUNDIR/backend.pid"
FRONTEND_PID="$RUNDIR/frontend.pid"
BACKEND_LOG="$DEVDIR/backend.log"
FRONTEND_LOG="$DEVDIR/frontend.log"
PGLOG="$DEVDIR/postgres.log"
LIVEKIT_DIR="$DEVDIR/livekit"
LIVEKIT_BIN="$LIVEKIT_DIR/livekit-server"
LIVEKIT_CONFIG="$LIVEKIT_DIR/config.yaml"
LIVEKIT_PID="$RUNDIR/livekit.pid"
LIVEKIT_LOG="$DEVDIR/livekit.log"
LIVEKIT_VERSION="1.13.7"
LIVEKIT_SHA256="6634aeeb2fb1366b6723708ae4320b9d5408106a4c63457c5e845ae3979c90e2"

BACKEND_PORT=8801
FRONTEND_PORT=3000
PGPORT=5432

mkdir -p "$RUNDIR"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

# pgserver (a PyPI wheel shipping PostgreSQL binaries) is how dev gets a
# server without root. Its binaries are cached in $PGSTASH so recreating the
# venv — e.g. `uv venv` replacing it — can never take PostgreSQL away again.
find_pgbin() {
  local cand
  [ -x "$PGSTASH/pginstall/bin/pg_ctl" ] && { PGBIN="$PGSTASH/pginstall/bin"; return 0; }
  for cand in "$VENV"/lib/python*/site-packages/pgserver/pginstall/bin; do
    [ -x "$cand/pg_ctl" ] && { PGBIN="$cand"; return 0; }
  done
  for cand in /usr/lib/postgresql/*/bin /usr/pgsql-*/bin; do
    [ -x "$cand/pg_ctl" ] && { PGBIN="$cand"; return 0; }
  done
  command -v pg_ctl >/dev/null 2>&1 && { PGBIN="$(dirname "$(command -v pg_ctl)")"; return 0; }
  return 1
}

# The wheel's binaries carry RPATH=$ORIGIN/../../../pgserver.libs, so the copy
# must keep the wheel's layout (pgserver/ next to pgserver.libs/) or the loader
# cannot find libpq and every binary fails to start.
stash_pgbins() {
  local site
  for site in "$VENV"/lib/python*/site-packages; do
    [ -x "$site/pgserver/pginstall/bin/pg_ctl" ] || continue
    [ -x "$PGSTASH/pginstall/bin/pg_ctl" ] && return 0
    say "caching PostgreSQL binaries -> $DEVDIR"
    rm -rf "$PGSTASH" "$DEVDIR/pgserver.libs"
    cp -a "$site/pgserver" "$DEVDIR/"
    [ -d "$site/pgserver.libs" ] && cp -a "$site/pgserver.libs" "$DEVDIR/"
    [ -x "$PGSTASH/pginstall/bin/pg_ctl" ] || return 1
    return 0
  done
  return 1
}

pid_alive() { [ -n "${1:-}" ] && [ -d "/proc/$1" ]; }
pid_of()    { [ -f "$1" ] && cat "$1" || true; }

port_busy() { ss -ltn 2>/dev/null | grep -q ":$1\b"; }
port_pid()  { ss -ltnp 2>/dev/null | grep ":$1\b" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2; }
pgid_of()   { ps -o pgid= -p "$1" 2>/dev/null | tr -d ' '; }

wait_for_port() {
  local port="$1" name="$2" i
  for i in $(seq 1 60); do
    port_busy "$port" && { say "$name is listening on :$port"; return 0; }
    sleep 0.5
  done
  return 1
}

load_env() {
  [ -f "$ROOT/.stream_db.env" ] || die "missing .stream_db.env — run: ./run.sh setup"
  unset STREAM_LIVEKIT_API_KEY STREAM_LIVEKIT_API_SECRET STREAM_LIVEKIT_URL STREAM_LIVEKIT_ROOM
  set -a; . "$ROOT/.stream_db.env"; set +a
}

pg_start() {
  find_pgbin || die "no PostgreSQL binaries found (pgserver missing from the venv) — run: ./run.sh setup"
  if [ -f "$PGDATA/postmaster.pid" ] && port_busy "$PGPORT"; then
    say "postgres already running on :$PGPORT"; return 0
  fi
  if [ ! -f "$PGDATA/PG_VERSION" ]; then
    say "initdb -> $PGDATA"
    "$PGBIN/initdb" -D "$PGDATA" -U stream --auth-local=trust --auth-host=scram-sha-256 -E UTF8 \
      > "$DEVDIR/initdb.log" 2>&1
  fi
  mkdir -p "$DEVDIR"
  "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGLOG" -o "-p $PGPORT -k /tmp -h 127.0.0.1" -w start >/dev/null
  port_busy "$PGPORT" || { tail -5 "$PGLOG" >&2; die "postgres failed to start"; }
  if [[ -n "${STREAM_PG_PASSWORD:-}" ]]; then
    "$PGBIN/psql" -h /tmp -p "$PGPORT" -U stream -d postgres -q \
      -c "ALTER USER stream WITH PASSWORD '$STREAM_PG_PASSWORD';"
  fi
  "$PGBIN/psql" -h /tmp -p "$PGPORT" -U stream -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='stream'" 2>/dev/null | grep -q 1 \
    || "$PGBIN/createdb" -h /tmp -p "$PGPORT" -U stream stream
  say "postgres ready on 127.0.0.1:$PGPORT (db 'stream')"
}

pg_stop() {
  find_pgbin || return 0
  [ -f "$PGDATA/postmaster.pid" ] || return 0
  "$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w stop >/dev/null 2>&1 && say "postgres stopped" || true
}

livekit_install() {
  [ -x "$LIVEKIT_BIN" ] && return 0
  local archive="$DEVDIR/livekit-${LIVEKIT_VERSION}-linux-amd64.tar.gz"
  mkdir -p "$LIVEKIT_DIR"
  say "downloading official LiveKit Server v$LIVEKIT_VERSION"
  net_env curl -fsSL --retry 2 \
    "https://github.com/livekit/livekit/releases/download/v$LIVEKIT_VERSION/livekit_${LIVEKIT_VERSION}_linux_amd64.tar.gz" \
    -o "$archive"
  printf '%s  %s\n' "$LIVEKIT_SHA256" "$archive" | sha256sum -c -
  tar -xzf "$archive" -C "$LIVEKIT_DIR" livekit-server
  chmod 0755 "$LIVEKIT_BIN"
  rm -f "$archive"
}

livekit_start() {
  local pid; pid="$(pid_of "$LIVEKIT_PID")"
  if pid_alive "$pid"; then say "LiveKit already running (pid $pid)"; return 0; fi
  port_busy 7880 && die ":7880 is already in use by pid $(port_pid) — not starting a second SFU"
  livekit_install
  if [ ! -f "$LIVEKIT_CONFIG" ]; then
    local api_key api_secret
    api_key="$(openssl rand -hex 12)"
    api_secret="$(openssl rand -hex 32)"
    ( umask 077; cat > "$LIVEKIT_CONFIG" <<EOF
port: 7880
log_level: info
rtc:
  tcp_port: 7881
  port_range_start: 50000
  port_range_end: 60000
  use_external_ip: false
keys:
  $api_key: $api_secret
EOF
    )
  fi
  if ! grep -q '^STREAM_LIVEKIT_URL=' "$ROOT/.stream_db.env"; then
    printf 'STREAM_LIVEKIT_URL=ws://127.0.0.1:7880\n' >> "$ROOT/.stream_db.env"
  fi
  claim_port 7880 "LiveKit SFU"
  setsid nohup "$LIVEKIT_BIN" --config "$LIVEKIT_CONFIG" > "$LIVEKIT_LOG" 2>&1 < /dev/null &
  wait_for_port 7880 "LiveKit SFU" \
    || { tail -20 "$LIVEKIT_LOG" >&2; die "LiveKit failed to start (see $LIVEKIT_LOG)"; }
  port_pid 7880 > "$LIVEKIT_PID"
  curl -fsS --max-time 3 http://127.0.0.1:7880/ >/dev/null \
    || { tail -20 "$LIVEKIT_LOG" >&2; die "LiveKit HTTP health endpoint failed"; }
}

livekit_stop() {
  kill_pidfile "$LIVEKIT_PID" "LiveKit SFU" 7880
}

kill_pidfile() {
  local file="$1" name="$2" port="$3" pid pgid
  pid="$(pid_of "$file")"
  if pid_alive "$pid"; then
    pgid="$(pgid_of "$pid")"; [ -n "$pgid" ] || pgid="$pid"
    kill -TERM -- "-$pgid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 40); do port_busy "$port" || break; sleep 0.25; done
    if port_busy "$port"; then
      kill -KILL -- "-$pgid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
      sleep 1
    fi
    say "$name stopped (pid $pid)"
  elif port_busy "$port"; then
    warn "$name pid file stale but :$port is held by pid $(port_pid "$port") — not started by this script"
  fi
  rm -f "$file"
}

claim_port() {
  local port="$1" name="$2"
  port_busy "$port" && die ":$port is already in use by pid $(port_pid) (not started by run.sh) — free it, then retry"
  say "starting $name"
}

backend_start() {
  local pid; pid="$(pid_of "$BACKEND_PID")"
  if pid_alive "$pid"; then say "backend already running (pid $pid)"; return 0; fi
  [ -x "$PY" ] || die "venv missing — run: ./run.sh setup"
  load_env
  [ -d "$ROOT/data/rooms" ] || mkdir -p "$ROOT/data/rooms"
  claim_port "$BACKEND_PORT" "backend"
  PATH="$VENV/bin:$PATH" setsid nohup "$PY" app.py > "$BACKEND_LOG" 2>&1 < /dev/null &
  wait_for_port "$BACKEND_PORT" "backend" \
    || { tail -20 "$BACKEND_LOG" >&2; die "backend failed to start (see $BACKEND_LOG)"; }
  port_pid "$BACKEND_PORT" > "$BACKEND_PID"
}

frontend_start() {
  local pid; pid="$(pid_of "$FRONTEND_PID")"
  if pid_alive "$pid"; then say "frontend already running (pid $pid)"; return 0; fi
  [ -d "$ROOT/webapp/node_modules" ] || die "webapp/node_modules missing — run: cd webapp && npm ci"
  if port_busy "$FRONTEND_PORT"; then
    local owner; owner="$(port_pid "$FRONTEND_PORT")"
    local command; command="$(ps -p "$owner" -o args= 2>/dev/null || true)"
    [[ "$command" == *next-server* || "$command" == *"next dev"* ]] \
      && { say "frontend already running externally (pid $owner)"; return 0; }
  fi
  claim_port "$FRONTEND_PORT" "frontend"
  ( cd "$ROOT/webapp" && setsid nohup npm run dev > "$FRONTEND_LOG" 2>&1 < /dev/null & )
  wait_for_port "$FRONTEND_PORT" "frontend" \
    || { tail -20 "$FRONTEND_LOG" >&2; die "frontend failed to start (see $FRONTEND_LOG)"; }
  port_pid "$FRONTEND_PORT" > "$FRONTEND_PID"
}

# This machine cannot reach files.pythonhosted.org directly, so every network
# step goes through the local sing-box proxy. Override by exporting your own.
net_env() { HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:10808}" HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:10808}" "$@"; }

have_uv() { command -v uv >/dev/null 2>&1; }

setup_venv() {
  if [ -x "$PY" ]; then
    say "reusing existing venv"
  elif have_uv; then
    uv python install 3.12 >/dev/null 2>&1 || true
    # No --seed: nothing needs pip *inside* the venv, uv installs into it directly.
    # --allow-existing: never prompt, never silently replace an existing venv.
    uv venv --allow-existing --python 3.12 "$VENV"
  else
    python3 -m venv "$VENV"
  fi
}

py_install() {
  if have_uv; then net_env uv pip install --python "$PY" -q "$@"
  else net_env "$PY" -m pip install -q "$@"; fi
}

cmd_setup() {
  setup_venv
  say "installing backend dependencies from the development lock"
  if have_uv; then
    net_env uv pip sync --python "$PY" "$ROOT/requirements-dev.lock.txt"
  else
    "$PY" -m pip install -r "$ROOT/requirements-dev.lock.txt"
  fi
  stash_pgbins || warn "pgserver installed but its binaries could not be cached"
  if [ -d "$ROOT/webapp/node_modules" ]; then
    say "frontend dependencies already installed (delete webapp/node_modules to force npm ci)"
  else
    say "installing frontend dependencies"
    ( cd webapp && npm ci )
  fi
  if [ ! -f "$ROOT/.stream_db.env" ]; then
    local db_password
    db_password="$(openssl rand -hex 32)"
    cat > "$ROOT/.stream_db.env" <<EOF
STREAM_DATABASE_URL=postgresql+psycopg2://stream:$db_password@127.0.0.1:$PGPORT/stream
STREAM_PG_PASSWORD=$db_password
STREAM_ENV=development
STREAM_SECRET_KEY=$(openssl rand -hex 32)
STREAM_JWT_SECRET=$(openssl rand -hex 32)
STREAM_ADMIN_EMAIL=the.arman.hosseini@gmail.com
STREAM_YTDLP_PROXY=socks5://127.0.0.1:10808
EOF
    say "wrote .stream_db.env"
  fi
  if ! grep -q '^STREAM_LIVEKIT_URL=' "$ROOT/.stream_db.env"; then
    printf 'STREAM_LIVEKIT_URL=ws://127.0.0.1:7880\n' >> "$ROOT/.stream_db.env"
  fi
  [ -f "$ROOT/webapp/.env.local" ] || printf 'NEXT_PUBLIC_API_ORIGIN=http://127.0.0.1:%s\n' "$BACKEND_PORT" \
    > "$ROOT/webapp/.env.local"
  load_env
  pg_start
  say "setup done — now: ./run.sh start"
}

cmd_start() {
  load_env
  pg_start
  livekit_start
  backend_start
  frontend_start
  printf '\n  frontend (use this) : http://localhost:%s/stream/\n' "$FRONTEND_PORT"
  printf '  backend / API       : http://127.0.0.1:%s\n' "$BACKEND_PORT"
  printf '  logs                : %s , %s\n\n' "$BACKEND_LOG" "$FRONTEND_LOG"
}

cmd_stop() {
  kill_pidfile "$FRONTEND_PID" "frontend" "$FRONTEND_PORT"
  kill_pidfile "$BACKEND_PID" "backend" "$BACKEND_PORT"
  livekit_stop
  pg_stop
}

cmd_status() {
  local pid
  pid="$(pid_of "$BACKEND_PID")"
  if pid_alive "$pid"; then say "backend   running (pid $pid) :$BACKEND_PORT"; elif port_busy "$BACKEND_PORT"; then say "backend   running externally :$BACKEND_PORT"; else warn "backend   stopped"; fi
  pid="$(pid_of "$FRONTEND_PID")"
  if pid_alive "$pid"; then say "frontend  running (pid $pid) :$FRONTEND_PORT"; elif port_busy "$FRONTEND_PORT"; then say "frontend  running externally :$FRONTEND_PORT"; else warn "frontend  stopped"; fi
  if port_busy "$PGPORT"; then say "postgres  running :$PGPORT"; else warn "postgres  stopped"; fi
  if port_busy 7880; then say "LiveKit   running :7880"; else warn "LiveKit   stopped"; fi
}

cmd_logs() {
  case "${1:-backend}" in
    backend)  tail -n 50 -f "$BACKEND_LOG" ;;
    frontend) tail -n 50 -f "$FRONTEND_LOG" ;;
    postgres) tail -n 50 -f "$PGLOG" ;;
    livekit)  tail -n 50 -f "$LIVEKIT_LOG" ;;
    *) die "usage: ./run.sh logs [backend|frontend|postgres|livekit]" ;;
  esac
}

case "${1:-start}" in
  setup)   cmd_setup ;;
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  logs)    cmd_logs "${2:-}" ;;
  *) die "usage: ./run.sh {setup|start|stop|restart|status|logs [backend|frontend|postgres|livekit]}" ;;
esac