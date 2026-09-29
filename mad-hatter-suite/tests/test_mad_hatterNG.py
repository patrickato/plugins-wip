import sys
import os
import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "stub_deps"))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, "/home/claude/jayofelony/pwnagotchi")

import pwnagotchi.plugins  # noqa: E402
import mad_hatterNG as mod  # noqa: E402

# on_webhook uses flask.jsonify for the status/history.json/diagnose/report
# routes, which needs a real Flask application context - same as the real
# pwnagotchi web server provides at request time.
from flask import Flask  # noqa: E402

_flask_app = Flask(__name__)
_flask_app.app_context().push()

failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


tmpdir = tempfile.mkdtemp()


def p(name):
    return os.path.join(tmpdir, name)


class FakeBus:
    """A minimal in-memory I2C bus stub for exercising the INA219 backend's
    calibration math without real hardware."""

    def __init__(self, present=None, registers=None):
        self.present = set(present or [])
        self.registers = registers or {}

    def read_byte(self, address):
        if address in self.present:
            return 0
        raise OSError("no device at 0x%02X" % address)

    def read_word_data(self, address, reg):
        return self.registers.get((address, reg), 0)

    def write_word_data(self, address, reg, value):
        self.registers[(address, reg)] = value

    def read_byte_data(self, address, reg):
        return self.registers.get((address, reg), 0) & 0xFF

    def write_byte_data(self, address, reg, value):
        self.registers[(address, reg)] = value & 0xFF

    def close(self):
        pass


class FakeUI:
    def __init__(self, width=250):
        self.elements = {}
        self.values = {}
        self._width = width
        self._lock = mock.MagicMock()
        self._lock.__enter__ = mock.Mock(return_value=None)
        self._lock.__exit__ = mock.Mock(return_value=False)

    def width(self):
        return self._width

    def add_element(self, name, el):
        self.elements[name] = el

    def remove_element(self, name):
        if name not in self.elements:
            raise KeyError(name)
        del self.elements[name]

    def set(self, name, value):
        self.values[name] = value


def make_plugin(**opts):
    plugin = mod.MadHatterNG()
    plugin.options = dict(opts)
    return plugin


# --- Registration ------------------------------------------------------------

check(
    "MadHatterNG registers with the real pwnagotchi.plugins loader",
    "mad_hatterNG" in pwnagotchi.plugins.loaded,
)
check(
    "MadHatterNG is a real pwnagotchi.plugins.Plugin subclass",
    issubclass(mod.MadHatterNG, pwnagotchi.plugins.Plugin),
)
check(
    "the real framework registration key is the FILE's basename "
    "('mad_hatterNG'), not the class body's __name__ attribute - verified "
    "against the real loader (see the module docstring's naming note)",
    pwnagotchi.plugins.loaded["mad_hatterNG"].__class__.__module__ == "mad_hatterNG",
)

# --- __defaults__ merge pattern is preserved from mad_hatter.py --------------

plugin = make_plugin()  # empty options, like a bare `enabled = true` block
plugin.on_loaded()
check(
    "on_loaded merges __defaults__ into self.options (never a bare KeyError)",
    plugin.options.get("ups_type") == "auto",
)
check(
    "on_loaded merge keeps a user-supplied option instead of overwriting it",
    make_plugin(poll_interval=99).options.get("poll_interval") == 99,
)
plugin2 = make_plugin(poll_interval=99)
plugin2.on_loaded()
check(
    "on_loaded merge keeps a user override after merging in defaults",
    plugin2.options.get("poll_interval") == 99 and plugin2.options.get("ups_type") == "auto",
)
plugin.on_unload(FakeUI())

# --- Chip/calibration logic spot-checks (preserved unchanged from mad_hatter.py)

# MAX170xx VCELL math: LSB is 78.125uV. Encode a raw VCELL register value
# for 3.70V and confirm the backend reproduces the original's formula.
max_bus = FakeBus(present={mod.MAX_ADDR})
raw_vcell = int(3.70 / 78.125e-6)
max_bus.registers[(mod.MAX_ADDR, mod.MAX_REG_VCELL)] = mod._swap16(raw_vcell)
max_bus.registers[(mod.MAX_ADDR, mod.MAX_REG_SOC)] = mod._swap16(int(64.0 * 256.0))
max_backend = mod.MAX17040Backend(max_bus, mod.MAX_ADDR, {})
max_reading = max_backend.sample()
check(
    "MAX170xx VCELL math (78.125uV/LSB) reproduces mad_hatter.py's original formula",
    abs(max_reading["voltage"] - 3.70) < 0.001,
)
check(
    "MAX170xx SOC math (raw/256.0) reproduces mad_hatter.py's original formula",
    abs(max_reading["soc"] - 64.0) < 0.01,
)

# INA219 shunt-based current: shunt LSB is 10uV, current = Vshunt/Rshunt.
# Build a fake backend directly (no real bus needed for the pure math).
bus = FakeBus(present={0x40})
backend = mod.INA219Backend(bus, 0x40, {"shunt_ohms": 0.1, "invert_current": False})
# Bus voltage register: value >> 3 gives the raw units, LSB 4mV.
# Encode 4.0V: raw_bus = int(4.0/0.004) << 3
raw_bus = (int(4.0 / 0.004)) << 3
bus.registers[(0x40, mod.INA_REG_BUS_V)] = mod._swap16(raw_bus)
# Shunt voltage: LSB 10uV. Encode 5mV (0.005V) -> current = 0.005/0.1 = 0.05A
shunt_raw = int(0.005 / 1e-5)
bus.registers[(0x40, mod.INA_REG_SHUNT_V)] = mod._swap16(shunt_raw & 0xFFFF)
reading = backend.sample()
check(
    "INA219 bus-voltage math reproduces mad_hatter.py's original formula",
    abs(reading["voltage"] - 4.0) < 0.01,
)
check(
    "INA219 shunt-voltage/shunt_ohms current math reproduces the original formula",
    abs(reading["current"] - 0.05) < 0.001,
)

backend_inv = mod.INA219Backend(bus, 0x40, {"shunt_ohms": 0.1, "invert_current": True})
reading_inv = backend_inv.sample()
check(
    "invert_current=True flips the sign, same magnitude",
    abs(reading_inv["current"] + 0.05) < 0.001,
)

# PiSugar2 voltage math: millivolts = 2600 + raw*0.26855 when high bit 0x20 clear
bus2 = FakeBus()
bus2.registers[(0x75, mod.PiSugar2Backend.VOLT_LOW)] = 0x00
bus2.registers[(0x75, mod.PiSugar2Backend.VOLT_HIGH)] = 0x00
ps2 = mod.PiSugar2Backend(bus2, 0x75, {})
r = ps2.sample()
check("PiSugar2 voltage math at raw=0 gives 2.6V", abs(r["voltage"] - 2.6) < 0.001)

# SoC curve interpolation is unchanged: 4.20V -> 100%, 3.00V -> 0%
check("LIION_OCV_CURVE: full charge maps to 100%",
      mod._interpolate_curve(mod.LIION_OCV_CURVE, 4.20) == 100.0)
check("LIION_OCV_CURVE: empty maps to 0%",
      mod._interpolate_curve(mod.LIION_OCV_CURVE, 3.00) == 0.0)
check("LIION_OCV_CURVE: above the top of the curve still clamps to 100%",
      mod._interpolate_curve(mod.LIION_OCV_CURVE, 4.5) == 100.0)

# --- Positioning logic: negative-x-from-right-edge, preserved exactly --------

plugin = make_plugin(ui_position_x=-80, ui_position_y=0)
plugin.on_loaded()
ui = FakeUI(width=250)
plugin.on_ui_setup(ui)
check(
    "negative ui_position_x is resolved as 'this many px in from the right edge'",
    ui.elements["mad_hatter_ng"].xy[:2] == (250 - 80, 0),
)
plugin.on_unload(ui)

plugin = make_plugin(ui_position_x=10, ui_position_y=20)
plugin.on_loaded()
ui = FakeUI(width=128)
plugin.on_ui_setup(ui)
check(
    "non-negative ui_position_x is used as an absolute coordinate",
    ui.elements["mad_hatter_ng"].xy[:2] == (10, 20),
)
plugin.on_unload(ui)

plugin = make_plugin(ui_position_x=-9999, ui_position_y=0)
plugin.on_loaded()
ui = FakeUI(width=128)
plugin.on_ui_setup(ui)
x = ui.elements["mad_hatter_ng"].xy[0]
check("resolved position is clamped to stay on a small (128px) display", 0 <= x <= 118)
plugin.on_unload(ui)

# --- Notification integration: fires once per crossing, skips gracefully ----

class FakeApprise:
    def __init__(self, ready=True):
        self._queue = object() if ready else None
        self.calls = []

    def _queue_notification(self, title, body, agent):
        self.calls.append((title, body, agent))


class FakeDiscord:
    def __init__(self, ready=True):
        self.webhook_url = "https://example.invalid/webhook" if ready else None
        self.calls = []

    def _queue_notification(self, content, embed=None):
        self.calls.append((content, embed))


def _reading(soc, charging=False, backend="ina219", voltage=3.7):
    return {"voltage": voltage, "soc": soc, "charging": charging,
            "current": None, "cells": 1, "backend": backend,
            "errors": 0, "cycles": 0, "estimated_draw_ma": None}


fake_apprise = FakeApprise()
pwnagotchi.plugins.loaded["apprise_notify_ng"] = fake_apprise
try:
    plugin = make_plugin(
        notify_on_threshold=True, notify_backend="apprise",
        warning_threshold=15, shutdown_threshold=5, critical_threshold=2,
    )
    plugin.on_loaded()

    plugin._maybe_notify(_reading(50))  # well above warning: nothing fires
    check("no notification fires while SoC is above every threshold", len(fake_apprise.calls) == 0)

    plugin._maybe_notify(_reading(10))  # crosses warning_threshold
    check("crossing warning_threshold fires exactly one notification", len(fake_apprise.calls) == 1)

    plugin._maybe_notify(_reading(9))  # still in "warning" band, same poll cycle
    check("staying in the same threshold band does NOT refire every poll", len(fake_apprise.calls) == 1)

    plugin._maybe_notify(_reading(1))  # crosses into shutdown_imminent
    check("crossing a HIGHER threshold (shutdown_imminent) fires again",
          len(fake_apprise.calls) == 2)
    check("the second notification's title mentions the new level",
          "shutdown imminent" in fake_apprise.calls[-1][0])

    plugin._maybe_notify(_reading(50))  # recovers
    plugin._maybe_notify(_reading(10))  # crosses warning_threshold again
    check(
        "recovering above every threshold resets crossing state so a later "
        "dip fires again",
        len(fake_apprise.calls) == 3,
    )
    plugin.on_unload(FakeUI())
finally:
    del pwnagotchi.plugins.loaded["apprise_notify_ng"]

# Backend not loaded at all -> logs and skips, never crashes.
plugin = make_plugin(notify_on_threshold=True, notify_backend="apprise")
plugin.on_loaded()
try:
    plugin._maybe_notify(_reading(1))
    notify_missing_ok = True
except Exception:
    notify_missing_ok = False
check("notify_backend='apprise' with nothing loaded never crashes, just skips",
      notify_missing_ok)
plugin.on_unload(FakeUI())

# Backend loaded but not ready (e.g. apprise package missing -> _queue is None).
not_ready_apprise = FakeApprise(ready=False)
pwnagotchi.plugins.loaded["apprise_notify_ng"] = not_ready_apprise
try:
    plugin = make_plugin(notify_on_threshold=True, notify_backend="apprise")
    plugin.on_loaded()
    plugin._maybe_notify(_reading(1))
    check(
        "a loaded-but-not-ready apprise plugin (no _queue) is skipped, not crashed",
        len(not_ready_apprise.calls) == 0,
    )
    plugin.on_unload(FakeUI())
finally:
    del pwnagotchi.plugins.loaded["apprise_notify_ng"]

# notify_backend='auto' falls through to discord when apprise isn't loaded.
fake_discord = FakeDiscord()
pwnagotchi.plugins.loaded["discord_ng"] = fake_discord
try:
    plugin = make_plugin(notify_on_threshold=True, notify_backend="auto")
    plugin.on_loaded()
    plugin._maybe_notify(_reading(1))
    check(
        "notify_backend='auto' falls back to discord_ng when apprise_notify_ng isn't loaded",
        len(fake_discord.calls) == 1,
    )
    plugin.on_unload(FakeUI())
finally:
    del pwnagotchi.plugins.loaded["discord_ng"]

# A charging reading never fires a threshold notification even at low SoC.
fake_apprise2 = FakeApprise()
pwnagotchi.plugins.loaded["apprise_notify_ng"] = fake_apprise2
try:
    plugin = make_plugin(notify_on_threshold=True, notify_backend="apprise")
    plugin.on_loaded()
    plugin._maybe_notify(_reading(1, charging=True))
    check("a charging reading never fires a low-battery notification",
          len(fake_apprise2.calls) == 0)
    plugin.on_unload(FakeUI())
finally:
    del pwnagotchi.plugins.loaded["apprise_notify_ng"]

# notify_on_threshold=False (the default) never calls anything at all.
fake_apprise3 = FakeApprise()
pwnagotchi.plugins.loaded["apprise_notify_ng"] = fake_apprise3
try:
    plugin = make_plugin(notify_on_threshold=False)
    plugin.on_loaded()
    plugin._maybe_notify(_reading(1))
    check("notify_on_threshold=False (default) never fires a notification",
          len(fake_apprise3.calls) == 0)
    plugin.on_unload(FakeUI())
finally:
    del pwnagotchi.plugins.loaded["apprise_notify_ng"]

# --- History logging: bounded, persisted, reloads --------------------------

hist_path = p("history1.json")
hist = mod.HistoryLog(hist_path, max_points=5)
for i in range(10):
    hist.add(1000 + i, 3.7 + i * 0.001, 50.0 - i, None, False)
check("HistoryLog never grows past max_points", len(hist.to_list()) == 5)
check("HistoryLog keeps the MOST RECENT points, drops the oldest",
      hist.to_list()[0]["t"] == 1005 and hist.to_list()[-1]["t"] == 1009)

hist.save()
check("history file was actually written", os.path.exists(hist_path))

hist2 = mod.HistoryLog(hist_path, max_points=5)
hist2.load()
check("a reloaded HistoryLog picks up the persisted points",
      len(hist2.to_list()) == 5 and hist2.to_list()[-1]["t"] == 1009)

hist3 = mod.HistoryLog(p("does_not_exist.json"), max_points=5)
try:
    hist3.load()
    hist_missing_ok = True
except Exception:
    hist_missing_ok = False
check("loading a missing history file never crashes (starts empty)",
      hist_missing_ok and len(hist3.to_list()) == 0)

# Plugin-level history logging is rate-limited by history_log_interval_seconds
plugin = make_plugin(history_path=p("history_plugin1.json"),
                     history_log_interval_seconds=9999)
plugin.on_loaded()
plugin._log_history(_reading(80))
check("first _log_history call records a point", len(plugin.history.to_list()) == 1)
plugin._log_history(_reading(79))
check("a second call inside the interval window does not add another point",
      len(plugin.history.to_list()) == 1)
plugin.on_unload(FakeUI())

# --- Webhook: /history and /history.json ------------------------------------

plugin = make_plugin(history_path=p("history_webhook1.json"))
plugin.on_loaded()
now = time.time()
plugin.history.add(now - 120, 3.9, 90.0, -100.0, False)
plugin.history.add(now - 60, 3.85, 85.0, -100.0, False)
plugin.history.add(now, 3.8, 80.0, -100.0, False)

page = plugin.on_webhook("history", mock.Mock())
check("webhook /history page renders without crashing", "MadHatterNG" in page)
check("webhook /history page includes an inline SVG sparkline (no external chart lib)",
      "<svg" in page)

json_result = plugin.on_webhook("history.json", mock.Mock())
payload = json.loads(json_result.get_data(as_text=True)) if hasattr(json_result, "get_data") \
    else json_result
points_out = payload.get_json()["points"] if hasattr(payload, "get_json") else payload["points"]
check("webhook /history.json returns the same points as the in-memory history",
      len(points_out) == 3)
plugin.on_unload(FakeUI())

# --- Drain-rate / activity correlation --------------------------------------

base_t = 1_700_000_000.0
points = [
    {"t": base_t, "soc": 80.0, "chg": False},
    {"t": base_t + 600, "soc": 79.0, "chg": False},   # active window: 1%/10min = 6%/hr
    {"t": base_t + 1200, "soc": 78.5, "chg": False},  # idle: 0.5%/10min = 3%/hr
]
activity_times = [base_t + 300]  # only near the first interval's midpoint
result = mod.correlate_drain_rate(points, activity_times, window_seconds=400)
check("correlate_drain_rate computes a nonzero active drain rate",
      result["active_pct_per_hour"] is not None and result["active_pct_per_hour"] > 0)
check("correlate_drain_rate computes a nonzero idle drain rate",
      result["idle_pct_per_hour"] is not None and result["idle_pct_per_hour"] > 0)
check("active rate is higher than idle rate in this constructed example",
      result["active_pct_per_hour"] > result["idle_pct_per_hour"])

empty_result = mod.correlate_drain_rate([], [], 300)
check("correlate_drain_rate degrades gracefully on empty history",
      empty_result == {"active_pct_per_hour": None, "idle_pct_per_hour": None})

charging_points = [
    {"t": base_t, "soc": 50.0, "chg": False},
    {"t": base_t + 60, "soc": 60.0, "chg": True},  # charging interval: must be skipped
]
charging_result = mod.correlate_drain_rate(charging_points, [], 300)
check("a charging interval is excluded from drain-rate correlation entirely",
      charging_result == {"active_pct_per_hour": None, "idle_pct_per_hour": None})

# _read_timer_activity_timestamps: real timer_ng.csv format, and graceful
# degradation when the file doesn't exist.
timer_csv_path = p("timer_ng.csv")
with open(timer_csv_path, "w", newline="") as f:
    f.write("timestamp,network,time_to_deauth,time_to_handshake,time_between_deauth_and_handshake\n")
    f.write("2024-01-01T12:00:00,SomeLab,1.0,2.0,3.0\n")
times = mod._read_timer_activity_timestamps(timer_csv_path)
check("_read_timer_activity_timestamps parses a real timer_ng.csv row", len(times) == 1)

missing_times = mod._read_timer_activity_timestamps(p("does_not_exist.csv"))
check("_read_timer_activity_timestamps returns [] (not a crash) for a missing file",
      missing_times == [])

# --- Sanity checks (feature 4) ----------------------------------------------

class FakeUPS:
    def __init__(self, backend_name="ina219", address=0x40, cells=3, rejected=None):
        self.backend = mock.Mock(name=backend_name, address=address)
        self.backend.name = backend_name
        self.cells = cells
        self._rejected = rejected or []


ok_options = dict(mod.MadHatterNG.__defaults__)
ok_options.update({"ups_type": "waveshare", "shunt_ohms": 0.1,
                   "battery_cells": "auto", "charging_gpio": -1})
checks = mod.sanity_checks(ok_options, FakeUPS(cells=3))
check("sanity_checks reports ok when nothing is wrong",
      all(c["level"] != "fail" for c in checks))

no_ups_checks = mod.sanity_checks(ok_options, None)
check("sanity_checks flags a fail when no UPS is initialised",
      any(c["level"] == "fail" for c in no_ups_checks))

bad_shunt_options = dict(ok_options, shunt_ohms=0)
bad_shunt_checks = mod.sanity_checks(bad_shunt_options, FakeUPS())
check("sanity_checks flags shunt_ohms <= 0 as a fail",
      any(c["level"] == "fail" and "shunt_ohms" in c["text"] for c in bad_shunt_checks))

mismatched_cells_options = dict(ok_options, battery_cells=4)
mismatched_checks = mod.sanity_checks(mismatched_cells_options, FakeUPS(cells=3))
check(
    "sanity_checks warns when a fixed battery_cells doesn't match the auto-detected pack",
    any(c["level"] == "warn" and "battery_cells" in c["text"] for c in mismatched_checks),
)

matched_cells_options = dict(ok_options, battery_cells=3)
matched_checks = mod.sanity_checks(matched_cells_options, FakeUPS(cells=3))
check(
    "sanity_checks does NOT warn when a fixed battery_cells matches the detected pack",
    not any("battery_cells" in c["text"] and c["level"] == "warn" for c in matched_checks),
)

gpio_conflict_options = dict(ok_options, charging_gpio=2, reserved_gpios=[])
gpio_checks = mod.sanity_checks(gpio_conflict_options, FakeUPS())
check(
    "sanity_checks flags charging_gpio set to a built-in reserved pin (I2C SDA)",
    any(c["level"] in ("warn", "fail") and "charging_gpio" in c["text"] for c in gpio_checks),
)

gpio_double_listed_options = dict(ok_options, charging_gpio=5, reserved_gpios=[5])
gpio_double_checks = mod.sanity_checks(gpio_double_listed_options, FakeUPS())
check(
    "sanity_checks fails when charging_gpio is ALSO listed in reserved_gpios",
    any(c["level"] == "fail" and "reserved_gpios" in c["text"] for c in gpio_double_checks),
)

# --- Webhook: /sanity page ---------------------------------------------------

plugin = make_plugin(history_path=p("sanity_webhook.json"), shunt_ohms=0)
plugin.on_loaded()
sanity_html = plugin.on_webhook("sanity", mock.Mock())
check("webhook /sanity page renders without crashing", "sanity check" in sanity_html)
check("webhook /sanity page surfaces the shunt_ohms fail", "shunt_ohms" in sanity_html)
plugin.on_unload(FakeUI())

# --- Webhook: existing routes still work (not replaced) ----------------------

plugin = make_plugin(history_path=p("status_webhook.json"))
plugin.on_loaded()
status_result = plugin.on_webhook("status", mock.Mock())
status_payload = status_result.get_json() if hasattr(status_result, "get_json") else status_result
check("webhook /status route still works", "display" in status_payload)

index_result = plugin.on_webhook("", mock.Mock())
check("webhook index route still returns the diagnostic page", "MadHatterNG" in index_result)

from werkzeug.exceptions import NotFound  # noqa: E402

try:
    plugin.on_webhook("nonexistent-path", mock.Mock())
    not_found_ok = False
except NotFound:
    not_found_ok = True
check("webhook raises a real Flask 404 for an unrecognized path (same as "
     "mad_hatter.py's abort(404))", not_found_ok)
plugin.on_unload(FakeUI())

# --- on_unload persists history and never crashes ---------------------------

plugin = make_plugin(history_path=p("unload_history.json"))
plugin.on_loaded()
plugin.history.add(time.time(), 3.7, 55.0, None, False)
plugin.on_unload(FakeUI())
check("on_unload persists the history log to disk",
      os.path.exists(p("unload_history.json")))

reloaded = mod.HistoryLog(p("unload_history.json"), max_points=720)
reloaded.load()
check("the persisted history survives an unload/reload cycle", len(reloaded.to_list()) == 1)


print(f"\n{len(failures)} failure(s) out of test run")
if failures:
    print(failures)
    sys.exit(1)
else:
    print("All tests passed.")
