#!/usr/bin/env python3
"""Render the userspace VPN-proxy config for the vergoboy apps.

One sing-box container, two loopback listeners:

    http  127.0.0.1:10808   STREAM_ARCHIVE_HTTP_PROXY, STREAM_YTDLP_PROXY
    socks 127.0.0.1:10809   manual use / diagnostics

The VPN endpoints come from the SAME subscription noisy uses, and the parsing
lives in /opt/noisy/egress/build_configs.py -- imported in place, never copied
(the sibling-deployment rule from the repo AGENTS.md).  This module only adds
what the TUN-shaped noisy config does not need: userspace inbounds instead of
a TUN, a runtime urltest failover group instead of a build-time primary, and
DNS that answers through the tunnel (poisoned local answers for blocked
domains must never reach the routing table).

No secret is ever written to stdout.
"""
import json
import os
import sys

NOISY_EGRESS = "/opt/noisy/egress"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sing-box.json")

# Private/multicast space never belongs in a tunnel: if a caller ever asks the
# proxy for one of these, dial it straight from the host.
DIRECT_CIDR = [
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4",
    "::1/128", "fc00::/7", "fe80::/10",
]

sys.path.insert(0, NOISY_EGRESS)
import build_configs as bc  # noqa: E402  (import has a __main__ guard)


def main() -> None:
    entries = bc.parse_subscription()
    outbounds, seen = [], set()
    for e in entries:
        if bc.xray_only(e):
            # XHTTP / ML-KEM stay noisy's xray's job; the apps do not need
            # them and this config must not nest one engine in another.
            continue
        ob = bc.singbox_outbound(e)
        if ob["tag"] in seen:  # duplicate tags are rejected by sing-box check
            continue
        seen.add(ob["tag"])
        outbounds.append(ob)
    if not outbounds:
        sys.exit("subscription has no sing-box-capable outbounds")

    tags = [ob["tag"] for ob in outbounds]
    outbounds += [
        # Runtime failover: first healthy candidate wins.  noisy picks its
        # primary at build time; a failover here must not need a rebuild, or
        # the watchdog would fight a legitimately changed exit IP.
        {
            "type": "urltest", "tag": "auto", "outbounds": tags,
            "url": "https://www.gstatic.com/generate_204",
            "interval": "5m", "tolerance": 50,
        },
        {"type": "direct", "tag": "direct"},
    ]

    cfg = {
        "log": {"level": "warn", "timestamp": True},
        # Inside the container sing-box MUST listen on 0.0.0.0: docker-proxy
        # forwards the published port to the container's eth0, never to its
        # loopback -- a 127.0.0.1 bind here means every host-side connection
        # is reset before sing-box ever sees it (measured 2026-10-16: RST,
        # zero logs).  Host-loopback-only is the `127.0.0.1:` port publish in
        # docker-compose.yml, which is the boundary that actually holds.
        "inbounds": [
            {"type": "http", "tag": "http-in",
             "listen": "0.0.0.0", "listen_port": 10808},
            {"type": "socks", "tag": "socks-in",
             "listen": "0.0.0.0", "listen_port": 10809},
        ],
        "outbounds": outbounds,
        "route": {
            "rules": [
                {"action": "route", "ip_cidr": DIRECT_CIDR,
                 "outbound": "direct"},
            ],
            "final": "auto",
            # Bootstrap only (VPN server hostnames, urltest probe host):
            # these domains are not censored and must resolve before -- and
            # independently of -- the tunnel.
            "default_domain_resolver": {"server": "dns-local"},
        },
        "dns": {
            "servers": [
                {"type": "local", "tag": "dns-local"},
                # Everything else answers THROUGH the tunnel: a poisoned local
                # answer for googlevideo.com would otherwise be dialed via the
                # tunnel and fail there, looking like a tunnel outage.
                {"type": "udp", "tag": "dns-tunnel",
                 "server": "1.1.1.1", "detour": "auto"},
            ],
            "final": "dns-tunnel",
            "strategy": "prefer_ipv4",
        },
    }

    fd = os.open(OUT + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(cfg, fh, indent=1)
    os.replace(OUT + ".tmp", OUT)
    os.chmod(OUT, 0o600)
    print(f"wrote {OUT}: {len(tags)} sing-box outbounds -> auto "
          f"({', '.join(tags)})")


if __name__ == "__main__":
    main()
