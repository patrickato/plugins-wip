import sys
import os
import csv
import tempfile
from unittest import mock
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))  # prctl + tomlkit (native/unavailable here)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")  # real pwnagotchi.plugins framework

import pwnagotchi.plugins  # noqa: E402
import timer_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


tmpdir = tempfile.mkdtemp()


def csv_path(name="times.csv"):
    return os.path.join(tmpdir, name)


def make_plugin(**opts):
    p = mod.TimerNG()
    p.options = dict(opts)
    return p


AP = {"mac": "AA:BB:CC:DD:EE:FF", "hostname": "MyLab", "channel": 6, "rssi": -40, "vendor": ""}
STA = {"mac": "11:22:33:44:55:66", "vendor": ""}


class FrozenClock:
    """Lets tests control datetime.datetime.now() deterministically."""

    def __init__(self, start):
        self.t = start

    def now(self, *a, **kw):
        return self.t

    def advance(self, seconds):
        self.t = self.t + timedelta(seconds=seconds)


# --- Test 1: real plugin registration ---
check("TimerNG registers with the real pwnagotchi.plugins loader", "timer_ng" in pwnagotchi.plugins.loaded)

# --- Test 2: _as_ap_dict / _network_name handle both dict and bare-string AP shapes ---
check("_network_name uses hostname when present", mod._network_name(AP) == "MyLab")
check("_network_name falls back to mac for a bare-string AP (on_handshake's other shape)", mod._network_name("AA:BB:CC:DD:EE:FF") == "AA:BB:CC:DD:EE:FF")
check("_network_name falls back to mac when hostname is hidden", mod._network_name({"mac": "X", "hostname": "<hidden>"}) == "X")

# --- Test 3: a full deauth -> handshake sequence records a row with the network name ---
path = csv_path("basic.json")
p = make_plugin(output_path=path)
clock = FrozenClock(datetime(2026, 1, 1, 12, 0, 0))
with mock.patch("timer_ng.datetime") as dt:
    dt.datetime.now.side_effect = lambda: clock.t
    agent = mock.Mock()
    p.on_wifi_update(agent, [AP])
    clock.advance(2.0)
    p.on_deauthentication(agent, AP, STA)
    clock.advance(1.5)
    p.on_handshake(agent, "/root/handshakes/MyLab.pcapng", AP, STA)

check("output CSV file was created", os.path.exists(path))
with open(path, newline="") as f:
    rows = list(csv.DictReader(f))
check("exactly one row was written", len(rows) == 1)
check("the row records the right network name", rows[0]["network"] == "MyLab")
check("time_to_deauth is ~2.0s", abs(float(rows[0]["time_to_deauth"]) - 2.0) < 0.01)
check("time_to_handshake is ~3.5s", abs(float(rows[0]["time_to_handshake"]) - 3.5) < 0.01)
check("time_between_deauth_and_handshake is ~1.5s", abs(float(rows[0]["time_between_deauth_and_handshake"]) - 1.5) < 0.01)

# --- Test 4: a passive handshake (no prior deauth) is not recorded ---
path = csv_path("passive.json")
p = make_plugin(output_path=path)
agent = mock.Mock()
p.on_wifi_update(agent, [AP])
p.on_handshake(agent, "/x.pcapng", AP, STA)  # no on_deauthentication call first
check("a passive capture with no prior deauth writes nothing", not os.path.exists(path))

# --- Test 5: bare-MAC-string AP shape on on_handshake doesn't crash and is recorded ---
path = csv_path("bare_mac.json")
p = make_plugin(output_path=path)
agent = mock.Mock()
p.on_wifi_update(agent, [AP])
p.on_deauthentication(agent, AP, STA)
p.on_handshake(agent, "/x.pcapng", "AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66")
with open(path, newline="") as f:
    rows = list(csv.DictReader(f))
check("a bare-MAC-string on_handshake still records a row without crashing", len(rows) == 1)

# --- Test 6: per-network best/worst stats are tracked across multiple captures ---
path = csv_path("stats.json")
p = make_plugin(output_path=path)
agent = mock.Mock()
times = [1.0, 3.0, 2.0]
for t in times:
    clock = FrozenClock(datetime(2026, 1, 1, 12, 0, 0))
    with mock.patch("timer_ng.datetime") as dt:
        dt.datetime.now.side_effect = lambda: clock.t
        p.on_wifi_update(agent, [AP])
        clock.advance(0.1)
        p.on_deauthentication(agent, AP, STA)
        clock.advance(t)
        p.on_handshake(agent, "/x.pcapng", AP, STA)

stats = p._network_stats["MyLab"]
check("stats track the right capture count", stats["count"] == 3)
check("stats track the best (fastest) time_to_handshake", abs(stats["best"] - 1.1) < 0.05)
check("stats track the worst (slowest) time_to_handshake", abs(stats["worst"] - 3.1) < 0.05)

# --- Test 7: CSV rotation keeps only the most recent max_rows entries ---
path = csv_path("rotate.json")
p = make_plugin(output_path=path, max_rows=2)
agent = mock.Mock()
for i in range(4):
    p.on_wifi_update(agent, [AP])
    p.on_deauthentication(agent, AP, STA)
    p.on_handshake(agent, "/x.pcapng", AP, STA)
with open(path, newline="") as f:
    rows = list(csv.DictReader(f))
check("rotation keeps at most max_rows rows", len(rows) == 2)

# --- Test 8: max_rows=0 means unlimited (matches original's unbounded behavior) ---
path = csv_path("unlimited.json")
p = make_plugin(output_path=path, max_rows=0)
agent = mock.Mock()
for i in range(5):
    p.on_wifi_update(agent, [AP])
    p.on_deauthentication(agent, AP, STA)
    p.on_handshake(agent, "/x.pcapng", AP, STA)
with open(path, newline="") as f:
    rows = list(csv.DictReader(f))
check("max_rows=0 keeps every row, unbounded", len(rows) == 5)

# --- Test 9: show_on_screen=false (default) never touches the UI ---
p = make_plugin(output_path=csv_path("noui.json"), show_on_screen=False)
agent = mock.Mock()
p.on_wifi_update(agent, [AP])
p.on_deauthentication(agent, AP, STA)
p.on_handshake(agent, "/x.pcapng", AP, STA)
check("show_on_screen=false never calls agent.view()", agent.view.call_count == 0)

# --- Test 10: show_on_screen=true updates the UI element with the last time ---
p = make_plugin(output_path=csv_path("ui_last.json"), show_on_screen=True, ui_metric="last")
agent = mock.Mock()
ui_element_values = {}
agent.view.return_value.set.side_effect = lambda name, val: ui_element_values.__setitem__(name, val)
p.on_wifi_update(agent, [AP])
p.on_deauthentication(agent, AP, STA)
p.on_handshake(agent, "/x.pcapng", AP, STA)
check("show_on_screen=true, ui_metric=last updates the element", "timer_ng" in ui_element_values)

# --- Test 11: ui_metric=average reflects a rolling average, not just the last value ---
p = make_plugin(output_path=csv_path("ui_avg.json"), show_on_screen=True, ui_metric="average", ui_average_window=10)
agent = mock.Mock()
ui_element_values = {}
agent.view.return_value.set.side_effect = lambda name, val: ui_element_values.__setitem__(name, val)
for t in [1.0, 3.0]:
    clock = FrozenClock(datetime(2026, 1, 1, 12, 0, 0))
    with mock.patch("timer_ng.datetime") as dt:
        dt.datetime.now.side_effect = lambda: clock.t
        p.on_wifi_update(agent, [AP])
        clock.advance(0.0)
        p.on_deauthentication(agent, AP, STA)
        clock.advance(t)
        p.on_handshake(agent, "/x.pcapng", AP, STA)
# average of ~1.0 and ~3.0 is ~2.0s
check("ui_metric=average shows a rolling average, not the last raw value", "2.0" in ui_element_values["timer_ng"])

# --- Test 12: webhook renders a per-network summary table ---
p = make_plugin(output_path=csv_path("webhook.json"))
agent = mock.Mock()
p.on_wifi_update(agent, [AP])
p.on_deauthentication(agent, AP, STA)
p.on_handshake(agent, "/x.pcapng", AP, STA)
html = p.on_webhook("", None)
check("webhook page mentions the network that was captured", "MyLab" in html)
empty_plugin = make_plugin(output_path=csv_path("webhook_empty.json"))
check("webhook page with no data yet doesn't crash", "No timed captures yet" in empty_plugin.on_webhook("", None))

# --- Test 13: on_epoch resets in-flight timing state (matches original's per-epoch reset) ---
p = make_plugin(output_path=csv_path("epoch_reset.json"))
agent = mock.Mock()
p.on_wifi_update(agent, [AP])
p.on_deauthentication(agent, AP, STA)
p.on_epoch(agent, 1, {})
p.on_handshake(agent, "/x.pcapng", AP, STA)  # deauth was reset by on_epoch - should be treated as passive
check("on_epoch resets in-flight deauth timing, so a later handshake is treated as passive", not os.path.exists(csv_path("epoch_reset.json")))


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
