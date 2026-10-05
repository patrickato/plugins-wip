#!/bin/bash
# enable_dwc2.sh - put the Pi's USB OTG port into gadget-capable mode so a USB
# Device Controller (UDC) appears, which badhid needs. Run ON the pi.
#
# It AUTO-DETECTS the board and does the right thing:
#   * Gadget-capable boards (Pi Zero/Zero W/Zero 2 W, Pi 4/4B, Pi 3A+): ensures
#       dtoverlay=dwc2,dr_mode=otg in config.txt (fixing a stale dr_mode=host).
#   * Pi 5: sets the same overlay but warns gadget support is experimental.
#   * Power-only boards (Pi 3B/3B+, Pi 400, Pi 1/2): refuses with a clear reason
#       (their USB port physically cannot act as a device) unless --force.
# The change only takes effect after a REBOOT. config.txt is backed up first and
# the diff is shown. Fully reversible: sudo ./enable_dwc2.sh --revert && reboot.
#
#   dr_mode: host=device-in only (no gadget) | peripheral=device only |
#            otg=auto (device when plugged into a host, host with an OTG cable).
#            otg is the default here - gadget when you need it, host still works.
#
# Usage:
#   sudo ./enable_dwc2.sh            # auto-detect, set dr_mode=otg (recommended)
#   sudo ./enable_dwc2.sh peripheral # force device-only mode
#   sudo ./enable_dwc2.sh --force    # proceed even on a board flagged incapable
#   sudo ./enable_dwc2.sh --revert   # restore the most recent backup
#
# Env override: CONFIG_TXT=/path/to/config.txt

set -euo pipefail

if [ -n "${CONFIG_TXT:-}" ]; then CFG="$CONFIG_TXT"
elif [ -f /boot/firmware/config.txt ]; then CFG=/boot/firmware/config.txt
elif [ -f /boot/config.txt ]; then CFG=/boot/config.txt
else echo "ERROR: couldn't find config.txt (/boot/firmware or /boot)." >&2; exit 1; fi

MODE="otg"; REVERT=0; FORCE=0
for a in "$@"; do
  case "$a" in
    otg|peripheral) MODE="$a" ;;
    --force) FORCE=1 ;;
    --revert) REVERT=1 ;;
    *) echo "Unknown arg: $a (use otg | peripheral | --force | --revert)"; exit 2 ;;
  esac
done

BK="$CFG.badhid.bak"

if [ "$REVERT" = "1" ]; then
  if [ -f "$BK" ]; then cp -a "$BK" "$CFG"; echo "Reverted $CFG. Reboot to apply: sudo reboot"
  else echo "No backup at $BK - nothing to revert."; fi
  exit 0
fi

# --- board detection --------------------------------------------------------
MODEL="$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0')"; MODEL="${MODEL:-unknown}"
echo "Board: $MODEL"

CAP="unknown"   # capable | experimental | incapable | unknown
# NOTE: order matters - "Pi 400" and "Pi 3 Model B" contain the substrings
# "Pi 4"/"Pi 3", so the incapable/specific cases MUST be tested first.
case "$MODEL" in
  *"Pi 400"*)                       CAP="incapable" ;; # USB-C power-only
  *"Pi 3 Model B"*)                 CAP="incapable" ;; # micro-USB power-only
  *"Pi 2 Model"*|*"Pi Model B"*|*"Pi Model A"*) CAP="incapable" ;;
  *"Pi 5"*)                         CAP="experimental" ;;
  *"Pi Zero"*)                      CAP="capable" ;;   # micro-USB OTG
  *"Pi 3 Model A"*|*"Pi 3A"*)       CAP="capable" ;;   # OTG
  *"Pi 4"*)                         CAP="capable" ;;   # USB-C OTG
  *"Compute Module"*)               CAP="capable" ;;   # CM USB OTG (header)
  *)                                CAP="unknown" ;;
esac

case "$CAP" in
  capable)      echo "USB gadget: supported on this board." ;;
  experimental) echo "USB gadget: EXPERIMENTAL on the Pi 5 - the power port can do"
                echo "            USB2 peripheral mode but support is newer/fiddlier."
                echo "            Proceeding; if no UDC appears after reboot, this board"
                echo "            may need a firmware update or isn't ready for it yet." ;;
  incapable)    echo "USB gadget: NOT POSSIBLE on this board - its USB port is power-only"
                echo "            (data runs through an onboard hub / no device controller)."
                echo "            badhid's HID injection needs a device-capable port, so"
                echo "            this board can't do the keystroke part."
                if [ "$FORCE" != "1" ]; then
                  echo "            Refusing (nothing changed). Re-run with --force only if you"
                  echo "            know this specific board has an OTG port."
                  exit 1
                fi
                echo "            --force given; proceeding anyway at your request." ;;
  unknown)      echo "USB gadget: couldn't classify this board; attempting the standard"
                echo "            dwc2 overlay. If no UDC appears after reboot, it likely"
                echo "            doesn't have a device-capable port." ;;
esac

# --- edit config.txt --------------------------------------------------------
[ -f "$BK" ] || cp -a "$CFG" "$BK"
echo "Backup: $BK"

BEFORE="$(grep -n 'dtoverlay=dwc2' "$CFG" || true)"
if grep -q '^[[:space:]]*dtoverlay=dwc2' "$CFG"; then
  sed -i -E "s|^[[:space:]]*dtoverlay=dwc2.*|dtoverlay=dwc2,dr_mode=$MODE|" "$CFG"
else
  printf '\n# added by enable_dwc2.sh for badhid gadget mode\ndtoverlay=dwc2,dr_mode=%s\n' "$MODE" >> "$CFG"
fi
AFTER="$(grep -n 'dtoverlay=dwc2' "$CFG" || true)"

echo "---- change ----"
echo "before: ${BEFORE:-'(no dwc2 line)'}"
echo "after : ${AFTER}"
echo "----------------"

if grep -q "^dtoverlay=dwc2,dr_mode=$MODE" "$CFG"; then
  echo "OK: dtoverlay=dwc2,dr_mode=$MODE set in $CFG"
else
  echo "!!! edit didn't land as expected - restoring backup."; cp -a "$BK" "$CFG"; exit 1
fi

echo ""
echo "=============================================================="
echo " REBOOT REQUIRED:   sudo reboot      (ethernet/wifi SSH survives)"
echo " After reboot, confirm a controller appeared:"
echo "     ls /sys/class/udc        # should list something now"
echo " Then bring up the keyboard gadget:"
echo "     cd ~/plugins-wip/badhid-suite && sudo ./setup_composite_gadget.sh --hid-only"
echo " Undo this change:   sudo ./enable_dwc2.sh --revert && sudo reboot"
echo "=============================================================="
