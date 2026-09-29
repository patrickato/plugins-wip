import sys
import os
import json
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

from flask import Flask  # noqa: E402

import pwnagotchi.plugins  # noqa: E402
import viz_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


app = Flask(__name__)

AP = {
    "mac": "AA:BB:CC:DD:EE:FF",
    "hostname": "MyLab",
    "vendor": "",
    "rssi": -50,
    "frequency": 2437,  # channel 6
    "clients": [{"mac": "11:22:33:44:55:66", "hostname": "phone", "vendor": ""}],
}

check("VizNG registers with the real pwnagotchi.plugins loader", "viz_ng" in pwnagotchi.plugins.loaded)
check("VizNG imports freq_to_channel from the real pwnagotchi.mesh.wifi module (the original's import was to a module that doesn't exist on this fork)", mod.freq_to_channel is not None)

# --- THE original bug: self.channel must be initialized, not just set-on-first-hop ---
p = mod.VizNG()
check("self.channel is initialized in __init__, not left undefined", hasattr(p, "channel") and p.channel is None)

# --- the webhook's update path must not crash before any channel-hop event has fired ---
with app.test_request_context("/"):
    try:
        response = p.on_webhook("update", None)
        crashed = False
    except AttributeError:
        crashed = True
check("update webhook doesn't crash (AttributeError) before any on_channel_hop has ever fired", not crashed)

# --- on_unfiltered_ap_list stores sorted AP data as JSON ---
p = mod.VizNG()
agent = mock.Mock()
p.on_unfiltered_ap_list(agent, [AP])
check("on_unfiltered_ap_list stores the AP data as JSON", json.loads(p.data)[0]["hostname"] == "MyLab")

# --- on_channel_hop updates self.channel ---
p.on_channel_hop(agent, 6)
check("on_channel_hop updates self.channel", p.channel == 6)

# --- create_graph handles channel=None without crashing (matches the pre-hop webhook case) ---
p2 = mod.VizNG()
p2.on_unfiltered_ap_list(agent, [AP])
try:
    graph_json = mod.VizNG.create_graph(p2.data, p2.channel)
    ok = True
except Exception:
    ok = False
check("create_graph handles channel=None (no channel-hop yet) without crashing", ok)

# --- create_graph handles a real channel value and includes the AP's name ---
mod.VizNG.create_graph.cache_clear()
graph_json = mod.VizNG.create_graph(p2.data, 6)
check("create_graph output mentions the AP's hostname", "MyLab" in graph_json)
check("create_graph output mentions the client's hostname", "phone" in graph_json)

# --- create_graph with no data returns an empty-ish graph, not a crash ---
mod.VizNG.create_graph.cache_clear()
check("create_graph with no data returns a benign empty result", mod.VizNG.create_graph(None, None) == "{}")

# --- lookup_color is deterministic for the same node name ---
c1 = mod.VizNG.lookup_color("MyLab")
c2 = mod.VizNG.lookup_color("MyLab")
check("lookup_color is deterministic (memoized) for the same node", c1 == c2)

# --- webhook "/" renders the template (mocked to avoid needing the real base.html) ---
p3 = mod.VizNG()
with mock.patch("viz_ng.render_template_string") as rts:
    rts.return_value = "<html>viz page</html>"
    with app.test_request_context("/"):
        result = p3.on_webhook("", None)
check("webhook '/' path renders the Viz template", rts.called)

# --- webhook rejects unknown paths with 404 ---
p4 = mod.VizNG()
with app.test_request_context("/"):
    try:
        p4.on_webhook("nonsense", None)
        aborted = False
    except Exception as e:
        aborted = "404" in str(e)
check("webhook aborts unknown paths with 404", aborted)

# --- ADDED: last-updated timestamp is tracked and served via the "meta" webhook path ---
p5 = mod.VizNG()
check("_last_update starts unset", p5._last_update is None)
p5.on_unfiltered_ap_list(mock.Mock(), [AP])
check("on_unfiltered_ap_list sets _last_update", p5._last_update is not None)
with app.test_request_context("/"):
    meta = p5.on_webhook("meta", None)
check("meta webhook reports the last-update timestamp", json.loads(meta.get_data())["last_update"] == p5._last_update)

# --- ADDED: meta webhook reports "never" before any data has ever arrived ---
p6 = mod.VizNG()
with app.test_request_context("/"):
    meta = p6.on_webhook("meta", None)
check("meta webhook reports 'never' before any AP data has arrived", json.loads(meta.get_data())["last_update"] == "never")

# --- ADDED: configurable poll_interval_ms is substituted into the rendered page ---
p7 = mod.VizNG()
p7.options = {"poll_interval_ms": 12345}
with mock.patch("viz_ng.render_template_string") as rts:
    rts.return_value = "<html>viz page</html>"
    with app.test_request_context("/"):
        p7.on_webhook("", None)
    rendered_source = rts.call_args[0][0]
check("configured poll_interval_ms appears in the rendered page's source", "12345" in rendered_source)
check("the placeholder token is fully substituted, not left in the page", "__POLL_INTERVAL_MS__" not in rendered_source)

# --- ADDED: an invalid poll_interval_ms falls back to the default instead of crashing ---
p8 = mod.VizNG()
p8.options = {"poll_interval_ms": -1}
with mock.patch("viz_ng.render_template_string") as rts:
    rts.return_value = "<html>viz page</html>"
    try:
        with app.test_request_context("/"):
            p8.on_webhook("", None)
        invalid_poll_ok = "5000" in rts.call_args[0][0]
    except Exception:
        invalid_poll_ok = False
check("an invalid poll_interval_ms falls back to the default (5000ms) instead of crashing", invalid_poll_ok)

# --- ADDED: nodes already in CrackHouseNG's cracked list are marked on the graph ---
crack_house_file = tempfile.NamedTemporaryFile(mode="w", suffix=".potfile", delete=False)
crack_house_file.write("MyLab:hunter2\n")
crack_house_file.close()

p9 = mod.VizNG()
p9.options = {"crack_house_saving_path": crack_house_file.name}
p9.on_unfiltered_ap_list(mock.Mock(), [AP])
mod.VizNG.create_graph.cache_clear()
cracked = p9._cracked_hostnames()
check("_cracked_hostnames reads CrackHouseNG's saving_path file", "mylab" in cracked)
graph_json = mod.VizNG.create_graph(p9.data, None, cracked)
check("a cracked AP node is marked [CRACKED] in the graph output", "MyLab [CRACKED]" in graph_json)
check("a cracked AP node uses the 'star' symbol", '"star"' in graph_json)

os.unlink(crack_house_file.name)

# --- ADDED: cross-referencing is case-insensitive, matching CrackHouseNG's own addition ---
crack_house_file2 = tempfile.NamedTemporaryFile(mode="w", suffix=".potfile", delete=False)
crack_house_file2.write("mylab:hunter2\n")  # lowercase in the potfile, "MyLab" over the air
crack_house_file2.close()

p10 = mod.VizNG()
p10.options = {"crack_house_saving_path": crack_house_file2.name}
p10.on_unfiltered_ap_list(mock.Mock(), [AP])
mod.VizNG.create_graph.cache_clear()
graph_json = mod.VizNG.create_graph(p10.data, None, p10._cracked_hostnames())
check("cracked cross-referencing matches case-insensitively", "MyLab [CRACKED]" in graph_json)

os.unlink(crack_house_file2.name)

# --- ADDED: a missing/unset crack_house_saving_path doesn't crash, and marks nothing ---
p11 = mod.VizNG()
p11.options = {"crack_house_saving_path": "/does/not/exist.potfile"}
try:
    cracked = p11._cracked_hostnames()
    missing_file_ok = cracked == frozenset()
except Exception:
    missing_file_ok = False
check("a missing crack_house_saving_path file doesn't crash, just yields no matches", missing_file_ok)

p12 = mod.VizNG()
p12.options = {"crack_house_saving_path": ""}
check("crack_house_saving_path='' (disabled) yields no matches", p12._cracked_hostnames() == frozenset())


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
