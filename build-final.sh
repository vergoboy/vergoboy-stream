#!/usr/bin/env bash
# vergoboy — build the FINAL desktop client + artifacts in one command
#
# This script builds:
# 1. Webapp export (for web + for Tauri package)
# 2. All Rust components from w2g-mpv (w2g.so, w2g CLI, w2g-server, w2g-token)
# 3. Tauri desktop binary (working tree)
# 4. Arch Linux .pkg.tar.zst package (from HEAD clone)
#
# Usage: see header below

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

W2G_MPV_ROOT="${ROOT}/../w2g-mpv"

PKGDIR="$ROOT/packaging/arch"

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

usage() {
  cat <<'USAGE'
vergoboy — build the FINAL desktop client and package

Options:
  --instance NAME   Build for instance (default: w2g)
  --no-package      Skip Arch package build
  --no-binary       Skip Tauri binary build
  --rebuild-ui      Force fresh web export
  --clean-first     Clean before building
  --install         pacman -U the package after build (sudo)
  --out DIR         Copy artifacts + SHA256SUMS to DIR
  -h, --help        Show this help
USAGE
}

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
    *)             die "unknown option: $1" ;;
  esac
  shift
done

[ "$BUILD_BINARY" -eq 1 ] || [ "$BUILD_PACKAGE" -eq 1 ] || die "nothing to build"

export INSTANCE API_ORIGIN

head_short="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
dirty=""
if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then
  dirty=" (working tree dirty)"
fi

say "final build: instance='$INSTANCE', API=$API_ORIGIN, HEAD $head_short$dirty"

# Build w2g-mpv artifacts if present
if [ -d "$W2G_MPV_ROOT" ]; then
  say "building w2g-mpv components"
  if [ -x "$W2G_MPV_ROOT/scripts/build-w2g.sh" ]; then
    (cd "$W2G_MPV_ROOT" && bash scripts/build-w2g.sh)
  fi
  (cd "$W2G_MPV_ROOT/w2g/app" && cargo build --release --offline 2>/dev/null || cargo build --release)
  (cd "$W2G_MPV_ROOT/w2g/server" && cargo build --release --offline 2>/dev/null || cargo build --release)
  mkdir -p "$ROOT/packaging/arch/dist"
  cp -f "$W2G_MPV_ROOT/build/w2g/w2g.so" "$ROOT/packaging/arch/dist/" 2>/dev/null || true
  cp -f "$W2G_MPV_ROOT/w2g/app/target/release/w2g" "$ROOT/packaging/arch/dist/" 2>/dev/null || true
  cp -f "$W2G_MPV_ROOT/w2g/server/target/release/w2g-server" "$ROOT/packaging/arch/dist/" 2>/dev/null || true
  cp -f "$W2G_MPV_ROOT/w2g/server/target/release/w2g-token" "$ROOT/packaging/arch/dist/" 2>/dev/null || true
  chmod +x "$ROOT/packaging/arch/dist/w2g" "$ROOT/packaging/arch/dist/w2g-server" "$ROOT/packaging/arch/dist/w2g-token" 2>/dev/null || true
  say "staged w2g-mpv artifacts to packaging/arch/dist/"
fi

# Build binary
if [ "$BUILD_BINARY" -eq 1 ]; then
  native_args=(--instance "$INSTANCE")
  [ "$REBUILD_UI" -eq 1 ] && native_args+=(--rebuild-ui)
  "$ROOT/scripts/build-native.sh" "${native_args[@]}"
fi

BIN="$ROOT/webapp/src-tauri/target/release/vergoboy-stream"

# Build package
if [ "$BUILD_PACKAGE" -eq 1 ]; then
  arch_args=(--instance "$INSTANCE")
  [ "$REBUILD_UI" -eq 1 ] && arch_args+=(--rebuild-ui)
  [ "$CLEAN_FIRST" -eq 1 ] && arch_args+=(--clean-first)
  [ "$INSTALL" -eq 1 ] && arch_args+=(--install)
  "$ROOT/build-arch.sh" "${arch_args[@]}"
fi

PKG="$(ls -t "$PKGDIR/vergoboy-$INSTANCE-"*.pkg.tar.zst 2>/dev/null | grep -v -- '-debug-' | head -1 || true)"

hash_of() { sha256sum "$1" 2>/dev/null | cut -c1-16 || echo '?'; }
size_of() { du -h "$1" 2>/dev/null | cut -f1 || echo '?'; }

say "final artifacts (instance '$INSTANCE')"
if [ "$BUILD_BINARY" -eq 1 ] && [ -x "$BIN" ]; then
  printf '    binary   %s\n' "$BIN"
  printf '               size %s  sha256 %s…\n' "$(size_of "$BIN")" "$(hash_of "$BIN")"
fi
if [ -n "$PKG" ]; then
  printf '    package  %s\n' "$PKG"
  printf '               size %s  sha256 %s…\n' "$(size_of "$PKG")" "$(hash_of "$PKG")"
fi

for f in "$ROOT/packaging/arch/dist"/*; do
  [ -e "$f" ] && printf '    staged   %s (size %s)\n' "$f" "$(size_of "$f")"
done

if [ -n "$COPY_OUT" ]; then
  mkdir -p "$COPY_OUT"
  sums="$COPY_OUT/SHA256SUMS"
  : > "$sums"
  if [ "$BUILD_BINARY" -eq 1 ] && [ -x "$BIN" ]; then
    cp -f "$BIN" "$COPY_OUT/"
    (cd "$COPY_OUT" && sha256sum "$(basename "$BIN")" >> "$sums")
  fi
  if [ -n "$PKG" ]; then
    cp -f "$PKG" "$COPY_OUT/"
    (cd "$COPY_OUT" && sha256sum "$(basename "$PKG")" >> "$sums")
  fi
  cp -f "$ROOT/packaging/arch/dist"/* "$COPY_OUT/" 2>/dev/null || true
  say "copied artifacts to $COPY_OUT"
fi
