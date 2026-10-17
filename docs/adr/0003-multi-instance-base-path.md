# ADR 0003 — One codebase, several instances (mount point + instance identity)

- **Status:** Accepted — 2026-10-15 (w2g delivery, track T1)
- **Complements:** ADR 0001 (slice-by-slice migration), ADR 0002 (mpv as a
  separate process)
- **Evidence:** `docs/PROGRESS.md` (Session 3, T1 block) — the `/stream` →
  `/w2g` build diff, the two full `make test-fast` runs, and the acceptance grep

## Context

The product has to be served a second time at `https://vergoboy.ir/w2g` from
`/opt/w2g`, completely independent of the original
`https://vergoboy.ir/stream` from `/opt/stream`. The original instance existed
first and is in production; it must not be disturbed.

Before this change the mount point was hardcoded everywhere: 59 Flask route
decorators, the Flask `static_url_path`, the Socket.IO path, every media URL the
backend hands to a browser, `next.config.ts`'s `basePath`, the Socket.IO path
and ~50 API paths in the client bundle, and the Flask-rendered legacy UI. A
second instance was therefore impossible without a second copy of the code.

## Decision

Introduce exactly one description of an instance and derive everything else
from it. Three environment variables, all defaulting to the original instance
so an unset environment changes nothing:

| Variable | Default | Meaning |
| --- | --- | --- |
| `STREAM_BASE_PATH` | `/stream` | The public URL prefix. Every route is registered under it and every URL the process emits is built from it. |
| `STREAM_INSTANCE` | `stream` | Short name. Stamps the JWT `iss`/`aud` claims and names the default LiveKit room. |
| `STREAM_INSTANCE_ROOT` | the checkout dir | Directory holding mutable state (`media/`, `data/`), so a release tree and its state can be versioned separately. |

Mechanism, per the plan approved with the user:

- **Backend route prefix: a module constant + f-strings.** `BP =
  Config.BASE_PATH` in `app.py`; `@app.route(f"{BP}/…")`,
  `static_url_path=f"{BP}/static"`, `SocketIO(path=f"{BP}/socket.io".lstrip("/"))`.
  Chosen over a Flask blueprint `url_prefix` (blueprints cannot retitle the
  already-registered error handlers and the static route cleanly) and over WSGI
  middleware (which rewrites the environment rather than the routes, so
  `url_for` and every generated URL would still be wrong).
- **Frontend mount point: one exported constant.** `webapp/lib/config.ts`
  exports `BASE_PATH` (from `NEXT_PUBLIC_BASE_PATH`, empty for the Tauri
  target) and `API_PREFIX`; `next.config.ts` imports the same constant so the
  build cannot disagree with itself. Next inlines `basePath` at build time, so
  the web export is produced once per mount point.
- **Instance identity in tokens.** `db._sign()` stamps `iss`/`aud` from
  `STREAM_INSTANCE`; `decode_token()` refuses a token whose `iss`/`aud`
  disagrees. Tokens minted before the claims existed carry neither and are still
  honoured, so upgrading an instance does not log its users out. The distinct
  per-instance `STREAM_JWT_SECRET` remains the primary isolation; the claims make
  it explicit.

Each instance's mutable state is namespaced by `STREAM_INSTANCE_ROOT`, so the
two deployments never write into each other's `media/` or `data/`.

## Consequences

- A second instance is a set of environment variables plus one nginx snippet
  and one unit file (`deploy/nginx-w2g.conf`, `deploy/vergoboy-w2g.service`),
  not a fork.
- The web export must be built once per mount point; the mount point is baked
  into the bundle and cannot be changed at runtime (a Next.js constraint).
- `build-arch.sh`'s "is this a web export?" guard is now prefix-agnostic: any
  non-empty `basePath` is wrong for the desktop bundle, not just `/stream`.
- The legacy Flask-rendered UI (`templates/index.html`, `static/js/app.js`)
  receives the prefix at render time via `window.__STREAM_BASE_PATH__`, keeping
  its fallback behaviour working under either instance.
