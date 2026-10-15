# Follow-ups

Out-of-scope findings kept out of the T1 change (AGENTS.md rule 4). Each entry
says what, why it was not done now, and what would be done later.

## From T1 (parametrize base path) — 2026-10-15

### `run.sh` only knows the original instance

The local dev runner hardcodes the original instance: backend port 8801, DB
name `stream` (line 283), and it prints the frontend URL as
`http://localhost:3000/stream/` (line 309). It never sets `STREAM_BASE_PATH`,
`STREAM_INSTANCE` or `NEXT_PUBLIC_BASE_PATH`, so `./run.sh` cannot bring up a
`/w2g` dev instance without hand-editing.

*Why not now:* the T1 acceptance gate and the deploy path both use `make
test-fast` / the production unit, which are already parametrized; `run.sh` is a
developer convenience and touching it risks the daily dev loop the original
instance depends on.

*Later:* derive the four values from the same env vars (`STREAM_BASE_PATH`,
`STREAM_PORT`, `STREAM_INSTANCE`) with the current values as defaults, and emit
`NEXT_PUBLIC_BASE_PATH` into `webapp/.env.local`.

### `get_state.py` pins one admin UUID

`get_state.py` mints a token for a hardcoded admin id
(`cceb5d42-a143-4d08-a634-4fa510507fab`). It is a throwaway diagnostic; the
guid is that instance's admin, so the script fails against any other database.

*Later:* look the id up (`SELECT id FROM users WHERE role='admin' LIMIT 1`),
the way `trigger_add.py` already does.

### `trigger_add.py` is a one-off with a baked-in subject

`trigger_add.py` still carries a specific movie URL and touches the database
directly to find an admin. It also references `urllib.error` without importing
it (it happens to work because `urllib.request` imports that submodule).
Left alone beyond the path/port parametrization.

*Later:* either delete it (it looks single-use) or fold it into a documented
admin CLI.

### `archive_scraper.py` "stream" is unrelated

`archive_scraper.py:327` mentions "download/stream links" — the word, not the
mount point. Only listed here so a future `grep /stream` sweep does not chase
it.
