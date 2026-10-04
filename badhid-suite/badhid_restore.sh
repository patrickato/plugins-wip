#!/bin/bash
# badhid_restore.sh - put the pi back exactly how it was before badhid. Run ON
# the pi. Restores config.toml (+ custom-plugins) from a backup tarball,
# removes the badhid plugin, and tears down the runtime USB gadget.
#
# Usage:
#   sudo ./badhid_restore.sh                 # use the newest backup
#   sudo ./badhid_restore.sh <backup.tar.gz> # use a specific backup
#   sudo ./badhid_restore.sh --reboot        # ...and reboot at the end
#
# Env overrides: CONFIG=... PLUGINS_DIR=... BACKUP_ROOT=...

set -euo pipefail

CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
PLUGINS_DIR="${PLUGINS_DIR:-/etc/pwnagotchi/custom-plugins}"
BACKUP_ROOT="${BACKUP_ROOT:-/etc/pwnagotchi/badhid_backups}"
SUITE_DIR="$(cd "$(dirname "$0")" && pwd)"

DO_REBOOT=0
TARBALL=""
for arg in "$@"; do
  case "$arg" in
    --reboot) DO_REBOOT=1 ;;
    *.tar.gz) TARBALL="$arg" ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

if [ -z "$TARBALL" ]; then
  TARBALL="$(ls -1t "$BACKUP_ROOT"/pwn-backup-*.tar.gz 2>/dev/null | head -n1 || true)"
fi
if [ -z "$TARBALL" ] || [ ! -f "$TARBALL" ]; then
  echo "ERROR: no backup tarball found (looked in $BACKUP_ROOT). Pass one explicitly." >&2
  exit 1
fi

echo "Restoring from: $TARBALL"

# Safety: snapshot the CURRENT state before overwriting it, so a restore is
# itself reversible.
PRE="$BACKUP_ROOT/pre-restore-$(date +%Y%m%d-%H%M%S).tar.gz"
mkdir -p "$BACKUP_ROOT"
tar -czf "$PRE" --absolute-names "$CONFIG" \
  $([ -d "$PLUGINS_DIR" ] && echo "$PLUGINS_DIR") 2>/dev/null || true
echo "  (current state saved first to $PRE)"

# 1) tear down the runtime gadget if the setup script is here.
if [ -x "$SUITE_DIR/setup_composite_gadget.sh" ]; then
  echo "  tearing down composite gadget..."
  "$SUITE_DIR/setup_composite_gadget.sh" --teardown || \
    echo "  (teardown reported an issue; a reboot also clears the runtime gadget)"
fi

# 2) remove the badhid plugin file.
if [ -f "$PLUGINS_DIR/badhid_ng.py" ]; then
  rm -f "$PLUGINS_DIR/badhid_ng.py"
  echo "  removed $PLUGINS_DIR/badhid_ng.py"
fi

# 3) restore config + plugins from the tarball (absolute paths inside it).
tar -xzf "$TARBALL" --absolute-names -C / 2>/dev/null
echo "  restored config + custom-plugins from backup"

# 4) restart pwnagotchi so the restored config takes effect.
if command -v systemctl >/dev/null 2>&1; then
  systemctl restart pwnagotchi 2>/dev/null && echo "  restarted pwnagotchi" || \
    echo "  (could not restart pwnagotchi via systemctl; restart it manually)"
fi

echo "RESTORE COMPLETE. The badhid_ng config block, if present, is whatever the"
echo "backup contained (i.e. gone if you backed up before installing)."

if [ "$DO_REBOOT" = "1" ]; then
  echo "Rebooting in 5s (Ctrl-C to cancel)..."; sleep 5; reboot
else
  echo "If anything still looks off, a reboot fully clears the runtime gadget:  sudo reboot"
fi
