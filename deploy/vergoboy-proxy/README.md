# vergoboy-proxy — userspace VPN proxy for the vergoboy apps

`vergoboy.ir`'s host censors YouTube, googlevideo **and** digimoviez (all
time out / reset from this box), so the app's archive scraper and yt-dlp
cannot work direct. This container reuses noisy's VPN subscription and
presents it as two loopback listeners:

| Listener | Used by |
| --- | --- |
| `http://127.0.0.1:10808` | `STREAM_ARCHIVE_HTTP_PROXY` and `STREAM_YTDLP_PROXY` of the app |
| `socks5://127.0.0.1:10809` | manual use / diagnostics |

Deliberately **not** a TUN like noisy's egress: the apps opt in per setting,
host routing is untouched, and the media pipeline (ffmpeg/ffprobe) stays
direct by the app's own design (`direct_media_environment()`).

## Layout

| Path | What it is |
| --- | --- |
| `sing-box.json` | generated config, 0600 — secrets live here |
| `build_config.py` | renders it from `/opt/noisy/secrets/subscription.txt` via `/opt/noisy/egress/build_configs.py` (imported in place, never copied) |
| `docker-compose.yml` | the container (loopback-only publish, cap_drop ALL) |
| `watchdog.sh` + `vergoboy-proxy-watchdog.timer` | exit-IP probe every minute |
| `proxy-status` | one-shot live status |
| `host_exit_ip`, `watchdog.state` | watchdog state |

## Operating

```sh
sudo /opt/vergoboy-proxy/proxy-status          # container, exit ip, probes
sudo systemctl restart vergoboy-proxy-watchdog.timer   # after stopping it
sudo docker restart vergoboy-proxy              # manual tunnel restart
sudo python3 /opt/vergoboy-proxy/build_config.py       # re-render config
sudo docker compose -f /opt/vergoboy-proxy/docker-compose.yml up -d
```

Switching VPN routes is not exposed: the config runs a **urltest** group
(auto-failover over every sing-box-capable subscription entry), so the active
route is health-selected at runtime and a changed exit IP is expected, not a
fault. The watchdog only asserts what must never happen — the proxy answering
with the **host's** IP — and repairs a wedged tunnel after two failed probes.

Re-render **must** be followed by `sing-box check` (see docs/DEPLOY_LOG.md,
session 6) before `up -d`: the generated file carries the subscription's
credentials and a bad render should never reach a running container untested.

## Wiring an app

```
STREAM_ARCHIVE_HTTP_PROXY=http://127.0.0.1:10808
STREAM_YTDLP_PROXY=http://127.0.0.1:10808
```

Both apps currently wired: `/opt/w2g/.stream_db.env` (2026-10-16). The
`/stream` instance was deliberately left alone (it is frozen until its own
deploy).

## Dependencies (accepted, documented)

- `/opt/noisy/secrets/subscription.txt` and `/opt/noisy/egress/build_configs.py`
  — if noisy moves, this builder breaks loudly (import error), never silently.
- The host exit IP is *not* pinned: urltest failover legitimately changes it.
