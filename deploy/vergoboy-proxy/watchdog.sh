#!/bin/bash
# Exit-IP watchdog for the userspace VPN proxy (vergoboy-proxy).
#
# The health check is the exit IP seen THROUGH the proxy, never DNS: DNS keeps
# answering from the host resolver whether or not the tunnel carries traffic,
# and a DNS-based check would call a dead tunnel healthy.  A probe that comes
# back empty or equal to the host IP means the tunnel is not working:
#
#   - empty          -> wedge; restart after 2 consecutive failures so one
#                       slow probe cannot loop the container
#   - host IP        -> impossible by construction (the client only ever
#                       talks to the proxy), asserted anyway; restart at once,
#                       a leak is not a flaky probe
#
# The container runs with `restart: unless-stopped`, so death is self-healing;
# this watchdog exists for the case that never self-heals: still running, no
# longer forwarding.
set -uo pipefail
DIR=/opt/vergoboy-proxy
STATE=$DIR/watchdog.state
HOSTIP=$DIR/host_exit_ip
PROXY=http://127.0.0.1:10808

host_ip=$(cat "$HOSTIP" 2>/dev/null)
if [ -z "$host_ip" ]; then
  host_ip=$(curl -sS --max-time 8 https://api.ipify.org 2>/dev/null)
  if [ -n "$host_ip" ]; then
    printf '%s\n' "$host_ip" >"$HOSTIP"
    chmod 600 "$HOSTIP"
  fi
fi

if ! docker ps --format '{{.Names}}' | grep -qx vergoboy-proxy; then
  # Absent is either not-yet-created (first boot, before compose up) or an
  # intentional stop; `unless-stopped` handles neither, and forcing it here
  # would fight an operator.  Report and leave it.
  echo "vergoboy-proxy container is not running" >&2
  rm -f "$STATE"
  exit 0
fi

ip=$(curl -sS -x "$PROXY" --max-time 12 https://api.ipify.org 2>/dev/null)

if [ -n "$ip" ] && { [ -z "$host_ip" ] || [ "$ip" != "$host_ip" ]; }; then
  rm -f "$STATE"
  exit 0
fi

why="no exit ip through the proxy"
[ -n "$ip" ] && why="proxy is egressing on the host ip ($host_ip)"

fails=0
[ -r "$STATE" ] && fails=$(cat "$STATE" 2>/dev/null || echo 0)
case "$fails" in (*[!0-9]*) fails=0 ;; esac
fails=$((fails + 1))
printf '%s\n' "$fails" >"$STATE"
echo "$why (failure $fails)" >&2

if [ -n "$ip" ] || [ "$fails" -ge 2 ]; then
  echo "restarting vergoboy-proxy" >&2
  docker restart vergoboy-proxy >/dev/null 2>&1 || true
  rm -f "$STATE"
fi
exit 1
