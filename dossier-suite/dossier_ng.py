"""
dossier_ng.py - pwnagotchi plugin (runs ON the pi)

DossierNG is a pure read-and-assemble/report plugin: it does not scan
WiFi or Bluetooth itself, run bettercap commands, or write anything to
any other suite's files. All it does is READ the already-written output
files of four other suites already in this repo, and merge them into
one "unified target dossier" per WiFi hostname - so instead of checking
four separate status pages and cross-referencing them by hand, you get
one assembled record (or one HTML page) per target network.

The four sources read, and their default output paths (matched to each
source suite's own default, so this "just works" out of the box on a
system already running all four with default configs):

  1. crack-house-suite  (crack_house_ng.py) - `saving_path`, default
     "/etc/pwnagotchi/handshakes/crack_house_ng.potfile". A plain-text
     file, one "hostname:password" line per cracked network.
  2. timer-suite        (timer_ng.py) - `output_path`, default
     "/etc/pwnagotchi/timer_ng.csv". A CSV, one row per handshake event
     (timestamp, network, time_to_deauth, time_to_handshake,
     time_between_deauth_and_handshake). A network can have more than
     one row across runs.
  3. gps-tagger-suite   (gps_tagger_ng.py) - `pn_output_path`, default
     "/etc/pwnagotchi/gps_tagger_ng" (a DIRECTORY). One JSON file per
     tagged AP, named "pn_ap_<sanitized_hostname>_<sanitized_mac>.json",
     each `{"ap": {...}, "gps": {...} or null, "update_type": ...,
     "tagged_at": <unix timestamp>}`.
  4. bluetooth-recon-suite (bluetooth_recon_ng.py) - `device_table_path`,
     default "/etc/pwnagotchi/handshakes/bluetooth_recon_ng.json". A
     JSON dict keyed by normalized MAC, each record carrying (among
     other fields) `correlated_networks` - a list of WiFi hostnames
     that suite's own `_correlate()` already determined were active
     within a time window of that Bluetooth device's sighting.

Design decision: read bluetooth-recon-suite's correlation, don't
re-derive it
------------------------------------------------------------------
bluetooth-recon-suite already computes WiFi<->Bluetooth time-window
correlation itself (its `_correlate()`/`_nearby_wifi_networks()`/
`_cracked_hostnames()`/`_gps_locations()` methods - it reads the same
crack-house/timer/gps-tagger sources this plugin does, for its own
per-device `correlated_networks`/`any_cracked`/`correlated_location`
fields). This plugin does NOT reimplement that time-window matching.
Instead, for the "nearby Bluetooth devices" side of a dossier, it reads
bluetooth-recon-suite's own persisted device table and INVERTS the
per-device `correlated_networks` field: for every Bluetooth device
record whose `correlated_networks` list contains a given hostname, that
device becomes a "possible nearby device" entry in that hostname's
dossier. Two benefits: no duplicate correlation logic to maintain, and
this plugin automatically benefits if bluetooth-recon-suite's own
correlation logic is ever improved, with no changes needed here.

Because that correlation is itself inherited, best-effort, time-window
heuristic (devices merely active nearby around the same time - never
proven physical co-location, and never a claim that a Bluetooth device
"belongs to" a network or its owner), every place this plugin surfaces
it (the HTML detail page, this docstring, NOTES.md) labels it plainly
as a heuristic, not a confirmed fact.

For the other three fields (cracked password, GPS tag, handshake
timing), this plugin reads crack-house-suite's potfile, gps-tagger-
suite's tag directory, and timer-suite's CSV DIRECTLY itself - no
correlation math needed there, just straightforward parsing keyed by
hostname (case-insensitively, matching crack-house-suite's own
internal comparison convention).

Defensive reads
----------------
Every one of the four source reads is wrapped in its own try/except,
independently. A missing file, missing directory, malformed JSON/CSV
row, or any other read error for ONE source can never prevent the
other three from being read, and can never crash on_loaded/on_webhook/
on_ui_update. `FileNotFoundError`/`NotADirectoryError` are logged at
DEBUG (a source simply not being installed yet is completely normal,
not a problem), while a source that exists but couldn't be parsed logs
at WARNING (mirroring the same style bluetooth-recon-suite already
uses for these same four file types).

Caching
-------
The full assembled-dossier rebuild re-reads and re-parses four files/
directories - re-doing that on every single UI tick or webhook hit is
wasteful. The result is cached and only rebuilt after
`refresh_interval_seconds` has elapsed since the last build (an
on-demand rebuild also happens if a webhook request arrives after the
cache has gone stale).

See config.toml for every option, README.md for install/usage, and
NOTES.md for the full design writeup, known limitations, and the "no
original config" note (this is a brand-new plugin - see below).
"""

import csv
import html
import json
import logging
import os
import time
from urllib.parse import parse_qs, urlsplit

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK

try:
    from flask import Response
except ImportError:  # pragma: no cover - flask is always present on-device
    Response = None


# This fork's plugin loader does NOT merge a plugin's __defaults__ into
# self.options (plugins.load() does a raw `plugin.options =
# config['main']['plugins'][name]` assignment) - every option here is
# read via _opt() with a real fallback, never bare self.options[...].
DEFAULTS = {
    # Pure read-only reporting, no scanning of its own, no new attack
    # surface beyond reading four other suites' already-written files -
    # safe to default on.
    "enabled": True,
    # Source paths, defaulting to match each source suite's own default
    # exactly, so this plugin "just works" out of the box on a system
    # already running all four suites with their default configs.
    "crack_house_potfile_path": "/etc/pwnagotchi/handshakes/crack_house_ng.potfile",
    "timer_csv_path": "/etc/pwnagotchi/timer_ng.csv",
    "bluetooth_device_table_path": "/etc/pwnagotchi/handshakes/bluetooth_recon_ng.json",
    "gps_tagger_dir_path": "/etc/pwnagotchi/gps_tagger_ng",
    # On-screen badge, off by default - purely informational, never
    # required to use this plugin (the real report surface is the
    # webhook page).
    "show_on_screen": False,
    "position_x": None,
    "position_y": None,
    # How often (seconds) the four source files are re-read and the
    # assembled dossier rebuilt. Not on every single UI tick/webhook
    # hit - the assembled result is cached until this elapses.
    "refresh_interval_seconds": 30,
}

ELEMENT_NAME = "dossier_ng"
MAPS_URL_TEMPLATE = "https://www.google.com/maps/search/?api=1&query={lat},{lon}"


def _google_maps_url(lat, lon):
    return MAPS_URL_TEMPLATE.format(lat=lat, lon=lon)


class DossierNG(plugins.Plugin):
    __author__ = (
        "built from scratch for this project's plugin audit - post-"
        "cluster-review red-team-ideas brainstorm, idea #1 (unified "
        "target dossier)"
    )
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Assembles a unified per-target dossier (cracked password, GPS "
        "tag, capture timing, and possible nearby Bluetooth devices) by "
        "reading crack-house-suite's, timer-suite's, gps-tagger-suite's, "
        "and bluetooth-recon-suite's own already-written output files - "
        "a pure read-only report, no scanning of its own."
    )
    __name__ = "DossierNG"
    __help__ = __description__
    __dependencies__ = {
        # Pure stdlib file/CSV/JSON parsing - nothing extra to install.
        "apt": [],
        "pip": [],
    }

    def __init__(self):
        self._dossiers = {}  # hostname.lower() -> dossier dict
        self._last_build = 0.0
        self._built_at_least_once = False

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    # ------------------------------------------------------------------
    # Lifecycle

    def on_loaded(self):
        logging.info(f"[{self.__class__.__name__}] plugin loaded")
        self._rebuild()

    def on_unload(self, ui):
        with ui._lock:
            try:
                ui.remove_element(ELEMENT_NAME)
            except KeyError:
                pass
        logging.info(f"[{self.__class__.__name__}] plugin unloaded")

    # ------------------------------------------------------------------
    # UI (optional, purely informational badge)

    def _default_position(self, ui):
        x = self._opt("position_x")
        y = self._opt("position_y")
        if x is not None and y is not None:
            return (x, y)
        # None-means-auto, bottom-left - same pattern showerthoughts_ng.py
        # used.
        try:
            return (0, ui.height() - 10)
        except Exception:
            return (0, 0)

    def on_ui_setup(self, ui):
        if not self._opt("show_on_screen"):
            return
        pos = self._default_position(ui)
        ui.add_element(
            ELEMENT_NAME,
            LabeledValue(
                color=BLACK,
                label="Dossier",
                value="0 (0)",
                position=pos,
                label_font=fonts.Bold,
                text_font=fonts.Small,
            ),
        )

    def on_ui_update(self, ui):
        if not self._opt("show_on_screen"):
            return
        self._maybe_rebuild()
        total = len(self._dossiers)
        cracked = sum(1 for d in self._dossiers.values() if d.get("cracked_password"))
        try:
            ui.set(ELEMENT_NAME, f"{total} dossiers ({cracked} cracked)")
        except Exception as err:
            logging.debug(
                f"[{self.__class__.__name__}] couldn't update UI element: {err!r}"
            )

    # ------------------------------------------------------------------
    # Caching

    def _maybe_rebuild(self):
        interval = self._opt("refresh_interval_seconds")
        now = time.time()
        if not self._built_at_least_once or (
            interval is not None and (now - self._last_build) >= interval
        ):
            self._rebuild()

    def _rebuild(self):
        self._dossiers = self._assemble()
        self._last_build = time.time()
        self._built_at_least_once = True

    # ------------------------------------------------------------------
    # Source readers - each independently guarded, absence is normal

    def _read_cracked_passwords(self):
        """Returns {hostname.lower(): (original_case_hostname, password)}."""
        path = self._opt("crack_house_potfile_path")
        out = {}
        try:
            with open(path) as f:
                for line in f:
                    line = line.rstrip("\n")
                    if not line or ":" not in line:
                        continue
                    hostname, _, password = line.partition(":")
                    if hostname:
                        out[hostname.lower()] = (hostname, password)
        except FileNotFoundError:
            logging.debug(
                f"[{self.__class__.__name__}] crack_house_potfile_path "
                f"{path} not found, skipping (crack-house-suite may not "
                f"be installed or hasn't cracked anything yet)"
            )
        except Exception as err:
            logging.warning(
                f"[{self.__class__.__name__}] couldn't read "
                f"crack_house_potfile_path {path}: {err!r}"
            )
        return out

    def _read_timer_rows(self):
        """Returns {hostname.lower(): {"original": str, "rows": [row, ...]}}
        with rows sorted oldest-first; malformed individual rows are
        skipped, never fatal to the rest of the file."""
        path = self._opt("timer_csv_path")
        by_host = {}
        try:
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        hostname = row.get("network")
                        if not hostname:
                            continue
                        key = hostname.lower()
                        entry = by_host.setdefault(key, {"original": hostname, "rows": []})
                        entry["rows"].append(row)
                    except Exception as err:
                        logging.warning(
                            f"[{self.__class__.__name__}] skipping malformed "
                            f"row in timer_csv_path {path}: {err!r}"
                        )
        except FileNotFoundError:
            logging.debug(
                f"[{self.__class__.__name__}] timer_csv_path {path} not "
                f"found, skipping (timer-suite may not be installed or "
                f"hasn't logged anything yet)"
            )
        except Exception as err:
            logging.warning(
                f"[{self.__class__.__name__}] couldn't read timer_csv_path "
                f"{path}: {err!r}"
            )

        for entry in by_host.values():
            entry["rows"].sort(key=lambda r: r.get("timestamp") or "")
        return by_host

    def _read_gps_tags(self):
        """Returns {hostname.lower(): {"original": str, "record": dict,
        "mtime": float}} - the most-recently-modified matching
        pn_ap_*.json file per hostname wins; a corrupt/unreadable
        individual file is skipped, never fatal to the rest of the
        directory."""
        path = self._opt("gps_tagger_dir_path")
        by_host = {}
        try:
            if not os.path.isdir(path):
                logging.debug(
                    f"[{self.__class__.__name__}] gps_tagger_dir_path "
                    f"{path} not found or not a directory, skipping "
                    f"(gps-tagger-suite may not be installed or hasn't "
                    f"tagged anything yet)"
                )
                return by_host
            for filename in os.listdir(path):
                if not (filename.startswith("pn_ap_") and filename.endswith(".json")):
                    continue
                full_path = os.path.join(path, filename)
                try:
                    with open(full_path) as f:
                        record = json.load(f)
                    ap = record.get("ap") or {}
                    hostname = ap.get("hostname")
                    if not hostname:
                        continue
                    mtime = os.path.getmtime(full_path)
                    key = hostname.lower()
                    existing = by_host.get(key)
                    if existing is None or mtime > existing["mtime"]:
                        by_host[key] = {
                            "original": hostname,
                            "record": record,
                            "mtime": mtime,
                        }
                except Exception as err:
                    logging.warning(
                        f"[{self.__class__.__name__}] couldn't read/parse "
                        f"{full_path}, skipping this file: {err!r}"
                    )
        except Exception as err:
            logging.warning(
                f"[{self.__class__.__name__}] couldn't read "
                f"gps_tagger_dir_path {path}: {err!r}"
            )
        return by_host

    def _read_bluetooth_devices(self):
        """Returns the raw device table dict (mac -> record), or {} on
        any read/parse failure."""
        path = self._opt("bluetooth_device_table_path")
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            logging.warning(
                f"[{self.__class__.__name__}] bluetooth_device_table_path "
                f"{path} did not contain a JSON object, ignoring"
            )
        except FileNotFoundError:
            logging.debug(
                f"[{self.__class__.__name__}] bluetooth_device_table_path "
                f"{path} not found, skipping (bluetooth-recon-suite may "
                f"not be installed or hasn't recorded anything yet)"
            )
        except Exception as err:
            logging.warning(
                f"[{self.__class__.__name__}] couldn't read "
                f"bluetooth_device_table_path {path}: {err!r}"
            )
        return {}

    # ------------------------------------------------------------------
    # Assembly

    def _assemble(self):
        cracked = self._read_cracked_passwords()
        timer_rows = self._read_timer_rows()
        gps_tags = self._read_gps_tags()
        bt_devices = self._read_bluetooth_devices()

        # Invert bluetooth-recon-suite's own already-computed
        # correlated_networks field: hostname.lower() -> [device, ...].
        # This deliberately does NOT re-derive the time-window matching
        # itself - see module docstring.
        nearby_by_host = {}
        for mac, record in bt_devices.items():
            if not isinstance(record, dict):
                continue
            try:
                correlated = record.get("correlated_networks") or []
                for network in correlated:
                    if not network:
                        continue
                    key = str(network).lower()
                    device_entry = {
                        "mac": record.get("mac", mac),
                        "name": record.get("name"),
                        "vendor": record.get("vendor"),
                        "is_tracker": bool(record.get("is_tracker")),
                        "tracker_type": record.get("tracker_type"),
                        "rssi": record.get("rssi"),
                        "last_seen": record.get("last_seen"),
                    }
                    nearby_by_host.setdefault(key, []).append(device_entry)
            except Exception as err:
                logging.warning(
                    f"[{self.__class__.__name__}] skipping malformed "
                    f"bluetooth device record for {mac!r}: {err!r}"
                )

        for devices in nearby_by_host.values():
            # Trackers first - a tracker correlated to a target is the
            # single most actionable finding this plugin can surface.
            devices.sort(key=lambda d: (not d["is_tracker"], d.get("mac") or ""))

        all_hosts = set(cracked) | set(timer_rows) | set(gps_tags) | set(nearby_by_host)

        dossiers = {}
        for key in all_hosts:
            original = (
                cracked.get(key, (None, None))[0]
                or timer_rows.get(key, {}).get("original")
                or gps_tags.get(key, {}).get("original")
            )
            if original is None:
                # Only the bluetooth-inverted side knows this hostname -
                # bluetooth-recon-suite's correlated_networks list is the
                # only source that ever mentioned it, so there's no
                # original-cased hostname available anywhere; fall back
                # to the lowercased matching key itself.
                original = key

            password = cracked.get(key, (None, None))[1]

            timing = None
            row_count = 0
            if key in timer_rows:
                rows = timer_rows[key]["rows"]
                row_count = len(rows)
                if rows:
                    latest = rows[-1]
                    timing = {
                        "timestamp": latest.get("timestamp"),
                        "time_to_deauth": latest.get("time_to_deauth"),
                        "time_to_handshake": latest.get("time_to_handshake"),
                        "time_between_deauth_and_handshake": latest.get(
                            "time_between_deauth_and_handshake"
                        ),
                        "historical_row_count": row_count,
                    }

            gps = None
            if key in gps_tags:
                gps_entry = gps_tags[key]["record"]
                gps_data = gps_entry.get("gps")
                if gps_data and gps_data.get("Latitude") is not None and gps_data.get("Longitude") is not None:
                    lat, lon = gps_data["Latitude"], gps_data["Longitude"]
                    gps = {
                        "lat": lat,
                        "lon": lon,
                        "tagged_at": gps_entry.get("tagged_at"),
                        "update_type": gps_entry.get("update_type"),
                        "maps_url": _google_maps_url(lat, lon),
                    }

            nearby = nearby_by_host.get(key, [])

            completeness = sum(
                [
                    password is not None,
                    gps is not None,
                    timing is not None,
                    bool(nearby),
                ]
            )

            dossiers[key] = {
                "hostname": original,
                "cracked_password": password,
                "gps": gps,
                "timing": timing,
                "nearby_bluetooth_devices": nearby,
                "completeness": completeness,
            }

        return dossiers

    # ------------------------------------------------------------------
    # Webhook: the report surface, no new server/port

    def on_webhook(self, path, request):
        self._maybe_rebuild()

        raw_path = path or ""
        # Support both "target/<hostname>" and "?host=<hostname>" -
        # split off any query string first so a stripped-path match
        # against "target/..." still works either way.
        split = urlsplit(raw_path)
        stripped = split.path.strip("/")
        query = parse_qs(split.query)

        if stripped == "" and not query.get("host"):
            return self._index_page()
        if stripped == "export":
            return self._export_json()
        if stripped.startswith("target/"):
            hostname = stripped[len("target/"):]
            return self._detail_page(hostname)
        if query.get("host"):
            return self._detail_page(query["host"][0])
        return "Not found", 404

    def _index_page(self):
        entries = sorted(
            self._dossiers.values(),
            key=lambda d: (-d["completeness"], d["hostname"].lower()),
        )

        rows = []
        for d in entries:
            hostname = html.escape(d["hostname"])
            cracked = "yes" if d["cracked_password"] else "no"
            gps_flag = "yes" if d["gps"] else "no"
            nearby = d["nearby_bluetooth_devices"]
            tracker_flag = " (tracker!)" if any(n["is_tracker"] for n in nearby) else ""
            link_target = html.escape(d["hostname"], quote=True)
            rows.append(
                "<tr>"
                f"<td><a href='target/{link_target}'>{hostname}</a></td>"
                f"<td>{d['completeness']}/4</td>"
                f"<td>{cracked}</td>"
                f"<td>{gps_flag}</td>"
                f"<td>{len(nearby)}{html.escape(tracker_flag)}</td>"
                "</tr>"
            )

        table_body = "".join(rows) or (
            "<tr><td colspan=5><i>no dossiers assembled yet - "
            "none of the four source suites have produced data</i></td></tr>"
        )

        return (
            "<html><head><title>DossierNG</title></head>"
            "<body style='font-family: sans-serif;'>"
            "<h2>DossierNG - unified target dossiers</h2>"
            "<p>Assembled from crack-house-suite, timer-suite, "
            "gps-tagger-suite, and bluetooth-recon-suite's own already-"
            "written output files. Read-only, best-effort - a subset of "
            "fields populate fine if any of those suites isn't installed "
            "or hasn't produced data yet.</p>"
            "<p><i>Bluetooth correlation is inherited, best-effort, "
            "time-window heuristic from bluetooth-recon-suite - a device "
            "seen active nearby around the same time, never proven "
            "physical co-location.</i></p>"
            f"<p>{len(entries)} target(s) known. "
            "<a href='export'>Download full assembled dossier (JSON)</a></p>"
            "<table border='1' cellpadding='4'>"
            "<tr><th>Hostname</th><th>Completeness</th><th>Cracked</th>"
            "<th>GPS</th><th>Nearby BT devices</th></tr>"
            f"{table_body}"
            "</table>"
            "</body></html>"
        )

    def _detail_page(self, hostname):
        key = (hostname or "").lower()
        dossier = self._dossiers.get(key)
        if dossier is None:
            return "Not found", 404

        safe_hostname = html.escape(dossier["hostname"])

        parts = [
            "<html><head><title>DossierNG - target</title></head>",
            "<body style='font-family: sans-serif;'>",
            f"<h2>Dossier: {safe_hostname}</h2>",
            "<p><a href='../'>&larr; back to index</a></p>",
        ]

        if dossier["cracked_password"]:
            parts.append(
                "<h3>Cracked password</h3><p><code>%s</code></p>"
                % html.escape(dossier["cracked_password"])
            )
        else:
            parts.append("<h3>Cracked password</h3><p><i>not cracked (yet)</i></p>")

        gps = dossier["gps"]
        if gps:
            parts.append(
                "<h3>GPS</h3>"
                f"<p>Lat/Lon: {gps['lat']}, {gps['lon']}<br>"
                f"<a href='{html.escape(gps['maps_url'], quote=True)}'>View on Google Maps</a><br>"
                f"Update type: {html.escape(str(gps.get('update_type') or ''))}<br>"
                f"Tagged at: {html.escape(str(gps.get('tagged_at') or ''))}</p>"
            )
        else:
            parts.append("<h3>GPS</h3><p><i>no GPS tag recorded</i></p>")

        timing = dossier["timing"]
        if timing:
            extra = ""
            if timing["historical_row_count"] > 1:
                extra = (
                    f" ({timing['historical_row_count']} historical rows total - "
                    "showing the most recent)"
                )
            parts.append(
                "<h3>Timing</h3>"
                f"<p>Timestamp: {html.escape(str(timing.get('timestamp') or ''))}{extra}<br>"
                f"Time to deauth: {html.escape(str(timing.get('time_to_deauth') or ''))}s<br>"
                f"Time to handshake: {html.escape(str(timing.get('time_to_handshake') or ''))}s<br>"
                f"Deauth&rarr;handshake: "
                f"{html.escape(str(timing.get('time_between_deauth_and_handshake') or ''))}s</p>"
            )
        else:
            parts.append("<h3>Timing</h3><p><i>no capture timing recorded</i></p>")

        nearby = dossier["nearby_bluetooth_devices"]
        parts.append("<h3>Possible nearby Bluetooth devices</h3>")
        parts.append(
            "<p><i>Heuristic, inherited from bluetooth-recon-suite's own "
            "time-window correlation - these devices were active nearby "
            "around the same time as activity on this network. This is "
            "NOT confirmed physical co-location and does NOT mean a "
            "device belongs to this network or its owner.</i></p>"
        )
        if nearby:
            rows = []
            for d in nearby:
                tracker = (
                    f"YES ({html.escape(str(d.get('tracker_type') or ''))})"
                    if d["is_tracker"]
                    else ""
                )
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(str(d.get('mac') or ''))}</td>"
                    f"<td>{html.escape(str(d.get('name') or ''))}</td>"
                    f"<td>{html.escape(str(d.get('vendor') or ''))}</td>"
                    f"<td>{tracker}</td>"
                    f"<td>{d.get('rssi') if d.get('rssi') is not None else ''}</td>"
                    f"<td>{html.escape(str(d.get('last_seen') or ''))}</td>"
                    "</tr>"
                )
            parts.append(
                "<table border='1' cellpadding='4'>"
                "<tr><th>MAC</th><th>Name</th><th>Vendor</th><th>Tracker</th>"
                "<th>RSSI</th><th>Last seen</th></tr>"
                f"{''.join(rows)}"
                "</table>"
            )
        else:
            parts.append("<p><i>none correlated</i></p>")

        parts.append("</body></html>")
        return "".join(parts)

    def _export_json(self):
        payload = json.dumps(self._dossiers).encode()
        if Response is not None:
            return Response(
                payload,
                mimetype="application/json",
                headers={
                    "Content-Disposition": "attachment; filename=dossier_ng.json"
                },
            )
        return payload  # pragma: no cover - flask always present on-device
