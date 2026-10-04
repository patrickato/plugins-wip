#!/bin/sh
set -eu
DST="/etc/pwnagotchi/custom-plugins/tweak_view_ng.py"
rm -f "$DST"
echo "Removed $DST"
echo "Layout files were preserved. Disable main.plugins.tweak_view_ng in config.toml and restart pwnagotchi."
