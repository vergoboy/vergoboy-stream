#!/usr/bin/env bash
#
# Provision and prove out an Archil disk for this project.
#
# Run this once you have an Archil API key (https://console.archil.com) and a
# machine with outbound network access. It performs steps 3-6 of the Archil
# setup procedure: verify access, create a disk, then prove that data written by
# one serverless exec survives into a second, separate exec.
#
# Required environment (see .env.example):
#   ARCHIL_API_KEY   key-...
# Optional:
#   ARCHIL_REGION    default aws-us-east-1 (must support serverless exec)
#   ARCHIL_DISK_NAME default vergoboy-stream-workspace
#   ARCHIL_DISK_ID   set to reuse an existing disk instead of creating one
#   DISK_CLI         default "npx --yes disk" (override for tests)
#
# Usage:
#   ARCHIL_API_KEY=key-... ARCHIL_REGION=aws-us-east-1 scripts/archil_provision.sh
#
set -euo pipefail

DISK_CLI="${DISK_CLI:-npx --yes disk}"
REGION="${ARCHIL_REGION:-aws-us-east-1}"
DISK_NAME="${ARCHIL_DISK_NAME:-vergoboy-stream-workspace}"

fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

[ -n "${ARCHIL_API_KEY:-}" ] || fail "ARCHIL_API_KEY is not set (get one at https://console.archil.com)"

case "$REGION" in
  aws-us-east-1|aws-us-west-2|aws-eu-west-1) ;;
  *) fail "ARCHIL_REGION='$REGION' does not support serverless exec (use aws-us-east-1, aws-us-west-2 or aws-eu-west-1)" ;;
esac

export ARCHIL_API_KEY ARCHIL_REGION="$REGION"

echo "== Step 3: verify access (region: $REGION) =="
$DISK_CLI list

disk_id="${ARCHIL_DISK_ID:-}"
if [ -z "$disk_id" ]; then
  echo
  echo "== Step 4: create disk '$DISK_NAME' =="
  create_out="$($DISK_CLI create "$DISK_NAME")"
  printf '%s\n' "$create_out"
  disk_id="$(printf '%s\n' "$create_out" | grep -oE 'dsk-[A-Za-z0-9_-]+' | head -n1 || true)"
  [ -n "$disk_id" ] || fail "could not parse a 'dsk-...' id from create output; set ARCHIL_DISK_ID and re-run"
  echo
  echo "NOTE: a one-time mount token was printed above. Store it now if you will"
  echo "      mount this disk directly — it is not shown again."
else
  echo "(reusing existing disk $disk_id)"
fi

echo
echo "== Step 5: prove exec runs =="
$DISK_CLI exec "$disk_id" "echo hello from archil > /mnt/data/hello.txt && ls -la /mnt/data"

echo
echo "== Step 6: prove persistence (separate exec) =="
# The container from step 5 is gone; only the disk survives. If this prints the
# line written above, the integration works.
out="$($DISK_CLI exec "$disk_id" "cat /mnt/data/hello.txt")"
printf '%s\n' "$out"
case "$out" in
  *"hello from archil"*)
    echo
    echo "PASS: persistence verified for disk $disk_id (region $REGION)"
    ;;
  *)
    fail "hello.txt did not read back; the disk is not persisting across execs"
    ;;
esac
