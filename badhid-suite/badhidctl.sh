#!/bin/bash
# badhidctl.sh - friendly control for badhid so you never type curl by hand.
# Run ON the pi (or anywhere that can reach the control URL, with the token).
#
#   ./badhidctl.sh status                 # arm state, HID device, payloads
#   ./badhidctl.sh list                   # list available payloads
#   ./badhidctl.sh arm                    # arm (opens the fire window)
#   ./badhidctl.sh disarm                 # disarm
#   ./badhidctl.sh fire [payload] [label] # fire (default: hello_world.duck)
#
# The token is read automatically from TOKEN_FILE (default
# /etc/pwnagotchi/badhid_ng/auth_token.txt, written by badhid_install.sh), or
# set BADHID_TOKEN in the environment. The base URL defaults to
# http://127.0.0.1:<port-from-config>; override with BADHID_URL.

set -euo pipefail

CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
TOKEN_FILE="${TOKEN_FILE:-/etc/pwnagotchi/badhid_ng/auth_token.txt}"

# --- token ---
TOKEN="${BADHID_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f "$TOKEN_FILE" ]; then TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"; fi
if [ -z "$TOKEN" ]; then
  # last resort: read it out of config.toml
  TOKEN="$(grep -A40 '^\[main\.plugins\.badhid_ng\]' "$CONFIG" 2>/dev/null | grep -m1 '^auth_token' | cut -d'"' -f2 || true)"
fi
if [ -z "$TOKEN" ]; then
  echo "ERROR: no token found. Set BADHID_TOKEN=..., or make sure $TOKEN_FILE exists." >&2
  exit 1
fi

# --- url ---
if [ -n "${BADHID_URL:-}" ]; then
  BASE="$BADHID_URL"
else
  PORT="$(grep -A40 '^\[main\.plugins\.badhid_ng\]' "$CONFIG" 2>/dev/null | grep -m1 '^port' | tr -dc '0-9' || true)"
  BASE="http://127.0.0.1:${PORT:-8083}"
fi

CMD="${1:-status}"; shift || true
H=(-H "Authorization: Bearer $TOKEN" --max-time 20 -s)

case "$CMD" in
  status)
    # the root page is HTML; show the key lines plainly
    curl "${H[@]}" "$BASE/" | grep -oE 'State:</b> [A-Z]+|device:</b> [^<]+|Bound:</b> [^<]+' | sed 's/<\/b>//; s/^/  /' \
      || echo "  (no response - is the plugin enabled and the server up? try: sudo ./badhid_doctor.sh)"
    ;;
  list)
    curl "${H[@]}" "$BASE/" | grep -oE '<li>[^<]+\.duck</li>|<li>[^<]+\.txt</li>' | sed 's/<[^>]*>//g; s/^/  /' \
      || echo "  (no payloads / no response)"
    ;;
  arm)     curl "${H[@]}" -X POST "$BASE/arm";    echo ;;
  disarm)  curl "${H[@]}" -X POST "$BASE/disarm"; echo ;;
  fire)
    PAYLOAD="${1:-hello_world.duck}"
    LABEL="${2:-}"
    echo "Firing '$PAYLOAD'${LABEL:+ (target: $LABEL)} ..."
    curl "${H[@]}" -X POST --data "payload=$PAYLOAD&target=$LABEL" "$BASE/fire"; echo
    ;;
  *)
    echo "Usage: $0 {status|list|arm|disarm|fire [payload] [label]}"; exit 2 ;;
esac
