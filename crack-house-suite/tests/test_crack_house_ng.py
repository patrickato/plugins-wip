import sys
import os
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import crack_house_ng as mod  # noqa: E402

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
    plugin = mod.CrackHouseNG()
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


check("CrackHouseNG registers with the real pwnagotchi.plugins loader", "crack_house_ng" in pwnagotchi.plugins.loaded)

# --- on_loaded doesn't crash when a configured file is simply missing ---
plugin = make_plugin(files=[p("does_not_exist.potfile")], saving_path=p("saved1.potfile"))
plugin.on_loaded()
check("on_loaded doesn't crash on a missing configured file", plugin._crack_menu == [])
check("on_loaded still writes an (empty) saving_path file", os.path.exists(p("saved1.potfile")))

# --- on_loaded correctly parses a .potfile ---
with open(p("real.potfile"), "w") as f:
    f.write("aabbccddeeff:112233445566:MyLab:hunter2\n")
plugin = make_plugin(files=[p("real.potfile")], saving_path=p("saved2.potfile"))
plugin.on_loaded()
check("on_loaded parses a .potfile entry as hostname:password", "MyLab:hunter2" in plugin._crack_menu)

# --- on_loaded correctly parses a .cracked file ---
with open(p("real.cracked"), "w") as f:
    f.write("2026-01-01,OtherLab,AA:BB:CC:DD:EE:FF,11:22:33:44:55:66,swordfish,note\n")
plugin = make_plugin(files=[p("real.cracked")], saving_path=p("saved3.potfile"))
plugin.on_loaded()
check("on_loaded parses a .cracked entry as hostname:password", "OtherLab:swordfish" in plugin._crack_menu)

# --- on_ui_setup never calls any is_waveshare_*-style display detection (the fatal original bug) ---
plugin = make_plugin(files=[], saving_path=p("saved4.potfile"))
plugin.on_loaded()
ui = FakeUI()
# A bare object with no is_* methods at all - if the plugin called any of
# them like the original did, this would AttributeError immediately.
plugin.on_ui_setup(ui)
check("on_ui_setup runs against a plain UI object with no is_waveshare_* methods", "crack_house_ng" in ui.elements)
check("on_ui_setup creates the stats element by default", "crack_house_ng_stats" in ui.elements)

# --- display_stats=false skips the stats element ---
plugin = make_plugin(files=[], saving_path=p("saved5.potfile"), display_stats=False)
plugin.on_loaded()
ui = FakeUI()
plugin.on_ui_setup(ui)
check("display_stats=false skips the stats element", "crack_house_ng_stats" not in ui.elements)

# --- configured position_x/position_y are honored ---
plugin = make_plugin(files=[], saving_path=p("saved6.potfile"), position_x=5, position_y=6)
plugin.on_loaded()
ui = FakeUI()
plugin.on_ui_setup(ui)
check("configured position_x/position_y are used", ui.elements["crack_house_ng"].xy[:2] == (5, 6))

# --- on_unload removes elements cleanly even if one was never created ---
plugin = make_plugin(files=[], saving_path=p("saved7.potfile"), display_stats=False)
plugin.on_loaded()
ui = FakeUI()
plugin.on_ui_setup(ui)
try:
    plugin.on_unload(ui)
    unload_ok = True
except Exception:
    unload_ok = False
check("on_unload doesn't crash when the stats element was never created", unload_ok)

# --- on_wifi_update finds the nearest cracked network by RSSI ---
plugin = make_plugin(files=[], saving_path=p("saved8.potfile"), iface="wlan0mon")
plugin.on_loaded()
plugin._crack_menu = ["FarLab:pass1", "NearLab:pass2"]
with mock.patch("crack_house_ng.os.popen") as popen:
    popen.return_value.read.return_value = "wlan0mon  IEEE 802.11  ESSID:off/any  Not-Associated"
    aps = [
        {"hostname": "FarLab", "rssi": -80},
        {"hostname": "NearLab", "rssi": -40},
        {"hostname": "SomeoneElse", "rssi": -10},
    ]
    plugin.on_wifi_update(mock.Mock(), aps)
check("on_wifi_update picks the strongest-RSSI cracked match", plugin._best_crack == ("NearLab", "pass2"))
check("on_wifi_update uses the configured iface in the iwconfig call", "wlan0mon" in popen.call_args[0][0])
check("on_wifi_update counts nearby cracked networks correctly", plugin._total_crack_nearby == 2)

# --- on_wifi_update skips re-scanning while already associated ---
plugin = make_plugin(files=[], saving_path=p("saved9.potfile"))
plugin.on_loaded()
plugin._crack_menu = ["NearLab:pass2"]
with mock.patch("crack_house_ng.os.popen") as popen:
    popen.return_value.read.return_value = "wlan0  IEEE 802.11  ESSID:\"Home\"  Mode:Managed"
    plugin.on_wifi_update(mock.Mock(), [{"hostname": "NearLab", "rssi": -40}])
check("on_wifi_update doesn't overwrite state while already associated", plugin._best_crack is None)

# --- on_ui_update falls back to the last known crack when nothing is nearby ---
plugin = make_plugin(files=[], saving_path=p("saved10.potfile"), display_stats=False)
plugin.on_loaded()
plugin._crack_menu = ["OldLab:oldpass"]
ui = FakeUI()
plugin.on_ui_update(ui)
check("on_ui_update falls back to the last known crack, not a hardcoded upstream file", ui.values["crack_house_ng"] == "OldLab\noldpass")

# --- webhook doesn't crash with nothing loaded ---
plugin = make_plugin(files=[], saving_path=p("saved11.potfile"))
plugin.on_loaded()
try:
    html = plugin.on_webhook("", None)
    webhook_ok = "CrackHouseNG" in html
except Exception:
    webhook_ok = False
check("webhook renders without crashing on an empty plugin", webhook_ok)


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
