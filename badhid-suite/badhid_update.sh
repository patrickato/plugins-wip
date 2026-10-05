#!/bin/bash
# badhid_update.sh - make a `git pull` actually take effect. Copies the updated
# plugin + payloads into the live locations and restarts pwnagotchi so the new
# code is running. Run ON the pi from inside badhid-suite/ after you pull.
#
#   cd ~/plugins-wip/badhid-suite && git -C .. pull && sudo ./badhid_update.sh
#
# It does NOT touch your config.toml (your token/bind_scope/etc. stay as they
# are). New plugin options just fall back to their built-in defaults until you
# add them. Env overrides: PLUGINS_DIR=... PAYLOADS_DIR=...

set -euo pipefail

SUITE_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGINS_DIR="${PLUGINS_DIR:-/etc/pwnagotchi/custom-plugins}"
PAYLOADS_DIR="${PAYLOADS_DIR:-/etc/pwnagotchi/badhid_ng/payloads}"

[ -f "$SUITE_DIR/badhid_ng.py" ] || { echo "ERROR: run me from inside badhid-suite/." >&2; exit 1; }

mkdir -p "$PLUGINS_DIR" "$PAYLOADS_DIR"
cp -a "$SUITE_DIR/badhid_ng.py" "$PLUGINS_DIR/"
n=0
for f in "$SUITE_DIR"/payloads/*.duck; do
  [ -e "$f" ] || continue
  cp -a "$f" "$PAYLOADS_DIR/"; n=$((n+1))
done
echo "updated: badhid_ng.py -> $PLUGINS_DIR/ ; $n payload(s) -> $PAYLOADS_DIR/"

if command -v systemctl >/dev/null 2>&1; then
  systemctl restart pwnagotchi && echo "restarted pwnagotchi - new code is live" \
    || echo "(couldn't restart pwnagotchi; restart it manually to load the new code)"
else
  echo "(no systemctl here; restart pwnagotchi to load the new code)"
fi
echo "Tip: sudo ./badhid_doctor.sh to confirm everything's green."
