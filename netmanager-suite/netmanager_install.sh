#!/bin/bash
# netmanager_install.sh - safe, reversible install of netmanager-suite onto the
# pi. Run ON the pi from inside the netmanager-suite folder of a plugins-wip
# clone.
#
#   1. BACKS UP config.toml first.
#   2. Copies netmanager_ng.py into custom-plugins and netmanager_phone.sh into
#      /etc/pwnagotchi/netmanager_ng/ (additive; other plugins untouched).
#   3. Appends the [main.plugins.netmanager_ng] block with a freshly generated
#      random auth_token, enabled=false, and authorized_targets=[] (empty gate).
#   4. VALIDATES config.toml still parses; auto-rolls back if not.
#
# It does NOT enable the plugin or add any authorized targets. See README.md.
#
# Usage:  sudo ./netmanager_install.sh
# Env overrides: CONFIG=... PLUGINS_DIR=...
set -eu

SUITE_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
PLUGINS_DIR="${PLUGINS_DIR:-/etc/pwnagotchi/custom-plugins}"
HOME_DIR="${NETMANAGER_HOME:-/etc/pwnagotchi/netmanager_ng}"

echo "### netmanager-suite installer ###"
echo "    suite   : $SUITE_DIR"
echo "    config  : $CONFIG"
echo "    plugins : $PLUGINS_DIR"
echo ""

[ -f "$SUITE_DIR/netmanager_ng.py" ] || { echo "ERROR: run me from inside netmanager-suite/." >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "ERROR: $CONFIG not found (set CONFIG=...)." >&2; exit 1; }

echo ">>> Step 1/4: backup config"
BACKUP_CONFIG="$CONFIG.$(date +%Y%m%d-%H%M%S).bak"
cp -a "$CONFIG" "$BACKUP_CONFIG"
echo "    backed up -> $BACKUP_CONFIG"
echo ""

echo ">>> Step 2/4: copy plugin + phone helper"
mkdir -p "$PLUGINS_DIR" "$HOME_DIR"
cp -a "$SUITE_DIR/netmanager_ng.py" "$PLUGINS_DIR/"
[ -f "$SUITE_DIR/netmanager_phone.sh" ] && { cp -a "$SUITE_DIR/netmanager_phone.sh" "$HOME_DIR/"; chmod +x "$HOME_DIR/netmanager_phone.sh"; }
[ -f "$SUITE_DIR/netmanagerctl.sh" ] && { cp -a "$SUITE_DIR/netmanagerctl.sh" "$HOME_DIR/"; chmod +x "$HOME_DIR/netmanagerctl.sh"; }
echo "    copied netmanager_ng.py -> $PLUGINS_DIR/  (+ netmanager_phone.sh, netmanagerctl.sh -> $HOME_DIR/)"
echo ""

echo ">>> Step 3/4: config block"
if grep -q '^\[main\.plugins\.netmanager_ng\]' "$CONFIG"; then
  echo "    [main.plugins.netmanager_ng] already present - leaving config untouched."
else
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))' 2>/dev/null || echo "")"
  if [ -z "$TOKEN" ]; then echo "ERROR: couldn't generate a token." >&2; cp -a "$BACKUP_CONFIG" "$CONFIG"; exit 1; fi
  {
    echo ""
    echo "# ===== netmanager-suite (added by netmanager_install.sh $(date -Iseconds)) ====="
    echo "# Random auth_token generated. enabled=false until you turn it on."
    echo "[main.plugins.netmanager_ng]"
    echo "enabled = false"
    echo "auth_token = \"$TOKEN\""
    echo "bind_scope = \"auto\""
    echo "port = 8085"
    echo "store_path = \"$HOME_DIR/networks.json\""
    echo "fire_timeout_seconds = 10"
    echo "handshakes_dir = \"/home/pi/handshakes\""
    echo "fleet_json_path = \"/home/pi/.config/fleetctl/fleet.json\""
    echo "authorized_targets = []"
    echo "ui_enabled = true"
    echo "ui_position_x = -40"
    echo "ui_position_y = 30"
  } >> "$CONFIG"
  mkdir -p "$HOME_DIR"
  echo "$TOKEN" > "$HOME_DIR/auth_token.txt"; chmod 600 "$HOME_DIR/auth_token.txt" 2>/dev/null || true
  echo "    appended [main.plugins.netmanager_ng] with a generated token (enabled=false)"
  echo "    >>> YOUR AUTH TOKEN: $TOKEN"
  echo "        (also saved root-only to $HOME_DIR/auth_token.txt)"
fi
echo ""

echo ">>> Step 4/4: validate config"
VALID=1
python3 - "$CONFIG" <<'PY' || VALID=0
import sys
try:
    import tomllib; tomllib.load(open(sys.argv[1], "rb"))
except ModuleNotFoundError:
    sys.exit(0)
except Exception as e:
    print("parse error:", e); sys.exit(1)
print("config.toml parses OK")
PY
if [ "$VALID" != "1" ]; then
  echo "!!! config.toml did NOT parse - ROLLING BACK."
  cp -a "$BACKUP_CONFIG" "$CONFIG"
  echo "    restored $CONFIG from $BACKUP_CONFIG"; exit 1
fi

echo ""
echo "=============================================================="
echo " INSTALL OK (safe state - plugin not enabled yet)."
echo ""
echo " Turn it on + make the page phone-reachable, then restart:"
echo "   sudo sed -i '/^\\[main\\.plugins\\.netmanager_ng\\]/,/^\\[/ s/^enabled = false/enabled = true/' $CONFIG"
echo "   sudo sed -i '/^\\[main\\.plugins\\.netmanager_ng\\]/,/^\\[/ s/^bind_scope = \"auto\"/bind_scope = \"lan\"/' $CONFIG"
echo "   sudo systemctl restart pwnagotchi && sleep 12"
echo ""
echo " Then open it on your phone (QR):"
echo "   sudo $HOME_DIR/netmanager_phone.sh"
echo ""
echo " Authorize a wifi_target to test (one command, no TOML editing):"
echo "   sudo $HOME_DIR/netmanagerctl.sh authorize <BSSID|SSID>   # only networks you're allowed to test"
echo "   sudo $HOME_DIR/netmanagerctl.sh list"
echo ""
echo " In the page: Bulk import -> pull your 50+ in one tap. Full undo:"
echo "   sudo cp -a $BACKUP_CONFIG $CONFIG && sudo systemctl restart pwnagotchi"
echo "=============================================================="
