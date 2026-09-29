import sys
import os
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import touch_ui_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.TouchUING()
    p.options = dict(opts)
    return p


check("TouchUING registers with the real pwnagotchi.plugins loader", "touch_ui_ng" in pwnagotchi.plugins.loaded)

# --- Touch_Button.draw(): a real drawing exception is logged, not turned into a second crash ---
button = mod.Touch_Button(position=(0, 0, 10, 10))
bad_canvas = mock.Mock()
bad_drawer = mock.Mock()
bad_drawer.rectangle.side_effect = RuntimeError("boom")
try:
    button.draw(bad_canvas, bad_drawer)
    crashed = False
except TypeError:
    # the original's `logging(repr(e))` bug - logging module isn't
    # callable, so a real drawing error became a second, unhandled
    # TypeError raised out of the except block itself
    crashed = True
except Exception:
    crashed = True
check("Touch_Button.draw logs a real exception instead of raising a second one", not crashed)

# --- on_internet_available: apt install list is built correctly, not check_output(None) ---
p = make_plugin()
p.needsAptPackages = ["evtest", "libts-bin"]
with mock.patch("touch_ui_ng.check_output") as co:
    p.on_internet_available(mock.Mock())
check("on_internet_available calls check_output with a real, non-None argument", co.call_args[0][0] is not None)
check("on_internet_available's apt command includes both needed packages", co.call_args[0][0] == ["apt", "install", "-y", "evtest", "libts-bin"])
check("needsAptPackages is cleared after a successful install", p.needsAptPackages is None)

# --- on_internet_available is a no-op when nothing is needed ---
p = make_plugin()
p.needsAptPackages = None
with mock.patch("touch_ui_ng.check_output") as co:
    p.on_internet_available(mock.Mock())
check("on_internet_available does nothing when needsAptPackages is unset", not co.called)

# --- process_touch: a momentary+reverse button doesn't NameError, and flips state correctly ---
p = make_plugin()
button = mod.Touch_Button(position=(0, 0, 10, 10), momentary=True, reverse=True, state=False)
fake_state = mock.Mock()
fake_state._state = {"mybutton": button}
fake_state._changes = {}
fake_view = mock.Mock()
fake_view._state = fake_state
p._view = fake_view
try:
    p.process_touch([5, 5], 100)  # a press
    crashed = False
except NameError:
    crashed = True
check("process_touch doesn't NameError on a momentary+reverse button (the original bug)", not crashed)
check("a reverse momentary button's state is inverted from the raw touch state", button.state is False)

# --- process_touch: touch_ready/press/release commands broadcast via plugins.on when no event_handler ---
p = make_plugin()
button2 = mod.Touch_Button(position=(0, 0, 10, 10), momentary=False, state=False)
fake_state2 = mock.Mock()
fake_state2._state = {"mybutton": button2}
fake_state2._changes = {}
fake_view2 = mock.Mock()
fake_view2._state = fake_state2
p._view = fake_view2
with mock.patch("touch_ui_ng.plugins.on") as on_mock:
    p.process_touch([5, 5], 100)
check("process_touch broadcasts via plugins.on when the button has no event_handler", on_mock.called)

# --- process_touch: a button with an event_handler is targeted via plugins.one, not broadcast ---
p = make_plugin()
button3 = mod.Touch_Button(position=(0, 0, 10, 10), momentary=False, state=False, event_handler="some_other_plugin")
fake_state3 = mock.Mock()
fake_state3._state = {"mybutton": button3}
fake_state3._changes = {}
fake_view3 = mock.Mock()
fake_view3._state = fake_state3
p._view = fake_view3
with mock.patch("touch_ui_ng.plugins.one") as one_mock, mock.patch("touch_ui_ng.plugins.on") as on_mock:
    p.process_touch([5, 5], 100)
check("process_touch targets a specific plugin via plugins.one when event_handler is set", one_mock.called and one_mock.call_args[0][0] == "some_other_plugin")
check("process_touch does NOT also broadcast via plugins.on for a targeted button", not on_mock.called)

# --- pointInBox correctness ---
p = make_plugin()
check("pointInBox: point inside box", p.pointInBox([5, 5], [0, 0, 10, 10]) is True)
check("pointInBox: point outside box", p.pointInBox([50, 50], [0, 0, 10, 10]) is False)

# --- touchScreenHandler: a missing evtest binary sets needsAptPackages (the original's dead check never could) ---
p = make_plugin()
with mock.patch("touch_ui_ng.Popen", side_effect=FileNotFoundError("no evtest")):
    p.touchScreenHandler()
check("a missing evtest binary correctly sets needsAptPackages (fixes the original's always-false 'not evtest' check)", p.needsAptPackages == ["evtest", "libts-bin"])

# --- init_gpio: configuring gpios calls GPIO.setup/add_event_detect per button, doesn't crash ---
p = make_plugin(gpios={"ok": 6, "next": 24})
import touch_ui_ng
with mock.patch.object(touch_ui_ng.GPIO, "setup") as setup_mock, \
     mock.patch.object(touch_ui_ng.GPIO, "add_event_detect") as aed_mock:
    p.init_gpio()
check("init_gpio calls GPIO.setup for each configured button", setup_mock.call_count == 2)
check("init_gpio registers both press and release edges per button", aed_mock.call_count == 4)

# --- init_gpio is a no-op when no gpios are configured ---
p = make_plugin()
with mock.patch.object(touch_ui_ng.GPIO, "setup") as setup_mock:
    p.init_gpio()
check("init_gpio does nothing when no gpios are configured", not setup_mock.called)

# --- on_unload cleans up UI elements without crashing when nothing was ever created ---
p = make_plugin()
fake_ui = mock.Mock()
try:
    p.on_unload(fake_ui)
    ok = True
except Exception:
    ok = False
check("on_unload doesn't crash with nothing set up yet", ok)


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
