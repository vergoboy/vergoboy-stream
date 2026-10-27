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

# This host sees intermittent rustc SIGSEGVs (two different toolchains, random
# crates, no compiler diagnostic — see the toolchain notes in
# packaging/arch/PKGBUILD's build()). A crash aborts the whole build step with
# nothing wrong in the source, so each Rust build step gets exactly one retry.
# Bounded on purpose: a real compile error costs one wasted attempt and then
# fails loudly with the diagnosis spelled out.
retry_once() {  # retry_once <step description> <command...>
  local what="$1"
  shift
  if "$@"; then
    return 0
  fi
  warn "$what failed — retrying once (this machine's rustc crashes intermittently; a real compile error will fail again right now)."
  warn "  if it fails again, check the machine rather than the code: dmesg | grep -iE 'segfault|general protection', and memtest86+"
  if ! "$@"; then
    die "$what failed twice — stopping."
  fi
}

# Offline first (makepkg/--offline hosts), plain build as fallback — the same
# expression build-final.sh used inline before retry_once() wrapped it.
w2g_cargo() {  # w2g_cargo <dir>
  (cd "$1" && cargo build --release --offline 2>/dev/null || cargo build --release)
}

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

# Stage the w2g-mpv artifacts the PKGBUILD installs as /usr/bin/{w2g,w2g.so,
# w2g-server,w2g-token}. dist/ is gitignored, so the package reads these from
# this checkout (${startdir}/dist), never from makepkg's clone of HEAD — and
# because the directory survives between runs, it is rebuilt from scratch here:
# a stale binary from an older w2g-mpv build would otherwise be shipped as if it
# were current.
DIST="$ROOT/packaging/arch/dist"
if [ -d "$W2G_MPV_ROOT" ]; then
  say "building w2g-mpv components"
  if [ -x "$W2G_MPV_ROOT/scripts/build-w2g.sh" ]; then
    (cd "$W2G_MPV_ROOT" && bash scripts/build-w2g.sh)
  fi
  retry_once "w2g-mpv app build"     w2g_cargo "$W2G_MPV_ROOT/w2g/app"
  retry_once "w2g-mpv server build"  w2g_cargo "$W2G_MPV_ROOT/w2g/server"

  rm -rf "$DIST"
  mkdir -p "$DIST"
  missing=""
  stage() {  # stage <built-path> <installed-name>
    if [ -f "$1" ]; then
      install -Dm755 "$1" "$DIST/$2"
    else
      missing="$missing $2"
    fi
  }
  stage "$W2G_MPV_ROOT/build/w2g/w2g.so"                     w2g.so
  stage "$W2G_MPV_ROOT/w2g/app/target/release/w2g"           w2g
  stage "$W2G_MPV_ROOT/w2g/server/target/release/w2g-server" w2g-server
  stage "$W2G_MPV_ROOT/w2g/server/target/release/w2g-token"  w2g-token
  # Previously every copy was `|| true` and the "staged" line printed anyway, so
  # a w2g-mpv build that produced nothing still yielded a package silently
  # missing all four binaries. Fail before the long Tauri build instead.
  [ -z "$missing" ] || die "w2g-mpv did not produce:$missing (under $W2G_MPV_ROOT) — fix that build, or remove $W2G_MPV_ROOT to package without these components"
  say "staged w2g-mpv artifacts to packaging/arch/dist/"
elif [ "$BUILD_PACKAGE" -eq 1 ]; then
  warn "no $W2G_MPV_ROOT — the package will not contain w2g/w2g.so/w2g-server/w2g-token"
fi

# Build binary
if [ "$BUILD_BINARY" -eq 1 ]; then
  native_args=(--instance "$INSTANCE")
  [ "$REBUILD_UI" -eq 1 ] && native_args+=(--rebuild-ui)
  retry_once "Tauri desktop binary (scripts/build-native.sh)" \
    "$ROOT/scripts/build-native.sh" "${native_args[@]}"
fi

BIN="$ROOT/webapp/src-tauri/target/release/vergoboy-stream"

# Build package
if [ "$BUILD_PACKAGE" -eq 1 ]; then
  arch_args=(--instance "$INSTANCE")
  [ "$REBUILD_UI" -eq 1 ] && arch_args+=(--rebuild-ui)
  [ "$CLEAN_FIRST" -eq 1 ] && arch_args+=(--clean-first)
  [ "$INSTALL" -eq 1 ] && arch_args+=(--install)
  retry_once "Arch package (build-arch.sh → makepkg)" \
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

# `if`, not `[ -e … ] && printf`: with an empty (or absent) dist/ the glob
# stays literal, and as the last command of the script that failed test would
# be build-final.sh's exit status — a green build reporting failure.
for f in "$DIST"/*; do
  if [ -e "$f" ]; then
    printf '    staged   %s (size %s)\n' "$f" "$(size_of "$f")"
  fi
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
  cp -f "$DIST"/* "$COPY_OUT/" 2>/dev/null || true
  say "copied artifacts to $COPY_OUT"
fi
