#!/bin/sh
# Tweak View NG - uninstaller. Removes the plugin file; leaves your layout JSON
# and config.toml intact (just disable the section and restart). Run as root:
#   sudo sh uninstall.sh
set -eu

CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
PLUGIN="tweak_view_ng.py"

if [ "$(id -u)" -ne 0 ]; then
  echo "please run as root (sudo sh uninstall.sh)" >&2; exit 1
fi

PLUGDIR="$(sed -n 's/^[[:space:]]*main\.custom_plugins[[:space:]]*=[[:space:]]*"\{0,1\}\([^"#]*[^"# ]\)"\{0,1\}.*/\1/p' "$CONFIG" 2>/dev/null | head -1)"
[ -n "${PLUGDIR:-}" ] || PLUGDIR="/etc/pwnagotchi/custom-plugins/"
DST="${PLUGDIR%/}/$PLUGIN"

if [ -f "$DST" ]; then
  rm -f "$DST"
  echo "removed $DST"
else
  echo "no plugin file at $DST (nothing to remove)"
fi

echo "Your layout file (/etc/pwnagotchi/tweak_view_ng.json) was left in place."
echo "To finish: set 'enabled = false' under [main.plugins.tweak_view_ng] in"
echo "$CONFIG, then: sudo systemctl restart pwnagotchi"
