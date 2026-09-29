import sys
import os
import json
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


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
