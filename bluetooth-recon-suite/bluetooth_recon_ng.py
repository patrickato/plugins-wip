"""
bluetooth_recon_ng.py - pwnagotchi plugin (runs ON the pi)

BluetoothReconNG merges two buggy community plugins into one:
  - blemon_plugin.py (itsdarklikehell / evilsocket) - BLE device tracking
    via bettercap's `ble.recon` module.
  - bluetoothsniffer.py (itsdarklikehell / diytechtinker / Jayofelony) -
    classic Bluetooth (BR/EDR) scanning via `hcitool`.

This was a user-directed design decision (merge, don't keep two
overlapping plugins) - see NOTES.md for the full rationale, not
re-litigated here.

Bugs fixed vs. the originals (all source-verified against this
project's cloned jayofelony/pwnagotchi fork - see NOTES.md for the
full writeup):

blemon_plugin.py:
  1. Defined a pile of AI-training/free-channel hooks
     (on_ai_ready/on_ai_policy/on_ai_training_start/step/end/
     on_ai_best_reward/on_ai_worst_reward/on_free_channel) that are not
     real hooks this fork's `plugins.on()` ever dispatches (confirmed
     against pwnagotchi/plugins/__init__.py and agent.py) - dead code,
     removed entirely.
  2. `ui.set("blecount", ...)` in `on_bcap_ble_device_lost`, but the
     element was registered under `"blemon_count"` - wrong key, so that
     event silently never updated the screen. This rebuild computes the
     BLE count live from the device table on every `on_ui_update`
     instead of hand-incrementing/decrementing a counter from two
     different event handlers that can drift apart, which both fixes
     the key mismatch and removes the whole class of "did I remember to
     update every counter" bugs.
  3. `if name is "":` - identity comparison on a string literal. Works
     by CPython interning accident, not by contract, and is a
     SyntaxWarning as of modern Python. Fixed to `==`.
  4. `__dependencies__ = {"pip": ["scapy"]}` but the file never imports
     `scapy` anywhere - the BLE recon calls only ever go through
     bettercap's own API via `agent.run(...)`, exactly like this
     project's wifi_jammer_ng.py and gps_tagger_ng.py already do for
     their own bettercap calls. No extra pip package is required.

bluetoothsniffer.py:
  1. `__init__` sets `self.options = {...defaults...}`, but this fork's
     loader does a RAW assignment of the whole `[main.plugins.<name>]`
     TOML table onto `plugin.options` in `plugins.load()` - `__defaults__`
     is never merged, and neither is an `__init__`-time default dict;
     both are simply overwritten before `on_loaded` ever runs. Every
     bare `self.options["key"]` read was one `KeyError` away from
     crashing on load unless the user's config.toml happened to set
     that exact key. Fixed with the same `_opt()`-reads-a-module-level-
     `DEFAULTS`-dict pattern already used by crack_house_ng.py/
     timer_ng.py/gps_tagger_ng.py in this repo.
  2. `on_unload` called `ui.remove_element("BluetoothSniffer")`, but the
     element was registered under `"BtS"` - wrong key, so
     `remove_element` always raised `KeyError` (masked by a broad
     `except Exception`), and the element itself was never actually
     removed. Fixed to use the real element keys, and per this
     project's established convention (crack_house_ng.py/timer_ng.py),
     each `remove_element` call is individually wrapped in
     `try/except KeyError` so removing a key that was never added (see
     the framework fact below) can never itself crash unload.
  3. Unguarded `hcitool` invocation - a missing binary
     (`bluez`/`bluez-tools` not installed) raised an uncaught
     `FileNotFoundError` straight out of the scan path. Guarded with
     `try/except (FileNotFoundError, OSError, subprocess.SubprocessError)`.

Framework facts this rebuild relies on (verified against the cloned
jayofelony/pwnagotchi fork, see this project's own research notes):
  - `plugins.load()` assigns `plugin.options` directly from the parsed
    TOML dict - `__defaults__` is NEVER merged. Every option read in
    this file goes through `_opt()`, never bare `self.options[...]`.
  - `State.remove_element()` does `del self._state[key]` with no guard,
    so removing a key that was never added raises `KeyError` -
    `on_unload` wraps each `remove_element` call individually.
  - `on_handshake`'s `access_point`/`client_station` (used here only
    indirectly, via the optional timer_ng.py CSV correlation source)
    can be a bare MAC string instead of a dict - not read directly by
    this plugin, but the correlation source files it reads were
    produced by plugins that already handle this (see gps_tagger_ng.py/
    timer_ng.py's `_as_ap_dict`).
  - bettercap events are re-dispatched to plugins as
    `on_bcap_<tag_with_dots_as_underscores>` (confirmed in agent.py, and
    already used by blemon_plugin.py's own `on_bcap_ble_device_*` hooks
    and gps_tagger_ng.py's `on_bcap_wifi_*` hooks) - this plugin keeps
    that same mechanism for BLE events instead of polling.

What's added on top of the two originals (all approved - see NOTES.md
for the full design writeup and every documented limitation):
  - One unified, deduplicated-by-MAC device table (BLE + classic) with a
    real JSON-serializable schema, persisted to disk and reloaded across
    reboots.
  - Configurable retention/expiry pruning (`retention_hours`).
  - Configurable RSSI-threshold noise filtering and a `known_devices`
    allowlist that flags (not deletes) devices of interest.
  - A curated offline OUI vendor lookup, extensible via an optional
    external JSON file.
  - Best-effort BLE tracker flagging for Apple Find My (AirTag and
    other Find My network accessories), Tile, and Samsung SmartTag
    devices, from their advertised manufacturer/service data.
  - Best-effort WiFi<->Bluetooth correlation against THREE other
    plugins-wip data sources (crack_house_ng.py's cracked-network
    potfile, timer_ng.py's per-handshake CSV, gps_tagger_ng.py's
    per-AP GPS JSON files) - every one of these is optional and
    degrades gracefully (try/except + a debug log line) if the file or
    directory isn't present.
  - A webhook status page (device table, counts by radio type) plus a
    JSON export route that always serves exactly the one persisted
    device-table file - it takes no path/filename from the request, so
    there is no path-traversal surface at all (see the note in
    `on_webhook` for why this matters and what it's avoiding).

See config.toml for every option, README.md for install/troubleshooting,
and NOTES.md for the full bug-fix/design writeup including exact OUI
table scope and tracker-signature bytes.
"""

import html
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK

try:
    from flask import Response
except ImportError:  # pragma: no cover - flask is always present on-device
    Response = None


# This fork's plugin loader does NOT merge a plugin's __defaults__ into
# self.options (see module docstring) - every option must be read via
# _opt() with a real fallback here, never bare self.options[...].
DEFAULTS = {
    "enabled": False,
    # Where the merged device table is persisted as JSON, reloaded on
    # on_loaded so counts/history survive a reboot. Post-cluster-review
    # update: moved from "/root/handshakes" to
    # "/etc/pwnagotchi/handshakes" to match crack_house_ng.py's own
    # updated saving_path default (this fork's actual real handshake
    # directory).
    "device_table_path": "/etc/pwnagotchi/handshakes/bluetooth_recon_ng.json",
    # Devices whose last_seen is older than this are pruned from memory
    # and from the persisted file on the next scan cycle. 168h = 7 days.
    "retention_hours": 168,
    # None/disabled by default. When set, a sighting with an RSSI value
    # weaker (more negative) than this is not recorded/counted. Only
    # applies when RSSI is actually available for that event.
    "rssi_threshold": None,
    # MAC strings to flag as is_known=True. Filtering never drops a
    # known device's data - it only reduces noise for counting/alerting.
    "known_devices": [],
    # How often (seconds) the classic BR/EDR `hcitool scan` loop runs.
    # BLE has no equivalent - it's event-driven through bettercap's own
    # ble.recon, matching blemon_plugin.py's original approach.
    "classic_scan_interval_seconds": 60,
    # Optional path to a JSON file of {"AA:BB:CC": "Vendor Name"} entries
    # to merge on top of the built-in curated OUI_TABLE (overrides it on
    # a prefix collision). None = built-in table only.
    "oui_extra_path": None,
    # How many minutes around a Bluetooth sighting to look for WiFi
    # network activity (timer_ng.py's CSV) when correlating.
    "correlation_window_minutes": 10,
    # Optional-source paths for the three best-effort correlation
    # sources. Defaults match each source plugin's own default so this
    # works out of the box if those plugins are also installed with
    # their defaults; all three are read only if the file/dir exists.
    # Post-cluster-review update: crack_house_ng.py's own saving_path
    # default moved to "/etc/pwnagotchi/handshakes" - matched here too.
    "crack_house_potfile_path": "/etc/pwnagotchi/handshakes/crack_house_ng.potfile",
    "timer_csv_path": "/etc/pwnagotchi/timer_ng.csv",
    "gps_tagger_dir_path": "/etc/pwnagotchi/gps_tagger_ng",
    # On-screen element positions. None on any of the four means: use
    # the built-in default position for that element - same
    # None-means-auto pattern as crack_house_ng.py's
    # position_x/position_y/stats_position_x/stats_position_y.
    "ble_position_x": None,
    "ble_position_y": None,
    "classic_position_x": None,
    "classic_position_y": None,
}

BLE_ELEMENT_NAME = "bluetooth_recon_ng_ble"
CLASSIC_ELEMENT_NAME = "bluetooth_recon_ng_bt"

# ---------------------------------------------------------------------------
# Curated OUI (first-3-octet) -> vendor lookup.
#
# This is a small, hand-curated subset of common consumer-electronics/
# mobile/IoT vendors, NOT the full IEEE OUI registry (which is several
# megabytes and not appropriate to embed here). Any prefix not in this
# table (or in an optional oui_extra_path override file) falls back to
# "Unknown". See NOTES.md for the exact scope/limitations and how to
# extend it via oui_extra_path.
# ---------------------------------------------------------------------------
OUI_TABLE = {
    # Apple
    "00:1B:63": "Apple, Inc.",
    "3C:15:C2": "Apple, Inc.",
    "40:CB:C0": "Apple, Inc.",
    "44:D8:84": "Apple, Inc.",
    "5C:95:AE": "Apple, Inc.",
    "68:96:7B": "Apple, Inc.",
    "70:56:81": "Apple, Inc.",
    "7C:6D:62": "Apple, Inc.",
    "88:E9:FE": "Apple, Inc.",
    "90:B0:ED": "Apple, Inc.",
    "A4:5E:60": "Apple, Inc.",
    "AC:BC:32": "Apple, Inc.",
    "B8:E8:56": "Apple, Inc.",
    "D0:E1:40": "Apple, Inc.",
    "F0:18:98": "Apple, Inc.",
    "F4:5C:89": "Apple, Inc.",
    # Samsung
    "00:12:47": "Samsung Electronics",
    "08:D4:2B": "Samsung Electronics",
    "1C:5A:3E": "Samsung Electronics",
    "34:23:BA": "Samsung Electronics",
    "5C:0A:5B": "Samsung Electronics",
    "78:1F:DB": "Samsung Electronics",
    "8C:71:F8": "Samsung Electronics",
    "A0:21:95": "Samsung Electronics",
    "CC:07:AB": "Samsung Electronics",
    "E8:50:8B": "Samsung Electronics",
    # Google
    "3C:5A:B4": "Google, Inc.",
    "54:60:09": "Google, Inc.",
    "94:EB:2C": "Google, Inc.",
    "F4:F5:D8": "Google, Inc.",
    # Amazon
    "0C:47:C9": "Amazon Technologies",
    "34:D2:70": "Amazon Technologies",
    "44:65:0D": "Amazon Technologies",
    "68:37:E9": "Amazon Technologies",
    "AC:63:BE": "Amazon Technologies",
    "F0:27:2D": "Amazon Technologies",
    # Microsoft
    "00:0D:3A": "Microsoft Corporation",
    "28:18:78": "Microsoft Corporation",
    "60:45:BD": "Microsoft Corporation",
    "7C:1E:52": "Microsoft Corporation",
    # Sony
    "04:5D:56": "Sony Corporation",
    "30:39:26": "Sony Corporation",
    "AC:9B:0A": "Sony Corporation",
    "FC:F1:52": "Sony Corporation",
    # LG Electronics
    "10:F1:F2": "LG Electronics",
    "88:C9:D0": "LG Electronics",
    "C4:36:6C": "LG Electronics",
    # Huawei
    "00:E0:FC": "Huawei Technologies",
    "20:F3:A3": "Huawei Technologies",
    "70:72:3C": "Huawei Technologies",
    # Xiaomi
    "28:6C:07": "Xiaomi Communications",
    "34:CE:00": "Xiaomi Communications",
    "64:CC:2E": "Xiaomi Communications",
    "78:11:DC": "Xiaomi Communications",
    # Fitbit
    "AC:37:43": "Fitbit, Inc.",
    "D0:33:11": "Fitbit, Inc.",
    # Garmin
    "00:0E:2D": "Garmin International",
    "A8:BB:50": "Garmin International",
    # Sonos
    "48:A6:B8": "Sonos, Inc.",
    "5C:AA:FD": "Sonos, Inc.",
    "94:9F:3E": "Sonos, Inc.",
    # Bose
    "04:52:C7": "Bose Corporation",
    "A0:E9:DB": "Bose Corporation",
    # Tile
    "D0:F8:8C": "Tile, Inc.",
    # Espressif (ESP32/ESP8266 - very common cheap IoT/BLE beacons)
    "24:0A:C4": "Espressif Inc.",
    "30:AE:A4": "Espressif Inc.",
    "3C:71:BF": "Espressif Inc.",
    "84:F3:EB": "Espressif Inc.",
    "AC:67:B2": "Espressif Inc.",
    # Raspberry Pi Foundation
    "28:CD:C1": "Raspberry Pi Foundation",
    "B8:27:EB": "Raspberry Pi Foundation",
    "D8:3A:DD": "Raspberry Pi Foundation",
    "DC:A6:32": "Raspberry Pi Foundation",
    "E4:5F:01": "Raspberry Pi Foundation",
    # Nordic Semiconductor (common BLE-only chipset in trackers/wearables)
    "F0:C7:7F": "Nordic Semiconductor ASA",
    # Texas Instruments (also common in BLE chipsets)
    "00:1A:B6": "Texas Instruments",
    "A0:E6:F8": "Texas Instruments",
    # Broadcom
    "00:10:18": "Broadcom",
    "3C:5A:37": "Broadcom",
    # Intel
    "00:1B:21": "Intel Corporate",
    "3C:A9:F4": "Intel Corporate",
    "A4:34:D9": "Intel Corporate",
    # TP-Link
    "50:C7:BF": "TP-Link Technologies",
    "AC:84:C6": "TP-Link Technologies",
    # Netgear
    "00:14:6C": "NETGEAR",
    "A0:04:60": "NETGEAR",
    # D-Link
    "00:1E:58": "D-Link Corporation",
    "5C:D9:98": "D-Link Corporation",
    # Ubiquiti
    "04:18:D6": "Ubiquiti Networks",
    "24:A4:3C": "Ubiquiti Networks",
    # Belkin
    "94:10:3E": "Belkin International",
    "EC:1A:59": "Belkin International",
    # Logitech
    "00:07:61": "Logitech, Inc.",
    "88:C6:26": "Logitech, Inc.",
    # Philips (Hue and friends)
    "00:17:88": "Philips Lighting (Signify)",
    "EC:B5:FA": "Philips Lighting (Signify)",
    # Ring
    "34:3E:A4": "Ring LLC",
    # Nest Labs
    "18:B4:30": "Nest Labs",
    "64:16:66": "Nest Labs",
    # Roku
    "AC:3A:7A": "Roku, Inc.",
    "B0:A7:37": "Roku, Inc.",
    # ASUSTek
    "1C:87:2C": "ASUSTek Computer",
    "50:46:5D": "ASUSTek Computer",
    # Dell
    "18:03:73": "Dell Inc.",
    "F8:B1:56": "Dell Inc.",
    # HP
    "3C:D9:2B": "HP Inc.",
    "9C:8E:99": "HP Inc.",
    # Lenovo
    "3C:A8:2A": "Lenovo",
    "60:D9:C7": "Lenovo",
    # Anker Innovations
    "58:2D:34": "Anker Innovations",
    # GoPro
    "AA:BB:57": "GoPro, Inc.",
    # DJI
    "60:60:1F": "DJI",
    # Withings
    "00:24:E4": "Withings",
    # Polar Electro
    "00:12:07": "Polar Electro Oy",
    # Jabra / GN Audio
    "00:0A:3A": "Jabra (GN Audio)",
    # Plantronics / Poly
    "64:16:7F": "Plantronics (Poly)",
    # Sennheiser
    "00:1B:66": "Sennheiser Electronic",
}


def _normalize_mac(mac):
    return str(mac).strip().upper()


def _oui_prefix(mac):
    mac = _normalize_mac(mac)
    parts = mac.split(":")
    if len(parts) < 3:
        return None
    return ":".join(parts[:3])


# ---------------------------------------------------------------------------
# Best-effort BLE tracker signature matching.
#
# These signatures are reverse-engineered/publicly-discussed patterns,
# NOT officially documented by Apple/Tile/Samsung, and they can change
# over time. Treat any match as a strong hint, not a certainty - see
# NOTES.md for exact byte values and caveats, and for why this could
# not be verified against a real AirTag/Tile/SmartTag in this sandbox.
#
# match_tracker() takes normalized, already-decoded inputs (an
# integer BLE manufacturer company ID, the manufacturer-specific
# payload bytes that followed it, and a list of lowercase advertised
# service UUID strings) so it's directly unit-testable against known
# byte sequences without depending on bettercap's exact BLE event JSON
# schema (which this plugin best-effort-extracts separately, in
# _extract_manufacturer_info - see that function's own caveat).
# ---------------------------------------------------------------------------
APPLE_COMPANY_ID = 0x004C
APPLE_FINDMY_TYPE = 0x12
APPLE_FINDMY_LENGTH = 0x19

TILE_COMPANY_ID = 0x0136

SAMSUNG_COMPANY_ID = 0x0075
SAMSUNG_SMARTTAG_SERVICE_UUID = "fd5a"
SAMSUNG_SMARTTAG_MFG_TYPE = 0x01


def match_tracker(company_id, payload, service_uuids=None):
    """Returns (is_tracker, tracker_type) for a decoded BLE advertisement.

    company_id: int or None
    payload: bytes (manufacturer-specific data after the company ID) or None
    service_uuids: iterable of str (16/32/128-bit UUIDs, any case) or None
    """
    service_uuids = [str(u).lower() for u in (service_uuids or [])]
    payload = payload or b""

    if (
        company_id == APPLE_COMPANY_ID
        and len(payload) >= 2
        and payload[0] == APPLE_FINDMY_TYPE
        and payload[1] == APPLE_FINDMY_LENGTH
    ):
        return True, "airtag"

    if company_id == TILE_COMPANY_ID:
        return True, "tile"

    if any(SAMSUNG_SMARTTAG_SERVICE_UUID in u for u in service_uuids):
        return True, "smarttag"

    if (
        company_id == SAMSUNG_COMPANY_ID
        and len(payload) >= 1
        and payload[0] == SAMSUNG_SMARTTAG_MFG_TYPE
    ):
        return True, "smarttag"

    return False, None


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts):
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


class BluetoothReconNG(plugins.Plugin):
    __author__ = (
        "merged/rebuilt for this project's plugin audit from "
        "itsdarklikehell's blemon_plugin.py (edited from evilsocket's "
        "example plugin) and bluetoothsniffer.py (itsdarklikehell/"
        "diytechtinker, fixed by Jayofelony)"
    )
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Unified BLE + classic Bluetooth recon: device table, OUI vendor "
        "lookup, best-effort tracker (AirTag/Tile/SmartTag) flagging, "
        "retention, and optional WiFi correlation."
    )
    __name__ = "BluetoothReconNG"
    __help__ = __description__
    __dependencies__ = {
        # bettercap (with its ble module) and bluez/hcitool are both
        # already part of the jayofelony image - nothing extra to
        # install beyond stdlib for this plugin itself.
        "apt": ["bluez"],
        "pip": [],
    }

    def __init__(self):
        self.devices = {}  # mac -> device record dict
        self._agent = None
        self._stop_recon = False
        self._last_classic_scan = 0.0
        self._oui_extra = {}

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    # ------------------------------------------------------------------
    # Lifecycle

    def on_loaded(self):
        logging.info(f"[{self.__class__.__name__}] plugin loaded")
        self._load_extra_oui()
        self._load_state()
        self._prune_expired()

    def on_ready(self, agent):
        self._agent = agent
        logging.info(f"[{self.__class__.__name__}] starting ble.recon")
        try:
            agent.run("ble.clear; ble.recon on")
            self._stop_recon = True
        except Exception as err:
            logging.warning(
                f"[{self.__class__.__name__}] ble.recon probably already "
                f"running: {err!r}"
            )
            self._stop_recon = False

    def on_unload(self, ui):
        logging.info(f"[{self.__class__.__name__}] plugin unloading")
        if self._stop_recon and self._agent is not None:
            try:
                self._agent.run("ble.recon off; ble.clear")
            except Exception as err:
                logging.warning(
                    f"[{self.__class__.__name__}] couldn't stop ble.recon: "
                    f"{err!r}"
                )
        self._agent = None

        with ui._lock:
            for name in (BLE_ELEMENT_NAME, CLASSIC_ELEMENT_NAME):
                try:
                    ui.remove_element(name)
                except KeyError:
                    pass

        self._save_state()

    # ------------------------------------------------------------------
    # UI

    def _default_ble_position(self):
        return (0, 15)

    def _default_classic_position(self):
        return (0, 25)

    def on_ui_setup(self, ui):
        ble_x = self._opt("ble_position_x")
        ble_y = self._opt("ble_position_y")
        if ble_x is None or ble_y is None:
            ble_x, ble_y = self._default_ble_position()

        ui.add_element(
            BLE_ELEMENT_NAME,
            LabeledValue(
                color=BLACK,
                label="BLE",
                value="0",
                position=(ble_x, ble_y),
                label_font=fonts.Bold,
                text_font=fonts.Medium,
            ),
        )

        bt_x = self._opt("classic_position_x")
        bt_y = self._opt("classic_position_y")
        if bt_x is None or bt_y is None:
            bt_x, bt_y = self._default_classic_position()

        ui.add_element(
            CLASSIC_ELEMENT_NAME,
            LabeledValue(
                color=BLACK,
                label="BT",
                value="0",
                position=(bt_x, bt_y),
                label_font=fonts.Bold,
                text_font=fonts.Medium,
            ),
        )

    def on_ui_update(self, ui):
        ble_count, classic_count = self._counts_by_radio()
        ui.set(BLE_ELEMENT_NAME, str(ble_count))
        ui.set(CLASSIC_ELEMENT_NAME, str(classic_count))

        interval = self._opt("classic_scan_interval_seconds")
        now = time.time()
        if interval and (now - self._last_classic_scan) >= interval:
            self._last_classic_scan = now
            self._classic_scan()

    def _counts_by_radio(self):
        ble = sum(1 for d in self.devices.values() if d["radio_type"] == "ble")
        classic = sum(1 for d in self.devices.values() if d["radio_type"] == "classic")
        return ble, classic

    # ------------------------------------------------------------------
    # BLE (bettercap ble.recon events)

    def on_bcap_ble_device_new(self, agent, event):
        self._handle_ble_event(event)

    def on_bcap_ble_device_connected(self, agent, event):
        self._handle_ble_event(event)

    def on_bcap_ble_device_lost(self, agent, event):
        # Devices are retained (not removed) until retention_hours expiry
        # - a "lost" event just means bettercap stopped seeing it in this
        # session, not that the device is gone for good. We still refresh
        # last_seen/rssi from the event if it carries anything useful.
        self._handle_ble_event(event)

    def _handle_ble_event(self, event):
        try:
            data = event.get("data", {}) if isinstance(event, dict) else {}
            mac = data.get("mac") or data.get("id")
            if not mac:
                return
            name = data.get("name") or None
            rssi = data.get("rssi")
            company_id, payload, service_uuids = self._extract_manufacturer_info(data)
            self._record_device(
                mac,
                "ble",
                name=name,
                rssi=rssi,
                company_id=company_id,
                payload=payload,
                service_uuids=service_uuids,
            )
        except Exception as err:
            logging.warning(
                f"[{self.__class__.__name__}] error handling BLE event: {err!r}"
            )

    def _extract_manufacturer_info(self, data):
        """Best-effort extraction of (company_id, payload_bytes,
        service_uuids) from a bettercap ble.recon event's "data" dict.

        This project's cloned framework doesn't ship bettercap's own Go
        source, so the exact JSON shape of BLE manufacturer-specific
        advertisement data on this fork's bettercap version is NOT
        independently confirmed here - this tries a couple of plausible
        key names bettercap is known to use elsewhere ("manufacturer",
        "vendor_data") and degrades to (None, b"", []) rather than
        raising if none match. See NOTES.md's "still needs real-hardware
        testing" section - match_tracker() itself is fully verified
        against known-good byte sequences independent of this
        extraction step.
        """
        try:
            service_uuids = data.get("service uuids") or data.get("service_uuids") or []
            raw = data.get("manufacturer") or data.get("vendor_data")
            if not raw:
                return None, b"", service_uuids
            if isinstance(raw, str):
                raw = bytes.fromhex(raw.replace(" ", "").replace(":", ""))
            elif isinstance(raw, (list, tuple)):
                raw = bytes(raw)
            if not isinstance(raw, (bytes, bytearray)) or len(raw) < 2:
                return None, b"", service_uuids
            company_id = raw[0] | (raw[1] << 8)  # little-endian, per BT spec
            payload = bytes(raw[2:])
            return company_id, payload, service_uuids
        except Exception as err:
            logging.debug(
                f"[{self.__class__.__name__}] couldn't parse manufacturer "
                f"data: {err!r}"
            )
            return None, b"", []

    # ------------------------------------------------------------------
    # Classic BT (hcitool scan)

    def _classic_scan(self):
        logging.info(f"[{self.__class__.__name__}] scanning for classic BT devices")
        try:
            output = subprocess.check_output(
                ["hcitool", "scan", "--flush"],
                stderr=subprocess.STDOUT,
                timeout=30,
            ).decode(errors="replace")
        except FileNotFoundError:
            logging.warning(
                f"[{self.__class__.__name__}] hcitool not found - install "
                f"bluez/bluez-tools, or set classic_scan_interval_seconds "
                f"to 0 to disable classic scanning entirely"
            )
            return
        except subprocess.CalledProcessError as err:
            logging.warning(
                f"[{self.__class__.__name__}] hcitool scan exited "
                f"non-zero: {err!r}"
            )
            return
        except (subprocess.SubprocessError, OSError) as err:
            logging.warning(
                f"[{self.__class__.__name__}] hcitool scan failed: {err!r}"
            )
            return

        for line in output.splitlines():
            line = line.strip()
            if not line or ":" not in line.split()[0]:
                continue
            parts = line.split("\t") if "\t" in line else line.split(None, 1)
            mac = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else None
            if mac.lower().startswith("scanning"):
                continue
            self._record_device(mac, "classic", name=name, rssi=None)

    # ------------------------------------------------------------------
    # Unified device table

    def _record_device(
        self,
        mac,
        radio_type,
        name=None,
        rssi=None,
        company_id=None,
        payload=None,
        service_uuids=None,
    ):
        mac = _normalize_mac(mac)
        threshold = self._opt("rssi_threshold")
        if threshold is not None and rssi is not None and rssi < threshold:
            logging.debug(
                f"[{self.__class__.__name__}] {mac} filtered by "
                f"rssi_threshold ({rssi} < {threshold})"
            )
            return

        now = _now_iso()
        existing = self.devices.get(mac)
        is_tracker, tracker_type = match_tracker(company_id, payload, service_uuids)

        known_macs = {_normalize_mac(m) for m in (self._opt("known_devices") or [])}
        is_known = mac in known_macs

        if existing is None:
            record = {
                "mac": mac,
                # Keep whichever radio_type first classified this MAC -
                # a genuine BLE-vs-classic collision on one real MAC
                # doesn't normally happen, so once set this doesn't
                # flip back and forth between scan sources.
                "radio_type": radio_type,
                "name": name,
                "vendor": self._oui_vendor(mac),
                "is_tracker": is_tracker,
                "tracker_type": tracker_type,
                "is_known": is_known,
                "first_seen": now,
                "last_seen": now,
                "rssi": rssi,
            }
            self.devices[mac] = record
        else:
            record = existing
            record["last_seen"] = now
            if name:
                record["name"] = name
            if rssi is not None:
                record["rssi"] = rssi
            if is_tracker:
                record["is_tracker"] = True
                record["tracker_type"] = tracker_type
            record["is_known"] = is_known or record.get("is_known", False)

        correlation = self._correlate(record, now)
        record["correlated_networks"] = correlation["correlated_networks"]
        record["any_cracked"] = correlation["any_cracked"]
        record["correlated_location"] = correlation["correlated_location"]

        self._save_state()

    def _oui_vendor(self, mac):
        prefix = _oui_prefix(mac)
        if prefix is None:
            return "Unknown"
        if prefix in self._oui_extra:
            return self._oui_extra[prefix]
        return OUI_TABLE.get(prefix, "Unknown")

    def _load_extra_oui(self):
        path = self._opt("oui_extra_path")
        self._oui_extra = {}
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self._oui_extra = {
                _normalize_mac(k): v for k, v in data.items() if isinstance(data, dict)
            }
            logging.info(
                f"[{self.__class__.__name__}] loaded {len(self._oui_extra)} "
                f"extra OUI entries from {path}"
            )
        except FileNotFoundError:
            logging.debug(
                f"[{self.__class__.__name__}] oui_extra_path {path} not found, skipping"
            )
        except Exception as err:
            logging.warning(
                f"[{self.__class__.__name__}] couldn't read oui_extra_path "
                f"{path}: {err!r}"
            )

    # ------------------------------------------------------------------
    # Retention / expiry

    def _prune_expired(self):
        retention_hours = self._opt("retention_hours")
        if not retention_hours:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
        expired = []
        for mac, record in self.devices.items():
            last_seen = _parse_iso(record.get("last_seen"))
            if last_seen is not None and last_seen < cutoff:
                expired.append(mac)
        for mac in expired:
            del self.devices[mac]
        if expired:
            logging.info(
                f"[{self.__class__.__name__}] pruned {len(expired)} expired "
                f"device(s) (retention_hours={retention_hours})"
            )
            self._save_state()

    # ------------------------------------------------------------------
    # Persistence

    def _load_state(self):
        path = self._opt("device_table_path")
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.devices = data
                logging.info(
                    f"[{self.__class__.__name__}] loaded {len(self.devices)} "
                    f"device(s) from {path}"
                )
        except FileNotFoundError:
            logging.debug(
                f"[{self.__class__.__name__}] no existing device table at "
                f"{path}, starting fresh"
            )
        except Exception as err:
            logging.warning(
                f"[{self.__class__.__name__}] couldn't read {path}: {err!r}"
            )

    def _save_state(self):
        path = self._opt("device_table_path")
        try:
            directory = os.path.dirname(path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
            with open(path, "w") as f:
                json.dump(self.devices, f)
        except Exception as err:
            logging.warning(
                f"[{self.__class__.__name__}] couldn't write {path}: {err!r}"
            )

    # ------------------------------------------------------------------
    # Best-effort WiFi<->Bluetooth correlation (all three sources optional)

    def _correlate(self, record, sighting_time_iso):
        result = {
            "correlated_networks": [],
            "any_cracked": False,
            "correlated_location": None,
        }
        try:
            sighting_time = _parse_iso(sighting_time_iso)
            if sighting_time is None:
                return result

            window = timedelta(minutes=self._opt("correlation_window_minutes"))
            networks = self._nearby_wifi_networks(sighting_time, window)
            if not networks:
                return result

            cracked = self._cracked_hostnames()
            result["correlated_networks"] = sorted(networks)
            result["any_cracked"] = any(n.lower() in cracked for n in networks)

            locations = self._gps_locations()
            for network in networks:
                loc = locations.get(network.lower())
                if loc:
                    result["correlated_location"] = loc
                    break
        except Exception as err:
            logging.debug(
                f"[{self.__class__.__name__}] correlation skipped: {err!r}"
            )
        return result

    def _nearby_wifi_networks(self, sighting_time, window):
        path = self._opt("timer_csv_path")
        networks = set()
        try:
            import csv

            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    ts = _parse_iso(row.get("timestamp"))
                    if ts is None:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if abs(sighting_time - ts) <= window:
                        network = row.get("network")
                        if network:
                            networks.add(network)
        except FileNotFoundError:
            logging.debug(
                f"[{self.__class__.__name__}] timer_csv_path {path} not "
                f"found, skipping WiFi correlation"
            )
        except Exception as err:
            logging.debug(
                f"[{self.__class__.__name__}] couldn't read timer_csv_path "
                f"{path}: {err!r}"
            )
        return networks

    def _cracked_hostnames(self):
        path = self._opt("crack_house_potfile_path")
        hostnames = set()
        try:
            with open(path) as f:
                for line in f:
                    line = line.rstrip()
                    if ":" in line:
                        hostname = line.split(":", 1)[0]
                        if hostname:
                            hostnames.add(hostname.lower())
        except FileNotFoundError:
            logging.debug(
                f"[{self.__class__.__name__}] crack_house_potfile_path "
                f"{path} not found, skipping cracked-network correlation"
            )
        except Exception as err:
            logging.debug(
                f"[{self.__class__.__name__}] couldn't read "
                f"crack_house_potfile_path {path}: {err!r}"
            )
        return hostnames

    def _gps_locations(self):
        path = self._opt("gps_tagger_dir_path")
        locations = {}
        try:
            if not os.path.isdir(path):
                return locations
            for filename in os.listdir(path):
                if not filename.startswith("pn_ap_") or not filename.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(path, filename)) as f:
                        record = json.load(f)
                except Exception:
                    continue
                ap = record.get("ap") or {}
                gps = record.get("gps") or {}
                hostname = ap.get("hostname")
                lat, lon = gps.get("Latitude"), gps.get("Longitude")
                if hostname and lat is not None and lon is not None:
                    locations[str(hostname).lower()] = {"lat": lat, "lon": lon}
        except Exception as err:
            logging.debug(
                f"[{self.__class__.__name__}] couldn't read "
                f"gps_tagger_dir_path {path}: {err!r}"
            )
        return locations

    # ------------------------------------------------------------------
    # Webhook: status page + export

    def on_webhook(self, path, request):
        self._prune_expired()

        path = (path or "").strip("/")
        if path in ("", "export"):
            if path == "export":
                return self._export_json()
            return self._status_page()
        return "Not found", 404

    def _status_page(self):
        ble_count, classic_count = self._counts_by_radio()
        total = len(self.devices)

        devices = sorted(
            self.devices.values(),
            key=lambda d: d.get("last_seen") or "",
            reverse=True,
        )

        rows = []
        for d in devices:
            correlated = ""
            if d.get("correlated_networks"):
                correlated = html.escape(", ".join(d["correlated_networks"]))
                if d.get("any_cracked"):
                    correlated += " (cracked)"
            loc = d.get("correlated_location")
            loc_str = f"{loc['lat']:.5f},{loc['lon']:.5f}" if loc else ""
            rows.append(
                "<tr>"
                f"<td>{html.escape(d.get('mac', ''))}</td>"
                f"<td>{html.escape(d.get('radio_type', ''))}</td>"
                f"<td>{html.escape(d.get('name') or '')}</td>"
                f"<td>{html.escape(d.get('vendor') or '')}</td>"
                f"<td>{html.escape(d.get('tracker_type') or '') if d.get('is_tracker') else ''}</td>"
                f"<td>{'yes' if d.get('is_known') else ''}</td>"
                f"<td>{html.escape(d.get('first_seen') or '')}</td>"
                f"<td>{html.escape(d.get('last_seen') or '')}</td>"
                f"<td>{d.get('rssi') if d.get('rssi') is not None else ''}</td>"
                f"<td>{correlated}</td>"
                f"<td>{loc_str}</td>"
                "</tr>"
            )

        table_body = "".join(rows) or (
            "<tr><td colspan=11><i>no devices recorded yet</i></td></tr>"
        )

        return (
            "<html><head><title>BluetoothReconNG</title></head>"
            "<body style='font-family: sans-serif;'>"
            "<h2>BluetoothReconNG</h2>"
            f"<p>Total: {total} &nbsp; BLE: {ble_count} &nbsp; Classic: {classic_count}</p>"
            "<p><a href='export'>Download full device table (JSON)</a></p>"
            "<table border='1' cellpadding='4'>"
            "<tr><th>MAC</th><th>Radio</th><th>Name</th><th>Vendor</th>"
            "<th>Tracker</th><th>Known</th><th>First seen</th>"
            "<th>Last seen</th><th>RSSI</th><th>Correlated networks</th>"
            "<th>Location</th></tr>"
            f"{table_body}"
            "</table>"
            "</body></html>"
        )

    def _export_json(self):
        # This takes NO path/filename from the request at all - it always
        # serves exactly the one persisted device-table file this plugin
        # itself writes (see _save_state). That's a deliberate design
        # choice, not an oversight: this project's own research found a
        # real path-traversal bug in another plugin's (pwndroid.py)
        # download handler from taking a user-supplied path and not
        # confining it to a safe directory. There is no equivalent
        # surface here because no request input ever reaches a filename.
        payload = json.dumps(self.devices).encode()
        if Response is not None:
            return Response(
                payload,
                mimetype="application/json",
                headers={
                    "Content-Disposition": "attachment; filename=bluetooth_recon_ng.json"
                },
            )
        return payload  # pragma: no cover - flask always present on-device
