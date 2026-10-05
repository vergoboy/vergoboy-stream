#!/usr/bin/env bash
# vergoboy-stream — build the Arch Linux / pacman .pkg.tar.zst package.
#
#   ./build-arch.sh                 build the package, then verify the artifact
#   ./build-arch.sh --clean-first   delete previous artifacts and build trees first
#   ./build-arch.sh --rebuild-ui    rebuild webapp/out even if it already exists
#   ./build-arch.sh --install       pacman -U the artifact afterwards (needs sudo)
#   ./build-arch.sh --out DIR       also copy the artifacts into DIR
#
# Packaging notes that this script exists to enforce:
#
#   * packaging/arch/PKGBUILD sources the app with
#       source=("vergoboy-stream::git+file://${startdir}/../..")
#     so makepkg builds a fresh clone of the *committed* tree. Anything left
#     uncommitted in the working tree — including binary assets such as the
#     Tauri icons — is silently absent from the package. The preflight below
#     shouts about a dirty tree for exactly that reason.
#   * PKGBUILD reuses this checkout's prebuilt webapp/out instead of running
#     Next inside makepkg's isolated worktree, so that export must exist before
#     makepkg runs. This script builds it with the same variables
#     src-tauri/tauri.conf.json uses for beforeBuildCommand.
#   * makepkg must never run as root; it drops privileges into fakeroot itself.
#
# Nothing here needs root except --install.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PKGDIR="$ROOT/packaging/arch"
PKGBUILD="$PKGDIR/PKGBUILD"
OUTDIR="$ROOT/webapp/out"

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

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --clean-first) CLEAN_FIRST=1 ;;
    --rebuild-ui)  REBUILD_UI=1 ;;
    --install)     INSTALL=1 ;;
    --out)         shift || die "--out needs a directory"; COPY_OUT="$1" ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

# ── preflight ────────────────────────────────────────────────────────────────

[ "$(id -u)" -eq 0 ] && die "do not run makepkg as root — run this as your normal user"
[ -f "$PKGBUILD" ] || die "missing $PKGBUILD"
command -v makepkg >/dev/null 2>&1 || die "makepkg not found — install the 'pacman' package (pacman -S pacman)"

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

if [ "$REBUILD_UI" -eq 1 ] || [ ! -f "$OUTDIR/index.html" ]; then
  say "building the static export (webapp/out)"
  [ "$REBUILD_UI" -eq 0 ] && say "webapp/out/index.html missing, so it has to be built"
  ( cd "$ROOT/webapp" && NEXT_PUBLIC_BUILD_TARGET=tauri NEXT_PUBLIC_API_ORIGIN="$API_ORIGIN" npm run build:tauri )
else
  say "reusing webapp/out (pass --rebuild-ui to force a fresh export)"
fi

# Tauri validates the icon formats itself, but the failure mode is an unhelpful
# proc-macro panic, so check the two PNGs the PKGBUILD installs up front.
for icon in 32x32 128x128; do
  path="$ROOT/webapp/src-tauri/icons/$icon.png"
  [ -f "$path" ] || warn "missing icon: webapp/src-tauri/icons/$icon.png"
done

# ── makepkg ──────────────────────────────────────────────────────────────────

# makepkg executes the PKGBUILD from this working tree but clones the *source*
# from git HEAD, so the origin has to be handed over explicitly: the PKGBUILD
# needs it to patch the clone's CSP (blob: for hls.js + the local origin).
printf '%s\n' "$API_ORIGIN" > "$PKGDIR/.api-origin"

if [ "$CLEAN_FIRST" -eq 1 ]; then
  say "removing previous build trees and artifacts"
  rm -rf "$PKGDIR/src" "$PKGDIR/pkg"
  rm -f "$PKGDIR"/*.pkg.tar.zst
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
pkg="$(ls -t "$PKGDIR"/vergoboy-stream-*.pkg.tar.zst 2>/dev/null | grep -v -- '-debug-' | head -1 || true)"
[ -n "$pkg" ] || die "makepkg reported success but no vergoboy-stream-*.pkg.tar.zst was produced"

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
  cp -f "$PKGDIR"/vergoboy-stream-*.pkg.tar.zst "$COPY_OUT/"
  say "copied artifacts to $COPY_OUT"
fi

if [ "$INSTALL" -eq 1 ]; then
  say "installing with pacman -U (sudo)"
  sudo pacman -U --noconfirm "$pkg"
  say "installed — launch it with: vergoboy-stream"
fi