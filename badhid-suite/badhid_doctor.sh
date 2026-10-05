#!/bin/bash
# badhid_doctor.sh - check every link in the chain and tell you the ONE next
# thing to do. Run ON the pi. Read-only: it changes nothing.
#
#   sudo ./badhid_doctor.sh
#
# It walks: board capability -> dwc2/UDC -> /dev/hidg0 gadget -> plugin file ->
# config block + token -> plugin enabled -> control server reachable, and prints
# a PASS/TODO for each with the exact command to fix the first thing that's off.

CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
PLUGINS_DIR="${PLUGINS_DIR:-/etc/pwnagotchi/custom-plugins}"
SUITE_DIR="$(cd "$(dirname "$0")" && pwd)"
NEXT=""

ok()   { echo "  [ OK ] $1"; }
todo() { echo "  [TODO] $1"; [ -z "$NEXT" ] && NEXT="$2"; }
info() { echo "         $1"; }

echo "===== BadHID doctor ====="
echo "  (tip: 'sudo ./badhid_setup.sh' can just do all of the below for you)"

# 1. board
MODEL="$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0')"; MODEL="${MODEL:-unknown}"
case "$MODEL" in
  *"Pi 400"*|*"Pi 3 Model B"*|*"Pi 2 Model"*|*"Pi Model"*)
    echo "  [STOP] $MODEL has a power-only USB port - it can't do HID. See COMPATIBILITY.md."; exit 0 ;;
  *"Pi 5"*) ok "board: $MODEL (gadget = experimental)";;
  *) ok "board: $MODEL";;
esac

# 2. dwc2 / UDC
if [ -n "$(ls /sys/class/udc 2>/dev/null)" ]; then
  ok "USB device controller present: $(ls /sys/class/udc | tr '\n' ' ')"
else
  todo "no USB device controller (UDC) - USB-C isn't in gadget mode yet" \
       "sudo ./enable_dwc2.sh && sudo reboot"
  info "then re-run this doctor after the reboot"
fi

# 3. /dev/hidg0 gadget
if [ -e /dev/hidg0 ]; then
  ok "/dev/hidg0 present (keyboard gadget is up)"
else
  if [ -n "$(ls /sys/class/udc 2>/dev/null)" ]; then
    todo "/dev/hidg0 missing - gadget not brought up yet" \
         "sudo ./setup_composite_gadget.sh --hid-only"
  else
    info "/dev/hidg0 will appear once the UDC exists and the gadget is up"
  fi
fi

# 4. plugin file
if [ -f "$PLUGINS_DIR/badhid_ng.py" ]; then
  ok "plugin installed: $PLUGINS_DIR/badhid_ng.py"
else
  todo "plugin not installed" "sudo ./badhid_install.sh"
fi

# 5. config block + token
if grep -q '^\[main\.plugins\.badhid_ng\]' "$CONFIG" 2>/dev/null; then
  ok "config block present in $CONFIG"
  TOK="$(grep -A40 '^\[main\.plugins\.badhid_ng\]' "$CONFIG" | grep -m1 '^auth_token' | cut -d'"' -f2)"
  if [ -n "$TOK" ] && [ "${#TOK}" -ge 12 ]; then ok "auth_token looks set (${#TOK} chars)"
  else todo "auth_token missing/short - server won't start" "edit $CONFIG and set a long auth_token"; fi
  EN="$(grep -A40 '^\[main\.plugins\.badhid_ng\]' "$CONFIG" | grep -m1 '^enabled' | tr -d ' ')"
  if [ "$EN" = "enabled=true" ]; then ok "plugin enabled"
  else todo "plugin not enabled yet (enabled=false)" "set enabled = true in $CONFIG, then: sudo systemctl restart pwnagotchi"; fi
else
  todo "no config block" "sudo ./badhid_install.sh"
fi

# 6. control server reachable (only meaningful if enabled)
PORT="$(grep -A40 '^\[main\.plugins\.badhid_ng\]' "$CONFIG" 2>/dev/null | grep -m1 '^port' | tr -dc '0-9')"
PORT="${PORT:-8083}"
if command -v curl >/dev/null 2>&1; then
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$PORT/" 2>/dev/null)"
  if [ "$CODE" = "401" ]; then ok "control server answering on 127.0.0.1:$PORT (401 = up, needs token)"
  elif [ "$CODE" = "200" ]; then ok "control server answering on 127.0.0.1:$PORT"
  else info "control server not answering on 127.0.0.1:$PORT (fine if plugin isn't enabled yet; check the pwnagotchi log for the bound URL)"; fi
fi

echo "-------------------------"
if [ -z "$NEXT" ]; then
  echo "  Everything's in place. Plug into a target, then:"
  echo "    sudo ./badhidctl.sh arm && sudo ./badhidctl.sh fire hello_world.duck"
else
  echo "  NEXT STEP:"
  echo "    $NEXT"
fi
echo "========================="
