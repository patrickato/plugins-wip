import sys
import os
import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import clock_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.ClockNG()
    p.options = dict(opts)
    return p


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


check("ClockNG registers with the real pwnagotchi.plugins loader", "clock_ng" in pwnagotchi.plugins.loaded)

# --- on_ui_setup creates both elements at the original's default positions ---
p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
check("on_ui_setup creates the date element", "clock_ng_date" in ui.elements)
check("on_ui_setup creates the time element", "clock_ng_time" in ui.elements)
check("date element defaults to the original's (100, 0) position", ui.elements["clock_ng_date"].xy[:2] == (100, 0))
check("time element defaults to the original's (100, 95) position", ui.elements["clock_ng_time"].xy[:2] == (100, 95))

# --- ADDED: configured positions are honored ---
p = make_plugin(date_position_x=5, date_position_y=6, time_position_x=7, time_position_y=8)
ui = FakeUI()
p.on_ui_setup(ui)
check("configured date position is honored", ui.elements["clock_ng_date"].xy[:2] == (5, 6))
check("configured time position is honored", ui.elements["clock_ng_time"].xy[:2] == (7, 8))

# --- on_ui_update sets both elements using the default (original) formats ---
p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
p.on_ui_update(ui)
now = datetime.datetime.now()
check("date element uses the default %m/%d/%y format", ui.values["clock_ng_date"] == now.strftime("%m/%d/%y"))
check("time element uses the default %I:%M%p format", ui.values["clock_ng_time"] == now.strftime("%I:%M%p"))

# --- ADDED: configured 24-hour time format is honored ---
p = make_plugin(time_format="%H:%M")
ui = FakeUI()
p.on_ui_setup(ui)
p.on_ui_update(ui)
now = datetime.datetime.now()
check("configured 24-hour time_format is honored", ui.values["clock_ng_time"] == now.strftime("%H:%M"))

# --- ADDED: configured date format is honored ---
p = make_plugin(date_format="%Y-%m-%d")
ui = FakeUI()
p.on_ui_setup(ui)
p.on_ui_update(ui)
now = datetime.datetime.now()
check("configured date_format is honored", ui.values["clock_ng_date"] == now.strftime("%Y-%m-%d"))

# --- ADDED: an invalid format string falls back to the default instead of crashing ---
p = make_plugin(time_format="%Q_not_a_real_directive_but_wont_crash")
ui = FakeUI()
p.on_ui_setup(ui)
try:
    p.on_ui_update(ui)
    invalid_format_ok = "clock_ng_time" in ui.values
except Exception:
    invalid_format_ok = False
check("on_ui_update doesn't crash on an unusual format string", invalid_format_ok)

with mock.patch("clock_ng.datetime") as fake_dt_module:
    class RaisingDatetime:
        def strftime(self, fmt):
            if fmt == "bad-format":
                raise ValueError("bad format")
            return "ok"
    fake_dt_module.datetime.now.return_value = RaisingDatetime()
    p2 = make_plugin(time_format="bad-format")
    ui2 = FakeUI()
    p2.on_ui_setup(ui2)
    try:
        p2.on_ui_update(ui2)
        crash_fallback_ok = ui2.values["clock_ng_time"] == "ok"
    except Exception:
        crash_fallback_ok = False
check("a strftime format that raises falls back to the default format instead of crashing", crash_fallback_ok)

# --- on_unload removes both elements cleanly ---
p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
try:
    p.on_unload(ui)
    unload_ok = "clock_ng_date" not in ui.elements and "clock_ng_time" not in ui.elements
except Exception:
    unload_ok = False
check("on_unload removes both elements", unload_ok)

# --- ADDED: on_unload doesn't crash when elements were never created ---
p = make_plugin()
ui = FakeUI()
try:
    p.on_unload(ui)
    unload_no_setup_ok = True
except Exception:
    unload_no_setup_ok = False
check("on_unload doesn't crash when on_ui_setup never ran (the original had no guard here)", unload_no_setup_ok)


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
