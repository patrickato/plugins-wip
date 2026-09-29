import datetime
import json
import os
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import wifi_adventures_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(data_path=None, **opts):
    p = mod.WifiAdventuresNG()
    options = dict(opts)
    if data_path is not None:
        options.setdefault("data_path", data_path)
    p.options = options
    return p


class FakeUI:
    def __init__(self):
        self.elements = {}

    def add_element(self, name, widget):
        self.elements[name] = widget

    def set(self, name, value):
        self.elements[name].value = value

    def remove_element(self, name):
        del self.elements[name]

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    @property
    def _lock(self):
        return FakeUI._Lock()


# --- Registration -----------------------------------------------------------

check(
    "WifiAdventuresNG registers as a real pwnagotchi.plugins.Plugin subclass",
    issubclass(mod.WifiAdventuresNG, pwnagotchi.plugins.Plugin),
)

# --- __init__ does no I/O and never raises -----------------------------------

try:
    p_init = mod.WifiAdventuresNG()
    init_ok = True
except Exception:
    init_ok = False
check("__init__ does no file/network I/O and never raises", init_ok)
check("__init__ leaves ready False until on_ready runs", p_init.ready is False)

# --- data_path resolution: default vs configured override -------------------

p_default = make_plugin()
default_path = p_default._resolve_data_path()
check(
    "default data_path sits next to the plugin file",
    default_path == os.path.join(os.path.dirname(os.path.realpath(mod.__file__)), "wifi_adventures_ng.json"),
)

p_override = make_plugin(data_path="/tmp/custom_wifi_adv.json")
check(
    "a configured data_path overrides the default",
    p_override._resolve_data_path() == "/tmp/custom_wifi_adv.json",
)

# --- on_ready: missing state file doesn't crash, leaves defaults ------------

with tempfile.TemporaryDirectory() as tmp:
    missing_path = os.path.join(tmp, "nope.json")
    p1 = make_plugin(data_path=missing_path)
    p1.on_ready(mock.Mock())

check("on_ready with no existing state file doesn't crash", p1.ready is True)
check("on_ready with no existing state file leaves handshake_count at 0", p1.handshake_count == 0)
check("on_ready with no existing state file sets the base title", p1.title == mod.TITLES[0])

# --- on_ready: corrupt state file doesn't crash ------------------------------

with tempfile.TemporaryDirectory() as tmp:
    bad_path = os.path.join(tmp, "bad.json")
    with open(bad_path, "w") as f:
        f.write("{not valid json")
    p2 = make_plugin(data_path=bad_path)
    try:
        p2.on_ready(mock.Mock())
        corrupt_ok = True
    except Exception:
        corrupt_ok = False

check("on_ready with a corrupt state file doesn't crash", corrupt_ok)
check("on_ready still becomes ready after a corrupt state file", p2.ready is True)

# --- state round-trips through save/load -------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "state.json")
    p3 = make_plugin(data_path=path)
    p3.on_ready(mock.Mock())
    p3.handshake_count = 7
    p3.new_networks_count = 3
    p3.seen_bssids = ["AA:BB:CC:DD:EE:FF"]
    p3.streak_days = 2
    p3.last_handshake_date = datetime.date(2026, 1, 1)
    p3._save()

    check("state file is written on save", os.path.exists(path))
    with open(path) as f:
        on_disk = json.load(f)
    check("saved JSON round-trips handshake_count", on_disk["handshake_count"] == 7)
    check("saved JSON round-trips seen_bssids", on_disk["seen_bssids"] == ["AA:BB:CC:DD:EE:FF"])

    p3b = make_plugin(data_path=path)
    p3b.on_ready(mock.Mock())
    check("reloading from disk restores handshake_count", p3b.handshake_count == 7)
    check("reloading from disk restores new_networks_count", p3b.new_networks_count == 3)
    check("reloading from disk restores seen_bssids", p3b.seen_bssids == ["AA:BB:CC:DD:EE:FF"])
    check("reloading from disk restores last_handshake_date", p3b.last_handshake_date == datetime.date(2026, 1, 1))

# --- on_handshake: real signature, counts, and title ladder ------------------

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "hs.json")
    p4 = make_plugin(data_path=path)
    p4.on_ready(mock.Mock())
    p4.on_handshake(mock.Mock(), "some.pcap", {"mac": "AA:BB:CC:DD:EE:FF"}, {})

check("on_handshake accepts the real 4-arg signature without raising", True)
check("on_handshake increments handshake_count", p4.handshake_count == 1)
check("on_handshake sets today as last_handshake_date", p4.last_handshake_date == datetime.date.today())
check("on_handshake starts the streak at 1", p4.streak_days == 1)

p5 = make_plugin()
p5._data_path = None
p5.handshake_count = 0
for threshold in [0, 5, 15, 30, 50, 100, 200, 400, 800, 1500]:
    p5.handshake_count = threshold
    p5._update_title()
    check(f"title at {threshold} handshakes is {mod.TITLES[threshold]}", p5.title == mod.TITLES[threshold])

p5.handshake_count = 4999
p5._update_title()
check("title caps at the highest ladder tier for a very high count", p5.title == mod.TITLES[1500])

# --- streak logic: consecutive day, same day, and gap ------------------------

p6 = make_plugin()
today = datetime.date.today()
p6.last_handshake_date = today - datetime.timedelta(days=1)
p6.streak_days = 4
p6._bump_streak()
check("a handshake on the consecutive day extends the streak", p6.streak_days == 5)

p7 = make_plugin()
p7.last_handshake_date = today
p7.streak_days = 5
p7._bump_streak()
check("a second handshake on the same day doesn't double-count the streak", p7.streak_days == 5)

p8 = make_plugin()
p8.last_handshake_date = today - datetime.timedelta(days=5)
p8.streak_days = 9
p8._bump_streak()
check("a handshake after a gap resets the streak to 1", p8.streak_days == 1)

# --- on_unfiltered_ap_list: real signature, only counts genuinely new APs ---

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "aps.json")
    p9 = make_plugin(data_path=path)
    p9.on_ready(mock.Mock())

    aps_round1 = [
        {"mac": "11:11:11:11:11:11", "hostname": "net1"},
        {"mac": "22:22:22:22:22:22", "hostname": "net2"},
    ]
    p9.on_unfiltered_ap_list(mock.Mock(), aps_round1)
    check("on_unfiltered_ap_list accepts the real 3-arg signature without raising", True)
    check("first sighting of two APs counts both as new", p9.new_networks_count == 2)

    aps_round2 = [
        {"mac": "11:11:11:11:11:11", "hostname": "net1"},  # already seen
        {"mac": "33:33:33:33:33:33", "hostname": "net3"},  # genuinely new
    ]
    p9.on_unfiltered_ap_list(mock.Mock(), aps_round2)
    check(
        "seeing an already-known BSSID again doesn't double-count it",
        p9.new_networks_count == 3,
    )

p10 = make_plugin()
p10.on_ready(mock.Mock())
try:
    p10.on_unfiltered_ap_list(mock.Mock(), [])
    empty_ok = True
except Exception:
    empty_ok = False
check("on_unfiltered_ap_list with an empty list doesn't crash", empty_ok)
check("on_unfiltered_ap_list with an empty list counts nothing new", p10.new_networks_count == 0)

p11 = make_plugin()
p11.on_ready(mock.Mock())
try:
    p11.on_unfiltered_ap_list(mock.Mock(), None)
    none_ok = True
except Exception:
    none_ok = False
check("on_unfiltered_ap_list tolerates access_points=None without crashing", none_ok)

# --- UI setup/update ----------------------------------------------------------

p12 = make_plugin()
p12.on_ready(mock.Mock())
p12.handshake_count = 12
p12._update_title()
ui = FakeUI()
p12.on_ui_setup(ui)
check("on_ui_setup registers the wifiAdventures element", "wifiAdventures" in ui.elements)

p12.handshake_count = 20
p12._update_title()
p12.on_ui_update(ui)
check("on_ui_update reflects the latest handshake_count", "20" in ui.elements["wifiAdventures"].value)

p13 = make_plugin()
# ready is False before on_ready runs
ui2 = FakeUI()
p13.on_ui_setup(ui2)
p13.handshake_count = 999
p13.on_ui_update(ui2)
check("on_ui_update is a no-op before ready is True", "999" not in ui2.elements["wifiAdventures"].value)

# --- on_webhook ----------------------------------------------------------------

p14 = make_plugin()
p14.on_ready(mock.Mock())
p14.handshake_count = 3
p14.new_networks_count = 2
p14.streak_days = 1
p14._update_title()
try:
    page = p14.on_webhook("/", mock.Mock())
    webhook_ok = True
except Exception:
    webhook_ok = False
check("on_webhook renders without crashing", webhook_ok)
check("on_webhook's page includes the handshake count", "3" in page)
check("on_webhook's page includes the current title", p14.title in page)

# --- on_unload -------------------------------------------------------------

p15 = make_plugin()
p15.on_ready(mock.Mock())
ui3 = FakeUI()
p15.on_ui_setup(ui3)
try:
    p15.on_unload(ui3)
    unload_ok = True
except Exception:
    unload_ok = False
check("on_unload doesn't crash", unload_ok)
check("on_unload removes the wifiAdventures UI element", "wifiAdventures" not in ui3.elements)


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    sys.exit(1)
