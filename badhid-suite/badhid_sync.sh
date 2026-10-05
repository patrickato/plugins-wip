#!/bin/bash
# badhid_sync.sh - copy this repo's payloads into the live payloads_dir the
# plugin actually reads. Run ON the pi after a `git pull` that added/changed
# payloads (a pull updates the repo clone, not the installed copy).
#
#   cd ~/plugins-wip/badhid-suite && sudo ./badhid_sync.sh
#
# Env override: PAYLOADS_DIR=/custom/path

set -euo pipefail
SUITE_DIR="$(cd "$(dirname "$0")" && pwd)"
PAYLOADS_DIR="${PAYLOADS_DIR:-/etc/pwnagotchi/badhid_ng/payloads}"

mkdir -p "$PAYLOADS_DIR"
n=0
for f in "$SUITE_DIR"/payloads/*.duck; do
  [ -e "$f" ] || continue
  cp -a "$f" "$PAYLOADS_DIR/"
  n=$((n+1))
done
echo "synced $n payload(s) -> $PAYLOADS_DIR"
ls -1 "$PAYLOADS_DIR"/*.duck 2>/dev/null | sed 's#.*/#  #' || echo "  (none)"
echo "The plugin picks these up immediately - no restart needed."
