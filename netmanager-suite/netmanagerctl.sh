#!/bin/bash
# netmanagerctl.sh - manage the netmanager_ng authorized_targets allowlist from
# the pi, with ONE sudo command instead of hand-editing TOML.
#
# The allowlist is the non-negotiable safety gate: a wifi_target "Fire Test" is
# REFUSED unless the target's BSSID or SSID is on it (empty by default). This
# helper keeps authorization exactly where it belongs - in config.toml, a
# deliberate root action - while making it low-friction. It never opens a network
# port and the web page still cannot edit the allowlist.
#
# Usage (run on the pi):
#   sudo ./netmanagerctl.sh list
#   sudo ./netmanagerctl.sh authorize   <BSSID|SSID> [more...]
#   sudo ./netmanagerctl.sh deauthorize <BSSID|SSID> [more...]
#
# After authorize/deauthorize it restarts pwnagotchi so the change takes effect
# (set NO_RESTART=1 to skip). config.toml is backed up first and auto-rolled-back
# if the edit would break parsing.
#
# >>> Only add networks you OWN or are explicitly AUTHORIZED to test. <<<
#
# Env overrides: CONFIG=... SECTION=... NO_RESTART=1
set -eu

CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
SECTION="${SECTION:-main.plugins.netmanager_ng}"
CMD="${1:-}"; shift || true

usage() {
  sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

[ -f "$CONFIG" ] || { echo "ERROR: $CONFIG not found (set CONFIG=...)." >&2; exit 1; }
case "$CMD" in list|authorize|deauthorize) : ;; ""|-h|--help|help) usage 0 ;; *) echo "unknown command: $CMD" >&2; usage 2 ;; esac
if [ "$CMD" != "list" ] && [ "$#" -eq 0 ]; then echo "ERROR: $CMD needs at least one BSSID or SSID." >&2; exit 2; fi

# The array edit + dedup + validation all live in python (tomllib validates).
# It prints the resulting list to stderr for the human and exits non-zero on error.
run_py() {
  CONFIG="$CONFIG" SECTION="$SECTION" OP="$1" python3 - "$@" <<'PY'
import os, re, sys
cfg = os.environ["CONFIG"]; section = os.environ["SECTION"]; op = os.environ["OP"]
items = sys.argv[2:]  # argv[1] is OP (passed positionally too); targets follow

def norm(s):
    # case-insensitive, and colon/dash-insensitive for MAC-like strings, so
    # "AA:BB.." and "aabb.." don't both end up on the list.
    s = s.strip()
    bare = re.sub(r"[:\-\s]", "", s).lower()
    return bare if re.fullmatch(r"[0-9a-f]{12}", bare) else s.lower()

with open(cfg, "r", encoding="utf-8") as fh:
    lines = fh.readlines()

# locate [section] ... up to the next top-level [header] or EOF
hdr = re.compile(r'^\s*\[' + re.escape(section) + r'\]\s*$')
start = next((i for i, ln in enumerate(lines) if hdr.match(ln)), None)
if start is None:
    sys.stderr.write("ERROR: [%s] not found in %s - is netmanager installed?\n" % (section, cfg)); sys.exit(3)
end = len(lines)
for i in range(start + 1, len(lines)):
    if re.match(r'^\s*\[[^\]]+\]\s*$', lines[i]):
        end = i; break

# find authorized_targets within the section; support a multi-line array
key = re.compile(r'^\s*authorized_targets\s*=')
kstart = next((i for i in range(start + 1, end) if key.match(lines[i])), None)
current = []
if kstart is not None:
    kend = kstart
    blob = lines[kstart]
    while "]" not in blob and kend + 1 < end:
        kend += 1; blob += lines[kend]
    inner = blob[blob.find("[") + 1: blob.rfind("]")] if "[" in blob and "]" in blob else ""
    for tok in inner.split(","):
        tok = tok.strip().strip('"').strip("'").strip()
        if tok:
            current.append(tok)
else:
    kstart = kend = None

seen = {norm(x) for x in current}
added, removed, skipped = [], [], []
if op == "authorize":
    for it in items:
        it = it.strip()
        if not it:
            continue
        if norm(it) in seen:
            skipped.append(it)
        else:
            seen.add(norm(it)); current.append(it); added.append(it)
elif op == "deauthorize":
    want = {norm(x) for x in items}
    kept = []
    for x in current:
        if norm(x) in want:
            removed.append(x)
        else:
            kept.append(x)
    current = kept
    skipped = [it for it in items if norm(it) not in {norm(r) for r in removed}]

if op == "list":
    sys.stderr.write("authorized_targets (%d):\n" % len(current))
    for x in current:
        sys.stderr.write("  - %s\n" % x)
    if not current:
        sys.stderr.write("  (empty - every wifi_target Fire Test is refused)\n")
    sys.exit(0)

newline = "authorized_targets = [" + ", ".join('"%s"' % x.replace('"', '\\"') for x in current) + "]\n"
if kstart is None:
    lines.insert(start + 1, newline)
else:
    lines[kstart:kend + 1] = [newline]

text = "".join(lines)
try:
    import tomllib
    tomllib.loads(text)
except ModuleNotFoundError:
    pass
except Exception as e:
    sys.stderr.write("ERROR: edit would break config.toml parsing (%s) - no change made.\n" % e); sys.exit(4)

with open(cfg, "w", encoding="utf-8") as fh:
    fh.write(text)

if added:   sys.stderr.write("authorized: " + ", ".join(added) + "\n")
if removed: sys.stderr.write("deauthorized: " + ", ".join(removed) + "\n")
if skipped: sys.stderr.write("unchanged (already %s): %s\n" % ("present" if op=="authorize" else "absent", ", ".join(skipped)))
sys.stderr.write("authorized_targets now has %d entr%s.\n" % (len(current), "y" if len(current)==1 else "ies"))
# exit 10 = changed (caller should restart), 0 = no change
sys.exit(10 if (added or removed) else 0)
PY
}

if [ "$CMD" = "list" ]; then
  run_py list
  exit 0
fi

BACKUP="$CONFIG.$(date +%Y%m%d-%H%M%S).bak"
cp -a "$CONFIG" "$BACKUP"

set +e
run_py "$CMD" "$@"
rc=$?
set -e

if [ "$rc" = "4" ] || [ "$rc" = "3" ]; then
  cp -a "$BACKUP" "$CONFIG"
  echo "rolled back $CONFIG from backup." >&2
  rm -f "$BACKUP"
  exit 1
fi
if [ "$rc" = "0" ]; then
  echo "no change - nothing to restart. (backup: $BACKUP)" >&2
  exit 0
fi
# rc == 10: changed
echo "    (backup: $BACKUP)" >&2
if [ "${NO_RESTART:-0}" = "1" ]; then
  echo ">>> NO_RESTART set - restart pwnagotchi yourself for it to take effect:" >&2
  echo "    sudo systemctl restart pwnagotchi" >&2
else
  echo ">>> restarting pwnagotchi so the change takes effect..." >&2
  systemctl restart pwnagotchi && echo "    done." >&2 || {
    echo "    restart failed - do it manually: sudo systemctl restart pwnagotchi" >&2; }
fi
