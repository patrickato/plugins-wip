#!/bin/bash
# badhid_phone.sh - show the phone one-tap URL + a scannable QR code, so a
# newcomer just scans it and the control page opens (token already in it).
# Run ON the pi as root (the token file is root-only).
#
#   sudo ./badhid_phone.sh          # show the URL + QR
#   sudo ./badhid_phone.sh --fix    # if it's not phone-reachable, switch
#                                   # bind_scope to lan, restart, then show it
set -eu

CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
HOME_DIR="${BADHID_HOME:-/etc/pwnagotchi/badhid_ng}"
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
FIX=0; NO_INSTALL=0
for a in "$@"; do
  case "$a" in
    --fix) FIX=1 ;;
    --no-install) NO_INSTALL=1 ;;   # never auto-install a QR tool; just print the URL
    *) echo "unknown option: $a  (use --fix and/or --no-install)"; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo ./badhid_phone.sh"; exit 1; }

# Render a scannable QR for a URL using whatever real encoder is available, and
# if none is, install one ONCE (we're already root) so a fresh pi still shows a
# code. Returns non-zero only if it truly couldn't (offline + --no-install, etc).
render_qr() {
  u="$1"
  if command -v qrencode >/dev/null 2>&1; then qrencode -t ANSIUTF8 "$u"; return 0; fi
  if python3 -c "import qrcode" 2>/dev/null; then
    python3 -c "import qrcode,sys;qr=qrcode.QRCode(border=1);qr.add_data(sys.argv[1]);qr.print_ascii()" "$u"
    return 0
  fi
  [ "$NO_INSTALL" = "1" ] && return 1
  echo "(no QR tool found - installing 'qrencode' once so you get a scannable code...)" >&2
  if command -v apt-get >/dev/null 2>&1; then
    if apt-get install -y qrencode >/dev/null 2>&1 \
       || { apt-get update >/dev/null 2>&1 && apt-get install -y qrencode >/dev/null 2>&1; }; then
      command -v qrencode >/dev/null 2>&1 && { qrencode -t ANSIUTF8 "$u"; return 0; }
    fi
  fi
  if command -v pip3 >/dev/null 2>&1 && pip3 install qrcode --break-system-packages >/dev/null 2>&1; then
    if python3 -c "import qrcode" 2>/dev/null; then
      python3 -c "import qrcode,sys;qr=qrcode.QRCode(border=1);qr.add_data(sys.argv[1]);qr.print_ascii()" "$u"
      return 0
    fi
  fi
  return 1
}

# Print only the [main.plugins.badhid_ng] block: from its header to the next
# top-level [section] header (or EOF). awk is on every pi, and this never bleeds
# into a neighbouring plugin's options regardless of config order.
blk() {
  awk '
    /^\[main\.plugins\.badhid_ng\][[:space:]]*$/ { inblk=1; next }
    /^\[/ { inblk=0 }
    inblk { print }
  ' "$CONFIG" 2>/dev/null
}
getopt_str() { blk | grep -m1 "^[[:space:]]*$1[[:space:]]*=" | cut -d'"' -f2; }
getopt_num() { blk | grep -m1 "^[[:space:]]*$1[[:space:]]*=" | tr -dc '0-9'; }

TOKEN="$(cat "$HOME_DIR/auth_token.txt" 2>/dev/null || true)"
[ -n "$TOKEN" ] || TOKEN="$(getopt_str auth_token || true)"
[ -n "$TOKEN" ] || { echo "no auth_token found - is the plugin installed/configured?"; exit 1; }

PORT="$(getopt_num port)"; PORT="${PORT:-8083}"
BIND="$(getopt_str bind_scope)"; BIND="${BIND:-auto}"
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
TS_IP="$( (tailscale ip -4 2>/dev/null || true) | head -1)"

HOST=""; NOTE=""
case "$BIND" in
  lan) HOST="$LAN_IP"; NOTE="LAN";;
  tailscale) HOST="$TS_IP"; NOTE="tailscale";;
  auto) if [ -n "$TS_IP" ]; then HOST="$TS_IP"; NOTE="tailscale"; else HOST=""; NOTE="localhost-only"; fi;;
  localhost) HOST=""; NOTE="localhost-only";;
esac

if [ -z "$HOST" ]; then
  echo "The control page isn't reachable from your phone yet:"
  echo "  bind_scope = \"$BIND\"  -> $NOTE (only this pi can reach it)."
  if [ "$FIX" = "1" ]; then
    echo "Switching bind_scope to \"lan\" so your phone can reach it..."
    python3 "$SELF_DIR/badhid_setopt.py" "$CONFIG" bind_scope '"lan"'
    systemctl restart pwnagotchi 2>/dev/null && sleep 12 || true
    BIND="lan"; HOST="$LAN_IP"; NOTE="LAN"
  else
    echo
    echo "Easiest fix - run:   sudo ./badhid_phone.sh --fix"
    echo "(that sets bind_scope=\"lan\" and restarts, then shows the QR)."
    echo "Or use Tailscale (more private): set bind_scope=\"tailscale\"."
    exit 0
  fi
fi

URL="http://$HOST:$PORT/?token=$TOKEN"

echo "=============================================================="
echo " Scan this with your phone's camera ($NOTE):"
echo "=============================================================="
render_qr "$URL" || {
  echo "(couldn't show a QR - no encoder and auto-install didn't work; are you online?)"
  echo "  install one and re-run:  sudo apt-get install -y qrencode"
  echo "  or run with --no-install to always skip that and just use the URL below."
}
echo
echo " Or type this URL into your phone browser:"
echo "   $URL"
echo
echo " Tip: open it once, then 'Add to Home screen' for true one-tap."
echo " Security: anyone on your $NOTE with this token can control it - don't share the link."
echo "=============================================================="
