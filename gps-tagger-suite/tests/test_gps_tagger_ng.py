import sys
import os
import types
import json
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))  # only for `prctl` (native, not installable here)
sys.path.insert(0, os.path.join(HERE, ".."))

# Load against the REAL jayofelony framework - pwnagotchi.plugins (the real
# Plugin base class + __init_subclass__ registration), pwnagotchi.ui.fonts,
# and pwnagotchi.ui.components are all the genuine cloned source. Only
# pwnagotchi.ui.view is faked (just its BLACK constant, value 0xFF, copied
# verbatim from that file's own source) because importing it for real pulls
# in pwnagotchi.utils -> tomlkit, which isn't installable in this sandbox
# and has nothing to do with what's under test here.
REAL_PWNAGOTCHI = "/home/claude/jayofelony/pwnagotchi"
sys.path.insert(0, REAL_PWNAGOTCHI)

_fake_view = types.ModuleType("pwnagotchi.ui.view")
_fake_view.BLACK = 0xFF  # verified against pwnagotchi/ui/view.py source
sys.modules["pwnagotchi.ui.view"] = _fake_view

import pwnagotchi.plugins  # noqa: E402
import gps_tagger_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


tmpdir = tempfile.mkdtemp()


def make_plugin(**opts):
    p = mod.GPSTaggerNG()
    p.options = {"pn_output_path": tmpdir, **opts}
    p.config = {}
    p.ready = True
    # Simulate on_ready() having already run with the recommended default
    # (manage_gps=false) - in real use this is what allows get_gps() to
    # read agent["gps"] at all. Without it, GPS is correctly never read,
    # which is its own real behavior but not what these tests are probing.
    p.gps_up = True
    return p


class FakeSession(dict):
    pass


class FakeAgent:
    def __init__(self, gps=None):
        self._gps = gps

    def session(self):
        return {"gps": self._gps} if self._gps is not None else {}


# --- Test 1: real framework registration -------------------------------
# The real Plugin.__init_subclass__ (pwnagotchi/plugins/__init__.py)
# auto-instantiates and registers any class in this module the moment the
# module is imported, keyed by module name - not something a mock could
# fake, this only passes against the genuine cloned framework.
check(
    "GPSTaggerNG registered itself in the REAL framework's plugins.loaded on import",
    "gps_tagger_ng" in pwnagotchi.plugins.loaded
    and isinstance(pwnagotchi.plugins.loaded["gps_tagger_ng"], mod.GPSTaggerNG),
)
check("plugin has on_handshake", hasattr(mod.GPSTaggerNG, "on_handshake"))
check("plugin has on_bcap_wifi_client_probe (fix #4)", hasattr(mod.GPSTaggerNG, "on_bcap_wifi_client_probe"))
check("plugin has on_bcap_wifi_ap_new (fix #4)", hasattr(mod.GPSTaggerNG, "on_bcap_wifi_ap_new"))

# --- Test 2: gps_hot is initialized, never undefined (fix #1) ----------
p = make_plugin()
check("gps_hot initialized to False in __init__", p.gps_hot is False)

# --- Test 3: the wifi.ap.new agent=None path never raises (fix #1/#2) --
p = make_plugin()
try:
    p.aps_update("NE", None, [{"mac": "AA:BB:CC:DD:EE:FF", "hostname": "TestAP", "vendor": "Acme"}])
    check("agent=None AP update does not raise (fix #1/#2)", True)
except Exception as e:
    check(f"agent=None AP update does not raise (got {type(e).__name__}: {e})", False)

# --- Test 4: no GPS -> AP still logged, with 'unknown' location, no crash
p = make_plugin()
agent = FakeAgent(gps=None)
try:
    p.aps_update("WU", agent, [{"mac": "11:22:33:44:55:66", "hostname": "NoGPS-AP", "vendor": "Acme"}])
    check("AP with no GPS fix logs without raising NameError (fix #2)", True)
except NameError as e:
    check(f"AP with no GPS fix logs without raising NameError (got {e})", False)
check("AP count incremented even without GPS", p.pn_count == 1)

# --- Test 5: bare-MAC-string AP (fix #6) --------------------------------
p = make_plugin()
try:
    p.aps_update("HS", None, ["DE:AD:BE:EF:00:01"])
    check("bare MAC string AP does not raise TypeError (fix #6)", True)
except TypeError as e:
    check(f"bare MAC string AP does not raise TypeError (got {e})", False)

# --- Test 6: filename collision fix - two APs with the same hostname ---
p = make_plugin()
agent = FakeAgent(gps={"Latitude": 40.0, "Longitude": -75.0})
p.aps_update("WU", agent, [{"mac": "AA:AA:AA:AA:AA:01", "hostname": "NETGEAR", "vendor": "Netgear"}])
p.aps_update("WU", agent, [{"mac": "BB:BB:BB:BB:BB:02", "hostname": "NETGEAR", "vendor": "Netgear"}])
written_files = [f for f in os.listdir(tmpdir) if f.startswith("pn_ap_NETGEAR")]
check("two same-hostname APs produce two separate files (fix #8)", len(written_files) == 2)

# --- Test 7: each output file is a single well-formed JSON object (fix #7)
for fname in written_files:
    with open(os.path.join(tmpdir, fname)) as fp:
        try:
            data = json.load(fp)
            ok = isinstance(data, dict) and "ap" in data and "gps" in data
        except json.JSONDecodeError:
            ok = False
    check(f"{fname} is a single valid JSON object (fix #7)", ok)

# --- Test 8: distance filter - same AP, tiny GPS jitter, should NOT rewrite
shutil.rmtree(tmpdir)
os.makedirs(tmpdir)
p = make_plugin(min_regap_distance_feet=50)
agent1 = FakeAgent(gps={"Latitude": 40.00000, "Longitude": -75.00000})
p.aps_update("WU", agent1, [{"mac": "CC:CC:CC:CC:CC:03", "hostname": "JitterAP", "vendor": "X"}])
fpath = os.path.join(tmpdir, "pn_ap_JitterAP_CC_CC_CC_CC_CC_03.json")
first_mtime = os.path.getmtime(fpath)
with open(fpath) as fp:
    first_record = json.load(fp)

# clear in-memory dedup to simulate a restart, but keep the file on disk
p.ap_list = {}
# ~5 feet of jitter (well under the 50ft threshold)
agent2 = FakeAgent(gps={"Latitude": 40.000014, "Longitude": -75.00000})
p.aps_update("WU", agent2, [{"mac": "CC:CC:CC:CC:CC:03", "hostname": "JitterAP", "vendor": "X"}])
with open(fpath) as fp:
    second_record = json.load(fp)
check(
    "small GPS jitter under threshold does not rewrite the stored fix (distance filter)",
    second_record["gps"]["Latitude"] == first_record["gps"]["Latitude"],
)

# --- Test 9: distance filter - genuine movement SHOULD rewrite ---------
p.ap_list = {}
# ~500 feet away, well over the 50ft threshold
agent3 = FakeAgent(gps={"Latitude": 40.0014, "Longitude": -75.00000})
p.aps_update("WU", agent3, [{"mac": "CC:CC:CC:CC:CC:03", "hostname": "JitterAP", "vendor": "X"}])
with open(fpath) as fp:
    third_record = json.load(fp)
check(
    "genuine movement past the threshold does rewrite the stored fix (distance filter)",
    third_record["gps"]["Latitude"] == 40.0014,
)

# --- Test 10: .gps.json sidecar written on handshake, matching schema --
shutil.rmtree(tmpdir)
os.makedirs(tmpdir)
p = make_plugin()
p.gps_hot = True
p.pn_gps_coords = {"Latitude": 41.5, "Longitude": -74.25}
capture_path = os.path.join(tmpdir, "somecapture.pcapng")
open(capture_path, "w").close()
p._write_gps_sidecar(capture_path)
sidecar_path = os.path.join(tmpdir, "somecapture.gps.json")
check("sidecar file created next to the capture", os.path.isfile(sidecar_path))
with open(sidecar_path) as fp:
    sidecar = json.load(fp)
check(
    "sidecar uses the Latitude/Longitude schema handshakes_dl_ng.py reads",
    sidecar == {"Latitude": 41.5, "Longitude": -74.25},
)

# --- Test 11: no sidecar written (and no crash) when GPS isn't hot -----
p2 = make_plugin()
p2.gps_hot = False
capture_path2 = os.path.join(tmpdir, "nogps.pcapng")
open(capture_path2, "w").close()
try:
    p2._write_gps_sidecar(capture_path2)
    check("no sidecar attempted without a GPS fix, no crash", not os.path.isfile(os.path.join(tmpdir, "nogps.gps.json")))
except Exception as e:
    check(f"no sidecar attempted without a GPS fix, no crash (got {type(e).__name__}: {e})", False)

# --- Test 12: on_bcap_wifi_ap_new wraps in a list, never crashes (fix #5)
p = make_plugin()
try:
    p.on_bcap_wifi_ap_new(None, {"tag": "wifi.ap.new", "data": {"mac": "FF:FF:FF:FF:FF:FF", "hostname": "New", "vendor": "X", "essid": "New"}})
    check("on_bcap_wifi_ap_new does not raise on a real-shaped event (fix #5)", True)
except TypeError as e:
    check(f"on_bcap_wifi_ap_new does not raise on a real-shaped event (got {e})", False)
check("on_bcap_wifi_ap_new actually tagged the new AP", p.pn_count == 1)

# --- Test 12b: before on_ready runs (gps_up still False), GPS is never
# read, and the AP is still logged safely (not a crash, just "unknown")
p = make_plugin()
p.gps_up = False  # simulate before on_ready() has run
agent = FakeAgent(gps={"Latitude": 40.0, "Longitude": -75.0})
try:
    p.aps_update("WU", agent, [{"mac": "01:01:01:01:01:01", "hostname": "TooEarly", "vendor": "X"}])
    check("AP update before on_ready() (gps_up=False) does not raise", True)
except Exception as e:
    check(f"AP update before on_ready() (gps_up=False) does not raise (got {type(e).__name__}: {e})", False)
check("GPS correctly not read before on_ready()", p.gps_hot is False)

# --- Test 13: empty AP list is handled without a warning-branch crash --
p = make_plugin()
try:
    p.aps_update("WU", None, [])
    check("empty access_points list handled without raising", True)
except Exception as e:
    check(f"empty access_points list handled without raising (got {type(e).__name__}: {e})", False)

# --- Test 14: on_ui_setup/on_ui_update against the REAL LabeledValue ----
class FakeUI:
    def __init__(self):
        self.elements = {}
        self.values = {}

    def add_element(self, name, widget):
        self.elements[name] = widget

    def set(self, name, value):
        self.values[name] = value


p = make_plugin()
ui = FakeUI()
try:
    p.on_ui_setup(ui)
    p.pn_count = 3
    p.pn_status = "Active"
    p.on_ui_update(ui)
    check("on_ui_setup/on_ui_update run against the real LabeledValue without raising", True)
except Exception as e:
    check(f"on_ui_setup/on_ui_update run against the real LabeledValue without raising (got {type(e).__name__}: {e})", False)
check("pn_count element shows the fixed (non-duplicated) counter text (fix #9)", ui.values.get("pn_count") == "3 APs")


# --- ADDED: on-screen element positions are configurable --------------------
class _PosUI:
    def __init__(self, w=250):
        self.elements = {}
        self._w = w

    def add_element(self, name, widget):
        self.elements[name] = widget

    def width(self):
        return self._w


_gp = make_plugin(status_position_x=5, status_position_y=6, count_position_x=50, count_position_y=60)
_gu = _PosUI()
_gp.on_ui_setup(_gu)
check("gps-tagger honors configured status position", _gu.elements["pn_status"].xy[:2] == (5, 6))
check("gps-tagger honors configured count position", _gu.elements["pn_count"].xy[:2] == (50, 60))

_gpd = make_plugin()
_gud = _PosUI()
_gpd.on_ui_setup(_gud)
check("gps-tagger default status position is (1, 76)", _gud.elements["pn_status"].xy[:2] == (1, 76))
check("gps-tagger default count position is (122, 94)", _gud.elements["pn_count"].xy[:2] == (122, 94))

_gpn = make_plugin(status_position_x=-20, status_position_y=10)
_gun = _PosUI(w=250)
_gpn.on_ui_setup(_gun)
check("gps-tagger negative x resolves from the right edge", _gun.elements["pn_status"].xy[:2] == (230, 10))

shutil.rmtree(tmpdir, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
