# Architecture Audit — Phase 0 baseline

Date: 2026-10-05 · Branch `feat/cinema-redesign` · HEAD `0ccf192` (`feat(logging): add
persistent end-to-end diagnostics and harden Arch packaging`) · **No production code changed
in phase 0.**

## 0. Baseline record

**Git.** Tree clean — no modified files, no untracked files (only `.gitignore`d build/runtime
artifacts). Branch is **ahead of `origin/feat/cinema-redesign` by 3 commits**
(`47b05e6..0ccf192`); nothing is pushed in phase 0. No stashes. `main` = `96c2fcc`.

Stray / non-source files found in the tree:

| File | State | Note |
| --- | --- | --- |
| `app.py.save` | **tracked** (since initial commit) *and* listed in `.gitignore` | 596-line stale copy of an old single-room `app.py`. Finding S4. |
| `i.txt` | tracked | 42 KB scraped Digimoviez search-form HTML. Finding S4. |
| `packaging/arch/*.pkg.tar.zst`, `packaging/arch/vergoboy-stream/` | ignored | local build outputs |
| `.stream_db.env` | ignored, **never committed** | real secrets (see S5) |

**Tag / exports** (all outside the repo, in `../exports/`):

- Tag `pre-mpv-baseline` (annotated) → commit `0ccf192118b762c629c29451c06985795f29818c` = HEAD.
- `../exports/pre-mpv-baseline.bundle` — 2 603 415 bytes, `git bundle verify` OK, "records a
  complete history", carries all 3 branches + the tag. md5 `b1beca485b7b3294c27adf79e3c5c05d`.
- `../exports/pre-mpv-baseline.tar.gz` — 1 203 283 bytes, 221 entries; decompressed stream is
  **byte-identical** to `git archive HEAD` (both md5 `b3e6f330bd1e82ddf1aa1004b4125eba`).
  md5 of file: `71996a0485bbb793715602c3e23de491`.

**Test suite** — `venv/bin/python -m pytest -q` (exit 0):

- **608 passed, 2 skipped, 26 subtests passed — 410.06 s (0:06:50)**, 2 warnings.
- The two skips are `tests/unit/test_ffmpeg_cmd_golden.py:121` (`direct_play` and
  `subtitle_convert` are not HLS commands) — expected, not regressions.
- Warnings: `MonkeyPatchWarning` (gevent `monkey.patch_all()` after `ssl` import, from
  `app.py:2`) and a 22-byte HMAC key `InsecureKeyLengthWarning` in the socket suite.
- The brief's "170 unittest tests" is now a subset: the suite collects **608 tests** from 434
  `def test_*` functions across 20 test files (media pipeline 170, archive 97,
  seek-frontier 43, socket 38, app-logging 25, ffmpeg goldens 26, planner 22, crash-routes
  13 — sum 434). Slowest: hard-timeout kill test 90.15 s, each socket handshake test 25 s.
- `tests/integration/` contains only `__init__.py`; real-ffmpeg coverage lives in
  `tests/test_media_*.py` behind the `integration` marker.

**Per-file line counts** (`.py`, excluding `venv/`, `node_modules/`, generated `webapp/`):

| File | Lines | | File | Lines |
| --- | ---: | --- | --- | ---: |
| `app.py` | 4156 | | `tests/unit/test_archive_auth_flow.py` | 271 |
| `tests/socket/test_live_socket.py` | 1021 | | `security_math.py` | 260 |
| `media_pipeline/runner.py` | 794 | | `scripts/characterize_encode.py` | 246 |
| `archive_scraper.py` | 786 | | `tests/unit/test_crash_routes.py` | 231 |
| `tests/test_media_runner.py` | 745 | | `tests/unit/test_archive_auth.py` | 230 |
| `tests/test_media_pipeline.py` | 696 | | `tests/test_media_errors.py` | 224 |
| `db.py` | 591 | | `archive_login_form.py` | 222 |
| `tests/unit/test_seek_frontier.py` | 570 | | `tests/unit/test_archive_login_marker.py` | 194 |
| `media_pipeline/ffmpeg_cmd.py` | 497 | | `tests/unit/fake_digimoviez.py` | 181 |
| `archive_auth.py` | 492 | | `archive_filters.py` | 154 |
| `media_pipeline/planner.py` | 462 | | `archive_challenge.py` | 153 |
| `media_pipeline/probe.py` | 416 | | `tests/unit/test_archive_login_form.py` | 132 |
| `tests/test_media_pipeline_runtime.py` | 394 | | `tests/unit/test_archive_question.py` | 123 |
| `permissions.py` | 390 | | `tests/support.py` | 109 |
| `state.py` | 381 | | `mail.py` | 97 |
| `scripts/capture_probe_fixtures.py` | 372 | | `tests/unit/test_archive_network.py` | 97 |
| `tests/unit/test_app_logging.py` | 369 | | `tests/argv_cases.py` | 92 |
| `tests/unit/test_planner_matrix.py` | 363 | | `media_pipeline/__init__.py` | 75 |
| `tests/conftest.py` | 361 | | `archive_logging.py` | 71 |
| `app_logging.py` | 360 | | `scripts/capture_argv_fixtures.py` | 68 |
| `tests/unit/test_ffmpeg_cmd_golden.py` | 321 | | `trigger_add.py` | 65 |
| `scripts/smoke_encode.py` | 316 | | `archive_question.py` | 61 |
| `config.py` | 303 | | `livekit_auth.py` | 50 |
| `media_pipeline/errors.py` | 294 | | `tests/unit/test_archive_security_questions.py` | 39 |
| | | | `tests/unit/test_archive_filters.py` | 39 |
| | | | `archive_network.py` | 37 |
| | | | `srt_to_vtt.py` | 32 |
| | | | `get_state.py` | 26 |
| | | | `archive_challenge_auto.py` | 23 |
| | | | `tests/unit/test_archive_challenge.py` | 23 |
| | | | `tests/unit/test_archive_collectors.py` | 17 |
| | | | `tests/__init__.py`, `tests/unit/__init__.py`, `tests/socket/__init__.py`, `tests/integration/__init__.py` | 1 each |

Total `.py` lines (excluding `venv/`): **19 096** across 60 files · tracked files in `HEAD`: **192**.

## 1. Responsibility map of `app.py` (4 156 lines)

| Lines | Responsibility | Notes |
| --- | --- | --- |
| 1–52 | gevent monkey-patch, stdlib/third-party imports, app-level imports (`config`, `media_pipeline`, `archive_*`, `db`, `permissions`, `mail`, …) | side effects at import time |
| 53–96 | `log_debug`, `_safe_source_for_log`, `safe_save`, state-import fallback (`LOCK/CHAT_LOCK/room/chat/rooms`) | |
| 98–142 | Flask app + CORS + SocketIO wiring, `ENCODE_SEMAPHORE`, `ENCODE_RUNNER`, `MEDIA_CLIENT` | process singletons |
| 144–150 | `allowed()`, `new_id()` | |
| 152–240 | auth: `_bearer_token`, `_current_user`, `_require_user`, `_AuthError`, error handlers, `_current_room(_code)` | |
| 243–351 | permission gates: `_perm_snapshot`, `_has_perm`, `_deny_unless`, `_quota_ok`, `_may_*`, `_is_admin`, `_is_room_owner`, `_bump_upload_used`, `_remaining_add_slots` | |
| 353–453 | broadcast + socket-session helpers: `broadcast_state/notify/presence`, `_refresh_socket_perms`, `_reload_socket_user`, `_kick_room_user`, `_room_for_item` | |
| 455–599 | **URL intake**: `url_ext`, `_download_to_file`, `_check_link_ok`, `_resolve_media_url_remote`, `_local_mirror`, `_resolve_media_url` | S1, S2 |
| 601–846 | **encode progress / seek frontier**: `TRANSCODE_PROGRESS`, `seek_frontier`, `clamp_seek_target`, `_warn_if_lagging`, `_encoded_frontier`, `_current_item` | |
| 848–1036 | **probe + ladder**: `_ffprobe_source`, subtitle/duration/height helpers, `_pick_ladder`, `_bandwidth_estimate`, `_build_single_rendition_cmd`, `_rewrite_master_playlist` | |
| 1038–1477 | **encode execution**: `_run_progressive_ffmpeg`, `_extract_subtitles_async`, `_encode_rendition`, `start_item_encoding`, `request_quality` | |
| 1479–1578 | housekeeping: `_collect_referenced_files`, `_cleanup_orphans`, `_chat_purge_loop`, `_delete_chat_images` | |
| 1580–1704 | health/log endpoints + static media serving: `index`, `_probe_storage/transcoder/livekit`, `api_health`, `api_log_client/diagnostics`, `serve_upload/sub/hls/chat_image/avatar` | |
| 1706–1762 | `api_state`, `api_upload` | |
| 1764–2045 | archive: error map, `api_archive_search/filters/auth_status/manual_session/title/files`, `_add_url_item`, `api_add_many` | |
| 2047–2481 | YouTube: `_LANG_NAME`, `_run_ytdlp`, `_ytdlp_cmd`, `api_youtube_formats`, `_fetch_and_encode_youtube`, `_attach_yt_subtitle`, `_queue_youtube_item`, `api_add_youtube(_playlist)` | |
| 2483–2536 | `api_add_url`, `api_request_quality` | |
| 2538–2632 | live/voice: `api_new_key`, `api_voice_token`, `api_add_live` | |
| 2634–2787 | playlist ops: `api_remove_item`, `api_cleanup`, `api_subtitle_upload/url`, `api_add_audio_track` | |
| 2789–2862 | chat + avatar routes | |
| 2864–3455 | auth routes + OAuth: `_issue_tokens`, register/verify/resend/forgot/reset/login, `OAUTH_STATES`, oauth start/exchange/finish/callback, refresh/logout/me | |
| 3457–3680 | room routes: info/join/mine/members/promote/demote/ban/unban | |
| 3682–3829 | admin routes: stats/users/update/delete | |
| 3831–4089 | socket handlers: `SOCKET_SESSIONS`, `on_connect/disconnect/join/watching/voice_*/control/request_sync/chat_send` | |
| 4091–4122 | `_resume_interrupted_encodes` (boot) | |
| 4125–4156 | `__main__`: `Config.validate()`, `init_db()`, boot cleanup, greenlet spawns, gevent WSGI server | |

## 2. Global mutable state inventory

| Location | Name | Kind | Risk |
| --- | --- | --- | --- |
| `state.py:18-19` | `LOCK`, `CHAT_LOCK` | `threading.Lock` | shared by every room mutation; used under gevent |
| `state.py:341-342` | `room`, `chat` | legacy single-room singletons | imported by `app.py` fallback (`app.py:86-95`); still load `data/state.json` |
| `state.py:345-378` | `RoomManager._rooms` / `_chats` | in-process dict cache | lazily materialized, never evicted; persisted to `data/rooms/<code>.json` |
| `state.py:380` | `rooms` | singleton manager | every handler reaches it directly |
| `state.py` | chat state | in-memory only, never persisted, 24 h purge | by design (module docstring) |
| `app.py:130` | `ENCODE_SEMAPHORE` | gevent bounded semaphore | global encode concurrency cap |
| `app.py:136` | `ENCODE_RUNNER` | `EncodeRunner` + HW circuit breaker | deliberately process-global |
| `app.py:141` | `MEDIA_CLIENT` | `requests` session (connection pool) | `trust_env=False`, no proxy |
| `app.py:611-612` | `TRANSCODE_PROGRESS`, `PROGRESS_LOCK` | dict + gevent semaphore | keyed `item:rendition`; must match client's `transcodeProgress`; cleared only per item |
| `app.py:656` | `_LAG_WARNED_AT` | dict | unbounded per item id |
| `app.py:648-651` | `SYNC_LOGGER` handler injection | global logger | import-order sensitive (asserted by crash-route tests) |
| `app.py:3168` | `OAUTH_STATES` | dict, in-memory | lost on restart, single-process only; pruned by `_clean_oauth_states` |
| `app.py:3828` | `SOCKET_SESSIONS` | dict `sid → (room, DBUser)` | lost on restart; auth trust anchor for all handlers |
| `app.py:98/100/123` | `app`, `app_log`, `socketio` | process singletons | |

All of the above is **process-local**: the backend must stay single-worker (it already runs one
gevent `WSGIServer`). Any multi-worker move would break sync, progress, OAuth state and socket
sessions simultaneously — out of scope for every phase here.

## 3. Open security findings

| ID | Severity | Finding | Evidence | Phase |
| --- | --- | --- | --- | --- |
| **S1** | **High** | **`_local_mirror` path traversal + host-agnostic local path mapping.** The regex accepts *any* host, unquotes the remainder and joins it onto `/opt/files/data` with no normalization or containment check, so `/files/data/../../../etc/...` escapes the root; the resulting local path is then used as the ffprobe/ffmpeg encode source. | `app.py:578-593`, used at `app.py:1942` → `start_item_encoding` | 1 (media) |
| **S2** | **High** | **SSRF in URL fetching.** `_check_link_ok`, `_download_to_file`, `_resolve_media_url_remote` and `MEDIA_CLIENT` follow redirects to arbitrary hosts with no scheme/host/IP policy — no blocking of loopback, link-local (`169.254.169.254`) or RFC1918 targets. Any user with add-permission can probe internal services or pull their contents to disk (subtitle download writes the response into `media/subs/`). | `app.py:464-500`, `522-597`, callers `2006`, `2506`, `2735` | 1 (media URL intake) |
| **S3** | **Medium** | **Access token accepted via query string.** `_bearer_token` falls back to `?token=`, and the socket handshake, email-verify and password-reset pages read `request.args["token"]` directly. Query tokens leak into nginx/browser history, proxy logs and `Referer`. | `app.py:157-168`, `3845`, `2943`, `3040` | 2 (socket handshake) + 4 (HTTP) |
| **S4** | **Low** | **Committed backup/scratch files.** `app.py.save` (596-line stale app copy, tracked *and* gitignored) and `i.txt` (42 KB scraped third-party HTML) are in `HEAD`. Risk: drift, confusion during extraction, and a future edit to the backup being committed. | `git ls-files` shows both; `.gitignore` also lists `app.py.save` | 4 (hygiene, needs explicit approval — `git rm --cached`) |
| **S5** | Info | `.stream_db.env` holds real secrets (`STREAM_SECRET_KEY`, `STREAM_JWT_SECRET`, `STREAM_DATABASE_URL`, archive credentials). Verified **never committed**: `git log --all -- .stream_db.env` is empty, and it is gitignored. Invariant to preserve in every later phase. | `.stream_db.env` | n/a |

Fixing S1/S2/S3 changes observable behavior, so each fix lands **with characterization tests
first** in its own phase (rule 2'), never as a drive-by in phase 0.

## 4. Slice map

| Domain | app.py lines | Extracting phase | Main risks |
| --- | --- | --- | --- |
| **Media** (URL intake, probe, ladder, encode execution, progress/seek frontier, orphan cleanup, transcoder probe, `serve_hls`, `request_quality`, encode resume) | 455–599, 601–846, 848–1477, 1479–1543, 1586–1611, 1634–1647, 1667–1689, 2522–2535, 4091–4122 | **1** | gevent greenlet concurrency around ffmpeg; `TRANSCODE_PROGRESS` keyed by playlist item ids (cross-domain reads); 17 argv goldens + ~250 media tests must stay byte-identical; S1/S2 fixes alter behavior and need characterization tests first; `ENCODE_SEMAPHORE`/`ENCODE_RUNNER` are process globals shared with queueing logic. |
| **Rooms / sync / sockets** (broadcast helpers, socket-session helpers, room routes, socket handlers, `SOCKET_SESSIONS`) | 353–453, 3457–3680, 3831–4089 | **2** | handshake is the security boundary (38 socket tests, 25 s each); broadcasts are called by *every* other domain (encode progress → `broadcast_state`), so helpers must move without dragging Flask/`app` into lower layers; `SOCKET_SESSIONS` is the auth trust anchor; S3 token-in-query at the handshake. |
| **Playlist** (state/upload, add-url/add-many, remove/cleanup, subtitles, audio track) | 1706–1762, 1939–2045, 2483–2519, 2634–2787 | **3** | item lifecycle spans media (encode) and sockets (broadcast); on-disk format `data/rooms/<code>.json` and `state.json` must not change; quota/permission checks (`_may_add`, `_quota_ok`) live in the auth helpers that are still in `app.py`. |
| **The rest** (bootstrap, auth+permission helpers, health/log/static serving, archive routes, YouTube, live/voice, chat/avatar, auth routes/OAuth, admin) | 1–351, 1580–1633, 1650–1665, 1692–1704, 1764–1936, 2047–2481, 2538–2632, 2789–2862, 2864–3455, 3682–3829, 4125–4156 | **4** | auth/permission helpers are the most widely depended-on code (called from all three earlier slices) — they must be extracted first *within* phase 4 or re-exported; archive/YouTube code shells out to `yt-dlp`/scrapers and is behavior-heavy; `app.py` should end the phase as a thin composition root only. |

Ordering rationale: phase 1 takes the leaf that touches fewest handlers, so the layering rules
can be written from real code before phases 2–4 depend on them.

## 5. What the native-mpv design makes obsolete

**Facts as of this baseline:** `grep -ri mpv` over the repo returns **no source hits** (only
compiled Android `.so` artifacts under the gitignored `webapp/src-tauri/gen/`), and no
native-mpv design/ADR exists in the tree. Everything below is therefore **provisional** and
must be confirmed by that design doc before anything is deleted.

Current playback path: browser `hls.js` over progressive EVENT-type HLS
(`README.md` §Architecture), served by `serve_hls` (`app.py:1676-1689`), fed by the rendition
ladder + `_rewrite_master_playlist` (`app.py:1018-1035`) and guarded by the seek frontier
(`app.py:601-846`).

If the native-mpv design (desktop/shell player reading media locally instead of browser HLS):

- **becomes obsolete**: `hls.js` client integration; `serve_hls` + `/stream/media/hls/**`
  once *no* browser client fetches segments; `_rewrite_master_playlist` and the rendition
  ladder *if* mpv consumes the source file directly (a `direct_play` mode already exists —
  `COPY_LIKE_MODES` at `app.py:633`, planner `direct_play`); `TRANSCODE_PROGRESS` seek-frontier
  guard and segment-progress broadcasts, which only exist to stop players seeking past the
  encoded timeline; segment-based `transcode_progress` events.
- **stays**: ffprobe/planner decisions for the web client and for shared rooms, uploads, room
  state, sync/control protocol, chat, auth, archive/YouTube intake.
- **when deletable**: only after (a) the native-mpv design is written and accepted as an ADR,
  (b) web *and* shell clients no longer request HLS, and (c) in the phase that removes it —
  characterization tests updated first, then the code moved/deleted **in the same branch**
  (rule 2'). **Phase 0 deletes nothing.** Earliest realistic deletion phase: after phase 4, or
  a dedicated phase 5 owned by the mpv design.
