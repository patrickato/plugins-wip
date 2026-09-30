import sys
import os
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import display_aircrack_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.DisplayAircrackNG()
    p.options = dict(opts)
    return p


class FakeUI:
    def __init__(self, width=250, height=122):
        self.elements = {}
        self.values = {}
        self._width = width
        self._height = height
        self._lock = mock.MagicMock()
        self._lock.__enter__ = mock.Mock(return_value=None)
        self._lock.__exit__ = mock.Mock(return_value=False)

    def width(self):
        return self._width

    def height(self):
        return self._height

    def add_element(self, name, el):
        self.elements[name] = el

    def remove_element(self, name):
        del self.elements[name]

    def set(self, name, value):
        self.values[name] = value


check("DisplayAircrackNG registers with the real pwnagotchi.plugins loader", "display_aircrack_ng" in pwnagotchi.plugins.loaded)

# --- default position matches the original's (ui.width() // 2 - 10, 0) ---
p = make_plugin()
ui = FakeUI(width=250)
p.on_ui_setup(ui)
check("default position matches the original (width//2 - 10, 0)", ui.elements["display_aircrack_ng"].xy[:2] == (115, 0))

# --- ADDED: configured position is honored ---
p = make_plugin(position_x=5, position_y=6)
ui = FakeUI()
p.on_ui_setup(ui)
check("configured position is honored", ui.elements["display_aircrack_ng"].xy[:2] == (5, 6))

# --- on_ui_update shows the running/stopped text based on the ps check ---
p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
with mock.patch("display_aircrack_ng.os.popen") as popen:
    popen.return_value.read.return_value = "  123 ?  00:00:01 aircrack-ng\n"
    p.on_ui_update(ui)
check("shows the running text when aircrack-ng is in the process list", ui.values["display_aircrack_ng"] == "AC:ON")

p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
with mock.patch("display_aircrack_ng.os.popen") as popen:
    popen.return_value.read.return_value = "  1 ?  00:00:01 init\n"
    p.on_ui_update(ui)
check("shows the stopped text when aircrack-ng is not in the process list", ui.values["display_aircrack_ng"] == "AC:OFF")

# --- ADDED: configurable running_text/stopped_text are honored ---
p = make_plugin(running_text="RUN", stopped_text="OFF")
ui = FakeUI()
p.on_ui_setup(ui)
with mock.patch("display_aircrack_ng.os.popen") as popen:
    popen.return_value.read.return_value = "aircrack-ng"
    p.on_ui_update(ui)
check("configured running_text is honored", ui.values["display_aircrack_ng"] == "RUN")

# --- ADDED: check_interval throttles how often ps is actually shelled out to ---
p = make_plugin(check_interval=100)
ui = FakeUI()
p.on_ui_setup(ui)
with mock.patch("display_aircrack_ng.os.popen") as popen:
    popen.return_value.read.return_value = "aircrack-ng"
    p.on_ui_update(ui)  # first call: real check happens
    first_call_count = popen.call_count
    p.on_ui_update(ui)  # second call, immediately after: should NOT re-check
    second_call_count = popen.call_count
check("on_ui_update performs a real check on the first call", first_call_count == 1)
check("a long check_interval prevents an immediate second ps check", second_call_count == first_call_count)
check("the displayed value still updates every tick from the last known state", ui.values["display_aircrack_ng"] == "AC:ON")

# --- ADDED: an invalid check_interval falls back to the default instead of crashing ---
p = make_plugin(check_interval=0)
ui = FakeUI()
p.on_ui_setup(ui)
with mock.patch("display_aircrack_ng.os.popen") as popen:
    popen.return_value.read.return_value = ""
    try:
        p.on_ui_update(ui)
        invalid_interval_ok = True
    except Exception:
        invalid_interval_ok = False
check("check_interval=0 falls back to the default instead of crashing", invalid_interval_ok)

# --- on_ui_update doesn't crash (and keeps the last known state) if the ps check itself fails ---
p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
p._running = True
with mock.patch("display_aircrack_ng.os.popen", side_effect=OSError("no ps here")):
    try:
        p.on_ui_update(ui)
        ps_failure_ok = ui.values["display_aircrack_ng"] == "AC:ON"
    except Exception:
        ps_failure_ok = False
check("on_ui_update doesn't crash if the ps check itself fails, and keeps the last known state", ps_failure_ok)

# --- on_unload removes the element cleanly ---
p = make_plugin()
ui = FakeUI()
p.on_ui_setup(ui)
try:
    p.on_unload(ui)
    unload_ok = "display_aircrack_ng" not in ui.elements
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

# --- ADDED: on_webhook returns a real body (never None -> Flask 500) ---
p = make_plugin()
_wh_body = p.on_webhook("/", None)
check("on_webhook returns a non-None body (avoids Flask 500 on index)", _wh_body is not None)
check("on_webhook body is HTML text", isinstance(_wh_body, str) and "<html" in _wh_body.lower())

# It reflects the last known running state in the body.
p = make_plugin(running_text="RUN", stopped_text="OFF")
p._running = True
check("on_webhook body reflects the running state", "RUN" in p.on_webhook("/", None))
p._running = False
check("on_webhook body reflects the stopped state", "OFF" in p.on_webhook("/", None))


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
