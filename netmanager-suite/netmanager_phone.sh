#!/bin/bash
# netmanager_phone.sh - show the Network Manager page's one-tap URL + a scannable
# QR, so you just scan it and the page opens (token already in it). Run ON the pi
# as root (the token file is root-only).
#
#   sudo ./netmanager_phone.sh          # show the URL + QR
#   sudo ./netmanager_phone.sh --fix    # if not phone-reachable, set bind_scope=lan, restart, then show it
#   sudo ./netmanager_phone.sh --no-install   # never auto-install a QR tool; just print the URL
set -eu

CONFIG="${CONFIG:-/etc/pwnagotchi/config.toml}"
HOME_DIR="${NETMANAGER_HOME:-/etc/pwnagotchi/netmanager_ng}"
FIX=0; NO_INSTALL=0
for a in "$@"; do
  case "$a" in
    --fix) FIX=1 ;;
    --no-install) NO_INSTALL=1 ;;
    *) echo "unknown option: $a  (use --fix and/or --no-install)"; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo ./netmanager_phone.sh"; exit 1; }

blk() {  # print only the [main.plugins.netmanager_ng] block (section-bounded)
  awk '/^\[main\.plugins\.netmanager_ng\][[:space:]]*$/{i=1;next}/^\[/{i=0}i' "$CONFIG" 2>/dev/null
}
getopt_str() { blk | grep -m1 "^[[:space:]]*$1[[:space:]]*=" | cut -d'"' -f2; }
getopt_num() { blk | grep -m1 "^[[:space:]]*$1[[:space:]]*=" | tr -dc '0-9'; }

render_qr() {
  u="$1"
  if command -v qrencode >/dev/null 2>&1; then qrencode -t ANSIUTF8 "$u"; return 0; fi
  if python3 -c "import qrcode" 2>/dev/null; then
    python3 -c "import qrcode,sys;qr=qrcode.QRCode(border=1);qr.add_data(sys.argv[1]);qr.print_ascii()" "$u"; return 0
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
    python3 -c "import qrcode" 2>/dev/null && {
      python3 -c "import qrcode,sys;qr=qrcode.QRCode(border=1);qr.add_data(sys.argv[1]);qr.print_ascii()" "$u"; return 0; }
  fi
  return 1
}

TOKEN="$(cat "$HOME_DIR/auth_token.txt" 2>/dev/null || true)"
[ -n "$TOKEN" ] || TOKEN="$(getopt_str auth_token || true)"
[ -n "$TOKEN" ] || { echo "no auth_token found - is the plugin installed/configured?"; exit 1; }

PORT="$(getopt_num port)"; PORT="${PORT:-8085}"
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
  echo "The Network Manager page isn't reachable from your phone yet:"
  echo "  bind_scope = \"$BIND\"  -> $NOTE (only this pi can reach it)."
  if [ "$FIX" = "1" ]; then
    echo "Switching bind_scope to \"lan\" so your phone can reach it..."
    sed -i '/^\[main\.plugins\.netmanager_ng\]/,/^\[/ s/^bind_scope = ".*"/bind_scope = "lan"/' "$CONFIG"
    systemctl restart pwnagotchi 2>/dev/null && sleep 12 || true
    BIND="lan"; HOST="$LAN_IP"; NOTE="LAN"
  else
    echo; echo "Easiest fix - run:   sudo ./netmanager_phone.sh --fix"
    echo "(sets bind_scope=\"lan\" and restarts, then shows the QR). Or use tailscale."
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
}
echo
echo " Or type this URL into your phone browser:"
echo "   $URL"
echo
echo " Tip: open it once, then 'Add to Home screen' for one-tap."
echo " Security: anyone on your $NOTE with this token can control it - don't share the link."
echo "=============================================================="
