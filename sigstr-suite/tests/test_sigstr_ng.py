import sys
import os
import tempfile
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import sigstr_ng as mod  # noqa: E402

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
    plugin = mod.SigStrNG()
    plugin.options = dict(opts)
    return plugin


class FakeUI:
    def __init__(self, width=250):
        self.elements = {}
        self.values = {}
        self._width = width
        self._lock = mock.MagicMock()
        self._lock.__enter__ = mock.Mock(return_value=None)
        self._lock.__exit__ = mock.Mock(return_value=False)

    def width(self):
        return self._width

    def add_element(self, name, el):
        self.elements[name] = el

    def remove_element(self, name):
        if name not in self.elements:
            raise KeyError(name)
        del self.elements[name]

    def set(self, name, value):
        self.values[name] = value


# --- Registration ------------------------------------------------------------

check(
    "SigStrNG registers with the real pwnagotchi.plugins loader",
    "sigstr_ng" in pwnagotchi.plugins.loaded,
)

for real_hook in ("on_loaded", "on_unload", "on_ui_setup", "on_ui_update", "on_webhook"):
    check(f"real hook {real_hook} is present", hasattr(mod.SigStrNG, real_hook))

# --- _opt() falls back to real defaults instead of KeyError -----------------

plugin = make_plugin()  # empty options, like a bare `enabled = true` block
check(
    "_opt() returns the real default for a key entirely absent from options",
    plugin._opt("interface") == mod.DEFAULTS["interface"],
)
check(
    "_opt_int() returns the real default for a key entirely absent from options",
    plugin._opt_int("bar_length") == mod.DEFAULTS["bar_length"],
)

# --- Bug #1 fix: on_unload(self, ui) accepts the required ui parameter ------
# The original defined on_unload(self) - calling it the real way (with a ui
# argument, exactly as the framework does) would have raised TypeError before
# the original's self.timer.cancel() body even ran.

plugin = make_plugin()
plugin.on_loaded()
ui = FakeUI()
plugin.on_ui_setup(ui)
try:
    plugin.on_unload(ui)
    unload_ok = True
except TypeError:
    unload_ok = False
check("on_unload(self, ui) accepts a ui argument without a TypeError", unload_ok)
check("on_unload actually removes the UI element", mod.ELEMENT_NAME not in ui.elements)

plugin2 = make_plugin()
plugin2.on_loaded()
ui2 = FakeUI()  # on_ui_setup never called - element never added
try:
    plugin2.on_unload(ui2)
    unload_no_element_ok = True
except Exception:
    unload_no_element_ok = False
check("on_unload doesn't crash when the UI element was never added", unload_no_element_ok)

# --- Bug #2 fix: no call to the nonexistent pwnagotchi.plugins.notify -------

source = open(os.path.join(HERE, "..", "sigstr_ng.py")).read()
# Strip the module docstring first - it discusses the ORIGINAL's buggy
# plugins.notify(...)/threading.Timer calls in prose, as history, which
# would otherwise make a naive substring search over the whole file a
# false negative here.
_docstring_end = source.index('"""', source.index('"""') + 3) + 3
code_only = source[_docstring_end:]
check("code never calls plugins.notify(...) (that function doesn't exist)", "plugins.notify(" not in code_only)
check("pwnagotchi.plugins module has no notify attribute at all (confirms the bug)", not hasattr(pwnagotchi.plugins, "notify"))

# --- Bug #3 fix: no separate background timer/thread is created ------------

check("code doesn't import threading at all (no separate timer thread)", "import threading" not in code_only)
check("code never instantiates threading.Timer(...)", "threading.Timer(" not in code_only)

plugin = make_plugin()
plugin.on_loaded()
ui = FakeUI()
plugin.on_ui_setup(ui)
with mock.patch.object(mod, "read_signal_strength", return_value={"dbm": -50, "percent": 100.0}), \
     mock.patch.object(mod, "list_wireless_interfaces", return_value=["wlan0"]):
    plugin.on_ui_update(ui)
plugin.on_unload(ui)
check("SigStrNG instance has no .timer attribute (unlike the original)", not hasattr(plugin, "timer"))

# --- Bug #5 fix: bar characters are the right way round ---------------------

check("generate_signal_bar(100) is fully filled with the block character", mod.generate_signal_bar(100, 10) == "|" + ("█" * 10) + "|")
check("generate_signal_bar(0) is fully empty with the light-shade character", mod.generate_signal_bar(0, 10) == "|" + ("░" * 10) + "|")
check("generate_signal_bar(50) is half filled, half empty", mod.generate_signal_bar(50, 10) == "|" + ("█" * 5) + ("░" * 5) + "|")
check("generate_signal_bar handles a non-default bar_length", len(mod.generate_signal_bar(100, 20)) == 22)  # 20 chars + 2 pipes
check("generate_signal_bar(None) doesn't crash and renders fully empty", mod.generate_signal_bar(None, 10) == "|" + ("░" * 10) + "|")

# --- Bug #4 fix: interface auto-fallback ------------------------------------

plugin = make_plugin(interface="wlan9")
with mock.patch.object(mod, "list_wireless_interfaces", return_value=["wlan0", "wlan1"]):
    configured, active, detected = plugin._resolve_interface()
check("configured interface not present triggers fallback to the first detected one", active == "wlan0")
check("_resolve_interface reports the originally-configured name separately", configured == "wlan9")
check("_resolve_interface reports detected=True when 'iw dev' enumeration succeeded", detected is True)

plugin = make_plugin(interface="wlan0")
with mock.patch.object(mod, "list_wireless_interfaces", return_value=["wlan0", "wlan1"]):
    configured, active, detected = plugin._resolve_interface()
check("configured interface present is used as-is, no fallback", active == "wlan0" and configured == "wlan0")

plugin = make_plugin(interface="wlan0")
with mock.patch.object(mod, "list_wireless_interfaces", return_value=[]):
    configured, active, detected = plugin._resolve_interface()
check("empty 'iw dev' listing (iw missing/failed) falls back to trusting the configured value", active == "wlan0")
check("empty 'iw dev' listing reports detected=False", detected is False)

# --- read_signal_strength / list_wireless_interfaces are guarded -----------

with mock.patch("subprocess.check_output", side_effect=FileNotFoundError()):
    result = mod.read_signal_strength("wlan0")
check("read_signal_strength returns None (not a crash) when 'iw' is missing", result is None)

with mock.patch("subprocess.check_output", return_value=b"Connected to aa:bb:cc:dd:ee:ff (on wlan0)\n\tSSID: Test\n\tsignal: -55 dBm\n\ttx bitrate: 100.0 MBit/s\n"):
    result = mod.read_signal_strength("wlan0")
check("read_signal_strength parses a real 'iw dev ... link' signal line", result is not None and result["dbm"] == -55)

with mock.patch("subprocess.check_output", return_value=b"Not connected.\n"):
    result = mod.read_signal_strength("wlan0")
check("read_signal_strength returns None when not associated (no 'signal:' line)", result is None)

with mock.patch("subprocess.check_output", side_effect=FileNotFoundError()):
    result = mod.list_wireless_interfaces()
check("list_wireless_interfaces returns [] (not a crash) when 'iw' is missing", result == [])

with mock.patch("subprocess.check_output", return_value=b"phy#0\n\tInterface wlan0\n\t\tifindex 3\n\tInterface wlan1\n\t\tifindex 4\n"):
    result = mod.list_wireless_interfaces()
check("list_wireless_interfaces parses real 'iw dev' output", result == ["wlan0", "wlan1"])

# --- Sparkline: bounded history buffer never grows unbounded ---------------

plugin = make_plugin(history_length=5)
plugin.on_loaded()
with mock.patch.object(mod, "list_wireless_interfaces", return_value=["wlan0"]):
    for i in range(20):
        with mock.patch.object(mod, "read_signal_strength", return_value={"dbm": -50, "percent": float(i)}):
            plugin._measure(float(i))
check("history buffer is capped at history_length even after many more measurements", len(plugin.history) == 5)
check("history buffer keeps only the most recent points after the cap", [p["percent"] for p in plugin.history] == [15.0, 16.0, 17.0, 18.0, 19.0])

check("render_sparkline([]) is empty", mod.render_sparkline([]) == "")
check("render_sparkline([0]) uses the lowest block character", mod.render_sparkline([0]) == mod.SPARK_CHARS[0])
check("render_sparkline([100]) uses the highest block character", mod.render_sparkline([100]) == mod.SPARK_CHARS[-1])
check("render_sparkline renders one character per value", len(mod.render_sparkline([10, 20, 30])) == 3)

# --- Threshold classification at boundary values ----------------------------

check("dBm exactly at strong_threshold_dbm classifies as strong", mod.classify_signal(-60, -60, -80) == "strong")
check("dBm one better than strong_threshold_dbm classifies as strong", mod.classify_signal(-59, -60, -80) == "strong")
check("dBm exactly at weak_threshold_dbm classifies as weak", mod.classify_signal(-80, -60, -80) == "weak")
check("dBm one worse than weak_threshold_dbm classifies as weak", mod.classify_signal(-81, -60, -80) == "weak")
check("dBm strictly between the two thresholds classifies as medium", mod.classify_signal(-70, -60, -80) == "medium")
check("dBm one worse than strong_threshold_dbm (just inside medium) classifies as medium", mod.classify_signal(-61, -60, -80) == "medium")
check("dBm one better than weak_threshold_dbm (just inside medium) classifies as medium", mod.classify_signal(-79, -60, -80) == "medium")
check("dbm=None classifies as unknown", mod.classify_signal(None, -60, -80) == "unknown")
check("classify_signal maps to the right short tag", mod.TAG_FOR_CLASS[mod.classify_signal(-50, -60, -80)] == "STR")
check("classify_signal maps a weak reading to the WEAK tag", mod.TAG_FOR_CLASS[mod.classify_signal(-90, -60, -80)] == "WEAK")

# --- Positioning math, including a negative-x case (copied from MadHatterNG) -

plugin = make_plugin(ui_position_x=-80, ui_position_y=0)
plugin.on_loaded()
ui = FakeUI(width=250)
plugin.on_ui_setup(ui)
check(
    "negative ui_position_x is resolved as 'this many px in from the right edge'",
    ui.elements[mod.ELEMENT_NAME].xy[:2] == (170, 0),  # 250 + (-80) = 170
)

plugin = make_plugin(ui_position_x=10, ui_position_y=20)
plugin.on_loaded()
ui = FakeUI(width=250)
plugin.on_ui_setup(ui)
check(
    "non-negative ui_position_x is used as an absolute coordinate",
    ui.elements[mod.ELEMENT_NAME].xy[:2] == (10, 20),
)

plugin = make_plugin(ui_position_x=-9999, ui_position_y=0)
plugin.on_loaded()
ui = FakeUI(width=128)
plugin.on_ui_setup(ui)
x, _ = ui.elements[mod.ELEMENT_NAME].xy[:2]
check("an extreme negative ui_position_x is clamped to stay on-screen", 0 <= x <= 128)

plugin = make_plugin()  # default (0, 205), matching the original's hardcoded position
plugin.on_loaded()
ui = FakeUI(width=250)
plugin.on_ui_setup(ui)
check(
    "default position matches the original's hardcoded (0, 205)",
    ui.elements[mod.ELEMENT_NAME].xy[:2] == (0, 205),
)

# --- Webhook page rendering + HTML escaping ---------------------------------

plugin = make_plugin()
plugin.on_loaded()
page_no_reading = plugin.on_webhook("", mock.Mock())
check("webhook status page renders without crashing before any reading exists", "SigStrNG" in page_no_reading)
check("webhook status page shows n/a before any reading exists", "n/a" in page_no_reading)

with mock.patch.object(mod, "list_wireless_interfaces", return_value=["wlan0"]), \
     mock.patch.object(mod, "read_signal_strength", return_value={"dbm": -55, "percent": 90.0}):
    plugin._measure(time_now := __import__("time").time())
page_with_reading = plugin.on_webhook("/", mock.Mock())
check("webhook status page shows the current dBm reading", "-55 dBm" in page_with_reading)

not_found = plugin.on_webhook("/somewhere-else", mock.Mock())
check("webhook returns a 404-style response for an unrecognized path", not_found[1] == 404)

# HTML-escaping: force a "detected" interface name and network name with a
# script tag through the render path and confirm it comes out escaped.
plugin = make_plugin(correlate_handshakes=True)
plugin.on_loaded()
with mock.patch.object(mod, "list_wireless_interfaces", return_value=["<script>evil</script>"]), \
     mock.patch.object(mod, "read_signal_strength", return_value={"dbm": -50, "percent": 100.0}):
    plugin.options["interface"] = "<script>evil</script>"
    plugin._measure(__import__("time").time())
csv_path = p("escape_timer_ng.csv")
with open(csv_path, "w", newline="") as f:
    f.write("timestamp,network,time_to_deauth,time_to_handshake,time_between_deauth_and_handshake\n")
    f.write(f"{datetime.now().isoformat(timespec='seconds')},<script>evil</script>,1.0,2.0,3.0\n")
plugin.options["timer_csv_path"] = csv_path
page = plugin.on_webhook("", mock.Mock())
check("webhook page HTML-escapes the interface name (no raw <script> tag)", "<script>evil</script>" not in page)
check("webhook page escapes the interface name correctly", "&lt;script&gt;evil&lt;/script&gt;" in page)

# --- Handshake correlation: with a stub CSV present -------------------------

plugin = make_plugin(correlate_handshakes=True, correlation_window_minutes=5)
plugin.on_loaded()
now_ts = __import__("time").time()
plugin.history.append({"t": now_ts, "dbm": -62, "percent": 76.0})

near_dt = datetime.now() - timedelta(seconds=30)
far_dt = datetime.now() - timedelta(hours=3)
corr_csv = p("corr_timer_ng.csv")
with open(corr_csv, "w", newline="") as f:
    f.write("timestamp,network,time_to_deauth,time_to_handshake,time_between_deauth_and_handshake\n")
    f.write(f"{near_dt.isoformat(timespec='seconds')},NearNet,1.0,2.0,3.0\n")
    f.write(f"{far_dt.isoformat(timespec='seconds')},FarNet,1.0,2.0,3.0\n")
plugin.options["timer_csv_path"] = corr_csv

with mock.patch("time.time", return_value=now_ts):
    points = plugin._correlate_handshakes()
by_network = {pt["network"]: pt for pt in points}
check("a handshake close in time to a reading is correlated with that reading's RSSI", by_network.get("NearNet", {}).get("rssi_dbm") == -62)
check("a handshake far outside the window gets no RSSI pairing (reported, not dropped)", by_network.get("FarNet", {}).get("rssi_dbm") is None)
check("both handshake events still appear in the correlation output even when unmatched", "FarNet" in by_network)

# --- Handshake correlation: gracefully absent -------------------------------

plugin = make_plugin(correlate_handshakes=True, timer_csv_path=p("does_not_exist.csv"))
plugin.on_loaded()
plugin.history.append({"t": __import__("time").time(), "dbm": -60, "percent": 80.0})
try:
    points = plugin._correlate_handshakes()
    correlate_missing_ok = True
except Exception:
    correlate_missing_ok = False
check("_correlate_handshakes never crashes when timer_csv_path doesn't exist", correlate_missing_ok)
check("_correlate_handshakes returns an empty list when the source file is missing", points == [])

plugin_off = make_plugin(correlate_handshakes=False, timer_csv_path=corr_csv)
plugin_off.on_loaded()
check("_correlate_handshakes returns [] when the feature is disabled, even with a real CSV present", plugin_off._correlate_handshakes() == [])

page_off = plugin_off.on_webhook("", mock.Mock())
check("webhook page says correlation is disabled when correlate_handshakes is false", "disabled" in page_off)


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    print(failures)
    sys.exit(1)
else:
    print("All tests passed.")
