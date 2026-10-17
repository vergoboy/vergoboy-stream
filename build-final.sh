#!/usr/bin/env bash
# vergoboy — build the FINAL desktop client in one command: the production
# binary plus the installable Arch package, with a checksummed inventory.
#
#   ./build-final.sh                 binary + package for instance 'w2g'
#   ./build-final.sh --instance NAME instance to build for (stream | w2g)
#   ./build-final.sh --no-package    binary only (skip makepkg entirely)
#   ./build-final.sh --no-binary     package only (skip the working-tree binary)
#   ./build-final.sh --rebuild-ui    force a fresh static export for both
#   ./build-final.sh --clean-first   wipe previous package build trees first
#   ./build-final.sh --install       pacman -U the package afterwards (sudo)
#   ./build-final.sh --out DIR       copy artifacts + SHA256SUMS into DIR
#
# This is a thin orchestrator on purpose. ./scripts/build-native.sh builds the
# working-tree binary (export verification, dev-shell gate, lock); build-arch.sh
# builds the makepkg package from a clone of HEAD (tree preflight, artifact
# verification, namcap). Both scripts own their locks, gates and warnings — this
# script only sequences them and prints the final inventory, so nothing about
# the build itself is defined twice.
#
# Two details worth knowing before using the output:
#
#   * the two artifacts come from different trees by design: the binary is
#     built from the *working tree* (uncommitted work included), the package
#     from a *clone of HEAD* (exactly what pacman would ship). If the tree is
#     dirty the inventory below says so — install the package, not the binary,
#     when you need "what users get".
#   * the static export is built once and reused by both (same lock, same
#     .out-instance marker), so instance/origin flags apply to both or neither.
#
# Not covered here, by design: the backend server (deployed remotely, see
# docs/FOLLOWUPS.md) and the embedded-libmpv prefix build (Track E — once
# scripts/build-libmpv.sh exists, both the binary link and the package will
# grow a dependency on it and this script gains a step).
#
# Nothing here needs root except --install.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PKGDIR="$ROOT/packaging/arch"

# Default instance is 'w2g' (https://vergoboy.ir/w2g) — the backend the native
# client talks to; build-native.sh has the same default. build-arch.sh alone
# defaults to the historical 'stream' instance, so always pass it explicitly
# here to keep both artifacts on one backend.
INSTANCE="${INSTANCE:-w2g}"
API_ORIGIN="${API_ORIGIN:-https://vergoboy.ir}"

BUILD_BINARY=1
BUILD_PACKAGE=1
REBUILD_UI=0
CLEAN_FIRST=0
INSTALL=0
COPY_OUT=""

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,29p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --instance)    shift || die "--instance needs a name"; INSTANCE="$1" ;;
    --no-package)  BUILD_PACKAGE=0 ;;
    --no-binary)   BUILD_BINARY=0 ;;
    --rebuild-ui)  REBUILD_UI=1 ;;
    --clean-first) CLEAN_FIRST=1 ;;
    --install)     INSTALL=1 ;;
    --out)         shift || die "--out needs a directory"; COPY_OUT="$1" ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

[ "$BUILD_BINARY" -eq 1 ] || [ "$BUILD_PACKAGE" -eq 1 ] \
  || die "--no-binary and --no-package together leave nothing to build"

# Exported so both child scripts see the same values even if only one got the
# --instance flag (both read INSTANCE/API_ORIGIN from the environment too).
export INSTANCE API_ORIGIN

head_short="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
dirty=""
# NB: an `if`, not `[ -n … ] && dirty=…` — a failing && list is the last
# command of its line and errexit would kill the script on a *clean* tree.
if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then
  dirty=" (working tree dirty)"
fi

say "final build: instance '$INSTANCE', API $API_ORIGIN, HEAD $head_short$dirty"

# ── 1. production binary from the working tree ──────────────────────────────

if [ "$BUILD_BINARY" -eq 1 ]; then
  native_args=(--instance "$INSTANCE")
  [ "$REBUILD_UI" -eq 1 ] && native_args+=(--rebuild-ui)
  "$ROOT/scripts/build-native.sh" "${native_args[@]}"
fi

BIN="$ROOT/webapp/src-tauri/target/release/vergoboy-stream"

# ── 2. installable package from a clone of HEAD ─────────────────────────────

if [ "$BUILD_PACKAGE" -eq 1 ]; then
  arch_args=(--instance "$INSTANCE")
  [ "$REBUILD_UI" -eq 1 ] && arch_args+=(--rebuild-ui)
  [ "$CLEAN_FIRST" -eq 1 ] && arch_args+=(--clean-first)
  [ "$INSTALL" -eq 1 ]     && arch_args+=(--install)
  "$ROOT/build-arch.sh" "${arch_args[@]}"
fi

# Same pick as build-arch.sh: newest non-debug artifact (makepkg finishes the
# -debug split after the main package, so "newest wins" must exclude it).
PKG="$(ls -t "$PKGDIR/vergoboy-$INSTANCE-"*.pkg.tar.zst 2>/dev/null | grep -v -- '-debug-' | head -1 || true)"

# ── 3. final inventory ──────────────────────────────────────────────────────

hash_of() { sha256sum "$1" 2>/dev/null | cut -c1-16 || echo '?'; }
size_of() { du -h "$1" 2>/dev/null | cut -f1 || echo '?'; }

say "final artifacts (instance '$INSTANCE')"
if [ "$BUILD_BINARY" -eq 1 ]; then
  [ -x "$BIN" ] || die "expected binary $BIN is missing — see the build output above"
  printf '    binary   %s\n' "$BIN"
  printf '               size %s  sha256 %s…  source: working tree%s\n' \
    "$(size_of "$BIN")" "$(hash_of "$BIN")" "$dirty"
else
  if [ -x "$BIN" ]; then
    printf '    binary   %s (not rebuilt this run)\n' "$BIN"
  fi
fi
if [ "$BUILD_PACKAGE" -eq 1 ]; then
  [ -n "$PKG" ] || die "expected package under $PKGDIR/vergoboy-$INSTANCE-*.pkg.tar.zst — see build-arch.sh output above"
  printf '    package  %s\n' "$PKG"
  printf '               size %s  sha256 %s…  source: clone of HEAD %s\n' \
    "$(size_of "$PKG")" "$(hash_of "$PKG")" "$head_short"
fi

if [ -n "$COPY_OUT" ]; then
  mkdir -p "$COPY_OUT"
  sums="$COPY_OUT/SHA256SUMS"
  : > "$sums"
  if [ "$BUILD_BINARY" -eq 1 ]; then
    cp -f "$BIN" "$COPY_OUT/"
    (cd "$COPY_OUT" && sha256sum "$(basename "$BIN")" >> "$sums")
  fi
  if [ -n "$PKG" ]; then
    cp -f "$PKG" "$COPY_OUT/"
    (cd "$COPY_OUT" && sha256sum "$(basename "$PKG")" >> "$sums")
  fi
  say "copied artifacts + SHA256SUMS to $COPY_OUT"
fi

[ "$INSTALL" -eq 1 ] || [ "$BUILD_PACKAGE" -eq 0 ] || \
  say "install with: sudo pacman -U $PKG"
[ "$BUILD_BINARY" -eq 0 ] || say "run the working-tree binary with: $BIN"
