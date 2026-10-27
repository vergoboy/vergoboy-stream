#!/usr/bin/env bash
# vergoboy — build a runnable desktop binary (no packaging), for the mpv work.
#
#   ./scripts/build-native.sh                build the binary, verify it, print its path
#   ./scripts/build-native.sh --rebuild-ui   rebuild webapp/out even if it can be reused
#   ./scripts/build-native.sh --instance N   instance the client talks to (default: stream)
#   ./scripts/build-native.sh --debug        debug profile instead of release
#   ./scripts/build-native.sh --run          exec the binary when the build is done
#
# This is the development counterpart to ./build-arch.sh. build-arch.sh builds a
# *package* (makepkg, .pkg.tar.zst, pacman -U); this builds the same production
# binary and stops — no PKGBUILD, no artifact, no root. It exists because the
# mpv player work (S2 onward) needs a real desktop app with mpv inside it, and
# `cargo build --release` by hand does not produce one (see the gate below).
#
# Unlike build-arch.sh this builds the *working tree*, not a clone of HEAD, so
# uncommitted changes are included — which is the point for iterating on the
# player. Nothing under packaging/, server/ or deploy/ is read or written
# except the shared build lock and the same webapp/out export build-arch.sh uses.
#
# Nothing here needs root.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PKGDIR="$ROOT/packaging/arch"
OUTDIR="$ROOT/webapp/out"
OUT_INSTANCE="$ROOT/webapp/.out-instance"

# Which backend the bundled client talks to. "stream" (default) is the original
# instance at https://vergoboy.ir/stream — the same default build-arch.sh uses.
# Override with --instance / INSTANCE, e.g. --instance w2g.
INSTANCE="${INSTANCE:-stream}"

# Same default and same reasoning as build-arch.sh: the origin is baked into the
# static export. Tauri's CSP also lists it, but CSP is taken from the working
# tree here (we do not run the PKGBUILD's injection), so a non-production origin
# only works if tauri.conf.json already allows it.
API_ORIGIN="${API_ORIGIN:-https://vergoboy.ir}"

DEBUG=0
REBUILD_UI=0
RUN=0

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --debug)       DEBUG=1 ;;
    --rebuild-ui)  REBUILD_UI=1 ;;
    --instance)    shift || die "--instance needs a name"; INSTANCE="$1" ;;
    --run)         RUN=1 ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

# Keep in sync with build-arch.sh. Every instance — "stream" included — is
# served under /<instance>: nginx proxies /stream/api/ to the backend mounted
# at /stream, and the domain root reaches nothing at all. The old "stream →
# domain root" mapping is the "cannot connect" bug recorded in
# docs/FOLLOWUPS.md. An API_BASE_PATH set in the environment still wins,
# including an explicit empty value for a genuinely root-mounted backend.
API_BASE_PATH="${API_BASE_PATH-/$INSTANCE}"

# ── preflight ────────────────────────────────────────────────────────────────

# tauri-cli discovers the host triple from `rustc -vV` and unwraps the result,
# so a rustc that cannot start (distro rustc linked against a libLLVM soname the
# system no longer ships — the state this machine was in) kills the CLI with an
# unexplained "unwrap() on a None value". Probe for a host triple, not for cargo.
# Not a `| grep -q` pipeline: grep exits on first match, rustc dies of SIGPIPE,
# and pipefail reports the successful probe as failed.
rustc_works() {
  local info
  info="$(rustc -vV 2>/dev/null || true)"
  case "$info" in
    *"host: "*) return 0 ;;
    *)          return 1 ;;
  esac
}

if ! rustc_works; then
  if [ -x "$HOME/.cargo/bin/rustc" ]; then
    export PATH="$HOME/.cargo/bin:$PATH"
    hash -r
  fi
  if rustc_works; then
    warn "'rustc' on PATH is unusable — using the rustup toolchain at $HOME/.cargo/bin"
  else
    die "no usable rustc: 'rustc -vV' reports no host triple — fix the toolchain (sudo pacman -Syu) or install rustup"
  fi
fi

command -v cargo >/dev/null 2>&1 || die "cargo not found on PATH"
command -v node  >/dev/null 2>&1 || die "node not found — the export and the Tauri CLI need Node.js"
command -v npm   >/dev/null 2>&1 || die "npm not found"
[ -d "$ROOT/webapp/node_modules" ] || die "webapp/node_modules missing — run: cd webapp && npm ci"

# Pin the toolchain the PKGBUILD pins, before anything reports a version.
# Since the rustc 1.99.0 update (Oct 2026) an opt-level-3 release build of
# vergoboy_stream_lib intermittently SIGSEGVs *inside rustc/LLVM itself* —
# reproduced 2026-10-25 in llvm::Module::dropAllReferences / LLVMContextDispose
# while compiling this very crate — and 1.97.1 (the toolchain that built every
# package before Oct 4) does not. RUST_MIN_STACK and CARGO_BUILD_JOBS below do
# not address it: the fault is far below the crashing thread's stack pointer, so
# rustc's own "increase RUST_MIN_STACK" hint is generic noise, and the PKGBUILD
# says the same. The pin is skipped (with a warning) when that toolchain is not
# installed, so a machine without it still builds — just less reliably.
PIN_TOOLCHAIN="${PIN_TOOLCHAIN:-1.97.1}"
if command -v rustup >/dev/null 2>&1; then
  _toolchains="$(rustup toolchain list 2>/dev/null || true)"
  case "$_toolchains" in
    *"${PIN_TOOLCHAIN}-x86_64"*)
      export RUSTUP_TOOLCHAIN="$PIN_TOOLCHAIN"
      ;;
    *)
      warn "rustup toolchain $PIN_TOOLCHAIN is not installed — building with the default"
      warn "($(rustc --version 2>/dev/null || echo '?')), which intermittently SIGSEGVs in rustc."
      warn "Install it with: rustup toolchain install $PIN_TOOLCHAIN"
      ;;
  esac
fi

say "toolchain: $(rustc --version) / $(cargo --version)"

# ── one build at a time ──────────────────────────────────────────────────────
#
# Same lock as build-arch.sh, because the two share webapp/out and cargo's
# target dir. Two exports racing would leave one of them packaging/building the
# other's UI, which is exactly the corruption .build.lock exists to prevent.
exec 9>"$PKGDIR/.build.lock" || die "cannot open $PKGDIR/.build.lock for locking"
if ! flock -n 9; then
  warn "another build is running (webapp/out and the target dir are shared) — waiting"
  flock -w 3600 9 || die "waited an hour for the lock; another build is stuck (pgrep -af build)"
fi

# ── the static export Tauri embeds ───────────────────────────────────────────
#
# Tauri compiles the export in webapp/out into the binary when its
# `custom-protocol` feature is on. The export must be the *tauri* target (a web
# export sets a basePath, and its chunks then 404 inside the app, leaving a
# blank white window) and must carry this instance's API origin and base path.
export_ui() {
  say "building the static export for instance '$INSTANCE' (API origin '$API_ORIGIN', API base path '${API_BASE_PATH:-/}')"
  # A stale .next cache does not fail cleanly (webpack's wasm hash layer dies
  # with "Cannot read properties of undefined (reading 'length')"), so drop it.
  rm -rf "$ROOT/webapp/.next" "$OUTDIR"
  ( cd "$ROOT/webapp" && NEXT_PUBLIC_BUILD_TARGET=tauri \
      NEXT_PUBLIC_API_ORIGIN="$API_ORIGIN" \
      NEXT_PUBLIC_API_BASE_PATH="$API_BASE_PATH" \
      npm run build:tauri )
  printf '%s\n' "$INSTANCE" > "$OUT_INSTANCE"
}

if [ "$REBUILD_UI" -eq 1 ] || [ ! -f "$OUTDIR/index.html" ]; then
  [ "$REBUILD_UI" -eq 0 ] && say "webapp/out/index.html missing, so it has to be built"
  export_ui
else
  # out/ is gitignored and shared, so its existence proves nothing about how it
  # was produced. Verify the properties the binary depends on, cheapest first.
  bad=""
  if [ "$(cat "$OUT_INSTANCE" 2>/dev/null || true)" != "$INSTANCE" ]; then
    bad="it was exported for instance '$(cat "$OUT_INSTANCE" 2>/dev/null || echo '?')', not '$INSTANCE'"
  fi
  if [ -z "$bad" ] && grep -qE '"/[^/"]+/_next/' "$OUTDIR/index.html" 2>/dev/null; then
    bad="it is a 'web'-target export (a basePath is set), whose chunks 404 inside the app"
  fi
  if [ -z "$bad" ] && ! grep -q '"/_next/static/chunks/' "$OUTDIR/index.html" 2>/dev/null; then
    bad="it does not look like a static export at all"
  fi
  if [ -z "$bad" ] && ! grep -rqF "$API_ORIGIN" "$OUTDIR/_next" 2>/dev/null; then
    bad="it was exported against a different API origin than '$API_ORIGIN'"
  fi
  if [ -z "$bad" ] && [ -n "$API_BASE_PATH" ] \
     && ! grep -rqF "\"$API_BASE_PATH\"" "$OUTDIR/_next" 2>/dev/null; then
    bad="it was not exported with the API base path '$API_BASE_PATH'"
  fi

  if [ -n "$bad" ]; then
    warn "webapp/out cannot be reused because $bad."
    warn "rebuilding it now (same as --rebuild-ui)"
    export_ui
  else
    say "reusing webapp/out (instance '$INSTANCE', tauri target, API origin $API_ORIGIN)"
  fi
fi

# ── the binary ───────────────────────────────────────────────────────────────
#
# `tauri build --no-bundle` is the canonical production build (the PKGBUILD uses
# the same command): it passes tauri's `custom-protocol` feature, which is what
# switches the `tauri` crate from `devUrl` (http://localhost:3000/) to the
# embedded assets at *compile* time. A raw `cargo build --release` does not pass
# it and produces a binary that shows WebKit's "Could not connect to localhost"
# page — reproduced 2026-10-16 on a hand-built binary installed as the real
# package. `--no-bundle` is what keeps this from being a packaging step: tauri
# only invokes patchelf for its bundling (AppImage/deb rpath fixups), never for
# the plain binary.
#
# beforeBuildCommand is disabled because we already built the export above with
# exactly these variables; leaving it on would re-run Next and, worse, bake the
# origin/instance from tauri.conf.json instead of ours.
#
# The codegen caps mirror the PKGBUILD. Since the rustc 1.99.0 update (Oct 2026)
# a 16-way parallel opt-level-3 release build intermittently SIGSEGVs inside
# rustc (SEGV_MAPERR while decoding optimized_mir) because the codegen workers
# oversubscribe RAM; the same graph compiles cleanly capped. Both are cheap.
# The toolchain pin above is the other half: this crash (LLVM teardown) is not
# the oversubscription one, and neither mitigation alone covers both.
export CARGO_BUILD_JOBS=4
export RUST_MIN_STACK=33554432
ulimit -s 65536 2>/dev/null || true

profile_args=()
bin_profile="release"
if [ "$DEBUG" -eq 1 ]; then
  profile_args+=(--debug)
  bin_profile="debug"
  warn "--debug: this binary still embeds the assets (custom-protocol is on), so it"
  warn "does not need 'tauri dev' or a running Next server — only a display."
fi

say "tauri build --no-bundle ($bin_profile) — this compiles Rust, expect several minutes"
( cd "$ROOT/webapp" && env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
    npm run tauri -- build --no-bundle "${profile_args[@]}" \
    --config '{"build":{"beforeBuildCommand":""}}' )

BIN="$ROOT/webapp/src-tauri/target/$bin_profile/vergoboy-stream"
[ -x "$BIN" ] || die "tauri reported success but $BIN is missing or not executable"

# ── the player, staged next to the build ────────────────────────────────────
#
# The app looks for mpv and the sync plugin in <its directory>/../lib/<its own
# name>/ before its own directory (src/native_player.rs), which for a
# working-tree binary is webapp/src-tauri/target/lib/<name>/. Staging them there
# means a dev build plays with no W2G_MPV/W2G_PLUGIN to remember — and it makes a
# missing player obvious here, instead of showing up in the room as "the player
# was not found on this installation", which reads like a server problem.
#
# The mpv fork (the sibling checkout the PKGBUILD builds, too) is where the
# plugin comes from; its own build-linux.sh prints how to compile mpv when that
# has not been done yet, in which case the system mpv is used instead — it can
# load the plugin as well. The system mpv is *not* a fallback goal here: the
# packaged client always ships the fork's own build.
PLAYER_SRC="${W2G_MPV_ROOT:-$(cd "$ROOT/.." && pwd)/w2g-mpv}"
PLAYER_DEST="$ROOT/webapp/src-tauri/target/lib/$(basename "$BIN")"
stage_player() {
  [ -f "$PLAYER_SRC/w2g/plugin/w2g_plugin.c" ] || {
    warn "no mpv fork at $PLAYER_SRC — not staging a player for $BIN"
    warn "(set W2G_MPV_ROOT, or install the fork; until then the binary needs both"
    warn " mpv and w2g.so next to it / in W2G_MPV and W2G_PLUGIN to play anything)"
    return 0
  }
  say "staging the player (mpv + sync plugin) into $(basename "$(dirname "$PLAYER_DEST")")/$(basename "$PLAYER_DEST")/"
  mkdir -p "$PLAYER_DEST"
  ( cd "$PLAYER_SRC/w2g/sync" && \
      (cargo build --release --features net --offline 2>/dev/null || cargo build --release --features net) ) \
    || return 1
  cc -O2 -Wall -Wextra -shared -fPIC \
    -I"$PLAYER_SRC/include" -I"$PLAYER_SRC/w2g/sync/include" \
    "$PLAYER_SRC/w2g/plugin/w2g_plugin.c" \
    "$PLAYER_SRC/w2g/sync/target/release/libw2g_sync.a" \
    -o "$PLAYER_DEST/w2g.so" -lpthread -ldl -lm || return 1
  if [ -x "$PLAYER_SRC/build/full/mpv" ]; then
    ln -sfn "$PLAYER_SRC/build/full/mpv" "$PLAYER_DEST/mpv"
  else
    warn "the fork has no build/full/mpv — using the system mpv for this build"
    warn "(build the fork with: cd $PLAYER_SRC && scripts/build-linux.sh full)"
    ln -sfn "$(command -v mpv || echo mpv)" "$PLAYER_DEST/mpv"
  fi
  ls -l "$PLAYER_DEST" | sed 's/^/    /'
}
stage_player || die "staging the player failed — see above"

# ── verify what we produced ──────────────────────────────────────────────────
#
# The dev-shell trap again, this time on the artifact itself: a production
# binary embeds the whole static export, so "_next/static" appears in it many
# times; a dev build embeds none of it. Checking the binary catches a stray
# `cargo build` that a source-level check would miss.
embedded="$(grep -ac '_next/static' "$BIN" 2>/dev/null || true)"
[ "${embedded:-0}" -ge 1 ] || die "$BIN embeds no frontend assets — this is a dev-shell build (it would load http://localhost:3000/ and show 'Could not connect to localhost'). Rebuild with this script; never 'cargo build --release' by hand."

if grep -aq 'localhost:3000' "$BIN" 2>/dev/null; then
  warn "$BIN still contains 'localhost:3000' — if the window is blank, this build"
  warn "picked up the dev URL; report it, the embedded-asset check passed anyway."
fi

say "built $BIN"
printf '    size: %s\n' "$(du -h "$BIN" | cut -f1)"
printf '    instance: %s (API %s%s)\n' "$INSTANCE" "$API_ORIGIN" "${API_BASE_PATH:-/}"
printf '    embeds the static export: %s asset references\n' "$embedded"

if [ "$RUN" -eq 1 ]; then
  if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    die "no DISPLAY/WAYLAND_DISPLAY — the app needs a display (run it from the desktop session, or use Xvfb)"
  fi
  say "running $BIN"
  exec "$BIN"
fi

if [ -L "$PLAYER_DEST/mpv" ] && [ -f "$PLAYER_DEST/w2g.so" ]; then
  say "player staged: $(readlink -f "$PLAYER_DEST/mpv") + $PLAYER_DEST/w2g.so"
else
  warn "no player staged — the app will report that it cannot find mpv/the plugin"
fi

say "run it with: $BIN"
