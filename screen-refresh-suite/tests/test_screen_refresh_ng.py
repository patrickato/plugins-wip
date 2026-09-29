import sys
import os
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import screen_refresh_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.ScreenRefreshNG()
    p.options = dict(opts)
    return p


class FakeUI:
    """A plain object with no init_display() at all - matching the real
    View class plugin hooks actually receive on this fork."""

    def __init__(self, with_implementation=True):
        self.values = {}
        if with_implementation:
            self._implementation = mock.Mock()

    def set(self, name, value):
        self.values[name] = value


check("ScreenRefreshNG registers with the real pwnagotchi.plugins loader", "screen_refresh_ng" in pwnagotchi.plugins.loaded)

# --- THE original bug: no crash against a UI object with no init_display() ---
p = make_plugin(refresh_interval=1)
ui = FakeUI()
try:
    p.on_ui_update(ui)
    crashed = False
except AttributeError:
    crashed = True
check("on_ui_update doesn't crash against a View-shaped object with no init_display()", not crashed)
check("on_ui_update calls the implementation's initialize() to force a refresh", ui._implementation.initialize.called)
check("on_ui_update sets the status element by default", ui.values.get("status") == "Screen cleaned")

# --- doesn't refresh before refresh_interval updates have happened ---
p = make_plugin(refresh_interval=3)
ui = FakeUI()
p.on_ui_update(ui)
p.on_ui_update(ui)
check("no refresh before refresh_interval ticks", not ui._implementation.initialize.called)
p.on_ui_update(ui)
check("refreshes on the refresh_interval-th tick", ui._implementation.initialize.called)

# --- counter resets after a refresh ---
p = make_plugin(refresh_interval=2)
ui = FakeUI()
p.on_ui_update(ui)
p.on_ui_update(ui)  # refresh #1
ui._implementation.initialize.reset_mock()
p.on_ui_update(ui)
check("counter doesn't refresh again on the very next tick", not ui._implementation.initialize.called)
p.on_ui_update(ui)
check("counter refreshes again after a full interval", ui._implementation.initialize.called)

# --- refresh_interval=0 disables refreshing entirely ---
p = make_plugin(refresh_interval=0)
ui = FakeUI()
for _ in range(10):
    p.on_ui_update(ui)
check("refresh_interval=0 disables refreshing", not ui._implementation.initialize.called)

# --- show_status=false skips the status element ---
p = make_plugin(refresh_interval=1, show_status=False)
ui = FakeUI()
p.on_ui_update(ui)
check("show_status=false skips setting the status element", "status" not in ui.values)

# --- no implementation attribute at all doesn't crash ---
p = make_plugin(refresh_interval=1)
ui = FakeUI(with_implementation=False)
try:
    p.on_ui_update(ui)
    ok = True
except Exception:
    ok = False
check("missing _implementation attribute doesn't crash", ok)

# --- a failing implementation.initialize() is caught, not fatal ---
p = make_plugin(refresh_interval=1)
ui = FakeUI()
ui._implementation.initialize.side_effect = RuntimeError("hw not ready")
try:
    p.on_ui_update(ui)
    ok = True
except Exception:
    ok = False
check("a failing refresh call is caught, not fatal", ok)


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
