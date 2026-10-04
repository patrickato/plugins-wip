#!/bin/sh
set -eu
SRC="${1:-./tweak_view_ng.py}"
DST="/etc/pwnagotchi/custom-plugins/tweak_view_ng.py"
test -f "$SRC" || { echo "Missing $SRC" >&2; exit 1; }
install -d -m 0755 /etc/pwnagotchi/custom-plugins
if [ -f "$DST" ]; then cp -a "$DST" "$DST.bak.$(date +%Y%m%d-%H%M%S)"; fi
install -m 0644 "$SRC" "$DST"
echo "Installed $DST"
echo "Add/enable [main.plugins.tweak_view_ng] in /etc/pwnagotchi/config.toml, then restart pwnagotchi."
