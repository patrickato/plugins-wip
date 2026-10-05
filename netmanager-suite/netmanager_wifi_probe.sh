#!/bin/bash
# netmanager_wifi_probe.sh - READ-ONLY inventory of the WiFi adapters on this pi,
# so you can pick a capture_iface for netmanager's wifi_target backend.
#
# It does NOT change interface modes, bring anything up/down, or transmit. It
# just reports: every wlan interface, its driver + USB chipset, whether the
# chip advertises MONITOR mode, and which one looks like the pwnagotchi radio
# (so you DON'T pick that one). Run it, then set capture_iface to a recommended
# second adapter.
#
#   sudo ./netmanager_wifi_probe.sh
#
# (sudo lets `iw phy` read full capabilities; it still changes nothing.)
set -u

echo "=== netmanager wifi adapter probe (read-only) ==="
echo

have() { command -v "$1" >/dev/null 2>&1; }
for t in iw; do have "$t" || echo "note: '$t' not found - install with: sudo apt install -y iw"; done

# Which iface is pwnagotchi/bettercap driving? (don't capture on this one)
PWN_IFACE=""
for cand in wlan0mon mon0 wlan0; do
  if ip link show "$cand" >/dev/null 2>&1; then PWN_IFACE="$cand"; break; fi
done
echo "pwnagotchi radio looks like: ${PWN_IFACE:-<none found>}   (do NOT use this as capture_iface)"
echo

# lsusb for the human (chipset hints)
if have lsusb; then
  echo "--- USB devices (wifi-ish) ---"
  lsusb | grep -iE "wi-?fi|wlan|802\.11|ralink|realtek|atheros|mediatek|ath9|rtl8|mt7|8812|8821|8188|ar9271|carl9170" || echo "  (no obvious wifi strings - see full 'lsusb' below)"
  echo
fi

# Walk every wlan* interface
RECOMMEND=""
for ifpath in /sys/class/net/*; do
  ifc="$(basename "$ifpath")"
  [ -d "$ifpath/wireless" ] || [ -L "$ifpath/phy80211" ] || continue

  driver="?"
  [ -L "$ifpath/device/driver" ] && driver="$(basename "$(readlink "$ifpath/device/driver")")"
  # USB chipset string for this iface, if it's a USB adapter
  chip=""
  if [ -r "$ifpath/device/uevent" ]; then
    vp="$(grep -i '^PRODUCT=' "$ifpath/device/uevent" 2>/dev/null | head -1)"
    [ -n "$vp" ] && chip="usb:$vp"
  fi
  phy=""
  [ -L "$ifpath/phy80211" ] && phy="$(basename "$(readlink "$ifpath/phy80211")")"

  mon="unknown"
  if have iw && [ -n "$phy" ]; then
    if iw phy "$phy" info 2>/dev/null | awk '/Supported interface modes/{f=1;next}/^\s*[A-Za-z]/{if(f&&!/\* /)f=0}f' | grep -qi '\* monitor'; then
      mon="YES"
    else
      mon="no"
    fi
  fi

  mode="$(iw dev "$ifc" info 2>/dev/null | awk '/type/{print $2; exit}')"

  tag=""
  if [ "$ifc" = "$PWN_IFACE" ]; then
    tag="  <- pwnagotchi radio (skip)"
  elif [ "$mon" = "YES" ]; then
    tag="  <- CANDIDATE capture_iface"
    [ -z "$RECOMMEND" ] && RECOMMEND="$ifc"
  fi

  echo "iface $ifc"
  echo "    driver : $driver"
  [ -n "$chip" ] && echo "    chipset: $chip"
  echo "    phy    : ${phy:-?}   current mode: ${mode:-?}"
  echo "    monitor-capable: $mon$tag"
  echo
done

echo "=============================================================="
if [ -n "$RECOMMEND" ]; then
  echo " Recommended capture_iface:  $RECOMMEND"
  echo " Put it in [main.plugins.netmanager_ng]:"
  echo "     capture_iface = \"$RECOMMEND\""
  echo "     capture_backend_enabled = true     # master switch (off by default)"
  echo " then restart pwnagotchi. (deauth_count stays 0 = passive capture until you set it.)"
else
  echo " No monitor-capable SECOND adapter detected."
  echo " - If an adapter is plugged in, its driver may not support monitor mode,"
  echo "   or needs a monitor-capable driver (e.g. 8812au for RTL8812AU)."
  echo " - Known-good monitor chipsets: Atheros AR9271 (ath9k_htc), RTL8812AU (8812au),"
  echo "   MT7612U (mt76x2u). The capture backend needs one of these as a 2nd adapter."
fi
echo "=============================================================="
echo
echo "Paste this whole output back and I'll set capture_iface for you."
