#!/bin/bash
# setup_ble_hid.sh - prepare a pi to act as a BLE HID keyboard. Run ON the pi.
#
# Installs the BlueZ/D-Bus Python deps the transport needs, checks the adapter,
# and prints the state. It does NOT start advertising - the GATT HID server is
# the on-device bring-up step (see BLE_DESIGN.md).
#
#   sudo ./setup_ble_hid.sh
#   sudo ./setup_ble_hid.sh --status
set -eu

status() {
  echo "=== bluez ===" ; bluetoothctl --version 2>/dev/null || echo "bluetoothctl not found"
  echo "=== adapters ===" ; hciconfig -a 2>/dev/null | grep -E "^hci|BD Address|UP|DOWN" || echo "no adapters (hciconfig)"
  echo "=== python dbus ===" ; python3 -c "import dbus, gi; print('dbus + gi OK')" 2>/dev/null || echo "python3-dbus / gi missing"
  echo "=== BT service ===" ; systemctl is-active bluetooth 2>/dev/null || echo "bluetooth service not active"
}

if [ "${1:-}" = "--status" ]; then status; exit 0; fi

if [ "$(id -u)" -ne 0 ]; then echo "run as root (sudo $0)"; exit 1; fi

echo ">>> installing BlueZ + python dbus deps"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq || true
  apt-get install -y bluez python3-dbus python3-gi libglib2.0-dev || \
    echo "apt install reported an issue - check the output above"
else
  echo "no apt-get here - install bluez, python3-dbus, python3-gi yourself"
fi

echo ">>> ensuring the bluetooth service is up"
systemctl enable bluetooth 2>/dev/null || true
systemctl start bluetooth 2>/dev/null || true
# bring the adapter up (ignore if already up)
hciconfig hci0 up 2>/dev/null || true

echo ""
status
echo ""
echo "=============================================================="
echo " Deps in place. REMAINING (on-device bring-up, see BLE_DESIGN.md):"
echo "   1) wire the GATT HID application (HidApplication) into"
echo "      BLEHidTransport.start()/send_report() in badhid_ble_ng.py"
echo "   2) pair a device YOU OWN, open a text field"
echo "   3) arm + fire hello_world.duck and watch it type over the air"
echo ""
echo " Radio note: BLE recon (bettercap) and an HID peripheral share the"
echo " adapter. Pause BT recon while advertising, or use a second BT dongle"
echo " dedicated to HID."
echo "=============================================================="
