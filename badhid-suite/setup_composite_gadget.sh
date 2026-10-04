#!/bin/bash
# setup_composite_gadget.sh - bring up a composite USB gadget (CDC-ECM
# ethernet + HID keyboard) on a gadget-mode Pi, so pwnagotchi keeps its
# usb0 management link AND gains a /dev/hidg0 keyboard for badhid_ng.
#
# ============================  READ THIS FIRST  ============================
#
#  * This RECONFIGURES the live USB gadget. If you are reaching the pi
#    ONLY over usb0 (the USB-data cable to your laptop), this can drop
#    that link mid-run and lock you out. Before running it, have a
#    fallback way in: SSH over Wi-Fi/Tailscale, or a keyboard+HDMI on the
#    pi. Do a dry run with a fallback path confirmed working.
#  * This is for YOUR OWN pi, to test against YOUR OWN hardware. A HID
#    keyboard gadget types into whatever it is physically plugged into and
#    cannot tell one host from another - only plug it into machines you
#    own or are authorized to test.
#  * It must run as root. It is deliberately NOT run by the plugin; you
#    run it by hand so the gadget change is always a conscious act.
#  * It's written against the jayofelony 64-bit image's dwc2/libcomposite
#    setup. If your image already builds its gadget a different way
#    (e.g. a systemd unit or /boot config), reconcile with that first
#    rather than running this blind. There is a --teardown to undo it.
#
# Usage:
#   sudo ./setup_composite_gadget.sh            # bring the gadget up
#   sudo ./setup_composite_gadget.sh --teardown # remove the gadget
#   sudo ./setup_composite_gadget.sh --status   # show current state
#
# ==========================================================================

set -euo pipefail

GADGET_DIR=/sys/kernel/config/usb_gadget/badhid
# Stable, locally-administered MACs (the '2' in the first octet). Change if
# they collide with anything on your bench.
HOST_MAC="02:11:22:33:44:55"   # the address the HOST (target) sees
DEV_MAC="02:11:22:33:44:66"    # the address the pi uses for usb0

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root (sudo $0 ...)" >&2
    exit 1
  fi
}

find_udc() {
  local udc
  udc=$(ls /sys/class/udc 2>/dev/null | head -n1 || true)
  if [ -z "${udc}" ]; then
    echo "ERROR: no UDC found under /sys/class/udc." >&2
    echo "       dwc2 must be enabled (dtoverlay=dwc2) and the module loaded" >&2
    echo "       (modprobe dwc2; modprobe libcomposite). Is this a gadget-" >&2
    echo "       capable port/board?" >&2
    exit 1
  fi
  echo "${udc}"
}

status() {
  echo "=== libcomposite ==="; lsmod | grep -q libcomposite && echo "loaded" || echo "NOT loaded"
  echo "=== gadget dir ==="; [ -d "${GADGET_DIR}" ] && echo "${GADGET_DIR} exists" || echo "no badhid gadget"
  echo "=== UDC binding ==="; [ -f "${GADGET_DIR}/UDC" ] && cat "${GADGET_DIR}/UDC" || echo "(none)"
  echo "=== hidg nodes ==="; ls -l /dev/hidg* 2>/dev/null || echo "(none)"
  echo "=== usb0 ==="; ip -brief addr show usb0 2>/dev/null || echo "(no usb0)"
}

teardown() {
  require_root
  if [ ! -d "${GADGET_DIR}" ]; then
    echo "No badhid gadget to tear down."
    return 0
  fi
  echo "" > "${GADGET_DIR}/UDC" 2>/dev/null || true
  # functions must be unlinked from configs before dirs can be removed
  rm -f "${GADGET_DIR}/configs/c.1/hid.usb0" 2>/dev/null || true
  rm -f "${GADGET_DIR}/configs/c.1/ecm.usb0" 2>/dev/null || true
  rmdir "${GADGET_DIR}/configs/c.1/strings/0x409" 2>/dev/null || true
  rmdir "${GADGET_DIR}/configs/c.1" 2>/dev/null || true
  rmdir "${GADGET_DIR}/functions/hid.usb0" 2>/dev/null || true
  rmdir "${GADGET_DIR}/functions/ecm.usb0" 2>/dev/null || true
  rmdir "${GADGET_DIR}/strings/0x409" 2>/dev/null || true
  rmdir "${GADGET_DIR}" 2>/dev/null || true
  echo "Teardown complete."
}

bringup() {
  require_root
  echo "WARNING: this reconfigures the live USB gadget and may drop usb0."
  echo "Press Ctrl-C within 5s to abort (have an SSH-over-wifi fallback up)."
  sleep 5

  modprobe libcomposite 2>/dev/null || true
  local udc; udc=$(find_udc)

  if [ -d "${GADGET_DIR}" ]; then
    echo "Existing badhid gadget found - tearing it down first."
    teardown
  fi

  mkdir -p "${GADGET_DIR}"
  cd "${GADGET_DIR}"
  echo 0x1d6b > idVendor   # Linux Foundation
  echo 0x0104 > idProduct  # Multifunction Composite Gadget
  echo 0x0100 > bcdDevice
  echo 0x0200 > bcdUSB

  mkdir -p strings/0x409
  echo "000badhid0001" > strings/0x409/serialnumber
  echo "tinker-shop"    > strings/0x409/manufacturer
  echo "BadHID Composite (ECM+HID)" > strings/0x409/product

  mkdir -p configs/c.1/strings/0x409
  echo "ECM + HID" > configs/c.1/strings/0x409/configuration
  echo 250 > configs/c.1/MaxPower

  # --- CDC-ECM ethernet function (this is what keeps usb0 alive) ---
  mkdir -p functions/ecm.usb0
  echo "${HOST_MAC}" > functions/ecm.usb0/host_addr
  echo "${DEV_MAC}"  > functions/ecm.usb0/dev_addr

  # --- HID keyboard function ---
  mkdir -p functions/hid.usb0
  echo 1 > functions/hid.usb0/protocol      # 1 = keyboard
  echo 1 > functions/hid.usb0/subclass      # 1 = boot interface
  echo 8 > functions/hid.usb0/report_length # 8-byte boot keyboard report
  # Standard 8-byte boot-keyboard HID report descriptor:
  printf '\x05\x01\x09\x06\xa1\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0' > functions/hid.usb0/report_desc

  ln -s functions/ecm.usb0 configs/c.1/
  ln -s functions/hid.usb0 configs/c.1/

  echo "${udc}" > UDC
  sleep 1

  echo "Gadget up. /dev/hidg0 should now exist:"
  ls -l /dev/hidg0 2>/dev/null || echo "  (hidg0 missing - check dmesg)"
  echo "Bringing usb0 up (adjust addressing to match your image's scheme):"
  ip link set usb0 up 2>/dev/null || echo "  (couldn't bring usb0 up - your image may manage it)"
  echo "Done. Re-run with --status to inspect."
}

case "${1:-}" in
  --teardown) teardown ;;
  --status)   status ;;
  "" )        bringup ;;
  * ) echo "Usage: sudo $0 [--teardown|--status]"; exit 2 ;;
esac
