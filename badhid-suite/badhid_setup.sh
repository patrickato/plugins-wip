#!/bin/bash
# badhid_setup.sh - the "just works" guided setup. Run it ON the pi as root and
# it walks you from nothing to a working, fireable BadHID, doing each step for
# you and explaining it in plain language. It is SAFE TO RE-RUN: it checks what's
# already done and only does the next needed thing, so after the one reboot in
# the middle you just run it again and it picks up where it left off.
#
#   sudo ./badhid_setup.sh            # guided, asks before anything risky
#   sudo ./badhid_setup.sh --yes      # assume "yes" to the safe prompts
#   sudo ./badhid_setup.sh --no-phone # skip the phone-access question
#
# What it will NOT do on its own: reboot (it tells you when to), and expose the
# control page to your LAN unless you say yes. It never touches the Pwnagotchi
# engine or the radios - only the USB gadget + this plugin's own config block.
#
# This reuses the very same checks as ./badhid_doctor.sh; run the doctor any time
# for a read-only "what's my state + what's the one next step".
set -eu

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
PLUGINS_DIR="${PLUGINS_DIR:-/etc/pwnagotchi/custom-plugins}"
HOME_DIR="${BADHID_HOME:-/etc/pwnagotchi/badhid_ng}"
ASSUME_YES=0
DO_PHONE=ask
# Paths the hardware checks look at - overridable only so the test suite can
# simulate a pi. On a real pi these defaults are exactly right.
UDC_DIR="${UDC_DIR:-/sys/class/udc}"
HIDG_DEV="${HIDG_DEV:-/dev/hidg0}"
for a in "$@"; do
  case "$a" in
    --yes|-y) ASSUME_YES=1 ;;
    --no-phone) DO_PHONE=no ;;
    --phone) DO_PHONE=yes ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $a"; exit 2 ;;
  esac
done

say()  { echo; echo ">>> $*"; }
info() { echo "    $*"; }
ask()  { # ask "question" ; returns 0 for yes
  [ "$ASSUME_YES" = "1" ] && return 0
  [ -t 0 ] || return 1   # non-interactive: treat as "no" (the safe answer)
  printf "    %s [y/N] " "$1"; read -r ans || true
  case "${ans:-}" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo:  sudo ./badhid_setup.sh"; exit 1; }

echo "=================================================================="
echo "               BadHID setup - let's get you firing"
echo "=================================================================="

# ---- 1. board capability -------------------------------------------------
MODEL="unknown"
[ -r /proc/device-tree/model ] && MODEL="$(tr -d '\0' < /proc/device-tree/model)"
case "$MODEL" in
  *"Pi 400"*|*"Pi 3 Model B"*|*"Pi 2 Model"*|*"Pi Model"*)
    say "This board ($MODEL) has a power-only USB port."
    info "It physically cannot act as a USB keyboard, so BadHID can't run here."
    info "See COMPATIBILITY.md for the list of boards that can. Nothing was changed."
    exit 0 ;;
  *"Pi 5"*) say "Board: $MODEL  (gadget mode works but is experimental on Pi 5)";;
  unknown)  say "Board: unknown - continuing, but if steps fail see COMPATIBILITY.md";;
  *)        say "Board: $MODEL";;
esac

# ---- 2. plugin file installed -------------------------------------------
if [ -f "$PLUGINS_DIR/badhid_ng.py" ]; then
  info "Plugin already installed."
else
  say "Installing the plugin files..."
  # plugins-wip ships badhid_install.sh; the graduated copy ships install.sh.
  if [ -x "$SELF_DIR/badhid_install.sh" ]; then
    "$SELF_DIR/badhid_install.sh"
  elif [ -f "$SELF_DIR/install.sh" ]; then
    sh "$SELF_DIR/install.sh"
  else
    info "No installer found next to me - run the suite's install step first, then re-run."
    exit 1
  fi
fi

# ---- 3. USB device controller (dwc2) -----------------------------------
if [ -n "$(ls "$UDC_DIR" 2>/dev/null)" ]; then
  info "USB gadget mode is on (controller: $(ls "$UDC_DIR" | tr '\n' ' '))."
else
  say "USB gadget mode isn't on yet - that's the switch that lets the pi be a keyboard."
  info "I can set it now. It edits the boot config (backed up first) and needs ONE reboot."
  if ask "Turn on USB gadget mode now?"; then
    "$SELF_DIR/enable_dwc2.sh"
    echo
    say "Done. Now REBOOT, then run this setup again to finish:"
    info "    sudo reboot"
    info "    # after it comes back:"
    info "    sudo $0"
    exit 0
  else
    info "Skipped. Nothing changed. Re-run when you're ready to enable it."
    exit 0
  fi
fi

# ---- 4. /dev/hidg0 keyboard gadget -------------------------------------
if [ -e "$HIDG_DEV" ]; then
  info "Keyboard gadget is up (/dev/hidg0)."
else
  say "Bringing up the keyboard gadget (/dev/hidg0)..."
  info "Note: if the ONLY way you reach this pi is the USB-data cable to a laptop,"
  info "this can briefly drop that link. Over Wi-Fi/Ethernet/SSH you're fine."
  if ask "Bring up the keyboard gadget now?"; then
    "$SELF_DIR/setup_composite_gadget.sh" --hid-only
  else
    info "Skipped. Re-run when ready."
    exit 0
  fi
  if [ -e "$HIDG_DEV" ]; then info "Keyboard gadget is up."
  else info "Hmm, /dev/hidg0 still missing - run ./badhid_doctor.sh to see why."; exit 1; fi
fi

# ---- 5. plugin enabled in config ---------------------------------------
enabled_now() {
  awk '/^\[main\.plugins\.badhid_ng\][[:space:]]*$/{i=1;next}/^\[/{i=0}i' "$CONFIG" 2>/dev/null \
    | grep -m1 '^[[:space:]]*enabled[[:space:]]*=' | tr -d ' '
}
CFG_CHANGED=0
if [ "$(enabled_now)" = "enabled=true" ]; then
  info "Plugin is enabled in the config."
else
  say "Enabling the plugin in the config..."
  python3 "$SELF_DIR/badhid_setopt.py" "$CONFIG" enabled true
  CFG_CHANGED=1
fi

# ---- 6. phone access (optional) -----------------------------------------
bind_now() {
  awk '/^\[main\.plugins\.badhid_ng\][[:space:]]*$/{i=1;next}/^\[/{i=0}i' "$CONFIG" 2>/dev/null \
    | grep -m1 '^[[:space:]]*bind_scope[[:space:]]*=' | cut -d'"' -f2
}
BIND="$(bind_now)"; BIND="${BIND:-auto}"
WANT_PHONE=0
case "$DO_PHONE" in
  yes) WANT_PHONE=1 ;;
  no)  WANT_PHONE=0 ;;
  ask)
    if [ "$BIND" = "lan" ] || [ "$BIND" = "tailscale" ]; then
      info "Control page is already reachable off-pi (bind_scope=\"$BIND\")."
    else
      say "Want to fire from your PHONE (not just the pi itself)?"
      info "I'd set the control page to be reachable on your home network (LAN)."
      info "Anyone on your Wi-Fi who has the token could then control it."
      if ask "Enable phone access over your LAN?"; then WANT_PHONE=1; fi
    fi ;;
esac
if [ "$WANT_PHONE" = "1" ] && [ "$BIND" != "lan" ] && [ "$BIND" != "tailscale" ]; then
  python3 "$SELF_DIR/badhid_setopt.py" "$CONFIG" bind_scope '"lan"'
  CFG_CHANGED=1
  info "Set bind_scope=\"lan\"."
fi

# ---- 7. apply (restart pwnagotchi if we changed config) ----------------
if [ "$CFG_CHANGED" = "1" ]; then
  say "Applying - restarting pwnagotchi so the plugin picks up the config..."
  if systemctl restart pwnagotchi 2>/dev/null; then
    info "Restarted. Giving it a few seconds to come up..."; sleep 12
  else
    info "Couldn't restart via systemd - restart pwnagotchi yourself to apply."
  fi
fi

# ---- 8. done - show how to fire ----------------------------------------
echo
echo "=================================================================="
say  "All set. Here's how to fire your first (harmless) payload:"
echo "=================================================================="
info "On the pi:"
info "    sudo $SELF_DIR/badhidctl.sh arm"
info "    sudo $SELF_DIR/badhidctl.sh fire hello_world.duck"
info ""
info "It just types 'Hello World!' into whatever the pi is plugged into."
info "Plug the pi's USB data port into a PC you own, click into Notepad/a text box, fire."

if [ "$WANT_PHONE" = "1" ] || [ "$BIND" = "lan" ] || [ "$BIND" = "tailscale" ]; then
  echo
  say "...or from your phone - scan this:"
  BADHID_HOME="$HOME_DIR" CONFIG="$CONFIG" "$SELF_DIR/badhid_phone.sh" || true
fi
echo
info "Stuck on any step? Run:  sudo $SELF_DIR/badhid_doctor.sh"
echo "=================================================================="
