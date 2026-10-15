#!/usr/bin/env bash
# vergoboy — build the Arch Linux / pacman .pkg.tar.zst package for one instance.
#
#   ./build-arch.sh                 build the package, then verify the artifact
#   ./build-arch.sh --clean-first   delete previous artifacts and build trees first
#   ./build-arch.sh --rebuild-ui    rebuild webapp/out even if it already exists
#   ./build-arch.sh --instance NAME build for instance NAME (default: stream)
#   ./build-arch.sh --install       pacman -U the artifact afterwards (needs sudo)
#   ./build-arch.sh --out DIR       also copy the artifacts into DIR
#
# An instance is one backend a bundled client talks to. "stream" is the
# original at https://vergoboy.ir/stream; "w2g" is the second at
# https://vergoboy.ir/w2g. It selects the package name (vergoboy-<instance>)
# and the API path baked into the export (API_BASE_PATH), nothing else: the
# Rust binary is the same source either way.
#
# Packaging notes that this script exists to enforce:
#
#   * packaging/arch/PKGBUILD sources the app with
#       source=("${pkgname}::git+file://${startdir}/../..")
#     so makepkg builds a fresh clone of the *committed* tree. Anything left
#     uncommitted in the working tree — including binary assets such as the
#     Tauri icons — is silently absent from the package. The preflight below
#     shouts about a dirty tree for exactly that reason.
#   * PKGBUILD reuses this checkout's prebuilt webapp/out instead of running
#     Next inside makepkg's isolated worktree, so that export must exist before
#     makepkg runs. This script builds it with the same variables
#     src-tauri/tauri.conf.json uses for beforeBuildCommand.
#   * makepkg must never run as root; it drops privileges into fakeroot itself.
#   * only one run may exist at a time. makepkg's $srcdir is
#     packaging/arch/src for every instance (makepkg.conf leaves BUILDDIR
#     unset), `makepkg -C` deletes it at the start of each build, and
#     webapp/out is shared too — so a second run wipes the first one's
#     checkout mid-compile. This script takes a lock rather than corrupt.
#
# Nothing here needs root except --install.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PKGDIR="$ROOT/packaging/arch"
PKGBUILD="$PKGDIR/PKGBUILD"
OUTDIR="$ROOT/webapp/out"

# Which instance the bundled client talks to. "stream" reproduces the original
# package exactly, including its historical use of the domain root for API
# calls (see API_BASE_PATH below), so the default is behaviour-preserving.
INSTANCE="${INSTANCE:-stream}"

# Keep in sync with build.beforeBuildCommand in webapp/src-tauri/tauri.conf.json.
# Override to package a build that talks to a local backend instead of
# production, e.g.
#   API_ORIGIN=http://127.0.0.1:8801 ./build-arch.sh --rebuild-ui
# Two things follow API_ORIGIN: the NEXT_PUBLIC_API_ORIGIN value baked into the
# static export, and -- because Tauri reads its CSP from the makepkg clone of
# HEAD, not from this working tree -- the CSP allowlist the PKGBUILD injects.
API_ORIGIN="${API_ORIGIN:-https://vergoboy.ir}"

CLEAN_FIRST=0
REBUILD_UI=0
INSTALL=0
COPY_OUT=""

# Records which instance the export in webapp/out was produced for, so a reuse
# can be rejected (and rebuilt) rather than silently packaging the wrong
# instance's client. out/ itself is a Next output directory and cannot hold a
# marker of ours without it being served publicly.
OUT_INSTANCE="$ROOT/webapp/.out-instance"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --clean-first) CLEAN_FIRST=1 ;;
    --rebuild-ui)  REBUILD_UI=1 ;;
    --instance)    shift || die "--instance needs a name"; INSTANCE="$1" ;;
    --install)     INSTALL=1 ;;
    --out)         shift || die "--out needs a directory"; COPY_OUT="$1" ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

# Derived after parsing, so --instance is in effect. The path the instance's API
# (and Socket.IO) is mounted under, as seen from the client. The desktop app has
# no mount point of its own, so this is the only thing that distinguishes one
# instance's package from another. "stream" has historically used the domain
# root here; "w2g" is served under /w2g.
case "$INSTANCE" in
  stream) API_BASE_PATH="${API_BASE_PATH-}" ;;
  *)      API_BASE_PATH="${API_BASE_PATH:-/$INSTANCE}" ;;
esac

# ── preflight ────────────────────────────────────────────────────────────────

[ "$(id -u)" -eq 0 ] && die "do not run makepkg as root — run this as your normal user"
[ -f "$PKGBUILD" ] || die "missing $PKGBUILD"
command -v makepkg >/dev/null 2>&1 || die "makepkg not found — install the 'pacman' package (pacman -S pacman)"

# ── one build at a time ──────────────────────────────────────────────────────
#
# Every run shares packaging/arch/src — makepkg's $srcdir, which is the same
# directory for both instances because makepkg.conf leaves BUILDDIR unset — plus
# packaging/arch/.instance, .api-origin and webapp/out. `makepkg -C` removes
# $srcdir outright at the start of every build, so two concurrent runs destroy
# each other's work: on 2026-10-15 a `./build-arch.sh` (stream) overlapped an
# `--instance w2g` run and left it with
#   error: could not write output to .../target/release/deps/....rcgu.o:
#          No such file or directory
# (an earlier attempt lost the checkout's tsconfig.json, so `npx tsc --noEmit`
# printed its help text and "failed"). Neither run is at fault and neither can
# detect the other: serialize on a lock instead of corrupting.
#
# The lock is held for the whole run — export build, makepkg, verification —
# and is released when the script exits, fd and all. A second run waits rather
# than failing, so a queued build still produces a package.
BUILD_LOCK="$PKGDIR/.build.lock"
exec 9>"$BUILD_LOCK" || die "cannot open $BUILD_LOCK for locking"
if ! flock -n 9; then
  warn "another build is running (the checkout and webapp/out are shared) — waiting"
  flock -w 3600 9 || die "waited an hour for $BUILD_LOCK; another build is stuck (pgrep -af build-arch)"
fi

# tauri-cli discovers the host triple by running `rustc -vV` and splitting the
# output on "host:", unwrapping the result. Any rustc that fails to start prints
# nothing there and the CLI dies with
#   crates/tauri-cli/src/interface/rust.rs: unwrap() on a None value
# which is exactly what a distro rustc linked against a libLLVM soname the system
# no longer ships does. So the probe has to be "does rustc -vV print a host:",
# not "is cargo on PATH" — cargo can be fine while rustc is broken, which is the
# current state of this machine.
# NOTE: this must not be a `rustc -vV | grep -q` pipeline. `grep -q` exits on the
# first match and closes the pipe, rustc dies with SIGPIPE (141), and `pipefail`
# reports the probe as failed even though it succeeded.
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
    warn "'rustc' on PATH cannot start (or prints no host triple) — using the rustup"
    warn "toolchain at $HOME/.cargo/bin instead. Compare 'pacman -Q rust' with 'rustup show'."
  else
    rustc -vV 2>&1 | grep -i "shared librar" >&2 || true
    die "no usable rustc: 'rustc -vV' does not report a host triple."
    die "fix the distro toolchain (sudo pacman -Syu) or install rustup, then retry."
  fi
fi

command -v cargo >/dev/null 2>&1 || die "cargo not found on PATH after toolchain selection"
say "toolchain: $(rustc --version) / $(cargo --version)"

command -v node >/dev/null 2>&1 || die "node not found — the Tauri build needs Node.js"
command -v npm  >/dev/null 2>&1 || die "npm not found — the Tauri build needs npm"

[ -d "$ROOT/webapp/node_modules" ] || die "webapp/node_modules missing — run: cd webapp && npm ci"

# Uncommitted work never reaches the package; say so before spending 10 minutes
# on a build that will silently ship the previous revision.
if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then
  warn "working tree has uncommitted changes — makepkg builds from a git clone of HEAD,"
  warn "so these will NOT be in the package (binary assets like src-tauri/icons are"
  warn "the usual casualty). Commit them, or expect the previous revision."
  git -C "$ROOT" status --short >&2 || true
fi

# ── static export the PKGBUILD expects to copy ───────────────────────────────

export_ui() {
  say "building the static export (webapp/out) for instance '$INSTANCE' (API origin '$API_ORIGIN', API base path '${API_BASE_PATH:-/}')"
  # A stale .next cache does not fail cleanly: webpack's wasm hash layer dies
  # with "TypeError: Cannot read properties of undefined (reading 'length')" and
  # "Next.js build worker exited with code: 1", naming neither the cache nor the
  # file that triggered it. Drop it so a rebuild always starts clean.
  rm -rf "$ROOT/webapp/.next" "$OUTDIR"
  ( cd "$ROOT/webapp" && NEXT_PUBLIC_BUILD_TARGET=tauri \
      NEXT_PUBLIC_API_ORIGIN="$API_ORIGIN" \
      NEXT_PUBLIC_API_BASE_PATH="$API_BASE_PATH" \
      npm run build:tauri )
  # Only now is the export known to belong to this instance.
  printf '%s\n' "$INSTANCE" > "$OUT_INSTANCE"
}

if [ "$REBUILD_UI" -eq 1 ] || [ ! -f "$OUTDIR/index.html" ]; then
  [ "$REBUILD_UI" -eq 0 ] && say "webapp/out/index.html missing, so it has to be built"
  export_ui
else
  # out/ is gitignored, so PKGBUILD copies it from this working tree rather than
  # from the clone, and nothing in the build ties it to how it was produced.
  # Five ways it can be wrong, none of which the mere existence of index.html
  # catches -- so verify the properties the package actually depends on.
  bad=""

  # 1. Instance. The export is produced per instance (its API base path is baked
  #    in), and webapp/out is a single shared directory, so building the w2g
  #    package from a stream export would silently ship a client pointed at the
  #    wrong backend. The marker is written by export_ui and compared here.
  if [ "$(cat "$OUT_INSTANCE" 2>/dev/null || true)" != "$INSTANCE" ]; then
    bad="it was exported for instance '$(cat "$OUT_INSTANCE" 2>/dev/null || echo '?')', not '$INSTANCE'"
  fi

  # 2. Build target. next.config.ts sets a basePath for the "web" target
  #    (historically "/stream"; a second instance uses "/w2g") and leaves it
  #    empty for "tauri", because the desktop app's origin is tauri://localhost.
  #    A web export's chunks therefore load as <prefix>/_next/..., which 404s
  #    inside the app: the page never hydrates and the window stays blank white.
  #    Symptom seen in the WebView error report:
  #      ChunkLoadError: Failed to load chunk /stream/_next/static/chunks/...
  #    The check is deliberately prefix-agnostic — any non-empty basePath is
  #    wrong here, not just the historical one.
  if [ -z "$bad" ]; then
    if grep -qE '"/[^/"]+/_next/' "$OUTDIR/index.html" 2>/dev/null; then
      bad="it is a 'web'-target export (a basePath is set); its chunks 404 inside the desktop app"
    elif ! grep -q '"/_next/static/chunks/' "$OUTDIR/index.html" 2>/dev/null; then
      bad="it does not look like a static export at all (no '/_next/static/chunks/' in index.html)"
    fi
  fi

  # 3. API origin. It is baked into the chunks at export time, so an export built
  #    for a different backend is the reason the app "cannot reach the server"
  #    even though the server is up. This is invisible to a freshness check: the
  #    export is newer than the source, so it looks perfectly fresh.
  if [ -z "$bad" ] && [ -n "$API_ORIGIN" ] \
     && ! grep -rqF "$API_ORIGIN" "$OUTDIR/_next" 2>/dev/null; then
    bad="it was exported against a different API origin than '$API_ORIGIN'"
  fi

  # 4. API base path. Same reasoning one level finer: the origin can match while
  #    the instance path differs. Only worth checking when non-empty — an empty
  #    prefix (the historical "stream" layout) has no literal to find.
  if [ -z "$bad" ] && [ -n "$API_BASE_PATH" ] \
     && ! grep -rqF "\"$API_BASE_PATH\"" "$OUTDIR/_next" 2>/dev/null; then
    bad="it was not exported with the API base path '$API_BASE_PATH'"
  fi

  # 5. Freshness. Older frontend source than the export means the shipped UI
  #    does not match the code. next-env.d.ts is excluded because Next rewrites
  #    it on every run and it carries no application code.
  if [ -z "$bad" ]; then
    stale="$(find "$ROOT/webapp" -type f \
      \( -name '*.tsx' -o -name '*.ts' -o -name '*.css' -o -name 'next.config.*' \) \
      -newer "$OUTDIR/index.html" \
      -not -path '*/node_modules/*' -not -path '*/.next/*' \
      -not -path '*/out/*' -not -path '*/target/*' -not -name 'next-env.d.ts' \
      -printf '%P\n' 2>/dev/null | head -5 || true)"
    if [ -n "$stale" ]; then
      bad="it is older than the frontend source ($(printf '%s' "$stale" | tr '\n' ' '))"
    fi
  fi

  if [ -n "$bad" ]; then
    warn "webapp/out cannot be reused because $bad."
    warn "Rebuilding it now (same as --rebuild-ui)."
    export_ui
  else
    say "reusing webapp/out (verified: instance '$INSTANCE', tauri target, API origin $API_ORIGIN, up to date)"
  fi
fi

# Tauri validates the icon formats itself, but the failure mode is an unhelpful
# proc-macro panic, so check the two PNGs the PKGBUILD installs up front.
for icon in 32x32 128x128; do
  path="$ROOT/webapp/src-tauri/icons/$icon.png"
  [ -f "$path" ] || warn "missing icon: webapp/src-tauri/icons/$icon.png"
done

# ── makepkg ──────────────────────────────────────────────────────────────────

# makepkg executes the PKGBUILD from this working tree but clones the *source*
# from git HEAD, so these two have to be handed over explicitly: the PKGBUILD
# needs the origin to patch the clone's CSP (blob: for hls.js + the local
# origin), and needs the instance to name the package and its files.
printf '%s\n' "$API_ORIGIN" > "$PKGDIR/.api-origin"
printf '%s\n' "$INSTANCE"   > "$PKGDIR/.instance"

# makepkg keeps a *bare* git mirror of the source under $SRCDEST (which defaults
# to the PKGBUILD directory, named after the package, so $PKGDIR/vergoboy-<instance>)
# and checks it out into src/. Both are keyed to the repo's absolute path, so a
# tree that was copied or moved to another machine leaves a mirror whose origin
# no longer exists and makepkg aborts before building with
#   ERROR: <dir> is not a clone of file:///…/packaging/arch/../..
# `makepkg -C` cleans only the checkout, never the mirror, so --clean-first did
# not clear this either. Drop a mirror that points outside this repo and let
# makepkg re-clone; leave a matching one alone so repeat builds stay fast.
SRC_MIRROR="$PKGDIR/vergoboy-$INSTANCE"
if [ -d "$SRC_MIRROR" ]; then
  mirror_origin="$(git -C "$SRC_MIRROR" config --get remote.origin.url 2>/dev/null || true)"
  case "$mirror_origin" in
    *"$ROOT"*) : ;;
    *)
      warn "dropping stale source mirror ($SRC_MIRROR -> ${mirror_origin:-unknown origin})"
      rm -rf "$SRC_MIRROR" "$PKGDIR/src"
      ;;
  esac
fi

if [ "$CLEAN_FIRST" -eq 1 ]; then
  say "removing previous build trees and artifacts for instance '$INSTANCE'"
  rm -rf "$PKGDIR/src" "$PKGDIR/pkg" "$SRC_MIRROR"
  rm -f "$PKGDIR/vergoboy-$INSTANCE-"*.pkg.tar.zst
fi

# -C cleans $srcdir and $pkgdir first, which is what makes a build reproducible:
# the Tauri icon bug in particular only reappeared when $srcdir was stale.
#
# makepkg takes NO path argument: given one it only ever looks for ./PKGBUILD
# and dies with "ERROR: PKGBUILD does not exist." So run it inside packaging/arch
# — startdir becomes that directory, which is what ${startdir}/../.. resolves.
say "makepkg -Ccf in packaging/arch (this compiles Rust, expect several minutes)"
( cd "$PKGDIR" && makepkg -Ccf --noconfirm )

# ── verify what we produced ──────────────────────────────────────────────────

# The main package, not the auto-generated -debug one: makepkg finishes the
# release package first and the debug split after it, so a plain "newest wins"
# pick hands back the wrong file.
pkg="$(ls -t "$PKGDIR/vergoboy-$INSTANCE-"*.pkg.tar.zst 2>/dev/null | grep -v -- '-debug-' | head -1 || true)"
[ -n "$pkg" ] || die "makepkg reported success but no vergoboy-$INSTANCE-*.pkg.tar.zst was produced"

say "built $pkg"
printf '    %s\n' "$(du -h "$pkg" | cut -f1)"

# zstd frame magic, little-endian on disk. Catches a stale or foreign artifact.
magic="$(od -An -tx1 -N4 "$pkg" | tr -d ' \n')"
[ "$magic" = "28b52ffd" ] || die "artifact is not zstd-compressed (leading bytes: $magic)"
say "zstd magic OK ($magic), $(file -b "$pkg" 2>/dev/null || echo 'file(1) unavailable')"

if command -v pacman >/dev/null 2>&1; then
  say "package metadata"
  pacman -Qip "$pkg" | sed 's/^/    /'
fi

if command -v namcap >/dev/null 2>&1; then
  say "namcap"
  namcap "$pkg" | sed 's/^/    /'
else
  warn "namcap not installed — skipping package lint (pacman -S namcap)"
fi

if [ -n "$COPY_OUT" ]; then
  mkdir -p "$COPY_OUT"
  cp -f "$PKGDIR/vergoboy-$INSTANCE-"*.pkg.tar.zst "$COPY_OUT/"
  say "copied artifacts to $COPY_OUT"
fi

if [ "$INSTALL" -eq 1 ]; then
  say "installing with pacman -U (sudo)"
  sudo pacman -U --noconfirm "$pkg"
  say "installed — launch it with: vergoboy-$INSTANCE"
fi