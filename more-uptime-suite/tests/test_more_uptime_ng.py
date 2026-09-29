import sys
import os
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import more_uptime_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.MoreUptimeNG()
    p.options = dict(opts)
    p.on_loaded()
    return p


class FakeUI:
    def __init__(self, width=250, height=122):
        self.elements = {}
        self.values = {}
        self._width = width
        self._height = height
        self._state = mock.Mock()
        self._state._state = {"uptime": mock.Mock(label=None)}

    def width(self):
        return self._width

    def height(self):
        return self._height

    def add_element(self, name, el):
        self.elements[name] = el

    def remove_element(self, name):
        del self.elements[name]

    def has_element(self, name):
        return name in self.elements

    def set(self, name, value):
        self.values[name] = value


check("MoreUptimeNG registers with the real pwnagotchi.plugins loader", "more_uptime_ng" in pwnagotchi.plugins.loaded)

# --- THE original bug: element must be created even with a custom position configured ---
p = make_plugin(override=False, position_x=5, position_y=6)
ui = FakeUI()
p.on_ui_setup(ui)
check("on_ui_setup creates the element when a custom position IS configured (the original bug)", "more_uptime_ng" in ui.elements)
check("configured position is honored", ui.elements["more_uptime_ng"].xy[:2] == (5, 6))

# --- default position used when unset ---
p = make_plugin(override=False)
ui = FakeUI(width=250)
p.on_ui_setup(ui)
check("default position falls back to (width-58, 12)", ui.elements["more_uptime_ng"].xy[:2] == (192, 12))

# --- override=true skips creating its own element ---
p = make_plugin(override=True)
ui = FakeUI()
p.on_ui_setup(ui)
check("override=true doesn't create a separate element", "more_uptime_ng" not in ui.elements)

# --- on_ui_update cycles through IN/PR/UP states over successive calls ---
p = make_plugin(override=False)
ui = FakeUI()
p.on_ui_setup(ui)
p._next = 0  # force a state advance on the very first call
p.on_ui_update(ui)
first_value = ui.values.get("more_uptime_ng")
check("on_ui_update sets a value on the element", first_value is not None and first_value.startswith("PR "))

# --- override=true updates the stock "uptime" element's label, not our own ---
p = make_plugin(override=True)
ui = FakeUI()
p.on_ui_setup(ui)
p._next = 0
p.on_ui_update(ui)
check("override=true sets the stock 'uptime' element, not more_uptime_ng", "uptime" in ui.values and "more_uptime_ng" not in ui.values)
check("override=true relabels the stock uptime element", ui._state._state["uptime"].label == "PR")

# --- on_ui_update doesn't crash (and doesn't mask the error with a second NameError) if /proc/uptime is unreadable ---
p = make_plugin(override=False)
ui = FakeUI()
p.on_ui_setup(ui)
with mock.patch("builtins.open", side_effect=FileNotFoundError("no /proc here")):
    try:
        p.on_ui_update(ui)
        crashed = False
    except Exception:
        crashed = True
check("on_ui_update doesn't crash (or NameError-mask) when /proc/uptime is unreadable", not crashed)

# --- on_unload removes the element cleanly when override is false ---
p = make_plugin(override=False)
ui = FakeUI()
p.on_ui_setup(ui)
try:
    p.on_unload(ui)
    unload_ok = "more_uptime_ng" not in ui.elements
except Exception:
    unload_ok = False
check("on_unload removes the element", unload_ok)

# --- on_unload is a no-op (doesn't crash) when override is true (no element was ever created) ---
p = make_plugin(override=True)
ui = FakeUI()
p.on_ui_setup(ui)
try:
    p.on_unload(ui)
    unload_ok = True
except Exception:
    unload_ok = False
check("on_unload doesn't crash when override=true (nothing to remove)", unload_ok)

# --- ADDED: configurable cycle_interval is honored instead of the hardcoded 5s ---
p = make_plugin(override=False, cycle_interval=100)
ui = FakeUI()
p.on_ui_setup(ui)
p._next = 0  # force the very first advance
before = p._state
p.on_ui_update(ui)
after_first = p._state
# A long interval means the NEXT call (immediately after) must NOT advance again.
p.on_ui_update(ui)
after_second = p._state
check("on_ui_update advances state once when _next has passed", after_first != before)
check("a long configured cycle_interval prevents an immediate second advance", after_second == after_first)

# --- ADDED: invalid cycle_interval falls back to the default rather than crashing/spinning ---
p = make_plugin(override=False, cycle_interval=0)
ui = FakeUI()
p.on_ui_setup(ui)
p._next = 0
try:
    p.on_ui_update(ui)
    invalid_interval_ok = p._next > time.time()
except Exception:
    invalid_interval_ok = False
check("cycle_interval=0 falls back to the default instead of crashing or spinning", invalid_interval_ok)

# --- ADDED: configurable states restricts which states are shown ---
p = make_plugin(override=False, states=["UP"])
ui = FakeUI()
p.on_ui_setup(ui)
p._next = 0
p.on_ui_update(ui)
value = ui.values.get("more_uptime_ng")
check("states=['UP'] only ever shows the UP state", value is not None and value.startswith("UP "))
p._next = 0
p.on_ui_update(ui)
value2 = ui.values.get("more_uptime_ng")
check("states=['UP'] keeps showing UP on subsequent cycles too (no other state to cycle to)", value2 is not None and value2.startswith("UP "))

# --- ADDED: configurable states honors a custom order/subset ---
p = make_plugin(override=False, states=["PR", "UP"])
ui = FakeUI()
p.on_ui_setup(ui)
p._next = 0  # advances _state from 0 to 1 -> states[1] == "UP"
p.on_ui_update(ui)
value = ui.values.get("more_uptime_ng")
check("custom states=['PR', 'UP'] order is respected", value is not None and value.startswith("UP "))

# --- ADDED: an empty/invalid states list falls back to the default IN/PR/UP instead of crashing ---
p = make_plugin(override=False, states=["NOT_A_REAL_STATE"])
ui = FakeUI()
p.on_ui_setup(ui)
p._next = 0
try:
    p.on_ui_update(ui)
    invalid_states_ok = "more_uptime_ng" in ui.values
except Exception:
    invalid_states_ok = False
check("an all-invalid states list falls back to the default instead of crashing", invalid_states_ok)

p = make_plugin(override=False, states=[])
ui = FakeUI()
p.on_ui_setup(ui)
p._next = 0
try:
    p.on_ui_update(ui)
    empty_states_ok = "more_uptime_ng" in ui.values
except Exception:
    empty_states_ok = False
check("an empty states list falls back to the default instead of a ZeroDivisionError", empty_states_ok)


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
