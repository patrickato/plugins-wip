#!/bin/bash
# badhid_backup.sh - snapshot everything the badhid install could touch, so
# the pi can be returned to exactly its current state. Run ON the pi.
#
# Backs up (small, safe): config.toml + the custom-plugins dir (+ conf.d if
# present). Does NOT back up handshakes/pcaps (large, untouched by badhid).
#
# The USB gadget state is NOT backed up because it doesn't need to be: the
# badhid gadget change is runtime-only (configfs), so a plain reboot already
# restores the original gadget. This script covers the on-disk pieces.
#
# Usage:  sudo ./badhid_backup.sh
# Env overrides: CONFIG=/path/config.toml  PLUGINS_DIR=/path/custom-plugins

set -euo pipefail

CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
PLUGINS_DIR="${PLUGINS_DIR:-/etc/pwnagotchi/custom-plugins}"
CONFD="/etc/pwnagotchi/conf.d"
BACKUP_ROOT="${BACKUP_ROOT:-/etc/pwnagotchi/badhid_backups}"
TS="$(date +%Y%m%d-%H%M%S)"

if [ ! -f "$CONFIG" ]; then
  echo "ERROR: config not found at $CONFIG (set CONFIG=... if it's elsewhere)" >&2
  exit 1
fi

mkdir -p "$BACKUP_ROOT"

# 1) A bare copy of config.toml for trivial one-file restore.
cp -a "$CONFIG" "$BACKUP_ROOT/config.toml.$TS.bak"

# 2) A full tarball of the config + plugins (what an install can modify).
TARBALL="$BACKUP_ROOT/pwn-backup-$TS.tar.gz"
PATHS=("$CONFIG")
[ -d "$PLUGINS_DIR" ] && PATHS+=("$PLUGINS_DIR")
[ -d "$CONFD" ] && PATHS+=("$CONFD")

tar -czf "$TARBALL" --absolute-names "${PATHS[@]}" 2>/dev/null

SIZE="$(du -h "$TARBALL" | cut -f1)"
if command -v sha256sum >/dev/null 2>&1; then
  SHA="$(sha256sum "$TARBALL" | cut -d' ' -f1)"
else
  SHA="(sha256sum not available)"
fi

echo "=============================================================="
echo " BACKUP COMPLETE"
echo "   tarball : $TARBALL   ($SIZE)"
echo "   sha256  : $SHA"
echo "   config  : $BACKUP_ROOT/config.toml.$TS.bak"
echo "   contents:"
tar -tzf "$TARBALL" | sed 's/^/     /' | head -20
echo ""
echo " To restore later:"
echo "   sudo ./badhid_restore.sh $TARBALL"
echo " Or just the config file:"
echo "   sudo cp $BACKUP_ROOT/config.toml.$TS.bak $CONFIG && sudo systemctl restart pwnagotchi"
echo "=============================================================="
# Emit the tarball path on its own last line for scripting (install reads it).
echo "BACKUP_TARBALL=$TARBALL"
