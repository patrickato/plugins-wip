#!/bin/bash
# badhid_install.sh - safe, reversible install of badhid-suite onto the pi.
# Run ON the pi from inside the badhid-suite folder of a plugins-wip clone.
#
# What it does, in order:
#   1. BACKS UP first (calls badhid_backup.sh) - nothing is changed until a
#      backup exists.
#   2. Copies badhid_ng.py into custom-plugins and the demo payloads into
#      payloads_dir (additive; your other plugins are untouched).
#   3. Appends the [main.plugins.badhid_ng] config block ONLY if it isn't
#      already present, with a freshly generated random auth_token and
#      enabled=false.
#   4. VALIDATES that config.toml still parses. If it doesn't, it AUTO-ROLLS
#      BACK the config from the backup and aborts.
#
# What it deliberately does NOT do: bring up the USB gadget, set enabled=true,
# or fire anything. Those stay your deliberate manual steps (see README.md).
#
# Usage:  sudo ./badhid_install.sh
# Env overrides: CONFIG=... PLUGINS_DIR=... PAYLOADS_DIR=...

set -euo pipefail

SUITE_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
PLUGINS_DIR="${PLUGINS_DIR:-/etc/pwnagotchi/custom-plugins}"
PAYLOADS_DIR="${PAYLOADS_DIR:-/etc/pwnagotchi/badhid_ng/payloads}"

echo "### badhid-suite installer ###"
echo "    suite   : $SUITE_DIR"
echo "    config  : $CONFIG"
echo "    plugins : $PLUGINS_DIR"
echo "    payloads: $PAYLOADS_DIR"
echo ""

[ -f "$SUITE_DIR/badhid_ng.py" ] || { echo "ERROR: run me from inside badhid-suite/." >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "ERROR: $CONFIG not found (set CONFIG=...)." >&2; exit 1; }

# 1) BACKUP FIRST.
echo ">>> Step 1/4: backup"
BK_OUT="$("$SUITE_DIR/badhid_backup.sh")"
echo "$BK_OUT"
BACKUP_TARBALL="$(echo "$BK_OUT" | sed -n 's/^BACKUP_TARBALL=//p' | tail -n1)"
BACKUP_CONFIG="$(echo "$BK_OUT" | grep -o '/[^ ]*/config\.toml\.[0-9-]*\.bak' | tail -n1)"
[ -n "$BACKUP_CONFIG" ] || { echo "ERROR: backup did not produce a config copy; aborting." >&2; exit 1; }
echo ""

# 2) copy plugin + payloads (additive).
echo ">>> Step 2/4: copy files"
mkdir -p "$PLUGINS_DIR" "$PAYLOADS_DIR"
cp -a "$SUITE_DIR/badhid_ng.py" "$PLUGINS_DIR/"
cp -a "$SUITE_DIR"/payloads/*.duck "$PAYLOADS_DIR/"
echo "    copied badhid_ng.py -> $PLUGINS_DIR/"
echo "    copied $(ls "$SUITE_DIR"/payloads/*.duck | wc -l) demo payloads -> $PAYLOADS_DIR/"
echo ""

# 3) append the config block if the section isn't already there.
echo ">>> Step 3/4: config block"
if grep -q '^\[main\.plugins\.badhid_ng\]' "$CONFIG"; then
  echo "    [main.plugins.badhid_ng] already present - leaving config untouched."
else
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))' 2>/dev/null || echo "")"
  if [ -z "$TOKEN" ]; then
    echo "ERROR: couldn't generate a token (python3 missing?). Aborting before edit." >&2
    exit 1
  fi
  {
    echo ""
    echo "# ===== badhid-suite (added by badhid_install.sh $(date -Iseconds)) ====="
    echo "# A random auth_token was generated for you. enabled=false until you"
    echo "# deliberately turn it on. See badhid-suite/README.md."
    # Take the shipped block but drop its own [header comments] duplicates by
    # just using the real section + our generated values for the two USER-INPUT
    # fields. We inline a minimal, correct block here.
    echo "[main.plugins.badhid_ng]"
    echo "enabled = false"
    echo "auth_token = \"$TOKEN\""
    echo "hid_device = \"/dev/hidg0\""
    echo "manage_gadget = false"
    echo "bind_scope = \"auto\""
    echo "port = 8083"
    echo "payloads_dir = \"$PAYLOADS_DIR\""
    echo "default_payload = \"hello_world.duck\""
    echo "authorized_targets = []"
    echo "arm_window_seconds = 120"
    echo "arm_one_shot = true"
    echo "fire_on_enumerate = false"
    echo "inter_key_delay_ms = 5"
    echo "default_delay_ms = 0"
    echo "max_actions = 20000"
    echo "write_timeout_seconds = 10"
    echo "ui_enabled = true"
    echo "ui_position_x = -55"
    echo "ui_position_y = 10"
  } >> "$CONFIG"
  echo "    appended [main.plugins.badhid_ng] with a generated token (enabled=false)"
  echo "    >>> YOUR AUTH TOKEN: $TOKEN"
  echo "        (also saved to $PLUGINS_DIR/../badhid_ng/auth_token.txt)"
  mkdir -p "$(dirname "$PAYLOADS_DIR")"
  echo "$TOKEN" > "$(dirname "$PAYLOADS_DIR")/auth_token.txt"
  chmod 600 "$(dirname "$PAYLOADS_DIR")/auth_token.txt" 2>/dev/null || true
fi
echo ""

# 4) validate config parses; auto-rollback if not.
echo ">>> Step 4/4: validate config"
VALID=1
python3 - "$CONFIG" <<'PY' || VALID=0
import sys
path = sys.argv[1]
try:
    import tomllib
    with open(path, "rb") as fh:
        tomllib.load(fh)
except ModuleNotFoundError:
    try:
        import tomlkit
        with open(path) as fh:
            tomlkit.parse(fh.read())
    except Exception as e:
        print("parse error:", e); sys.exit(1)
except Exception as e:
    print("parse error:", e); sys.exit(1)
print("config.toml parses OK")
PY

if [ "$VALID" != "1" ]; then
  echo "!!! config.toml did NOT parse after the edit - ROLLING BACK."
  cp -a "$BACKUP_CONFIG" "$CONFIG"
  echo "    restored $CONFIG from $BACKUP_CONFIG"
  echo "    (plugin file + payloads were copied but are inert without the config block)"
  exit 1
fi

echo ""
echo "=============================================================="
echo " INSTALL OK (safe state)."
echo "   - files in place, config block added, enabled=false"
echo "   - NOTHING is running yet; the gadget is NOT up"
echo " Backup for a full undo:"
echo "     sudo ./badhid_restore.sh $BACKUP_TARBALL"
echo ""
echo " Next, when you're ready to actually test (see README.md):"
echo "   1) sudo ./setup_composite_gadget.sh   # bring up /dev/hidg0 (reboot-reversible)"
echo "   2) set enabled = true in $CONFIG"
echo "   3) sudo systemctl restart pwnagotchi  # watch the log for the control URL"
echo "=============================================================="
