# Archil disk setup — status and runbook

Date: 2026-10-05 · Branch `feat/cinema-redesign` · HEAD `0ccf192`

## Status: BLOCKED — not provisioned

The `disk` CLI cannot be fetched from this machine and no Archil credentials
exist, so the disk was **not** created and persistence was **not** proved here.
Two independent blockers:

1. **No network egress.** `npx disk --help` fails with `EAI_AGAIN`
   (`getaddrinfo registry.npmjs.org`); `getent hosts archil.com` also fails. No
   outbound DNS, so `npx` can neither fetch the `disk` package nor reach Archil.
   No `disk`/`archil` binary is installed locally and no copy is cached.
2. **No API key.** `ARCHIL_API_KEY` is unset in the environment, and creating one
   requires a human to sign up at <https://console.archil.com>. Setting up an
   account is deliberately not automated.

Nothing was guessed or fabricated: no key was invented, no disk id/region/output
is claimed. The work below is the reviewable part.

## What was added (uncommitted)

| File | Purpose |
| --- | --- |
| `.env.example` | Placeholder `ARCHIL_API_KEY` / `ARCHIL_REGION` (no real values). |
| `.gitignore` | `!.env.example` so the template is not swallowed by `.env.*`. |
| `scripts/archil_provision.sh` | Runs steps 3–6 once a key + network exist. |
| `docs/ARCHIL_SETUP.md` | This runbook. |

## How to finish (operator, on a networked machine)

```bash
# 1. Get a key (human step): https://console.archil.com
#    Region must support serverless exec and be closest to your compute:
#    aws-us-east-1 | aws-us-west-2 | aws-eu-west-1
export ARCHIL_API_KEY="key-..."        # never commit, never echo back
export ARCHIL_REGION="aws-us-east-1"

# 2. Verify + create + prove persistence, in one go:
scripts/archil_provision.sh
```

The script:
- verifies access with `npx --yes disk list` and **stops** if it fails,
- runs `npx --yes disk create vergoboy-stream-workspace`,
- prints the one-time mount token (store it immediately if you will mount; it is
  not shown again),
- executes a write and then a **second, separate** `exec` that reads the file
  back, printing `PASS` only if the data survived the discarded container.

Override `ARCHIL_DISK_NAME`, set `ARCHIL_DISK_ID` to reuse an existing disk, or
set `DISK_CLI` to test with a stub. Raw equivalent commands:

```bash
npx disk list
npx disk create <name>                                     # -> dsk-..., mount token
npx disk exec <disk-id> "echo hello from archil > /mnt/data/hello.txt && ls -la /mnt/data"
npx disk exec <disk-id> "cat /mnt/data/hello.txt"          # must print the line above
```

## Where credentials belong

- The `disk` CLI and `scripts/archil_provision.sh` read `ARCHIL_API_KEY` /
  `ARCHIL_REGION` from the process environment.
- On this server, real secrets live in `.stream_db.env` (gitignored, mode 0600).
  Add the two Archil values there; do not put them in `.env.example` or in any
  tracked file. `.gitignore` already excludes `.env`, `.env.*` and
  `.stream_db.env`.

## Caveats (from the Archil docs)

- **Exec is AWS-only today** — GCP regions do not support serverless exec.
- Exec mounts disks in **shared mode**: writing into a directory owned by another
  client can fail with `Read-only file system`. Write into a fresh per-job
  subdirectory, or run `archil checkout <path>` inside the exec command.
- **Command timeout is 5 minutes**; stdout and stderr are each capped at 128 KiB.
- Consistency between Archil clients is strong after `fsync`; between Archil and
  S3 it is eventual (seconds to minutes).

## Wiring into this project (when actually needed)

This repo is a Python/gevent Flask backend plus a Next.js/Tauri frontend; its
media workspace is local (`media/`, `data/`, both gitignored). Any move to an
Archil-backed workspace is a **behavior change to the media slice** and must
follow ADR 0001: characterization tests first, one domain per phase, no
speculative modules. The Node.js library example (`import * as archil from
"disk"`) applies only to the frontend/shell side and is not needed for the
Python backend.
