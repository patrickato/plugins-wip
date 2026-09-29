import sys
import os
import json
import tempfile
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import bluetooth_recon_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


tmpdir = tempfile.mkdtemp()


def p(name):
    return os.path.join(tmpdir, name)


def make_plugin(**opts):
    plugin = mod.BluetoothReconNG()
    plugin.options = dict(opts)
    return plugin


class FakeUI:
    def __init__(self):
        self.elements = {}
        self.values = {}
        self._lock = mock.MagicMock()
        self._lock.__enter__ = mock.Mock(return_value=None)
        self._lock.__exit__ = mock.Mock(return_value=False)

    def add_element(self, name, el):
        self.elements[name] = el

    def remove_element(self, name):
        if name not in self.elements:
            raise KeyError(name)
        del self.elements[name]

    def set(self, name, value):
        self.values[name] = value


class FakeAgent:
    def __init__(self):
        self.commands = []

    def run(self, cmd):
        self.commands.append(cmd)


# --- Registration -----------------------------------------------------------

check(
    "BluetoothReconNG registers with the real pwnagotchi.plugins loader",
    "bluetooth_recon_ng" in pwnagotchi.plugins.loaded,
)

# --- _opt() falls back to real defaults instead of KeyError (bluetoothsniffer
#     .py's central bug: self.options is a RAW assignment, never merged with
#     any defaults) -------------------------------------------------------

plugin = make_plugin()  # empty options, like a bare `enabled = true` block
check(
    "_opt() returns the real default for a key entirely absent from options",
    plugin._opt("retention_hours") == mod.DEFAULTS["retention_hours"],
)
check(
    "_opt() never raises KeyError even with completely empty options",
    plugin._opt("device_table_path") == mod.DEFAULTS["device_table_path"],
)

# --- Dead hooks from blemon_plugin.py are gone --------------------------

for dead_hook in (
    "on_ai_ready", "on_ai_policy", "on_ai_training_start", "on_ai_training_step",
    "on_ai_training_end", "on_ai_best_reward", "on_ai_worst_reward", "on_free_channel",
):
    check(
        f"dead hook {dead_hook} was removed (not a real framework hook)",
        not hasattr(mod.BluetoothReconNG, dead_hook),
    )

for real_hook in ("on_loaded", "on_ready", "on_unload", "on_ui_setup", "on_ui_update", "on_webhook"):
    check(f"real hook {real_hook} is present", hasattr(mod.BluetoothReconNG, real_hook))

# --- UI element keys: both elements registered under distinct real keys,
#     and on_ui_update updates both under those same keys (blemon_plugin.py's
#     "blecount" vs "blemon_count" key-mismatch bug, fixed by computing counts
#     live from the table instead of a hand-maintained counter) ------------

plugin = make_plugin(device_table_path=p("ui1.json"))
plugin.on_loaded()
ui = FakeUI()
plugin.on_ui_setup(ui)
check("BLE element created under its real key", mod.BLE_ELEMENT_NAME in ui.elements)
check("Classic element created under its real key", mod.CLASSIC_ELEMENT_NAME in ui.elements)
check("BLE element is labeled 'BLE'", ui.elements[mod.BLE_ELEMENT_NAME].label == "BLE")
check("Classic element is labeled 'BT'", ui.elements[mod.CLASSIC_ELEMENT_NAME].label == "BT")

plugin._record_device("AA:BB:CC:00:00:01", "ble", name="Thing")
plugin._record_device("AA:BB:CC:00:00:02", "classic", name="OtherThing")
plugin.on_ui_update(ui)
check("on_ui_update sets the BLE count under the real BLE key", ui.values[mod.BLE_ELEMENT_NAME] == "1")
check("on_ui_update sets the classic count under the real BT key", ui.values[mod.CLASSIC_ELEMENT_NAME] == "1")

# --- Configured ble_position_x/y and classic_position_x/y are honored,
#     independently, following crack_house_ng.py's None-means-auto pattern -

plugin = make_plugin(
    device_table_path=p("ui2.json"),
    ble_position_x=5, ble_position_y=6,
    classic_position_x=50, classic_position_y=60,
)
plugin.on_loaded()
ui = FakeUI()
plugin.on_ui_setup(ui)
check("configured ble_position_x/y is used", ui.elements[mod.BLE_ELEMENT_NAME].xy[:2] == (5, 6))
check("configured classic_position_x/y is used", ui.elements[mod.CLASSIC_ELEMENT_NAME].xy[:2] == (50, 60))

plugin = make_plugin(device_table_path=p("ui3.json"))
plugin.on_loaded()
ui = FakeUI()
plugin.on_ui_setup(ui)
check(
    "default BLE position is used when unset",
    ui.elements[mod.BLE_ELEMENT_NAME].xy[:2] == plugin._default_ble_position(),
)
check(
    "default classic position is used when unset",
    ui.elements[mod.CLASSIC_ELEMENT_NAME].xy[:2] == plugin._default_classic_position(),
)

# --- on_unload never crashes even when an element was never added (State
#     .remove_element() has no guard - the framework fact bluetoothsniffer.py
#     ignored with the wrong element key) ------------------------------------

plugin = make_plugin(device_table_path=p("unload1.json"))
plugin.on_loaded()
ui = FakeUI()  # no elements ever added
try:
    plugin.on_unload(ui)
    unload_ok = True
except Exception:
    unload_ok = False
check("on_unload doesn't crash when no UI elements were ever created", unload_ok)

# --- on_unload only stops ble.recon if this instance actually started it ---

plugin = make_plugin(device_table_path=p("unload2.json"))
plugin.on_loaded()
agent = FakeAgent()
plugin.on_ready(agent)
ui = FakeUI()
plugin.on_ui_setup(ui)
plugin.on_unload(ui)
check("on_unload stops ble.recon when this instance started it", "ble.recon off; ble.clear" in agent.commands)

plugin2 = make_plugin(device_table_path=p("unload3.json"))
plugin2.on_loaded()
ui2 = FakeUI()
plugin2.on_unload(ui2)  # never called on_ready
check("on_unload doesn't crash when ble.recon was never started", True)

# --- OUI lookup --------------------------------------------------------

plugin = make_plugin(device_table_path=p("oui1.json"))
plugin.on_loaded()
check(
    "a known OUI prefix resolves to its real vendor",
    plugin._oui_vendor("D0:F8:8C:11:22:33") == "Tile, Inc.",
)
check(
    "an unmatched OUI prefix falls back to Unknown",
    plugin._oui_vendor("02:00:00:11:22:33") == "Unknown",
)

extra_path = p("oui_extra.json")
with open(extra_path, "w") as f:
    json.dump({"D0:F8:8C": "Totally Not Tile"}, f)
plugin = make_plugin(device_table_path=p("oui2.json"), oui_extra_path=extra_path)
plugin.on_loaded()
check(
    "oui_extra_path overrides the built-in table on a prefix collision",
    plugin._oui_vendor("D0:F8:8C:11:22:33") == "Totally Not Tile",
)
check(
    "oui_extra_path doesn't affect prefixes it doesn't mention",
    plugin._oui_vendor("00:1B:63:11:22:33") == "Apple, Inc.",
)

# --- Tracker flagging: match_tracker() against known-good sample bytes ----

check(
    "Apple Find My signature (company 0x004C, type 0x12, len 0x19) is flagged as airtag",
    mod.match_tracker(0x004C, bytes([0x12, 0x19, 0x00, 0x01]), []) == (True, "airtag"),
)
check(
    "Apple company ID with a non-FindMy payload is NOT flagged",
    mod.match_tracker(0x004C, bytes([0x02, 0x15]), []) == (False, None),
)
check(
    "Tile company ID (0x0136) is flagged as tile",
    mod.match_tracker(0x0136, bytes([0x00, 0x01]), []) == (True, "tile"),
)
check(
    "Samsung SmartTag service UUID 0xFD5A is flagged as smarttag",
    mod.match_tracker(None, b"", ["0000FD5A-0000-1000-8000-00805F9B34FB"]) == (True, "smarttag"),
)
check(
    "Samsung company ID (0x0075) with type byte 0x01 is flagged as smarttag",
    mod.match_tracker(0x0075, bytes([0x01, 0xAA]), []) == (True, "smarttag"),
)
check(
    "Samsung company ID with an unrelated type byte is NOT flagged",
    mod.match_tracker(0x0075, bytes([0x99]), []) == (False, None),
)
check(
    "an ordinary non-tracker BLE advertisement is not flagged at all",
    mod.match_tracker(0x004C, bytes([0x0A, 0x08]), ["0000180F-0000-1000-8000-00805F9B34FB"]) == (False, None),
)
check(
    "no company id / no payload / no service uuids is not flagged",
    mod.match_tracker(None, None, None) == (False, None),
)

# --- Recording a device end-to-end sets is_tracker/tracker_type ------------

plugin = make_plugin(device_table_path=p("tracker1.json"))
plugin.on_loaded()
plugin._record_device(
    "11:22:33:44:55:66", "ble", name=None, rssi=-40,
    company_id=0x004C, payload=bytes([0x12, 0x19, 0x00]), service_uuids=[],
)
rec = plugin.devices["11:22:33:44:55:66"]
check("_record_device sets is_tracker=True for a matched AirTag signature", rec["is_tracker"] is True)
check("_record_device sets tracker_type='airtag' for a matched AirTag signature", rec["tracker_type"] == "airtag")

# --- Retention / expiry pruning --------------------------------------------

plugin = make_plugin(device_table_path=p("retention1.json"), retention_hours=1)
plugin.on_loaded()
old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
recent_time = datetime.now(timezone.utc).isoformat()
plugin.devices = {
    "OLD:MAC": {"mac": "OLD:MAC", "radio_type": "ble", "last_seen": old_time, "first_seen": old_time, "rssi": None, "name": None, "vendor": "Unknown", "is_tracker": False, "tracker_type": None, "is_known": False},
    "NEW:MAC": {"mac": "NEW:MAC", "radio_type": "ble", "last_seen": recent_time, "first_seen": recent_time, "rssi": None, "name": None, "vendor": "Unknown", "is_tracker": False, "tracker_type": None, "is_known": False},
}
plugin._prune_expired()
check("an expired device is pruned", "OLD:MAC" not in plugin.devices)
check("a recent device is kept", "NEW:MAC" in plugin.devices)

plugin2 = make_plugin(device_table_path=p("retention2.json"), retention_hours=0)
plugin2.on_loaded()
plugin2.devices = {"OLD:MAC": {"mac": "OLD:MAC", "radio_type": "ble", "last_seen": old_time}}
plugin2._prune_expired()
check("retention_hours=0 disables pruning entirely", "OLD:MAC" in plugin2.devices)

# --- RSSI filtering ----------------------------------------------------

plugin = make_plugin(device_table_path=p("rssi1.json"), rssi_threshold=-70)
plugin.on_loaded()
plugin._record_device("RSSI:WEAK:MAC", "ble", rssi=-90)
check("a sighting weaker than rssi_threshold is not recorded", "RSSI:WEAK:MAC" not in plugin.devices)

plugin._record_device("RSSI:STRONG:MAC", "ble", rssi=-40)
check("a sighting stronger than rssi_threshold is recorded", "RSSI:STRONG:MAC" in plugin.devices)

plugin._record_device("RSSI:UNKNOWN:MAC", "classic", rssi=None)
check("a sighting with no RSSI available is never filtered, even with a threshold set", "RSSI:UNKNOWN:MAC" in plugin.devices)

# --- known_devices flags only the matching MAC -----------------------------

plugin = make_plugin(device_table_path=p("known1.json"), known_devices=["AA:AA:AA:AA:AA:AA"])
plugin.on_loaded()
plugin._record_device("aa:aa:aa:aa:aa:aa", "ble")  # lowercase on the wire, config is uppercase
plugin._record_device("BB:BB:BB:BB:BB:BB", "ble")
check("a MAC in known_devices is flagged is_known, case-insensitively", plugin.devices["AA:AA:AA:AA:AA:AA"]["is_known"] is True)
check("a MAC not in known_devices is not flagged", plugin.devices["BB:BB:BB:BB:BB:BB"]["is_known"] is False)

# --- Correlation logic against real-format fixture files -------------------

crack_house_path = p("crack_house_ng.potfile")
with open(crack_house_path, "w") as f:
    f.write("MyLab:hunter2\n")

timer_csv_path = p("timer_ng.csv")
sighting_dt = datetime.now(timezone.utc)
nearby_ts = (sighting_dt - timedelta(minutes=2)).isoformat(timespec="seconds")
far_ts = (sighting_dt - timedelta(hours=3)).isoformat(timespec="seconds")
with open(timer_csv_path, "w", newline="") as f:
    f.write("timestamp,network,time_to_deauth,time_to_handshake,time_between_deauth_and_handshake\n")
    f.write(f"{nearby_ts},MyLab,1.0,2.0,3.0\n")
    f.write(f"{far_ts},FarAwayLab,1.0,2.0,3.0\n")

gps_dir = p("gps_tagger_ng")
os.makedirs(gps_dir)
with open(os.path.join(gps_dir, "pn_ap_MyLab_aabbccddeeff.json"), "w") as f:
    json.dump({"ap": {"hostname": "MyLab", "mac": "aa:bb:cc:dd:ee:ff"}, "gps": {"Latitude": 12.34, "Longitude": 56.78}}, f)

plugin = make_plugin(
    device_table_path=p("corr1.json"),
    crack_house_potfile_path=crack_house_path,
    timer_csv_path=timer_csv_path,
    gps_tagger_dir_path=gps_dir,
    correlation_window_minutes=10,
)
plugin.on_loaded()
plugin._record_device("CO:RR:EL:AT:ED:01", "ble", rssi=-40)
rec = plugin.devices["CO:RR:EL:AT:ED:01"]
check("correlation picks up the nearby network within the time window", "MyLab" in rec["correlated_networks"])
check("correlation does not pick up a network outside the time window", "FarAwayLab" not in rec["correlated_networks"])
check("correlation flags any_cracked=True when the network is in crack_house_ng's potfile", rec["any_cracked"] is True)
check(
    "correlation attaches a location from gps_tagger_ng's per-AP file",
    rec["correlated_location"] == {"lat": 12.34, "lon": 56.78},
)

# --- Correlation degrades gracefully when none of the three sources exist --

plugin = make_plugin(
    device_table_path=p("corr2.json"),
    crack_house_potfile_path=p("does_not_exist.potfile"),
    timer_csv_path=p("does_not_exist.csv"),
    gps_tagger_dir_path=p("does_not_exist_dir"),
)
plugin.on_loaded()
try:
    plugin._record_device("NO:CO:RR:EL:AT:02", "classic", rssi=-40)
    corr_ok = True
except Exception:
    corr_ok = False
rec = plugin.devices.get("NO:CO:RR:EL:AT:02", {})
check("recording a device never crashes when all three correlation sources are missing", corr_ok)
check("correlated_networks is empty when no sources exist", rec.get("correlated_networks") == [])
check("any_cracked is False when no sources exist", rec.get("any_cracked") is False)
check("correlated_location is None when no sources exist", rec.get("correlated_location") is None)

# --- Persistence: state survives a reload (on_loaded reads device_table_path)

persist_path = p("persist1.json")
plugin = make_plugin(device_table_path=persist_path)
plugin.on_loaded()
plugin._record_device("PE:RS:IS:TE:NT:01", "ble", name="Persisted")
check("device table file was actually written", os.path.exists(persist_path))

plugin_reloaded = make_plugin(device_table_path=persist_path)
plugin_reloaded.on_loaded()
check("a reloaded plugin picks up the persisted device", "PE:RS:IS:TE:NT:01" in plugin_reloaded.devices)

# --- Classic scan: guarded against a missing hcitool binary ----------------

plugin = make_plugin(device_table_path=p("classic1.json"))
plugin.on_loaded()
with mock.patch("subprocess.check_output", side_effect=FileNotFoundError()):
    try:
        plugin._classic_scan()
        classic_missing_ok = True
    except Exception:
        classic_missing_ok = False
check("_classic_scan doesn't crash when hcitool is missing (FileNotFoundError)", classic_missing_ok)

import subprocess as _subprocess  # noqa: E402

with mock.patch("subprocess.check_output", side_effect=_subprocess.CalledProcessError(1, "hcitool")):
    try:
        plugin._classic_scan()
        classic_nonzero_ok = True
    except Exception:
        classic_nonzero_ok = False
check("_classic_scan doesn't crash on a non-zero hcitool exit", classic_nonzero_ok)

with mock.patch("subprocess.check_output", return_value=b"Scanning ...\nAA:BB:CC:DD:EE:FF\tMyPhone\n"):
    plugin._classic_scan()
check("_classic_scan records a device parsed from real hcitool scan output", "AA:BB:CC:DD:EE:FF" in plugin.devices)
check(
    "_classic_scan records the device's radio_type as classic",
    plugin.devices["AA:BB:CC:DD:EE:FF"]["radio_type"] == "classic",
)

# --- on_ui_update triggers a classic scan on the configured interval -------

plugin = make_plugin(device_table_path=p("classic2.json"), classic_scan_interval_seconds=1)
plugin.on_loaded()
ui = FakeUI()
plugin.on_ui_setup(ui)
with mock.patch.object(plugin, "_classic_scan") as scan_mock:
    plugin._last_classic_scan = 0.0
    plugin.on_ui_update(ui)
check("on_ui_update triggers a classic scan once the interval has elapsed", scan_mock.called)

with mock.patch.object(plugin, "_classic_scan") as scan_mock2:
    plugin._last_classic_scan = time_now = __import__("time").time()
    plugin.on_ui_update(ui)
check("on_ui_update does not scan again before the interval has elapsed", not scan_mock2.called)

# --- BLE event handling: on_bcap_ble_device_new records a device -----------

plugin = make_plugin(device_table_path=p("ble1.json"))
plugin.on_loaded()
event = {"data": {"mac": "CC:CC:CC:CC:CC:CC", "name": "MyEarbuds", "rssi": -55}}
plugin.on_bcap_ble_device_new(mock.Mock(), event)
check("on_bcap_ble_device_new records a device from a real event shape", "CC:CC:CC:CC:CC:CC" in plugin.devices)
check("recorded device has radio_type ble", plugin.devices["CC:CC:CC:CC:CC:CC"]["radio_type"] == "ble")
check("recorded device keeps its broadcast name", plugin.devices["CC:CC:CC:CC:CC:CC"]["name"] == "MyEarbuds")

# --- on_bcap_ble_device_* never crashes on a malformed/empty event ---------

plugin2 = make_plugin(device_table_path=p("ble2.json"))
plugin2.on_loaded()
try:
    plugin2.on_bcap_ble_device_new(mock.Mock(), {})
    plugin2.on_bcap_ble_device_lost(mock.Mock(), {"data": {}})
    plugin2.on_bcap_ble_device_connected(mock.Mock(), None)
    ble_malformed_ok = True
except Exception:
    ble_malformed_ok = False
check("BLE event handlers never crash on malformed/empty events", ble_malformed_ok)

# --- Webhook: status page rendering -----------------------------------------

plugin = make_plugin(device_table_path=p("webhook1.json"))
plugin.on_loaded()
html_empty = plugin.on_webhook("", mock.Mock())
check("webhook status page renders without crashing on an empty table", "BluetoothReconNG" in html_empty)
check("webhook status page labels the BLE count clearly", "BLE:" in html_empty)
check("webhook status page labels the classic count clearly", "Classic:" in html_empty)

plugin._record_device("WE:BH:OO:KT:ES:T1", "ble", name="<script>evil</script>", rssi=-30)
html_with_device = plugin.on_webhook("/", mock.Mock())
check("webhook status page includes a recorded device's MAC", "WE:BH:OO:KT:ES:T1" in html_with_device)
check(
    "webhook status page HTML-escapes device-supplied fields (no raw <script> tag)",
    "<script>evil</script>" not in html_with_device and "&lt;script&gt;" in html_with_device,
)

not_found = plugin.on_webhook("/somewhere-else", mock.Mock())
check("webhook returns a 404-style response for an unrecognized path", not_found[1] == 404)

# --- Webhook: export route ---------------------------------------------------

plugin = make_plugin(device_table_path=p("webhook2.json"))
plugin.on_loaded()
plugin._record_device("EX:PO:RT:TE:ST:01", "classic", name="ExportMe")
result = plugin.on_webhook("export", mock.Mock())
check("export route returns a real flask Response", hasattr(result, "headers") and hasattr(result, "data"))
check(
    "export route sets Content-Disposition: attachment",
    "attachment" in result.headers.get("Content-Disposition", ""),
)
exported = json.loads(result.data)
check("exported JSON contains the recorded device", "EX:PO:RT:TE:ST:01" in exported)


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    print(failures)
    sys.exit(1)
else:
    print("All tests passed.")
