"""
WifiJammerNG - a rebuild of wifi_jammer.py (itsdarklikehell/pwnagotchi-plugins,
author "Deus Dust").

============================================================================
 FOR YOUR OWN AUTHORIZED HARDWARE ONLY. READ config.toml BEFORE ENABLING.
============================================================================

This plugin sends real 802.11 deauthentication frames via bettercap's
`wifi.deauth` module. It will NEVER fire against anything unless you
explicitly list that network's BSSID or SSID in `authorized_networks` in
config.toml - the list is empty by default, and an empty list means this
plugin loads but does nothing at all, ever. That allowlist is the actual
safety mechanism here, not physical signal range - WiFi frames don't
respect property lines the way line-of-sight assumptions suggest (walls,
antenna gain, and elevation all change how far a deauth frame actually
carries), so the list is what keeps this plugin from ever touching a
network you didn't name.

Original bugs fixed (see NOTES.md for the full writeup):
  FIX #1: `from pwnagotchi.plugins import BasePlugin` - BasePlugin does
          not exist anywhere in this fork (confirmed against the real
          cloned source). The plugin could never even import, let alone
          load. Rebuilt on the real `plugins.Plugin` base class.
  FIX #2: `access_point.bssid` (attribute access) - this fork's real AP
          argument is a dict (`access_point['mac']`), not an object with
          a `.bssid` attribute. Would have been an AttributeError even
          with FIX #1 alone.
  FIX #3: no authorization/scoping at all - the original fired at EVERY
          AP that ever yielded a handshake, unconditionally. Replaced
          with the authorized_networks allowlist described above.
  FIX #4: shelled out to `aireplay-ng --deauth 0` (0 = unlimited packets,
          forever) as a brand-new subprocess fighting bettercap for the
          same monitor interface. Replaced with `agent.run('wifi.deauth
          <mac>')` - the exact same in-process bettercap API call this
          fork's own core agent.py already uses for its normal deauth
          behavior (pwnagotchi/agent.py's Agent.deauth()). No new binary
          dependency, no interface-name guessing, no risk of two
          processes fighting over the same monitor interface.

What's added beyond the original's intent:
  - A per-target cooldown so an authorized target can't be hammered on
    every single association/handshake event in a tight loop.
  - Fires on `on_association` (as soon as the AP is seen/interacted
    with) in addition to `on_handshake` (the original's only trigger),
    so you don't have to wait for a full handshake capture to test
    against your own gear - both are on by default, per the "make it
    easy to use in my own lab" request this was built for.
  - A manual "fire now" webhook page for on-demand testing independent
    of any live event - visit http://pwnagotchi.local:8080/plugins/
    wifi_jammer_ng/ to see your configured MAC-form targets and fire a
    burst at any of them on demand.
  - SSID matching in addition to BSSID matching in authorized_networks,
    so you can list your own network by name instead of hunting for its
    MAC address if you don't have it handy (BSSID is still the more
    precise match - an SSID string can never be as unambiguous as a MAC
    - but for a lab you control, either is a legitimate, explicit choice
    you're making about your own hardware).
"""

import logging
import re
import time

import pwnagotchi.plugins as plugins

_MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


def _as_ap_dict(ap):
    """Normalize an AP/handshake argument that may be a full dict OR a
    bare MAC string (this fork's agent.py falls back to bare MAC strings
    for on_handshake when it can't match the AP in the live bettercap
    session - same shape GPSTaggerNG already normalizes for)."""
    if isinstance(ap, dict):
        return ap
    return {"mac": str(ap), "hostname": ""}


class WifiJammerNG(plugins.Plugin):
    __author__ = "Deus Dust (original); rewritten for jayofelony fork"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Sends bettercap wifi.deauth bursts at explicitly authorized "
        "targets only - for testing your own gear in a controlled lab."
    )
    __name__ = "WifiJammerNG"
    __help__ = __description__
    __dependencies__ = {"pip": [], "apt": []}
    __defaults__ = {
        "enabled": False,
        "authorized_networks": [],
        "cooldown_seconds": 15,
        "fire_on_association": True,
        "fire_on_handshake": True,
    }

    def __init__(self):
        self._agent = None
        self._last_fired = {}
        self._authorized_macs = set()
        self._authorized_ssids = set()
        self._warned_empty = False

    def _load_targets(self):
        """(Re)build the authorized-target lookup sets from config. Called
        on_loaded and again on_config_changed, so a config edit + reload
        takes effect without needing a full plugin/process restart."""
        self._authorized_macs = set()
        self._authorized_ssids = set()
        for entry in self.options.get("authorized_networks") or []:
            entry = str(entry).strip()
            if not entry:
                continue
            if _MAC_RE.match(entry):
                self._authorized_macs.add(entry.upper())
            else:
                self._authorized_ssids.add(entry.lower())

    def on_loaded(self):
        self._load_targets()
        total = len(self._authorized_macs) + len(self._authorized_ssids)
        if total == 0:
            logging.warning(
                f"[{self.__class__.__name__}] loaded, but authorized_networks "
                f"is empty - this plugin will not fire at anything until you "
                f"add at least one BSSID or SSID to config.toml."
            )
        else:
            logging.info(
                f"[{self.__class__.__name__}] loaded with {len(self._authorized_macs)} "
                f"authorized BSSID(s) and {len(self._authorized_ssids)} authorized "
                f"SSID(s). This plugin will ONLY ever fire at these."
            )

    def on_config_changed(self, config):
        self._load_targets()
        logging.info(f"[{self.__class__.__name__}] authorized target list reloaded")

    def on_ready(self, agent):
        # Keep a handle to the live agent so the manual "fire now" webhook
        # can call agent.run() later, outside of a real bettercap event.
        self._agent = agent

    def _match(self, ap):
        """Return the AP's MAC if it's an authorized target, else None."""
        mac = str(ap.get("mac", "")).upper()
        hostname = str(ap.get("hostname", "")).lower()
        if mac and mac in self._authorized_macs:
            return mac
        if hostname and hostname in self._authorized_ssids:
            return mac or None
        return None

    def _cooldown_ok(self, mac):
        last = self._last_fired.get(mac, 0)
        return (time.time() - last) >= self.options.get("cooldown_seconds", 15)

    def _fire(self, agent, mac, reason):
        if not mac:
            logging.warning(
                f"[{self.__class__.__name__}] matched an authorized SSID but had "
                f"no MAC to target ({reason}) - skipping this event."
            )
            return
        if not self._cooldown_ok(mac):
            logging.debug(
                f"[{self.__class__.__name__}] {mac} matched ({reason}) but is "
                f"still in its cooldown window - skipping."
            )
            return
        try:
            agent.run(f"wifi.deauth {mac}")
            self._last_fired[mac] = time.time()
            logging.info(f"[{self.__class__.__name__}] fired deauth at {mac} ({reason})")
        except Exception as e:
            logging.warning(f"[{self.__class__.__name__}] deauth against {mac} failed: {e}")

    def on_association(self, agent, access_point):
        if not self.options.get("fire_on_association", True):
            return
        if not (self._authorized_macs or self._authorized_ssids):
            return
        ap = _as_ap_dict(access_point)
        mac = self._match(ap)
        if mac:
            self._fire(agent, mac, "association with authorized target")

    def on_handshake(self, agent, filename, access_point, client_station):
        if not self.options.get("fire_on_handshake", True):
            return
        if not (self._authorized_macs or self._authorized_ssids):
            return
        ap = _as_ap_dict(access_point)
        mac = self._match(ap)
        if mac:
            self._fire(agent, mac, "handshake captured from authorized target")

    def on_webhook(self, path, request):
        from flask import make_response

        macs = sorted(self._authorized_macs)
        if request.method == "GET" and (not path or path == "/"):
            rows = "".join(
                f'<tr><td>{m}</td>'
                f'<td><a href="?fire={m}">fire now</a></td></tr>'
                for m in macs
            ) or "<tr><td colspan=2><i>no BSSID-form authorized targets configured</i></td></tr>"
            fire_target = request.args.get("fire")
            fired_msg = ""
            if fire_target:
                if fire_target.upper() in self._authorized_macs:
                    if self._agent is None:
                        fired_msg = "<p><b>Not ready yet</b> - agent isn't available yet, try again in a moment.</p>"
                    else:
                        self._fire(self._agent, fire_target.upper(), "manual webhook trigger")
                        fired_msg = f"<p><b>Fired at {fire_target.upper()}</b> (subject to its cooldown).</p>"
                else:
                    fired_msg = "<p><b>Refused</b> - that target isn't in authorized_networks.</p>"
            html = f"""
            <html><head><title>WifiJammerNG</title></head>
            <body style="font-family: sans-serif;">
            <h2>WifiJammerNG - manual trigger</h2>
            <p>For your own authorized lab gear only. SSID-form entries in
            authorized_networks fire automatically on association/handshake
            but aren't listed here for manual firing (no live MAC lookup) -
            only BSSID-form entries can be fired on demand.</p>
            {fired_msg}
            <table border="1" cellpadding="6">
            <tr><th>Authorized BSSID</th><th>Action</th></tr>
            {rows}
            </table>
            </body></html>
            """
            return make_response(html)
        return make_response("not found", 404)
