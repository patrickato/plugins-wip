#!/bin/bash
# enable_dwc2.sh - put the Pi 4's USB-C port into gadget-capable mode so a USB
# Device Controller (UDC) appears, which badhid needs. Run ON the pi.
#
# It edits ONE devicetree line in config.txt:
#     dtoverlay=dwc2,dr_mode=host   ->   dtoverlay=dwc2,dr_mode=otg
# (or adds `dtoverlay=dwc2,dr_mode=otg` if no dwc2 overlay exists). That change
# only takes effect after a REBOOT. config.txt is backed up first, and the exact
# diff is shown. Fully reversible (restore the backup, reboot).
#
#   dr_mode meanings:
#     host       - USB-C only acts as a host (devices plug INTO it). No gadget.
#     peripheral - USB-C only acts as a device/gadget (deterministic). No host.
#     otg        - auto: device when plugged into a host PC, host with an OTG
#                  cable. Recommended for badhid - gadget when you need it,
#                  without giving up host capability.
#
# Usage:
#   sudo ./enable_dwc2.sh            # set dr_mode=otg (recommended)
#   sudo ./enable_dwc2.sh peripheral # force device-only mode instead
#   sudo ./enable_dwc2.sh --revert   # restore the most recent backup
#
# Env override: CONFIG_TXT=/path/to/config.txt

set -euo pipefail

# Find config.txt (Bookworm/jayofelony: /boot/firmware/config.txt; older: /boot)
if [ -n "${CONFIG_TXT:-}" ]; then
  CFG="$CONFIG_TXT"
elif [ -f /boot/firmware/config.txt ]; then
  CFG=/boot/firmware/config.txt
elif [ -f /boot/config.txt ]; then
  CFG=/boot/config.txt
else
  echo "ERROR: couldn't find config.txt (looked in /boot/firmware and /boot)." >&2
  exit 1
fi

MODE="otg"
REVERT=0
for a in "$@"; do
  case "$a" in
    otg|peripheral) MODE="$a" ;;
    --revert) REVERT=1 ;;
    *) echo "Unknown arg: $a (use otg | peripheral | --revert)"; exit 2 ;;
  esac
done

BK="$CFG.badhid.bak"

if [ "$REVERT" = "1" ]; then
  if [ -f "$BK" ]; then
    cp -a "$BK" "$CFG"
    echo "Reverted $CFG from $BK. Reboot to apply:  sudo reboot"
  else
    echo "No backup found at $BK - nothing to revert."
  fi
  exit 0
fi

# Back up once (don't clobber an earlier pre-change backup).
[ -f "$BK" ] || cp -a "$CFG" "$BK"
echo "Backup: $BK"

BEFORE="$(grep -n 'dtoverlay=dwc2' "$CFG" || true)"

if grep -q '^[[:space:]]*dtoverlay=dwc2' "$CFG"; then
  # Replace the whole dwc2 overlay line with a normalized one.
  sed -i -E "s|^[[:space:]]*dtoverlay=dwc2.*|dtoverlay=dwc2,dr_mode=$MODE|" "$CFG"
else
  # No dwc2 overlay: append one (under [all] if present, else EOF).
  printf '\n# added by enable_dwc2.sh for badhid gadget mode\ndtoverlay=dwc2,dr_mode=%s\n' "$MODE" >> "$CFG"
fi

AFTER="$(grep -n 'dtoverlay=dwc2' "$CFG" || true)"

echo "---- change ----"
echo "before: ${BEFORE:-'(no dwc2 line)'}"
echo "after : ${AFTER}"
echo "----------------"

# Sanity: config.txt is line-based, so just confirm the line is present/correct.
if grep -q "^dtoverlay=dwc2,dr_mode=$MODE" "$CFG"; then
  echo "OK: dtoverlay=dwc2,dr_mode=$MODE is set in $CFG"
else
  echo "!!! edit didn't land as expected - restoring backup."
  cp -a "$BK" "$CFG"
  exit 1
fi

echo ""
echo "=============================================================="
echo " REBOOT REQUIRED for this to take effect:   sudo reboot"
echo " You're on ethernet, so SSH will drop briefly and come back."
echo " After reboot, confirm a controller appeared:"
echo "     ls /sys/class/udc     # should list something now"
echo " Then bring up the gadget:"
echo "     cd ~/plugins-wip/badhid-suite && sudo ./setup_composite_gadget.sh --hid-only"
echo " To undo this change:   sudo ./enable_dwc2.sh --revert && sudo reboot"
echo "=============================================================="
