import sys
import os
import subprocess
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import fortune_cookie_ng as mod  # noqa: E402

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def make_plugin(**opts):
    p = mod.FortuneCookieNG()
    p.options = dict(opts)
    return p


def make_fake_ui(**flags):
    ui = mock.Mock()
    ui._lock = mock.MagicMock()
    for name in [
        "is_waveshare_v2", "is_waveshare_v3", "is_waveshare_v1",
        "is_waveshare144lcd", "is_inky", "is_waveshare27inch",
    ]:
        setattr(ui, name, mock.Mock(return_value=flags.get(name, False)))
    return ui


# --- Registration ------------------------------------------------------

check(
    "FortuneCookieNG registers as a real pwnagotchi.plugins.Plugin subclass",
    issubclass(mod.FortuneCookieNG, pwnagotchi.plugins.Plugin),
)

# --- Empty options dict (the original's exact crash scenario) doesn't
#     KeyError in on_ui_setup or on_ui_update ---------------------------

p = make_plugin()
ui = make_fake_ui()
try:
    p.on_ui_setup(ui)
    setup_ok = True
except KeyError:
    setup_ok = False
check("on_ui_setup runs without KeyError on a completely empty options dict", setup_ok)

try:
    p.on_ui_update(ui)
    update_ok = True
except KeyError:
    update_ok = False
check("on_ui_update runs without KeyError on a completely empty options dict", update_ok)

# --- orientation genuinely changes the built element (previously a no-op) --

ui_v = make_fake_ui()
p_v = make_plugin(orientation="vertical")
p_v.on_ui_setup(ui_v)
vertical_kwargs = ui_v.add_element.call_args[0][1]

ui_h = make_fake_ui()
p_h = make_plugin(orientation="horizontal")
p_h.on_ui_setup(ui_h)
horizontal_kwargs = ui_h.add_element.call_args[0][1]

check(
    "vertical orientation gets a real label",
    vertical_kwargs.label == "Fortune:",
)
check(
    "horizontal orientation has no separate label",
    horizontal_kwargs.label == "",
)
check(
    "vertical and horizontal now genuinely build different elements (orientation has a real effect)",
    vertical_kwargs.label != horizontal_kwargs.label,
)

# --- default orientation (missing from options) behaves like horizontal ----

ui_default = make_fake_ui()
p_default = make_plugin()
p_default.on_ui_setup(ui_default)
default_kwargs = ui_default.add_element.call_args[0][1]
check("default orientation (unset) behaves like horizontal", default_kwargs.label == "")

# --- on_unload removes the element without crashing -------------------

p2 = make_plugin()
ui2 = make_fake_ui()
p2.on_unload(ui2)
check("on_unload removes the fortune-cookie element", ui2.remove_element.call_args[0] == ("fortune-cookie",))

# --- enabled=False shows the disabled message --------------------------

p3 = make_plugin(enabled=False)
ui3 = mock.Mock()
p3.on_ui_update(ui3)
check(
    "enabled=False shows the disabled message",
    ui3.set.call_args[0] == ("fortune-cookie", "Fortune Cookie Plugin is disabled"),
)

# --- fortune selection uses only the configured list when fortune_command
#     is unset ----------------------------------------------------------

custom_fortunes = ["Only fortune A", "Only fortune B"]
p4 = make_plugin(enabled=True, fortunes=custom_fortunes, fortune_command="")
fortune = p4._get_fortune()
check("with no fortune_command, the fortune comes from the configured list", fortune in custom_fortunes)

# --- empty fortunes list falls back to DEFAULT_FORTUNES, no crash ------

p5 = make_plugin(enabled=True, fortunes=[], fortune_command="")
try:
    fortune5 = p5._get_fortune()
    ok5 = True
except IndexError:
    ok5 = False
check("an empty configured fortunes list falls back to DEFAULT_FORTUNES instead of crashing", ok5)
check("the fallback fortune comes from DEFAULT_FORTUNES", fortune5 in mod.DEFAULT_FORTUNES)

# --- Rotation timing: fortune doesn't change before the interval elapses,
#     and is re-rolled after it does ------------------------------------

p6 = make_plugin(enabled=True, fortunes=["Only one option"], rotate_interval_seconds=1000)
ui6 = mock.Mock()
p6.on_ui_update(ui6)
first_fortune = p6.current_fortune
first_rotate_time = p6._last_rotate

p6.on_ui_update(ui6)  # called again immediately - interval hasn't elapsed
check(
    "fortune does not re-roll (rotation timestamp unchanged) before rotate_interval_seconds elapses",
    p6._last_rotate == first_rotate_time,
)

p6._last_rotate = 0  # simulate the interval having elapsed
with mock.patch("random.choice", return_value="A different fortune entirely"):
    p6.on_ui_update(ui6)
check(
    "fortune re-rolls once rotate_interval_seconds has elapsed",
    p6.current_fortune == "A different fortune entirely",
)

# --- fortune_command: success path returns its stdout -------------------

p7 = make_plugin(enabled=True, fortune_command="fortune")
fake_result = mock.Mock(returncode=0, stdout="A command-sourced fortune.\n", stderr="")
with mock.patch("subprocess.run", return_value=fake_result) as mock_run:
    result = p7._get_fortune()
check("fortune_command success returns the command's stdout, stripped", result == "A command-sourced fortune.")
check("fortune_command is actually invoked via subprocess.run", mock_run.called)

# --- fortune_command: non-zero exit falls back to the list --------------

p8 = make_plugin(enabled=True, fortunes=["List fallback A"], fortune_command="fortune")
fake_fail = mock.Mock(returncode=1, stdout="", stderr="boom")
with mock.patch("subprocess.run", return_value=fake_fail):
    result8 = p8._get_fortune()
check("a non-zero exit from fortune_command falls back to the configured list", result8 == "List fallback A")

# --- fortune_command: TimeoutExpired falls back gracefully --------------

p9 = make_plugin(enabled=True, fortunes=["List fallback B"], fortune_command="fortune")
with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="fortune", timeout=5)):
    try:
        result9 = p9._get_fortune()
        ok9 = True
    except subprocess.TimeoutExpired:
        ok9 = False
check("fortune_command timeout never raises and falls back to the list", ok9 and result9 == "List fallback B")

# --- fortune_command: missing binary (OSError) falls back gracefully ----

p10 = make_plugin(enabled=True, fortunes=["List fallback C"], fortune_command="not-a-real-binary")
with mock.patch("subprocess.run", side_effect=OSError("No such file or directory")):
    try:
        result10 = p10._get_fortune()
        ok10 = True
    except OSError:
        ok10 = False
check("a missing fortune binary (OSError) never crashes and falls back to the list", ok10 and result10 == "List fallback C")

# --- fortune_command: empty stdout falls back to the list ---------------

p11 = make_plugin(enabled=True, fortunes=["List fallback D"], fortune_command="fortune")
fake_empty = mock.Mock(returncode=0, stdout="   \n", stderr="")
with mock.patch("subprocess.run", return_value=fake_empty):
    result11 = p11._get_fortune()
check("empty stdout from fortune_command falls back to the configured list", result11 == "List fallback D")


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    sys.exit(1)
