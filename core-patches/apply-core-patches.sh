#!/bin/sh
# apply-core-patches.sh — re-apply pwnagotchi core-file fixes after an update.
#
# WHY THIS EXISTS
# Jayofelony pwnagotchi 2.9.5.x on the BCM4345/nexmon radio has a boot race:
# agent.py queries the monitor interface's supported channels exactly once, the
# instant the interface comes up. On this hardware wlan0mon isn't ready yet, so
# iface_channels() returns empty, pwnagotchi believes the radio supports NO
# channels, and never channel-hops (symptom: stuck on one channel, epochs show
# hops=0, almost nothing captured). The patch retries the channel query up to
# 15x (1s apart) until the list populates.
#
# A pwnagotchi pip/image update OVERWRITES agent.py and silently removes this
# fix, reverting you to the stuck-on-one-channel bug. Run this script after any
# such update to restore it.
#
# Target file (adjust if your venv path differs):
AGENT="/opt/.pwn/lib/python3.13/site-packages/pwnagotchi/agent.py"
PATCH_DIR="$(cd "$(dirname "$0")" && pwd)"
PATCH="$PATCH_DIR/0001-agent-channel-ready-retry.patch"

set -e

[ -f "$AGENT" ] || { echo "ERROR: agent.py not found at $AGENT — edit AGENT= in this script"; exit 1; }
[ -f "$PATCH" ] || { echo "ERROR: patch not found at $PATCH"; exit 1; }

# Already applied?
if grep -q "has no channels yet; retrying" "$AGENT"; then
    echo "OK: channel-ready retry already present in agent.py — nothing to do."
    exit 0
fi

# Back up before touching it.
BK="$AGENT.bak.$(date +%Y%m%d-%H%M%S)"
sudo cp "$AGENT" "$BK"
echo "backed up current agent.py -> $BK"

# Try a real patch first; fall back to a dry-run check so we never half-apply.
if sudo patch -p1 --dry-run -d /opt/.pwn/lib/python3.13/site-packages < "$PATCH" >/dev/null 2>&1; then
    sudo patch -p1 -d /opt/.pwn/lib/python3.13/site-packages < "$PATCH"
    echo "PATCH APPLIED."
else
    echo "WARNING: context patch did not apply cleanly (agent.py may have changed"
    echo "upstream). Apply the change by hand — see $PATCH for the 8 lines to add"
    echo "right after the 'waiting for monitor interface' loop, before the"
    echo "'supported channels:' log line. Nothing was changed."
    exit 2
fi

# Verify + remind to restart.
if grep -q "has no channels yet; retrying" "$AGENT"; then
    echo "VERIFIED: fix is present. Restart to take effect:  sudo systemctl restart pwnagotchi"
else
    echo "ERROR: verification failed — restoring backup."
    sudo cp "$BK" "$AGENT"
    exit 3
fi
