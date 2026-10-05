#!/bin/bash
# remoteexec_install.sh - safe, reversible install of remoteexec-suite onto the
# pi. Run ON the pi from inside the remoteexec-suite folder of a plugins-wip
# clone.
#
# What it does, in order:
#   1. BACKS UP config.toml first - nothing is changed until a backup exists.
#   2. Copies remoteexec_ng.py into custom-plugins (additive; other plugins
#      untouched).
#   3. Appends the [main.plugins.remoteexec_ng] config block ONLY if it isn't
#      already present, with a freshly generated random auth_token and
#      enabled=false, command_mode="tasks", allow_free_mode=false. Scalars are
#      written first and the [.tasks] sub-table LAST (TOML requirement).
#   4. VALIDATES that config.toml still parses; AUTO-ROLLS BACK if not.
#
# What it deliberately does NOT do: set enabled=true, open free mode, or widen
# bind_scope. Out of the box the agent runs NOTHING until you turn it on, and
# even then only the 5 read-only diagnostic tasks. Those stay your deliberate
# manual steps (see README.md).
#
# Usage:  sudo ./remoteexec_install.sh
# Env overrides: CONFIG=... PLUGINS_DIR=...
set -euo pipefail

SUITE_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
PLUGINS_DIR="${PLUGINS_DIR:-/etc/pwnagotchi/custom-plugins}"
HOME_DIR="${REMOTEEXEC_HOME:-/etc/pwnagotchi/remoteexec_ng}"

echo "### remoteexec-suite installer ###"
echo "    suite   : $SUITE_DIR"
echo "    config  : $CONFIG"
echo "    plugins : $PLUGINS_DIR"
echo ""

[ -f "$SUITE_DIR/remoteexec_ng.py" ] || { echo "ERROR: run me from inside remoteexec-suite/." >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "ERROR: $CONFIG not found (set CONFIG=...)." >&2; exit 1; }

# 1) BACKUP FIRST.
echo ">>> Step 1/4: backup config"
BACKUP_CONFIG="$CONFIG.$(date +%Y%m%d-%H%M%S).bak"
cp -a "$CONFIG" "$BACKUP_CONFIG"
echo "    backed up -> $BACKUP_CONFIG"
echo ""

# 2) copy plugin (additive).
echo ">>> Step 2/4: copy plugin"
mkdir -p "$PLUGINS_DIR"
cp -a "$SUITE_DIR/remoteexec_ng.py" "$PLUGINS_DIR/"
echo "    copied remoteexec_ng.py -> $PLUGINS_DIR/"
echo ""

# 3) append the config block if the section isn't already there.
echo ">>> Step 3/4: config block"
if grep -q '^\[main\.plugins\.remoteexec_ng\]' "$CONFIG"; then
  echo "    [main.plugins.remoteexec_ng] already present - leaving config untouched."
else
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))' 2>/dev/null || echo "")"
  if [ -z "$TOKEN" ]; then
    echo "ERROR: couldn't generate a token (python3 missing?). Aborting before edit." >&2
    cp -a "$BACKUP_CONFIG" "$CONFIG"
    exit 1
  fi
  {
    echo ""
    echo "# ===== remoteexec-suite (added by remoteexec_install.sh $(date -Iseconds)) ====="
    echo "# Random auth_token generated for you. enabled=false + tasks-mode until"
    echo "# you deliberately turn it on. Scalars first, [.tasks] sub-table LAST."
    echo "[main.plugins.remoteexec_ng]"
    echo "enabled = false"
    echo "auth_token = \"$TOKEN\""
    echo "bind_scope = \"auto\""
    echo "port = 8084"
    echo "command_mode = \"tasks\""
    echo "allow_free_mode = false"
    echo "command_timeout_seconds = 30"
    echo "max_output_chars = 20000"
    echo "audit_log = \"$HOME_DIR/audit.log\""
    echo "ui_enabled = true"
    echo "ui_position_x = -40"
    echo "ui_position_y = 20"
    echo ""
    echo "[main.plugins.remoteexec_ng.tasks]"
    echo "uptime = \"uptime\""
    echo "status = \"systemctl is-active pwnagotchi\""
    echo "disk = \"df -h /\""
    echo "temp = \"vcgencmd measure_temp\""
    echo "ip = \"hostname -I\""
  } >> "$CONFIG"
  echo "    appended [main.plugins.remoteexec_ng] with a generated token (enabled=false, tasks mode)"
  mkdir -p "$HOME_DIR"
  echo "$TOKEN" > "$HOME_DIR/auth_token.txt"
  chmod 600 "$HOME_DIR/auth_token.txt" 2>/dev/null || true
  echo "    >>> YOUR AUTH TOKEN: $TOKEN"
  echo "        (also saved root-only to $HOME_DIR/auth_token.txt)"
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
  exit 1
fi

echo ""
echo "=============================================================="
echo " INSTALL OK (safe state - nothing is running yet)."
echo ""
echo " To turn it on (read-only diagnostic tasks only) - section-scoped, no nano:"
echo "   sudo sed -i '/^\\[main\\.plugins\\.remoteexec_ng\\]/,/^\\[/ s/^enabled = false/enabled = true/' $CONFIG"
echo "   sudo systemctl restart pwnagotchi"
echo ""
echo " Then test from the pi:"
echo "   $SUITE_DIR/remoteexecctl.sh tasks        # list the allowed tasks"
echo "   $SUITE_DIR/remoteexecctl.sh run uptime   # run one"
echo ""
echo " It stays in tasks mode (only the 5 read-only commands) unless you set"
echo " BOTH command_mode=\"free\" AND allow_free_mode=true. Full undo:"
echo "   sudo cp -a $BACKUP_CONFIG $CONFIG && sudo systemctl restart pwnagotchi"
echo "=============================================================="
