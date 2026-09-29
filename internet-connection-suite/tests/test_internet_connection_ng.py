import sys
import os
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))  # prctl + tomlkit (native/unavailable here)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")  # real pwnagotchi.plugins framework

import pwnagotchi.plugins  # noqa: E402
import internet_connection_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.InternetConnectionNG()
    p.options = dict(opts)
    return p


class FakeUI:
    """Minimal stand-in for pwnagotchi.ui.view.View - just enough for
    on_ui_setup/on_unload/agent.view().set() to exercise real code."""

    def __init__(self, width=250):
        self._width = width
        self.elements = {}
        self._lock = mock.MagicMock()
        self._lock.__enter__ = mock.Mock(return_value=None)
        self._lock.__exit__ = mock.Mock(return_value=False)

    def width(self):
        return self._width

    def add_element(self, name, widget):
        self.elements[name] = widget

    def remove_element(self, name):
        del self.elements[name]

    def set(self, name, value):
        self.elements[name].value = value


# --- Test 1: real plugin registration ---
check(
    "InternetConnectionNG registers with the real pwnagotchi.plugins loader",
    "internet_connection_ng" in pwnagotchi.plugins.loaded,
)

# --- Test 2: on_ui_setup uses the automatic default position when unset ---
p = make_plugin()
ui = FakeUI(width=250)
p.on_ui_setup(ui)
el = ui.elements["internet_connection_ng"]
check("default position_x is ui.width()/2 - 35 when unset", el.xy[0] == 250 / 2 - 35)
check("default position_y is 0 when unset", el.xy[1] == 0)
check("initial value is the disconnected_value", el.value == "D")

# --- Test 3: config-driven position overrides the default ---
p = make_plugin(position_x=10, position_y=20)
ui = FakeUI()
p.on_ui_setup(ui)
el = ui.elements["internet_connection_ng"]
check("configured position_x is honored", el.xy[0] == 10)
check("configured position_y is honored", el.xy[1] == 20)

# --- Test 4: configurable label/values ---
p = make_plugin(label="NET", connected_value="OK", disconnected_value="NO")
ui = FakeUI()
p.on_ui_setup(ui)
el = ui.elements["internet_connection_ng"]
check("configured label is honored", el.label == "NET")
check("configured disconnected_value is honored", el.value == "NO")

# --- Test 5: on_internet_available sets the connected value (real event, no polling) ---
p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
agent = mock.Mock()
agent.view.return_value = ui
p.on_internet_available(agent)
check("on_internet_available sets the connected value", ui.elements["internet_connection_ng"].value == "C")

# --- Test 6: with active_recheck=False, on_epoch never probes and never flips state back ---
p = make_plugin(active_recheck=False)
ui = FakeUI()
p.on_ui_setup(ui)
agent = mock.Mock()
agent.view.return_value = ui
p.on_internet_available(agent)
with mock.patch("socket.create_connection", side_effect=AssertionError("should not be called")):
    p.on_epoch(agent, 1, {})
check(
    "active_recheck=false: on_epoch never calls the network probe",
    ui.elements["internet_connection_ng"].value == "C",
)

# --- Test 7: with active_recheck=True (default), on_epoch can flip the icon back off ---
# This is the core fix over all three originals: internet-connection.py's event-only
# design can never revert to disconnected, and wanmon.py's own internet_available/
# dns_resolving booleans were only ever set True and never reset, so it had the same
# "stuck on connected" problem in practice despite polling every epoch.
p = make_plugin(active_recheck=True)
ui = FakeUI()
p.on_ui_setup(ui)
agent = mock.Mock()
agent.view.return_value = ui
p.on_internet_available(agent)
check("connected after the real internet_available event", ui.elements["internet_connection_ng"].value == "C")
with mock.patch("socket.create_connection", side_effect=OSError("network unreachable")):
    p.on_epoch(agent, 2, {})
check(
    "active_recheck=true: a failed probe on the next epoch flips the icon back to disconnected",
    ui.elements["internet_connection_ng"].value == "D",
)

# --- Test 8: a successful probe on a later epoch flips it back on again ---
with mock.patch("socket.create_connection") as cc:
    cc.return_value.__enter__ = mock.Mock(return_value=mock.Mock())
    cc.return_value.__exit__ = mock.Mock(return_value=False)
    p.on_epoch(agent, 3, {})
check(
    "a later successful probe flips the icon back to connected",
    ui.elements["internet_connection_ng"].value == "C",
)

# --- Test 9: on_ready also performs an initial probe when active_recheck is on ---
p = make_plugin(active_recheck=True)
ui = FakeUI()
p.on_ui_setup(ui)
agent = mock.Mock()
agent.view.return_value = ui
with mock.patch("socket.create_connection") as cc:
    cc.return_value.__enter__ = mock.Mock(return_value=mock.Mock())
    cc.return_value.__exit__ = mock.Mock(return_value=False)
    p.on_ready(agent)
check("on_ready probes immediately and can show connected before any epoch runs", ui.elements["internet_connection_ng"].value == "C")

# --- Test 10: on_ready does nothing when active_recheck is off (matches original's pure event-driven design) ---
p = make_plugin(active_recheck=False)
ui = FakeUI()
p.on_ui_setup(ui)
agent = mock.Mock()
agent.view.return_value = ui
with mock.patch("socket.create_connection", side_effect=AssertionError("should not be called")):
    p.on_ready(agent)
check("on_ready never probes when active_recheck is off", ui.elements["internet_connection_ng"].value == "D")

# --- Test 11: on_unload removes the element cleanly ---
p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
p.on_unload(ui)
check("on_unload removes the UI element", "internet_connection_ng" not in ui.elements)

# --- Test 12: quick-check uses the configured host/port/timeout ---
p = make_plugin(recheck_test_host="1.2.3.4", recheck_test_port=443, recheck_timeout=2.5)
with mock.patch("socket.create_connection") as cc:
    cc.return_value.__enter__ = mock.Mock(return_value=mock.Mock())
    cc.return_value.__exit__ = mock.Mock(return_value=False)
    p._quick_check()
    check(
        "the probe uses the configured host/port/timeout, not hardcoded ones",
        cc.call_args == mock.call(("1.2.3.4", 443), timeout=2.5),
    )


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
