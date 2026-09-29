import sys
import os
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi  # noqa: E402
import pwnagotchi.plugins  # noqa: E402
import display_version_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.DisplayVersionNG()
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
        del self.elements[name]

    def set(self, name, value):
        self.values[name] = value


check("DisplayVersionNG registers with the real pwnagotchi.plugins loader", "display_version_ng" in pwnagotchi.plugins.loaded)

# --- default position matches the original's hardcoded (185, 110) ---
p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
check("default position matches the original (185, 110)", ui.elements["display_version_ng"].xy[:2] == (185, 110))

# --- ADDED: configured position is honored ---
p = make_plugin(position_x=5, position_y=6)
ui = FakeUI()
p.on_ui_setup(ui)
check("configured position is honored", ui.elements["display_version_ng"].xy[:2] == (5, 6))

# --- on_ui_update sets the real pwnagotchi version ---
p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
p.on_ui_update(ui)
check("on_ui_update shows the real pwnagotchi.__version__", ui.values["display_version_ng"] == f"v{pwnagotchi.__version__}")

# --- on_unload removes the element cleanly ---
p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
try:
    p.on_unload(ui)
    unload_ok = "display_version_ng" not in ui.elements
except Exception:
    unload_ok = False
check("on_unload removes the element", unload_ok)

# --- on_unload doesn't crash when the element was never created ---
p = make_plugin()
ui = FakeUI()
try:
    p.on_unload(ui)
    unload_no_setup_ok = True
except Exception:
    unload_no_setup_ok = False
check("on_unload doesn't crash when on_ui_setup never ran", unload_no_setup_ok)

# --- webhook doesn't crash ---
p = make_plugin()
try:
    p.on_webhook("", None)
    webhook_ok = True
except Exception:
    webhook_ok = False
check("webhook doesn't crash", webhook_ok)


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
