"""
gps_tagger_ng.py - pwnagotchi plugin (runs ON the pi)

Rewrite of privacy-nightmare.py (itsdarklikehell/pwnagotchi-plugins,
originally by glenn@pegden.com), fixed for the jayofelony 64-bit fork.

GPS-tags every AP the pwnagotchi sees, and writes a `.gps.json` sidecar
next to each handshake capture using the same schema
(`{"Latitude": .., "Longitude": ..}`) that handshakes_dl_ng.py already
knows how to read - so a tagged capture shows up with a GPS link on that
plugin's web page automatically, with nothing extra to configure.

Bugs fixed vs. the original (all source-verified against this fork):
  1. `self.gps_hot` was never initialized, and was only ever set inside
     `get_gps()`, which is only called when `agent is not None`. The very
     first AP ever seen can arrive through `on_event`'s "wifi.ap.new"
     handler with `agent=None` - so `self.gps_hot` could be read before
     it was ever set, raising AttributeError. Fixed: initialized to
     False in __init__.
  2. `latlong` was only assigned inside `if self.gps_hot == True:` but
     read unconditionally right after - guaranteed NameError any time
     GPS wasn't locked yet, which is the common case, not an edge case.
     Fixed: always given a default ("unknown") before that branch.
  3. `self.options["pn_output_path"]` and `self.options["gps_speed"]`
     were read with bare indexing - KeyError if either was ever left
     unset. Fixed: read with .get() and sane defaults everywhere.
  4. The original ran its own second bettercap websocket connection in a
     background thread (`hook_ws_events`/`_event_poller`) just to catch
     the "wifi.client.probe" and "wifi.ap.new" events - the author's own
     comment called this "an ugly approach". It's unnecessary: this
     fork's real agent (pwnagotchi/agent.py) already re-dispatches every
     bettercap event to plugins as `on_bcap_<tag_with_dots_as_underscores>`
     (see agent.py's `_on_event`, which calls
     `plugins.on('bcap_%s' % re.sub(...), self, jmsg)`) - confirmed by
     reading the fork's own source. Implementing
     `on_bcap_wifi_client_probe` / `on_bcap_wifi_ap_new` directly gets
     the same events through the normal plugin hook mechanism, with no
     second connection, no background thread, and nothing to leak or
     clean up on unload.
  5. The "wifi.ap.new" handler passed a single AP dict where a *list* of
     AP dicts was expected (`self.aps_update("NE", None, jmsg["data"])`
     instead of `[jmsg["data"]]`). `aps_update` then iterated over the
     dict's keys (plain strings) instead of the AP itself, guaranteeing
     a TypeError ("string indices must be integers") every time a
     genuinely new AP came in through that path. Fixed: wrapped in a
     list.
  6. This fork's real handshake/association/deauth events can hand a
     plugin either a full AP/station dict, OR just a bare MAC string
     (pwnagotchi/agent.py falls back to the bare MAC if it can't find a
     matching AP/station in the current bettercap session at the moment
     the event fires) - confirmed directly in agent.py's `_on_event`.
     The original always assumed a dict (`ap["hostname"]`, `ap["mac"]`,
     `ap["vendor"]`), which would raise TypeError on the bare-string
     case. Fixed: normalized to a dict either way before use.
  7. The per-AP output file was built from two separate `json.dump()`
     calls into the same file handle with no separator
     (`json.dump(ap, fp); json.dump(self.pn_gps_coords, fp)`) - this
     does not produce a single valid JSON document, only two concatenated
     ones, which most JSON readers (including this project's own
     handshakes_dl_ng.py-style tooling) cannot parse as-is. Fixed: a
     single well-formed JSON object per file.
  8. The output filename was built from the AP's hostname alone
     (`pn_ap_<hostname>.json`) - any two APs sharing an SSID (extremely
     common: "NETGEAR", "xfinitywifi", hidden APs that all show up as
     the same "Unknown-<vendor>" placeholder, etc.) would silently
     overwrite each other's file. Fixed: filename now includes the
     AP's MAC address too.
  9. The on-screen counter was a copy/paste bug: `"%s/%s" % (self.pn_count,
     self.pn_count)` always displays e.g. "5/5". Fixed to show a single,
     meaningful count.

What's added (all approved improvements, on top of the fixes above):
  - `.gps.json` sidecar written next to each handshake capture, in the
    schema handshakes_dl_ng.py already reads - free interop with that
    plugin's web page, no new format to maintain.
  - A distance filter (`min_regap_distance_feet`, default 50 feet) so a
    previously-logged AP's file is only rewritten if the new GPS fix is
    at least that far from the last position recorded for it - otherwise
    a restart (which resets the in-memory "already seen" list) would
    blindly rewrite every AP's file again even if you haven't moved an
    inch. 50 feet is a general-purpose default, not tuned to any one
    setup: typical consumer GPS (non-RTK, the kind of USB/UART GPS
    module normally paired with a pwnagotchi) drifts roughly 10-20 feet
    under a clear sky and more under tree cover or near buildings, so 50
    feet comfortably clears that jitter while still being tight enough
    that genuinely different locations don't get merged together. Raise
    it if your GPS is noisier than that; lower it if you're using a
    higher-precision receiver.
  - A rate-limited "no GPS fix yet" log line (default: at most once every
    5 minutes) instead of either spamming a line per event or crashing -
    the AP is still logged, just without coordinates, until a fix comes
    in.
  - `manage_gps` (default False): by default this plugin does NOT touch
    bettercap's `gps.device`/`gps.baudrate`/`gps on` settings at all, on
    the assumption you're already running this fork's own built-in
    `main.plugins.gps`. Only set `manage_gps = true` if you want this
    plugin itself to own the GPS device - never enable that alongside
    the built-in `gps` plugin, since both driving the same serial device
    will conflict.

See config.toml.example in this folder for what to add to config.toml,
and README.md for full install/usage notes.
"""

import json
import logging
import os
import time
from math import atan2, cos, radians, sin, sqrt

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK

EARTH_RADIUS_FEET = 20925721.0  # 6,371,000 m * 3.28084 ft/m


def _distance_feet(lat1, lon1, lat2, lon2):
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_FEET * atan2(sqrt(a), sqrt(1 - a))


def _sanitize(value):
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(value))


def _as_ap_dict(ap):
    """Normalize an AP argument that may be a full dict OR a bare MAC
    string (this fork's agent.py falls back to a bare MAC when it can't
    find a matching AP in the current bettercap session - see fix #6)."""
    if isinstance(ap, dict):
        return ap
    return {"mac": str(ap), "hostname": "", "vendor": "Unknown"}


class GPSTaggerNG(plugins.Plugin):
    __author__ = "itsdarklikehell, glenn@pegden.com (original); rewritten for jayofelony fork"
    __version__ = "2.0.0"
    __license__ = "GPL3"
    __description__ = (
        "GPS-tags every AP the pwnagotchi sees, and writes a .gps.json "
        "sidecar next to each handshake capture in the same schema "
        "handshakes_dl_ng.py already reads."
    )
    __name__ = "GPSTaggerNG"
    __help__ = __description__
    __dependencies__ = {
        "apt": ["none"],
        "pip": [],
    }
    # NOTE: this fork's plugin loader does NOT read __defaults__ - set
    # every option explicitly in config.toml. See config.toml.example.
    __defaults__ = {
        "enabled": False,
    }

    def __init__(self):
        self.ready = False
        self.config = None
        self.running = True
        self.gps_up = False
        self.gps_hot = False  # FIX #1: initialized, never left unset
        self.pn_count = 0
        self.pn_status = "Waiting..."
        self.pn_gps_coords = None
        self.ap_list = {}
        self._last_no_gps_log = 0.0
        logging.debug(f"[{self.__class__.__name__}] plugin init")

    # ------------------------------------------------------------------

    def on_loaded(self):
        logging.info(f"[{self.__class__.__name__}] plugin loaded")
        out_path = self.options.get("pn_output_path")  # FIX #3
        if out_path and not os.path.exists(out_path):
            os.makedirs(out_path, exist_ok=True)

    def on_config_changed(self, config):
        self.config = config
        self.ready = True

    def on_ready(self, agent):
        logging.info(f"[{self.__class__.__name__}] plugin ready")
        if self.options.get("manage_gps", False):
            self._enable_gps(agent)
        else:
            logging.info(
                f"[{self.__class__.__name__}] manage_gps is off - assuming "
                "main.plugins.gps (or another GPS source) is already "
                "configured; this plugin will only read coordinates."
            )
            self.gps_up = True  # allow get_gps() to try reading agent["gps"]

    # ------------------------------------------------------------------
    # Standard hooks

    def on_wifi_update(self, agent, access_points):
        self.aps_update("WU", agent, access_points)

    def on_association(self, agent, access_point):
        self.aps_update("AS", agent, [access_point])

    def on_deauthentication(self, agent, access_point, client_station):
        self.aps_update("DA", agent, [access_point])

    def on_handshake(self, agent, filename, access_point, client_station):
        self.aps_update("HS", agent, [access_point])
        self._write_gps_sidecar(filename)

    # FIX #4: real event hooks instead of a hand-rolled second websocket.
    # This fork's agent.py re-dispatches every bettercap event as
    # on_bcap_<tag>, so these fire exactly like the original's custom
    # listener did, with none of the thread/connection overhead.
    def on_bcap_wifi_client_probe(self, agent, event):
        essid = event.get("data", {}).get("essid", "<unknown>")
        self.pn_status = f"Probe from {essid}"
        logging.info(f"[{self.__class__.__name__}] probe: {essid}")

    def on_bcap_wifi_ap_new(self, agent, event):
        data = event.get("data", {})
        essid = data.get("essid", "<unknown>")
        self.pn_status = f"New AP {essid}"
        logging.info(f"[{self.__class__.__name__}] new AP: {essid}")
        self.aps_update("NE", None, [data])  # FIX #5: wrapped in a list

    # ------------------------------------------------------------------
    # UI

    def _resolve_position(self, ui, x_key, y_key, default_x, default_y):
        # This fork's loader does not merge __defaults__, so read each option
        # with a real fallback. Negative x = pixels in from the right edge
        # (repo-wide convention). Defaults preserve the original hardcoded spots.
        try:
            x = int(self.options.get(x_key, default_x))
        except (TypeError, ValueError):
            x = default_x
        try:
            y = int(self.options.get(y_key, default_y))
        except (TypeError, ValueError):
            y = default_y
        if x < 0:
            try:
                x = int(ui.width()) + x
            except Exception:
                x = 0
        return (x, y)

    def on_ui_setup(self, ui):
        ui.add_element(
            "pn_status",
            LabeledValue(
                color=BLACK,
                label="",
                value="Active",
                position=self._resolve_position(ui, "status_position_x", "status_position_y", 1, 76),
                label_font=fonts.Small,
                text_font=fonts.Small,
            ),
        )
        ui.add_element(
            "pn_count",
            LabeledValue(
                color=BLACK,
                label="",
                value="0",
                position=self._resolve_position(ui, "count_position_x", "count_position_y", 122, 94),
                label_font=fonts.Small,
                text_font=fonts.Small,
            ),
        )

    def on_ui_update(self, ui):
        ui.set("pn_status", str(self.pn_status))
        ui.set("pn_count", f"{self.pn_count} APs")  # FIX #9

    def on_unload(self, ui):
        with ui._lock:
            try:
                ui.remove_element("pn_status")
                ui.remove_element("pn_count")
            except Exception as e:
                logging.error(f"[{self.__class__.__name__}] unload: {e}")
        logging.info(f"[{self.__class__.__name__}] plugin unloaded")

    def on_webhook(self, path, request):
        return (
            f"[{self.__class__.__name__}] {self.pn_count} AP(s) tagged. "
            f"GPS: {'locked' if self.gps_hot else 'not locked'}."
        )

    # ------------------------------------------------------------------
    # GPS

    def _enable_gps(self, agent):
        gps_device = self.options.get("gps_device")  # >>> USER INPUT REQUIRED <<< (only if manage_gps=true)
        if not gps_device:
            logging.warning(
                f"[{self.__class__.__name__}] manage_gps is on but gps_device "
                "is not set - GPS will not be enabled."
            )
            return
        if not os.path.exists(gps_device):
            logging.warning(f"[{self.__class__.__name__}] no GPS device at {gps_device}")
            return

        gps_speed = self.options.get("gps_speed", 19200)  # FIX #3
        try:
            agent.run("gps off")
        except Exception:
            pass
        agent.run(f"set gps.device {gps_device}")
        agent.run(f"set gps.baudrate {gps_speed}")
        agent.run("gps on")
        self.gps_up = True
        logging.info(f"[{self.__class__.__name__}] enabled bettercap GPS on {gps_device}")

    def get_gps(self, session):
        if not self.gps_up:
            self.gps_hot = False
            return
        self.pn_gps_coords = session.get("gps")
        if self.pn_gps_coords and self.pn_gps_coords.get("Latitude") and self.pn_gps_coords.get("Longitude"):
            self.gps_hot = True
        else:
            self.gps_hot = False
            self._log_no_gps_ratelimited()

    def _log_no_gps_ratelimited(self):
        interval = self.options.get("no_gps_log_interval_seconds", 300)
        now = time.monotonic()
        if now - self._last_no_gps_log >= interval:
            logging.info(f"[{self.__class__.__name__}] no GPS fix yet - logging AP(s) without coordinates")
            self._last_no_gps_log = now

    # ------------------------------------------------------------------
    # Core AP logging

    def aps_update(self, update_type, agent, access_points):
        if not self.running or not access_points:
            if not access_points:
                logging.debug(f"[{self.__class__.__name__}] empty AP list from {update_type}, ignoring")
            return

        if agent is not None:
            self.get_gps(agent.session())

        latlong = "unknown"  # FIX #2: always defined before use
        if self.gps_hot:
            update_type = f"{update_type} <G>"
            latlong = f"[{self.pn_gps_coords['Latitude']}][{self.pn_gps_coords['Longitude']}]"

        for raw_ap in access_points:
            ap = _as_ap_dict(raw_ap)  # FIX #6
            hostname = ap.get("hostname") or f"Unknown-{ap.get('vendor', 'Unknown')}"
            mac = ap.get("mac", "unknown-mac")
            apuid = f"{hostname}%{mac}"

            if apuid in self.ap_list:
                logging.debug(f"[{self.__class__.__name__}] already know about {apuid}, skipping")
                continue

            out_path = self.options.get("pn_output_path")
            if not out_path:
                logging.debug(f"[{self.__class__.__name__}] pn_output_path not set, not writing a file for {apuid}")
                self.ap_list[apuid] = True
                self.pn_count += 1
                continue

            pn_filename = os.path.join(
                out_path, f"pn_ap_{_sanitize(hostname)}_{_sanitize(mac)}.json"  # FIX #8
            )

            new_lat = self.pn_gps_coords["Latitude"] if self.gps_hot else None
            new_lon = self.pn_gps_coords["Longitude"] if self.gps_hot else None

            if not self._should_write(pn_filename, new_lat, new_lon):
                logging.debug(
                    f"[{self.__class__.__name__}] {apuid} hasn't moved far enough since last tag, skipping rewrite"
                )
                self.ap_list[apuid] = True
                continue

            logging.info(f"[{self.__class__.__name__}] AP ({update_type}): {hostname} at {latlong}")
            self.ap_list[apuid] = True
            self.pn_status = f"AP ({update_type}): {hostname}"
            self.pn_count += 1

            record = {
                "ap": ap,
                "gps": {"Latitude": new_lat, "Longitude": new_lon} if self.gps_hot else None,
                "update_type": update_type,
                "tagged_at": time.time(),
            }
            try:
                with open(pn_filename, "w") as fp:
                    json.dump(record, fp)  # FIX #7: one well-formed JSON object
            except OSError as e:
                logging.warning(f"[{self.__class__.__name__}] could not write {pn_filename}: {e}")

    def _should_write(self, pn_filename, new_lat, new_lon):
        """Distance-filter gate: skip rewriting a previously-tagged AP's
        file unless the new fix is at least min_regap_distance_feet away
        from the last one recorded for it, or we have no prior record."""
        if new_lat is None or new_lon is None:
            return not os.path.isfile(pn_filename)  # write once even without GPS; don't spam without it

        if not os.path.isfile(pn_filename):
            return True

        try:
            with open(pn_filename) as fp:
                old = json.load(fp)
            old_gps = old.get("gps")
            if not old_gps or old_gps.get("Latitude") is None:
                return True
            threshold = self.options.get("min_regap_distance_feet", 50)
            dist = _distance_feet(old_gps["Latitude"], old_gps["Longitude"], new_lat, new_lon)
            return dist >= threshold
        except (OSError, ValueError, KeyError):
            return True

    # ------------------------------------------------------------------

    def _write_gps_sidecar(self, filename):
        if not self.gps_hot or not self.pn_gps_coords:
            self._log_no_gps_ratelimited()
            return
        try:
            base, _ext = os.path.splitext(filename)
            sidecar_path = base + ".gps.json"
            with open(sidecar_path, "w") as fp:
                json.dump(
                    {
                        "Latitude": self.pn_gps_coords["Latitude"],
                        "Longitude": self.pn_gps_coords["Longitude"],
                    },
                    fp,
                )
            logging.info(f"[{self.__class__.__name__}] wrote GPS sidecar {sidecar_path}")
        except OSError as e:
            logging.warning(f"[{self.__class__.__name__}] could not write GPS sidecar for {filename}: {e}")
