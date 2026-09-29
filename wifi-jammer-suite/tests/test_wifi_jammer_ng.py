import sys
import os
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))  # prctl + tomlkit (native/unavailable here)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")  # real pwnagotchi.plugins framework

import pwnagotchi.plugins  # noqa: E402
import wifi_jammer_ng as mod  # noqa: E402
from flask import Flask  # noqa: E402

app = Flask(__name__)

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(authorized_networks=None, cooldown_seconds=15,
                 fire_on_association=True, fire_on_handshake=True):
    p = mod.WifiJammerNG()
    p.options = {
        "authorized_networks": authorized_networks or [],
        "cooldown_seconds": cooldown_seconds,
        "fire_on_association": fire_on_association,
        "fire_on_handshake": fire_on_handshake,
    }
    p.on_loaded()
    return p


# --- Test 1: real plugin registration ---
check(
    "WifiJammerNG registers with the real pwnagotchi.plugins loader",
    "wifi_jammer_ng" in pwnagotchi.plugins.loaded,
)

# --- Test 2: empty authorized_networks -> never fires ---
p = make_plugin(authorized_networks=[])
agent = mock.Mock()
ap = {"mac": "AA:BB:CC:DD:EE:FF", "hostname": "SomeNetwork"}
p.on_association(agent, ap)
p.on_handshake(agent, "/root/handshakes/SomeNetwork_AA-BB-CC-DD-EE-FF.pcapng", ap, {"mac": "11:22:33:44:55:66"})
check("empty authorized_networks -> on_association never calls agent.run", agent.run.call_count == 0)

# --- Test 3: BSSID-form authorized target fires on association ---
p = make_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"])
agent = mock.Mock()
ap = {"mac": "AA:BB:CC:DD:EE:FF", "hostname": "MyLab"}
p.on_association(agent, ap)
check(
    "authorized BSSID fires on on_association with the right command",
    agent.run.call_args is not None and agent.run.call_args[0][0] == "wifi.deauth AA:BB:CC:DD:EE:FF",
)

# --- Test 4: BSSID-form authorized target fires on handshake ---
p = make_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"])
agent = mock.Mock()
ap = {"mac": "AA:BB:CC:DD:EE:FF", "hostname": "MyLab"}
p.on_handshake(agent, "/root/handshakes/MyLab_AA-BB-CC-DD-EE-FF.pcapng", ap, {"mac": "11:22:33:44:55:66"})
check(
    "authorized BSSID fires on on_handshake",
    agent.run.call_args is not None and agent.run.call_args[0][0] == "wifi.deauth AA:BB:CC:DD:EE:FF",
)

# --- Test 5: unauthorized AP never fires ---
p = make_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"])
agent = mock.Mock()
other_ap = {"mac": "11:11:11:11:11:11", "hostname": "NeighborsNetwork"}
p.on_association(agent, other_ap)
p.on_handshake(agent, "/root/handshakes/NeighborsNetwork_11-11-11-11-11-11.pcapng", other_ap, {"mac": "22:22:22:22:22:22"})
check("an AP not in authorized_networks never triggers agent.run", agent.run.call_count == 0)

# --- Test 6: SSID-form authorized target matches by hostname and fires the real MAC ---
p = make_plugin(authorized_networks=["MyLabNetwork"])
agent = mock.Mock()
ap = {"mac": "CC:CC:CC:CC:CC:CC", "hostname": "MyLabNetwork"}
p.on_association(agent, ap)
check(
    "SSID-form authorized target fires using the AP's real MAC",
    agent.run.call_args is not None and agent.run.call_args[0][0] == "wifi.deauth CC:CC:CC:CC:CC:CC",
)

# case-insensitive SSID match
p2 = make_plugin(authorized_networks=["mylabnetwork"])
agent2 = mock.Mock()
p2.on_association(agent2, {"mac": "DD:DD:DD:DD:DD:DD", "hostname": "MyLabNetwork"})
check("SSID matching is case-insensitive", agent2.run.call_count == 1)

# --- Test 7: cooldown blocks an immediate re-fire, then allows after it elapses ---
p = make_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"], cooldown_seconds=0.2)
agent = mock.Mock()
ap = {"mac": "AA:BB:CC:DD:EE:FF", "hostname": "MyLab"}
p.on_association(agent, ap)
p.on_association(agent, ap)  # immediate second event - should be blocked by cooldown
check("cooldown blocks an immediate second fire against the same target", agent.run.call_count == 1)
time.sleep(0.25)
p.on_association(agent, ap)
check("cooldown allows a fire again after it elapses", agent.run.call_count == 2)

# --- Test 8: on_handshake handles the bare-MAC-string AP/station shape ---
p = make_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"])
agent = mock.Mock()
# real agent.py can call plugins.on('handshake', self, filename, ap_mac, sta_mac) with
# bare strings instead of dicts when it can't match the session at that moment.
p.on_handshake(agent, "/root/handshakes/x.pcapng", "AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66")
check(
    "bare-MAC-string on_handshake args still match and fire correctly",
    agent.run.call_args is not None and agent.run.call_args[0][0] == "wifi.deauth AA:BB:CC:DD:EE:FF",
)

# --- Test 9: fire_on_association / fire_on_handshake toggles are respected ---
p = make_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"], fire_on_association=False)
agent = mock.Mock()
p.on_association(agent, {"mac": "AA:BB:CC:DD:EE:FF", "hostname": "MyLab"})
check("fire_on_association=false suppresses the association trigger", agent.run.call_count == 0)

p = make_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"], fire_on_handshake=False)
agent = mock.Mock()
p.on_handshake(agent, "/x.pcapng", {"mac": "AA:BB:CC:DD:EE:FF", "hostname": "MyLab"}, {"mac": "y"})
check("fire_on_handshake=false suppresses the handshake trigger", agent.run.call_count == 0)

# --- Test 10: manual webhook trigger only fires for configured BSSIDs ---
p = make_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"])
agent = mock.Mock()
p.on_ready(agent)
with app.test_request_context("/?fire=AA:BB:CC:DD:EE:FF"):
    from flask import request
    resp = p.on_webhook("", request)
check(
    "webhook fire=<authorized BSSID> calls agent.run with the right command",
    agent.run.call_args is not None and agent.run.call_args[0][0] == "wifi.deauth AA:BB:CC:DD:EE:FF",
)
check("webhook response mentions the fired target", b"AA:BB:CC:DD:EE:FF" in resp.get_data())

# unauthorized target via webhook is refused
p = make_plugin(authorized_networks=["AA:BB:CC:DD:EE:FF"])
agent = mock.Mock()
p.on_ready(agent)
with app.test_request_context("/?fire=11:11:11:11:11:11"):
    from flask import request
    resp = p.on_webhook("", request)
check("webhook refuses to fire an unauthorized BSSID even when directly requested", agent.run.call_count == 0)
check("webhook response says the target was refused", b"Refused" in resp.get_data())

# --- Test 11: config reload picks up a newly-added target ---
p = make_plugin(authorized_networks=[])
agent = mock.Mock()
ap = {"mac": "AA:BB:CC:DD:EE:FF", "hostname": "MyLab"}
p.on_association(agent, ap)
check("before config reload, unlisted target does not fire", agent.run.call_count == 0)
p.options["authorized_networks"] = ["AA:BB:CC:DD:EE:FF"]
p.on_config_changed({})
p.on_association(agent, ap)
check("after on_config_changed picks up new target, it fires", agent.run.call_count == 1)


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
