import sys
import os
import time
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

# --- ADDED: webhook returns a real status page instead of nothing ---
p = make_plugin()
html = p.on_webhook("", None)
check("webhook returns real HTML, not None (the original returned nothing)", html is not None and "TouchUING" in html)
check("webhook status page reports 'never' when no touch has been seen yet", "Last touch seen: never" in html)
check("webhook status page reports the reader thread isn't running when unset", "Reader thread running: False" in html)
check("webhook status page reports no touchscreen process when unset", "Touchscreen process active: False" in html)
check("webhook status page reports no missing packages by default", "Missing apt packages: none" in html)

# --- ADDED: webhook status page reflects real state once it exists ---
p = make_plugin()
p.needsAptPackages = ["evtest", "libts-bin"]
p.touchscreen = mock.Mock()
alive_thread = mock.Mock()
alive_thread.is_alive.return_value = True
p._ts_thread = alive_thread
p._last_touch = "12:34:56"
html = p.on_webhook("", None)
check("webhook status page reflects a running reader thread", "Reader thread running: True" in html)
check("webhook status page reflects an active touchscreen process", "Touchscreen process active: True" in html)
check("webhook status page reflects the last touch timestamp", "Last touch seen: 12:34:56" in html)
check("webhook status page lists missing apt packages", "evtest" in html and "libts-bin" in html)

# --- ADDED: process_touch records a last-touch timestamp ---
p = make_plugin()
button4 = mod.Touch_Button(position=(0, 0, 10, 10), momentary=False, state=False)
fake_state4 = mock.Mock()
fake_state4._state = {"mybutton": button4}
fake_state4._changes = {}
fake_view4 = mock.Mock()
fake_view4._state = fake_state4
p._view = fake_view4
check("_last_touch starts unset", p._last_touch is None)
p.process_touch([5, 5], 100)
check("process_touch records a last-touch timestamp", p._last_touch is not None)

# --- ADDED: a long-held press dispatches an extra touch_longpress event on release ---
p = make_plugin(longpress_seconds=0.05)
button5 = mod.Touch_Button(position=(0, 0, 10, 10), momentary=False, state=False)
fake_state5 = mock.Mock()
fake_state5._state = {"mybutton": button5}
fake_state5._changes = {}
fake_view5 = mock.Mock()
fake_view5._state = fake_state5
p._view = fake_view5
with mock.patch("touch_ui_ng.plugins.on") as on_mock:
    p.process_touch([5, 5], 100)  # press
    time.sleep(0.08)  # hold past the configured 0.05s threshold
    p.process_touch([5, 5], 0)  # release
    dispatched_events = [call.args[0] for call in on_mock.call_args_list]
check("a press held past longpress_seconds dispatches touch_release", "touch_release" in dispatched_events)
check("a press held past longpress_seconds also dispatches touch_longpress", "touch_longpress" in dispatched_events)

# --- ADDED: a short press does NOT dispatch touch_longpress ---
p = make_plugin(longpress_seconds=5)
button6 = mod.Touch_Button(position=(0, 0, 10, 10), momentary=False, state=False)
fake_state6 = mock.Mock()
fake_state6._state = {"mybutton": button6}
fake_state6._changes = {}
fake_view6 = mock.Mock()
fake_view6._state = fake_state6
p._view = fake_view6
with mock.patch("touch_ui_ng.plugins.on") as on_mock:
    p.process_touch([5, 5], 100)  # press
    p.process_touch([5, 5], 0)  # immediate release, well under 5s
    dispatched_events = [call.args[0] for call in on_mock.call_args_list]
check("a short press does not dispatch touch_longpress", "touch_longpress" not in dispatched_events)

# --- ADDED: touch_longpress is targeted via plugins.one for a button with an event_handler, like other events ---
p = make_plugin(longpress_seconds=0.05)
button7 = mod.Touch_Button(position=(0, 0, 10, 10), momentary=False, state=False, event_handler="some_other_plugin")
fake_state7 = mock.Mock()
fake_state7._state = {"mybutton": button7}
fake_state7._changes = {}
fake_view7 = mock.Mock()
fake_view7._state = fake_state7
p._view = fake_view7
with mock.patch("touch_ui_ng.plugins.one") as one_mock, mock.patch("touch_ui_ng.plugins.on") as on_mock:
    p.process_touch([5, 5], 100)
    time.sleep(0.08)
    p.process_touch([5, 5], 0)
    dispatched_one_events = [call.args[1] for call in one_mock.call_args_list]
check("touch_longpress is targeted via plugins.one when event_handler is set", "touch_longpress" in dispatched_one_events)
check("touch_longpress does not also broadcast via plugins.on for a targeted button", not on_mock.called)


print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
else:
    print("All tests passed.")
