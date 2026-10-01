"""
MadHatterNG - Universal UPS / battery plugin for Pwnagotchi
=============================================================

Feature-upgrade rebuild of mad_hatter.py (AlienMajik, v2.3.0). This is
NOT a bugfix - the original has no known bugs, and every existing chip
backend, calibration formula, auto-detection rule, and on-screen
positioning behaviour (including the negative-x-from-right-edge
convention) is carried over unchanged. See NOTES.md for the full
rationale.

Naming note: the plugin FILE is named `MadHatterNG.py` (capital NG, no
underscore) per an explicit request. Its config section is
`[main.plugins.MadHatterNG]` - matching the file's exact basename,
NOT snake_case (`mad_hatter_ng`). This is a verified framework fact,
not a stylistic choice: `pwnagotchi/plugins/__init__.py` registers
every plugin under `loaded[<file basename, exact case, ".py" dropped>]`
(`load_from_path`/`load_from_file`/`Plugin.__init_subclass__`'s
`cls.__module__.split('.')[0]`), and `load()` looks up both the
`enabled` flag and the options table in `config['main']['plugins']`
under that SAME key - a class body's `__name__ = "..."` assignment
(as the original mad_hatter.py also had) does NOT override
`type.__name__` in Python and has zero effect on this lookup, confirmed
empirically. A config section spelled `mad_hatter_ng` for a file named
`MadHatterNG.py` would never even appear in the `enabled` list, so the
plugin would silently never load at all - not a merge/options bug, a
total no-load. See NOTES.md for the full writeup, including what to do
if you'd rather use a snake_case config section instead.

Still supports MAX17040/17048 fuel gauges (Geekworm X1200, UPS-Lite),
all INA219-based HATs (Waveshare, Seengreat, SB Components, EP-0136,
...), and the PiSugar 2 / 2 Pro / 3 families, with the same I2C
auto-detection, Kalman-ish charge/drain estimation, and diagnostic
webhook the original shipped.

Four new features on top (all approved - see NOTES.md for the full
design writeup):
  1. Optional Apprise/Discord notifications on a threshold crossing
     (warning -> critical -> shutdown-imminent), fired once per
     crossing rather than every poll.
  2. Bounded, persisted history logging (voltage/SoC/current) with a
     new /history (HTML + inline-SVG sparkline) and /history.json
     webhook page.
  3. Best-effort drain-rate correlation against timer-suite's
     per-handshake CSV (active-epoch vs idle drain rate), shown
     alongside the existing flat mAh/current time estimate.
  4. A /sanity webhook page that cross-checks the configured options
     against what was actually auto-detected on the I2C bus.

Framework facts this rebuild relies on (verified against the cloned
jayofelony/pwnagotchi fork, pwnagotchi/plugins/__init__.py):
  - `plugins.load()` does a RAW assignment of the parsed TOML table
    onto `plugin.options` - `__defaults__` is NEVER merged by the
    framework. This file keeps the original's own defensive pattern:
    `self.options = dict(self.__defaults__)` in `__init__`, then
    `self.options.update(...)`'d with the merged result in `on_loaded`.
  - `pwnagotchi.plugins.loaded` is a plain dict of {plugin_name:
    instance}, keyed by the *module* basename (e.g. "apprise_notify_ng",
    "discord_ng") - this is how the notification integration (feature 1)
    looks up another already-loaded plugin instance. Neither
    apprise_notify_ng.py nor discord_ng.py expose a stable public
    "notify()" method; both funnel through an internal
    `_queue_notification(...)` (with different signatures - see
    `_notify_via_apprise`/`_notify_via_discord` below). Calling a
    leading-underscore method on another plugin is not pretty, but it's
    the only real entry point either one currently has, so every call is
    wrapped in try/except and guarded with getattr/hasattr checks: an
    internal rename in either plugin degrades to a skipped, logged
    notification rather than a crash.
"""

import html
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime

import pwnagotchi
import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK

# ---------------------------------------------------------------------------
# Optional imports - never let a missing library take down the whole daemon.
# ---------------------------------------------------------------------------

SMBus = None
_SMBUS_IMPORT_ERROR = None
try:
    from smbus2 import SMBus  # preferred: pure python, no deprecation issues
except Exception:  # pragma: no cover
    try:
        from smbus import SMBus
    except Exception as exc:  # pragma: no cover
        _SMBUS_IMPORT_ERROR = exc

GPIO = None
try:
    import RPi.GPIO as GPIO  # noqa: N814  (also satisfied by rpi-lgpio on Pi 5)
except Exception:  # pragma: no cover
    GPIO = None

LOG = "[MadHatterNG]"

# ---------------------------------------------------------------------------
# Register maps - unchanged from mad_hatter.py
# ---------------------------------------------------------------------------

MAX_ADDR = 0x36
MAX_REG_VCELL = 0x02
MAX_REG_SOC = 0x04
MAX_REG_MODE = 0x06
MAX_REG_VERSION = 0x08
MAX_REG_CONFIG = 0x0C

INA_ADDRS = (0x40, 0x41, 0x42, 0x43, 0x44, 0x45)
INA_REG_CONFIG = 0x00
INA_REG_SHUNT_V = 0x01
INA_REG_BUS_V = 0x02
INA_REG_CURRENT = 0x04
INA_REG_CALIB = 0x05

# 32V bus range, +/-320mV PGA, 12 bit both ADCs, shunt+bus continuous
INA_CONFIG_VALUE = 0x399F

PISUGAR2_ADDR = 0x75   # IP5209 (PiSugar 2) / IP5312 (PiSugar 2 Pro)
PISUGAR3_ADDR = 0x57   # PiSugar 3 / 3 Plus

# Default charging-detect GPIO (BCM numbering) per board family.
# Deliberately empty - see mad_hatter.py's own note: nothing is claimed
# unless the user names it, because auto-picking a pin that a stacked
# HAT owns is how you stop the daemon booting.
DEFAULT_CHARGING_GPIOS = {}

# Lines that belong to a kernel driver or a display HAT. rpi-lgpio (which
# provides the RPi.GPIO API on Bookworm / Pi 5) goes through /dev/gpiochip and
# will NOT let two owners share a line - so if this plugin claims one of these,
# whoever needs it next fails with lgpio.error: 'GPIO not allocated'.
RESERVED_GPIOS = {
    0: "HAT EEPROM ID_SD", 1: "HAT EEPROM ID_SC",
    2: "I2C1 SDA", 3: "I2C1 SCL",
    7: "SPI0 CE1", 8: "SPI0 CE0", 9: "SPI0 MISO",
    10: "SPI0 MOSI", 11: "SPI0 SCLK",
    14: "UART TX", 15: "UART RX",
}

# Extra lines claimed by specific display HATs, keyed by the pwnagotchi
# ui.display.type string.
DISPLAY_GPIOS = {
    "displayhatmini": {
        9: "Display HAT Mini D/C", 13: "Display HAT Mini backlight",
        5: "Display HAT Mini button A", 6: "Display HAT Mini button B",
        16: "Display HAT Mini button X", 24: "Display HAT Mini button Y",
        17: "Display HAT Mini LED R", 27: "Display HAT Mini LED G",
        22: "Display HAT Mini LED B",
    },
    "waveshare27inch": {17: "e-Paper RST", 25: "e-Paper DC", 24: "e-Paper BUSY"},
    "waveshare29inch": {17: "e-Paper RST", 25: "e-Paper DC", 24: "e-Paper BUSY"},
    "inky": {17: "Inky RST", 22: "Inky BUSY", 27: "Inky DC"},
}

# Resting open-circuit-voltage curve for a single Li-ion / LiPo cell.
# (volts, state-of-charge %). Must be ordered high -> low.
LIION_OCV_CURVE = (
    (4.20, 100.0), (4.15, 95.0), (4.11, 90.0), (4.08, 85.0), (4.02, 80.0),
    (3.98, 75.0), (3.95, 70.0), (3.91, 65.0), (3.87, 60.0), (3.85, 55.0),
    (3.84, 50.0), (3.82, 45.0), (3.80, 40.0), (3.79, 35.0), (3.77, 30.0),
    (3.75, 25.0), (3.73, 20.0), (3.71, 15.0), (3.69, 10.0), (3.61, 5.0),
    (3.40, 2.0), (3.00, 0.0),
)

STATE_FILE = "/root/.mad_hatter_ng_state.json"
# Previous-generation state files, read once (read-only) so switching from
# mad_hatter.py to MadHatterNG.py doesn't reset cycle-count/draw history.
LEGACY_NG_NONE = None
LEGACY_STATE_FILE = "/root/.mad_hatter_state.json"
LEGACY_CYCLE_FILE = "/root/.mad_hatter_cycle_count"

HISTORY_FILE = "/root/.mad_hatter_ng_history.json"


# ---------------------------------------------------------------------------
# Small helpers - unchanged from mad_hatter.py
# ---------------------------------------------------------------------------

def _swap16(value):
    """Convert between the byte order SMBus gives us and the big-endian order
    every one of these chips actually uses on the wire."""
    value &= 0xFFFF
    return ((value & 0xFF) << 8) | (value >> 8)


def _to_signed16(value):
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def _clamp(value, low, high):
    return max(low, min(high, value))


def _as_optional_int(value):
    """TOML has no ``null``. Accept None, -1, '', 'none', 'null', 'auto', 'off'
    as 'not set' so the config can never explode on a literal the user copied
    out of an old README."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in ("", "none", "null", "auto", "off", "-1", "nil"):
            return None
        try:
            value = int(cleaned)
        except ValueError:
            return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return None if value < 0 else value


def _fmt_minutes(minutes):
    minutes = int(round(minutes))
    if minutes <= 0:
        return None
    if minutes >= 6000:          # >100h means our estimate is meaningless
        return None
    if minutes < 60:
        return "%dm" % minutes
    return "%dh%02dm" % (minutes // 60, minutes % 60)


def _interpolate_curve(curve, voltage):
    """Piecewise-linear lookup on a (voltage, soc) curve ordered high -> low."""
    if voltage >= curve[0][0]:
        return 100.0
    if voltage <= curve[-1][0]:
        return 0.0
    for i in range(len(curve) - 1):
        v_hi, s_hi = curve[i]
        v_lo, s_lo = curve[i + 1]
        if v_lo <= voltage <= v_hi:
            span = v_hi - v_lo
            if span <= 0:
                return s_lo
            frac = (voltage - v_lo) / span
            return s_lo + frac * (s_hi - s_lo)
    return 0.0


# Devices we can recognise on the bus but deliberately do NOT drive. Detecting
# them and saying so is far better than staying silent or, worse, misreading
# them through a driver that does not fit.
KNOWN_UNSUPPORTED = {
    0x14: ("PiJuice", "speaks its own MCU protocol; use the pijuice plugin"),
    0x0B: ("LC709203F fuel gauge", "Adafruit boards; not implemented"),
    0x55: ("BQ27441 fuel gauge", "not implemented"),
    0x6B: ("BQ2589x charger", "charger only, no usable gauge"),
}

# Register 0xFE returns 0x5449 ("TI") on every INA22x/INA26x part. The INA219
# has no such register, so this cleanly separates chips that share addresses
# 0x40-0x4F but scale their registers completely differently.
TI_MANUFACTURER_ID = 0x5449
INA_REG_MANUF_ID = 0xFE
INA_REG_DIE_ID = 0xFF

INA_DIE_NAMES = {
    0x2260: "INA226", 0x2270: "INA260", 0x2300: "INA230",
    0x0238: "INA238", 0x0237: "INA237",
}


def identify_ina_variant(bus, address):
    """-> (is_ina219, human_name). Never raises."""
    try:
        raw = bus.read_word_data(address, INA_REG_MANUF_ID)
        manuf = _swap16(raw)
    except Exception:
        return True, "INA219"
    if manuf != TI_MANUFACTURER_ID:
        return True, "INA219"
    try:
        die = _swap16(bus.read_word_data(address, INA_REG_DIE_ID))
    except Exception:
        die = 0
    name = INA_DIE_NAMES.get(die & 0xFFF0 if die > 0x1000 else die)
    if not name:
        name = INA_DIE_NAMES.get(die, "INA22x/INA26x (die 0x%04X)" % die)
    return False, name


class UPSError(Exception):
    """Raised when a backend cannot talk to its chip."""


def _configured_display_type():
    """Ask the running pwnagotchi what display it is driving, so we know which
    lines are already spoken for. Returns '' if we cannot tell."""
    try:
        cfg = getattr(pwnagotchi, "config", None) or {}
        return str(cfg.get("ui", {}).get("display", {}).get("type", "")).lower()
    except Exception:
        return ""


def gpio_conflict(pin, options=None):
    """Return a human-readable reason this BCM pin must not be claimed, or None.

    Users can add their own via `reserved_gpios = [20, 21]`, or override the
    whole check with `allow_unsafe_gpio = true` if they really know the pin is
    free on their build.
    """
    options = options or {}
    if options.get("allow_unsafe_gpio", False):
        return None

    extra = options.get("reserved_gpios") or []
    try:
        if pin in {int(p) for p in extra}:
            return "a pin you listed in reserved_gpios"
    except (TypeError, ValueError):
        pass

    if pin in RESERVED_GPIOS:
        return RESERVED_GPIOS[pin]

    display = _configured_display_type()
    for name, pins in DISPLAY_GPIOS.items():
        if name in display and pin in pins:
            return pins[pin]
    return None


# ---------------------------------------------------------------------------
# Backends - unchanged from mad_hatter.py (calibration math preserved as-is)
# ---------------------------------------------------------------------------

class Backend:
    """Base class. ``sample()`` returns a dict with whatever the chip knows:

        voltage  : pack volts (float)          - always present
        current  : amps, POSITIVE = charging   - None if not measurable
        soc      : 0..100 from a fuel gauge    - None if we must estimate it
        charging : True/False                  - None if not directly known
    """

    name = "generic"
    reports_soc = False
    reports_current = False

    def __init__(self, bus, address, options):
        self.bus = bus
        self.address = address
        self.options = options

    # -- byte-order-correct primitives ------------------------------------
    def _rd_u16(self, reg):
        return _swap16(self.bus.read_word_data(self.address, reg))

    def _wr_u16(self, reg, value):
        self.bus.write_word_data(self.address, reg, _swap16(value))

    def _rd_s16(self, reg):
        return _to_signed16(self._rd_u16(reg))

    def _rd_u8(self, reg):
        return self.bus.read_byte_data(self.address, reg)

    def _wr_u8(self, reg, value):
        self.bus.write_byte_data(self.address, reg, value & 0xFF)

    def setup(self):
        pass

    def sample(self):
        raise NotImplementedError

    def teardown(self):
        pass


class MAX17040Backend(Backend):
    """MAX17040/17041/17048/17049 fuel gauge - Geekworm X1200, UPS-Lite, etc.

    The VCELL LSB is 1.25mV/16 == 78.125uV for the 17040 family and exactly
    78.125uV for the 17048 family, so one formula covers both.
    """

    name = "max170xx"
    reports_soc = True

    def __init__(self, bus, address, options, gpio_pin=None):
        super().__init__(bus, address, options)
        self.gpio_pin = gpio_pin
        self._gpio_active_high = bool(options.get("charging_gpio_active_high", True))

    def setup(self):
        # QuickStart: MODE <- 0x4000.
        try:
            self._wr_u16(MAX_REG_MODE, 0x4000)
            time.sleep(0.5)          # datasheet: first valid SOC after ~500ms
            logging.info("%s MAX170xx quick-start issued", LOG)
        except Exception as exc:
            logging.warning("%s MAX170xx quick-start failed: %s", LOG, exc)

        # CONFIG low byte bits 4:0 = ATHD; alert fires at (32 - ATHD) percent.
        try:
            threshold = _clamp(int(self.options.get("alert_threshold", 10)), 1, 31)
            athd = 32 - threshold
            config = self._rd_u16(MAX_REG_CONFIG)
            config = (config & 0xFFE0) | (athd & 0x1F)
            self._wr_u16(MAX_REG_CONFIG, config)
            logging.debug("%s MAX170xx alert threshold set to %d%%", LOG, threshold)
        except Exception as exc:
            logging.warning("%s MAX170xx alert threshold failed: %s", LOG, exc)

        if self.gpio_pin is not None:
            reason = gpio_conflict(self.gpio_pin, self.options)
            if reason:
                logging.error(
                    "%s REFUSING to claim BCM %d: it belongs to %s. Under rpi-lgpio "
                    "a line can have exactly one owner, and claiming this would stop "
                    "that hardware initialising. Set charging_gpio to a free pin, or "
                    "-1 to disable GPIO charge detection.",
                    LOG, self.gpio_pin, reason)
                self.gpio_pin = None
            elif GPIO is None:
                logging.warning("%s no RPi.GPIO/rpi-lgpio available; ignoring "
                                "charging_gpio %d", LOG, self.gpio_pin)
                self.gpio_pin = None

        if self.gpio_pin is not None and GPIO is not None:
            try:
                GPIO.setwarnings(False)
                try:
                    GPIO.setmode(GPIO.BCM)
                except ValueError:
                    if GPIO.getmode() != GPIO.BCM:
                        raise
                GPIO.setup(self.gpio_pin, GPIO.IN)
                logging.info("%s charging detection on BCM GPIO %d", LOG, self.gpio_pin)
            except Exception as exc:
                logging.warning("%s GPIO %s unavailable (%s); falling back to "
                                "voltage-trend charge detection",
                                LOG, self.gpio_pin, exc)
                self.gpio_pin = None

    def sample(self):
        voltage = self._rd_u16(MAX_REG_VCELL) * 78.125e-6
        soc = _clamp(self._rd_u16(MAX_REG_SOC) / 256.0, 0.0, 100.0)

        charging = None
        if self.gpio_pin is not None and GPIO is not None:
            try:
                level = GPIO.input(self.gpio_pin) == GPIO.HIGH
                charging = level if self._gpio_active_high else (not level)
            except Exception:
                charging = None

        return {"voltage": voltage, "current": None, "soc": soc, "charging": charging}

    def teardown(self):
        if self.gpio_pin is not None and GPIO is not None:
            try:
                GPIO.cleanup(self.gpio_pin)
            except Exception:
                pass


class INA219Backend(Backend):
    """INA219 shunt/bus monitor - Waveshare, Seengreat, SB Components, EP-0136.

    Current is derived from the SHUNT VOLTAGE register rather than the CURRENT
    register. The current register reads zero until the calibration register is
    programmed, and a load spike can silently reset calibration back to zero at
    any time; the shunt register has no such dependency.
    """

    name = "ina219"
    reports_current = True

    def __init__(self, bus, address, options):
        super().__init__(bus, address, options)
        self.shunt_ohms = float(options.get("shunt_ohms", 0.1)) or 0.1
        self.sign = -1.0 if options.get("invert_current", False) else 1.0

    def setup(self):
        is_219, name = identify_ina_variant(self.bus, self.address)
        if not is_219:
            raise UPSError(
                "0x%02X is a %s, not an INA219. Its registers scale "
                "differently (bus LSB 1.25mV with no shift vs 4mV shifted by "
                "3), so reading it with INA219 maths gives a wrong but "
                "plausible-looking voltage. Refusing rather than lying."
                % (self.address, name))

        self._wr_u16(INA_REG_CONFIG, INA_CONFIG_VALUE)

        current_lsb = 0.0001                       # 100uA per bit
        cal = int(0.04096 / (current_lsb * self.shunt_ohms))
        while cal > 0xFFFE:
            current_lsb *= 2
            cal = int(0.04096 / (current_lsb * self.shunt_ohms))
        try:
            self._wr_u16(INA_REG_CALIB, max(cal, 1))
        except Exception as exc:
            logging.debug("%s INA219 calibration write failed: %s", LOG, exc)

        logging.info("%s INA219 at 0x%02X configured (shunt %.3f ohm)",
                     LOG, self.address, self.shunt_ohms)

    def sample(self):
        raw_bus = self._rd_u16(INA_REG_BUS_V)
        voltage = (raw_bus >> 3) * 0.004

        shunt_v = self._rd_s16(INA_REG_SHUNT_V) * 1e-5
        current = (shunt_v / self.shunt_ohms) * self.sign

        return {"voltage": voltage, "current": current, "soc": None, "charging": None}


class PiSugar2Backend(Backend):
    """PiSugar 2 (IP5209) at 0x75. Voltage lives at 0xA2/0xA3, NOT 0x22/0x23 -
    those belong to the PiSugar 3, which sits at a completely different
    address."""

    name = "pisugar2"
    VOLT_LOW, VOLT_HIGH = 0xA2, 0xA3
    CHARGE_REG, CHARGE_BIT = 0x55, 0x10

    def sample(self):
        low = self._rd_u8(self.VOLT_LOW)
        high = self._rd_u8(self.VOLT_HIGH)
        if high & 0x20:
            low = (~low) & 0xFF
            high = (~high) & 0x1F
            millivolts = 2600.0 - (((high << 8) + low) + 1) * 0.26855
        else:
            millivolts = 2600.0 + (((high & 0x1F) << 8) + low) * 0.26855

        charging = None
        try:
            charging = bool(self._rd_u8(self.CHARGE_REG) & self.CHARGE_BIT)
        except Exception:
            pass

        return {"voltage": millivolts / 1000.0, "current": None,
                "soc": None, "charging": charging}


class PiSugar2ProBackend(PiSugar2Backend):
    """PiSugar 2 Pro (IP5312), also at 0x75 but with a different register map."""

    name = "pisugar2_pro"
    VOLT_LOW, VOLT_HIGH = 0x64, 0x65
    CHARGE_REG, CHARGE_BIT = 0x58, 0x10

    def sample(self):
        low = self._rd_u8(self.VOLT_LOW)
        high = self._rd_u8(self.VOLT_HIGH)
        millivolts = (((high & 0x1F) << 8) + low) * 0.26855 + 2600.0

        charging = None
        try:
            charging = bool(self._rd_u8(self.CHARGE_REG) & self.CHARGE_BIT)
        except Exception:
            pass

        return {"voltage": millivolts / 1000.0, "current": None,
                "soc": None, "charging": charging}


class PiSugar3Backend(Backend):
    """PiSugar 3 / 3 Plus at 0x57."""

    name = "pisugar3"

    def sample(self):
        high = self._rd_u8(0x22)
        low = self._rd_u8(0x23)
        voltage = ((high << 8) | low) / 1000.0

        charging = None
        try:
            charging = bool(self._rd_u8(0x02) & (1 << 7))
        except Exception:
            pass

        return {"voltage": voltage, "current": None, "soc": None, "charging": charging}


# ---------------------------------------------------------------------------
# Estimation helpers - unchanged from mad_hatter.py
# ---------------------------------------------------------------------------

class ChargeDetector:
    """Decides charging vs discharging with hysteresis so noise around zero
    cannot make the icon flicker every refresh."""

    def __init__(self, threshold_ma=30.0):
        self.threshold = threshold_ma / 1000.0
        self.state = False
        self._voltage_ema = None
        self._last_trend_check = 0.0
        self._trend_reference = None

    def update(self, voltage, current, hint):
        if hint is not None:
            self.state = bool(hint)
            return self.state

        if current is not None:
            if current > self.threshold:
                self.state = True
            elif current < -self.threshold:
                self.state = False
            return self.state

        now = time.monotonic()
        if self._voltage_ema is None:
            self._voltage_ema = voltage
            self._trend_reference = voltage
            self._last_trend_check = now
        else:
            self._voltage_ema += 0.2 * (voltage - self._voltage_ema)

        if now - self._last_trend_check >= 120:
            delta = self._voltage_ema - self._trend_reference
            if delta > 0.008:
                self.state = True
            elif delta < -0.008:
                self.state = False
            self._trend_reference = self._voltage_ema
            self._last_trend_check = now

        return self.state


class SocEstimator:
    """Voltage-based state of charge with IR compensation and smoothing."""

    def __init__(self, internal_resistance=0.12, smoothing=0.25):
        self.internal_resistance = max(0.0, float(internal_resistance))
        self.smoothing = _clamp(float(smoothing), 0.01, 1.0)
        self.value = None

    def update(self, cell_voltage, cell_current):
        ocv = cell_voltage
        if cell_current is not None and self.internal_resistance > 0:
            ocv = cell_voltage - (cell_current * self.internal_resistance)

        raw = _interpolate_curve(LIION_OCV_CURVE, ocv)
        if self.value is None:
            self.value = raw
        else:
            self.value += self.smoothing * (raw - self.value)
        return _clamp(self.value, 0.0, 100.0)


class DrawEstimator:
    """Estimate average current from how fast a real SOC gauge falls."""

    MIN_SPAN_S = 900
    MIN_DROP_PCT = 1.5
    MAX_GAP_S = 3600
    MAX_AGE_S = 6 * 3600
    MAX_POINTS = 360
    SAMPLE_EVERY_S = 60
    SANE_MA = (15.0, 6000.0)

    def __init__(self, capacity_mah, samples=None, value_ma=None):
        self.capacity_mah = max(1.0, float(capacity_mah))
        self.samples = [list(s) for s in (samples or [])]
        self.value_ma = value_ma
        self.confident = False

    def update(self, soc, charging, now=None):
        now = time.time() if now is None else now

        if charging:
            self.samples = []
            self.confident = False
            return self.value_ma

        if self.samples:
            last = self.samples[-1][0]
            if now < last or now - last > self.MAX_GAP_S:
                self.samples = []

        if not self.samples or now - self.samples[-1][0] >= self.SAMPLE_EVERY_S:
            self.samples.append([now, float(soc)])

        self.samples = [s for s in self.samples
                        if now - s[0] <= self.MAX_AGE_S][-self.MAX_POINTS:]
        self._fit()
        return self.value_ma

    def _fit(self):
        self.confident = False
        if len(self.samples) < 5:
            return
        span = self.samples[-1][0] - self.samples[0][0]
        drop = self.samples[0][1] - self.samples[-1][1]
        if span < self.MIN_SPAN_S or drop < self.MIN_DROP_PCT:
            return

        times = [s[0] for s in self.samples]
        socs = [s[1] for s in self.samples]
        mean_t = sum(times) / len(times)
        mean_s = sum(socs) / len(socs)
        denom = sum((t - mean_t) ** 2 for t in times)
        if denom <= 0:
            return
        slope = sum((t - mean_t) * (s - mean_s)
                    for t, s in zip(times, socs)) / denom
        pct_per_hour = -slope * 3600.0
        if pct_per_hour <= 0:
            return

        milliamps = pct_per_hour / 100.0 * self.capacity_mah
        low, high = self.SANE_MA
        if not (low <= milliamps <= high):
            return

        if self.value_ma is None:
            self.value_ma = milliamps
        else:
            self.value_ma += 0.3 * (milliamps - self.value_ma)
        self.confident = True

    def state(self):
        return {"samples": self.samples,
                "value_ma": round(self.value_ma, 1) if self.value_ma else None}


class CycleCounter:
    """Counts real charge cycles: one cycle per battery_mah of cumulative
    discharge."""

    MAX_GAP_SECONDS = 300.0

    def __init__(self, capacity_mah, discharged_mah=0.0, cycles=0):
        self.capacity_mah = max(1.0, float(capacity_mah))
        self.discharged_mah = float(discharged_mah)
        self.cycles = int(cycles)
        self._last_time = None

    def update(self, current_a):
        now = time.monotonic()
        previous, self._last_time = self._last_time, now
        if previous is None or current_a is None or current_a >= 0:
            return

        elapsed = now - previous
        if elapsed <= 0 or elapsed > self.MAX_GAP_SECONDS:
            return

        self.discharged_mah += abs(current_a) * 1000.0 * (elapsed / 3600.0)
        while self.discharged_mah >= self.capacity_mah:
            self.discharged_mah -= self.capacity_mah
            self.cycles += 1


# ---------------------------------------------------------------------------
# NEW: bounded/persisted trend history (feature 2)
# ---------------------------------------------------------------------------

class HistoryLog:
    """Bounded, append-only trend log of voltage/SoC/current samples,
    periodically persisted as JSON. A `deque(maxlen=...)` means this can
    NEVER grow past `max_points` - the oldest sample is dropped as soon
    as a new one pushes it over the cap, so this is safe to leave
    running indefinitely, unlike an unbounded list."""

    def __init__(self, path, max_points=720):
        self.path = path
        self.max_points = max(1, int(max_points))
        self.points = deque(maxlen=self.max_points)

    def add(self, ts, voltage, soc, current_ma, charging):
        self.points.append({
            "t": round(float(ts), 1),
            "v": round(float(voltage), 3),
            "soc": round(float(soc), 1),
            "ma": round(float(current_ma), 1) if current_ma is not None else None,
            "chg": bool(charging),
        })

    def to_list(self):
        return list(self.points)

    def load(self):
        try:
            with open(self.path, "r") as handle:
                data = json.load(handle) or []
            if isinstance(data, list):
                self.points = deque(data[-self.max_points:], maxlen=self.max_points)
                logging.info("%s loaded %d history point(s) from %s",
                             LOG, len(self.points), self.path)
        except FileNotFoundError:
            pass
        except Exception as exc:
            logging.debug("%s history load failed: %s", LOG, exc)

    def save(self):
        try:
            directory = os.path.dirname(self.path) or "."
            handle = tempfile.NamedTemporaryFile(
                "w", dir=directory, prefix=".mad_hatter_ng_history.", delete=False)
            with handle:
                json.dump(self.to_list(), handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self.path)
        except Exception as exc:
            logging.debug("%s history save failed: %s", LOG, exc)


def _svg_sparkline(values, width=600, height=70, stroke="#4ec27f"):
    """A dependency-free inline SVG polyline - no charting library needed."""
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return "<p class='muted'>not enough data yet</p>"
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    step = width / max(1, n - 1)
    pts = []
    for i, v in enumerate(values):
        x = i * step
        y = height - ((v - lo) / span) * (height - 6) - 3
        pts.append("%.1f,%.1f" % (x, y))
    return (
        '<svg width="%d" height="%d" viewBox="0 0 %d %d" '
        'xmlns="http://www.w3.org/2000/svg">'
        '<polyline fill="none" stroke="%s" stroke-width="2" points="%s"/>'
        "</svg>" % (width, height, width, height, stroke, " ".join(pts))
    )


# ---------------------------------------------------------------------------
# NEW: drain-rate / activity correlation against timer-suite's CSV (feature 3)
# ---------------------------------------------------------------------------

def _read_timer_activity_timestamps(path):
    """Best-effort parse of timer_ng.csv's `timestamp` column into a sorted
    list of epoch-second floats. Same optional-source degrade pattern
    already established by bluetooth-recon-suite's
    `_nearby_wifi_networks`/`_cracked_hostnames`: a missing or unreadable
    file just means no correlation, never a crash."""
    times = []
    try:
        import csv
        with open(path, newline="") as handle:
            for row in csv.DictReader(handle):
                raw_ts = row.get("timestamp")
                if not raw_ts:
                    continue
                try:
                    dt = datetime.fromisoformat(raw_ts)
                except (TypeError, ValueError):
                    continue
                try:
                    times.append(dt.timestamp())
                except (OverflowError, OSError, ValueError):
                    continue
    except FileNotFoundError:
        logging.debug("%s timer_csv_path %s not found, skipping activity "
                      "correlation", LOG, path)
    except Exception as exc:
        logging.debug("%s couldn't read timer_csv_path %s: %s", LOG, path, exc)
    times.sort()
    return times


def correlate_drain_rate(points, activity_times, window_seconds):
    """Splits consecutive-sample SoC drops from `points` (ascending by "t",
    each a dict with "t"/"soc"/"chg" - see HistoryLog.add) into 'active'
    (within `window_seconds` of a timer-suite activity timestamp) and
    'idle' buckets, and returns a %/hour drain rate for each - or None
    where there isn't enough data yet. Charging intervals are skipped
    entirely (a rising SoC isn't a drain rate); a gap over an hour is
    treated as a restart/sleep, not real elapsed time, and skipped too.

    This is a genuine extra data point alongside the existing flat
    mAh/avg_current_ma time estimate, not a replacement for it - see
    NOTES.md.
    """
    active_pct = active_hours = 0.0
    idle_pct = idle_hours = 0.0

    for prev, cur in zip(points, points[1:]):
        if prev.get("chg") or cur.get("chg"):
            continue
        dt = cur["t"] - prev["t"]
        if dt <= 0 or dt > 3600:
            continue
        drop = prev["soc"] - cur["soc"]
        if drop <= 0:
            continue
        hours = dt / 3600.0
        mid_t = (prev["t"] + cur["t"]) / 2.0
        is_active = any(abs(mid_t - a) <= window_seconds for a in activity_times)
        if is_active:
            active_pct += drop
            active_hours += hours
        else:
            idle_pct += drop
            idle_hours += hours

    def _rate(pct, hours):
        return round(pct / hours, 2) if hours > 0 else None

    return {
        "active_pct_per_hour": _rate(active_pct, active_hours),
        "idle_pct_per_hour": _rate(idle_pct, idle_hours),
    }


# ---------------------------------------------------------------------------
# NEW: config sanity checks (feature 4)
# ---------------------------------------------------------------------------

def sanity_checks(options, ups):
    """Returns a list of {"level": ok/warn/fail/info, "text": ...} dicts for
    the /sanity webhook page, reporting which `ups_type` was configured vs.
    what was actually auto-detected, and flagging option combinations that
    look internally inconsistent. `ups` is the live MadHatterUPS instance,
    or None if initialisation hasn't succeeded (yet, or at all)."""
    checks = []

    def add(level, text):
        checks.append({"level": level, "text": text})

    requested = str(options.get("ups_type", "auto")).strip().lower()

    if ups is None:
        add("fail", "no UPS initialised - configured ups_type=%r. Check the "
                    "plugin log for the real reason (missing smbus2, no chip "
                    "responded on the bus, etc.)" % requested)
    else:
        add("ok", "configured ups_type=%r resolved to backend %r at 0x%02X"
            % (requested, ups.backend.name, ups.backend.address))
        for addr, name in getattr(ups, "_rejected", []):
            add("warn", "0x%02X on the bus is a %s, not an INA219 - it was "
                        "seen but deliberately not driven (see the plugin "
                        "log for why)" % (addr, name))

    shunt = options.get("shunt_ohms", 0.1)
    try:
        shunt_f = float(shunt)
    except (TypeError, ValueError):
        add("fail", "shunt_ohms=%r is not a number" % (shunt,))
    else:
        if shunt_f <= 0:
            add("fail", "shunt_ohms=%.4g must be > 0 - the INA219 backend "
                        "silently falls back to 0.1 ohm internally when this "
                        "happens, which is almost certainly not your real "
                        "shunt resistor, so current/mA readings will be "
                        "wrong" % shunt_f)
        else:
            add("ok", "shunt_ohms = %.4g" % shunt_f)

    cells_option = options.get("battery_cells", "auto")
    cells_fixed = None
    if isinstance(cells_option, bool):
        pass
    elif isinstance(cells_option, int):
        cells_fixed = cells_option
    elif isinstance(cells_option, str) and cells_option.strip().isdigit():
        cells_fixed = int(cells_option.strip())

    if cells_fixed is not None:
        if ups is not None and cells_fixed != ups.cells:
            add("warn", "battery_cells is fixed at %d, but the auto-detected "
                        "pack voltage implies %dS - if that's wrong, SoC and "
                        "time-remaining estimates will be off. Set "
                        "battery_cells = \"auto\" to let it infer this from "
                        "voltage instead" % (cells_fixed, ups.cells))
        else:
            add("ok", "battery_cells is fixed at %d" % cells_fixed)
    else:
        add("info", "battery_cells is 'auto' - inferred as %dS from pack "
                    "voltage" % (ups.cells if ups is not None else 1))

    gpio = _as_optional_int(options.get("charging_gpio", -1))
    if gpio is not None:
        reserved_raw = options.get("reserved_gpios") or []
        try:
            reserved = {int(p) for p in reserved_raw}
        except (TypeError, ValueError):
            reserved = set()
        if gpio in reserved:
            add("fail", "charging_gpio=%d is ALSO listed in reserved_gpios - "
                        "pick one or the other, this will always be refused"
                        % gpio)
        else:
            conflict = gpio_conflict(gpio, options)
            if conflict:
                add("warn", "charging_gpio=%d conflicts with %s - it will be "
                            "refused at runtime and charge state falls back "
                            "to the voltage trend instead" % (gpio, conflict))
            else:
                add("ok", "charging_gpio=%d has no known conflict" % gpio)

    return checks


# ---------------------------------------------------------------------------
# Device manager - unchanged from mad_hatter.py
# ---------------------------------------------------------------------------

class MadHatterUPS:
    def __init__(self, options):
        if SMBus is None:
            raise UPSError("no smbus library available (%s); "
                           "try: sudo apt install python3-smbus2" % _SMBUS_IMPORT_ERROR)

        self.options = options
        self.bus_number = int(options.get("i2c_bus", 1))
        self.bus = SMBus(self.bus_number)
        self.backend = None

        self.cells = 1
        self._cells_option = options.get("battery_cells", "auto")

        self.charge_detector = ChargeDetector(
            float(options.get("charging_threshold_ma", 30)))
        self.soc_estimator = SocEstimator(
            float(options.get("internal_resistance", 0.12)),
            float(options.get("soc_smoothing", 0.25)))
        self.cycle_counter = CycleCounter(options.get("battery_mah", 2000))
        self.draw_estimator = DrawEstimator(options.get("battery_mah", 2000))

        self.consecutive_errors = 0
        self.total_errors = 0
        self.bus_lock = threading.Lock()
        self._rejected = []

        self._select_backend()

    # -- detection ---------------------------------------------------------
    def _probe(self, address):
        try:
            self.bus.read_byte(address)
            return True
        except Exception:
            return False

    def _scan(self):
        candidates = [MAX_ADDR, PISUGAR3_ADDR, PISUGAR2_ADDR] + list(INA_ADDRS)
        found = [addr for addr in candidates if self._probe(addr)]
        if found:
            logging.info("%s I2C devices found: %s",
                         LOG, ", ".join("0x%02X" % a for a in found))

        for addr, (name, why) in KNOWN_UNSUPPORTED.items():
            if self._probe(addr):
                logging.warning("%s found %s at 0x%02X - not supported (%s)",
                                LOG, name, addr, why)
        return found

    def _find_ina(self, found):
        configured = _as_optional_int(self.options.get("i2c_address"))
        if configured is not None and configured in found:
            return configured
        for addr in INA_ADDRS:
            if addr not in found:
                continue
            is_219, name = identify_ina_variant(self.bus, addr)
            if is_219:
                return addr
            logging.warning("%s 0x%02X is a %s, not an INA219 - skipping",
                            LOG, addr, name)
            self._rejected.append((addr, name))
        return None

    def _select_backend(self):
        requested = str(self.options.get("ups_type", "auto")).strip().lower()
        found = self._scan()

        aliases = {
            "x1200": "max170xx", "ups_lite": "max170xx", "upslite": "max170xx",
            "max17040": "max170xx", "max17048": "max170xx",
            "ina219_generic": "ina219", "ina": "ina219", "waveshare": "ina219",
            "seengreat": "ina219", "ep-0136": "ina219", "ep0136": "ina219",
            "sbcomponents": "ina219",
            "pisugar": "pisugar2", "pisugar2pro": "pisugar2_pro",
        }
        requested = aliases.get(requested, requested)

        if requested == "x750" or requested == "ip5310":
            logging.warning("%s ups_type 'x750' has no verified register map and "
                            "previously reused PiSugar registers, which is wrong. "
                            "Falling back to auto-detection.", LOG)
            requested = "auto"

        gpio_pin = _as_optional_int(self.options.get("charging_gpio"))

        def build_max():
            pin = gpio_pin
            if pin is None:
                pin = DEFAULT_CHARGING_GPIOS.get(
                    str(self.options.get("ups_type", "")).strip().lower())
                if pin is not None:
                    reason = gpio_conflict(pin, self.options)
                    if reason:
                        logging.warning(
                            "%s default charging GPIO %d for this board collides "
                            "with %s - not claiming it. Charge state will come from "
                            "the voltage trend instead.", LOG, pin, reason)
                        pin = None
                    else:
                        logging.info("%s using default charging GPIO %d", LOG, pin)
            return MAX17040Backend(self.bus, MAX_ADDR, self.options, pin)

        builders = {
            "max170xx": (MAX_ADDR, build_max),
            "pisugar3": (PISUGAR3_ADDR,
                         lambda: PiSugar3Backend(self.bus, PISUGAR3_ADDR, self.options)),
            "pisugar2": (PISUGAR2_ADDR,
                         lambda: PiSugar2Backend(self.bus, PISUGAR2_ADDR, self.options)),
            "pisugar2_pro": (PISUGAR2_ADDR,
                             lambda: PiSugar2ProBackend(self.bus, PISUGAR2_ADDR, self.options)),
        }

        if requested != "auto":
            if requested == "ina219":
                address = self._find_ina(found)
                if address is None:
                    address = _as_optional_int(self.options.get("i2c_address")) or 0x40
                    logging.warning("%s no INA219 responded; forcing 0x%02X",
                                    LOG, address)
                self.backend = INA219Backend(self.bus, address, self.options)
            elif requested in builders:
                _, builder = builders[requested]
                self.backend = builder()
            else:
                raise UPSError("unsupported ups_type: %s" % requested)
        else:
            if MAX_ADDR in found:
                self.backend = build_max()
            elif PISUGAR3_ADDR in found:
                self.backend = PiSugar3Backend(self.bus, PISUGAR3_ADDR, self.options)
            else:
                ina = self._find_ina(found)
                if ina is not None:
                    self.backend = INA219Backend(self.bus, ina, self.options)
                elif PISUGAR2_ADDR in found:
                    self.backend = PiSugar2Backend(self.bus, PISUGAR2_ADDR, self.options)
                else:
                    raise UPSError(self._nothing_found_message(found))

        self.backend.setup()
        logging.info("%s backend '%s' at 0x%02X",
                     LOG, self.backend.name, self.backend.address)

    def _nothing_found_message(self, found):
        parts = ["no supported UPS found on i2c-%d" % self.bus_number]
        for addr, name in self._rejected:
            parts.append("0x%02X is a %s - its registers scale differently "
                         "from an INA219, so driving it would produce wrong "
                         "readings rather than no readings" % (addr, name))
        for addr, (name, why) in KNOWN_UNSUPPORTED.items():
            if self._probe(addr):
                parts.append("0x%02X is a %s - %s" % (addr, name, why))
        if len(parts) == 1 and found:
            parts.append("devices responded at %s but none is a UPS I handle"
                         % ", ".join("0x%02X" % a for a in found))
        return "; ".join(parts)

    # -- reading -----------------------------------------------------------
    def _read_raw(self, retries=3):
        last_exc = None
        for attempt in range(retries):
            try:
                with self.bus_lock:
                    data = self.backend.sample()
                self.consecutive_errors = 0
                return data
            except Exception as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(0.05)
        self.consecutive_errors += 1
        self.total_errors += 1
        raise UPSError("i2c read failed: %s" % last_exc)

    def _resolve_cells(self, voltage):
        if self.cells != 1:
            return
        option = self._cells_option
        if isinstance(option, int) and option > 0:
            self.cells = option
            return
        if isinstance(option, str) and option.strip().isdigit():
            self.cells = max(1, int(option.strip()))
            return
        if voltage > 12.6:
            self.cells = 4
        elif voltage > 8.6:
            self.cells = 3
        elif voltage > 4.5:
            self.cells = 2
        else:
            self.cells = 1
        if self.cells > 1:
            logging.info("%s detected %dS pack (%.2fV)", LOG, self.cells, voltage)

    def read(self):
        """Returns a normalised reading dict, or raises UPSError."""
        raw = self._read_raw()
        voltage = float(raw.get("voltage") or 0.0)
        self._resolve_cells(voltage)

        current = raw.get("current")
        charging = self.charge_detector.update(voltage, current, raw.get("charging"))

        if raw.get("soc") is not None:
            soc = _clamp(float(raw["soc"]), 0.0, 100.0)
        else:
            cell_v = voltage / self.cells
            cell_i = current
            soc = self.soc_estimator.update(cell_v, cell_i)

        self.cycle_counter.update(current)

        estimated_ma = None
        if current is None and self.backend.reports_soc:
            self.draw_estimator.update(soc, charging)
            if self.draw_estimator.confident:
                estimated_ma = self.draw_estimator.value_ma

        return {
            "voltage": voltage,
            "current": current,
            "soc": soc,
            "charging": charging,
            "cells": self.cells,
            "backend": self.backend.name,
            "errors": self.total_errors,
            "cycles": self.cycle_counter.cycles,
            "estimated_draw_ma": round(estimated_ma, 1) if estimated_ma else None,
        }

    def close(self):
        try:
            if self.backend:
                self.backend.teardown()
        except Exception:
            pass
        try:
            self.bus.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

NOTIFY_LEVELS = ("warning", "critical", "shutdown_imminent")


class MadHatterNG(plugins.Plugin):
    # NOTE: this attribute is cosmetic only - Python does not let a class
    # body's `__name__ = ...` override `type.__name__` (confirmed
    # empirically), and the real framework registers/looks up this plugin
    # by its FILE's basename ("MadHatterNG", exact case) regardless of
    # what's written here. See the module docstring's naming note.
    __name__ = "MadHatterNG"
    __author__ = "AlienMajik (original mad_hatter.py), feature-upgrade rebuild"
    __version__ = "3.0.0"
    __license__ = "GPL3"
    __description__ = ("Universal UPS plugin: MAX170xx / INA219 / PiSugar, "
                       "with real current sensing, IR-compensated SOC, "
                       "background polling, safe shutdown, threshold "
                       "notifications, trend history, and drain-rate "
                       "correlation against real agent activity.")

    __defaults__ = {
        "enabled": True,
        # display
        "show_voltage": False,
        "show_time_estimate": True,
        "show_icon": True,
        "use_emoji": False,
        "label": "UPS",
        "ui_position_x": -80,
        "ui_position_y": 0,
        "ui_font": "medium",
        # shutdown
        "shutdown_enabled": False,
        "shutdown_threshold": 5,
        "critical_threshold": 2,
        "warning_threshold": 15,
        "shutdown_grace": 3,
        "shutdown_grace_period": 30,
        # battery / hardware
        "battery_mah": 2000,
        "avg_current_ma": 200,
        "battery_cells": "auto",
        "internal_resistance": 0.12,
        "soc_smoothing": 0.25,
        "shunt_ohms": 0.1,
        "invert_current": False,
        "charging_threshold_ma": 30,
        "charging_gpio": -1,
        "charging_gpio_active_high": True,
        "reserved_gpios": [],
        "allow_unsafe_gpio": False,
        "alert_threshold": 10,
        "ups_type": "auto",
        "i2c_bus": 1,
        "i2c_address": -1,
        # behaviour
        "poll_interval": 10,
        "debug_mode": False,
        # NEW: notifications (feature 1)
        "notify_on_threshold": False,
        "notify_backend": "auto",     # "apprise" | "discord" | "auto"
        # Post-cluster-review update: the sibling plugin names below
        # were previously hardcoded ("apprise_notify_ng"/"discord_ng").
        # A rename of either suite's .py file would then silently break
        # this integration with no visible error (see on_loaded's new
        # startup log line below). Now configurable so a rename is a
        # one-line config change instead of a code change.
        "apprise_plugin_name": "apprise_notify_ng",
        "discord_plugin_name": "discord_ng",
        # NEW: history logging (feature 2)
        "history_max_points": 720,
        "history_log_interval_seconds": 60,
        "history_path": HISTORY_FILE,
        # NEW: drain-rate / activity correlation (feature 3)
        "timer_csv_path": "/etc/pwnagotchi/timer_ng.csv",
        "activity_correlation_window_minutes": 5,
    }

    def __init__(self):
        self.ups = None
        self.options = dict(self.__defaults__)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._reading = None
        self._status_text = "..."
        self._init_error = None
        self._next_init_retry = 0.0
        self._init_fail_count = 0
        self._init_retry_delay = 0
        self._low_since = None
        self._low_polls = 0
        self._last_warning_log = 0.0
        self._last_state_save = 0.0
        self._ui_ready = False
        self._ui = None
        self._pause_poll = threading.Event()
        self._diag_lock = threading.Lock()
        self._diag = {"running": False, "progress": 0, "lines": [],
                      "config": None, "started_at": 0}
        # NEW
        self._agent = None
        self.history = None
        self._last_history_log = 0.0
        self._last_history_save = 0.0
        self._notified = {lvl: False for lvl in NOTIFY_LEVELS}

    # -- option access -----------------------------------------------------
    def _opt(self, key, default=None):
        value = self.options.get(key, self.__defaults__.get(key, default))
        return default if value is None else value

    def _opt_float(self, key, default=0.0):
        try:
            return float(self._opt(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _opt_int(self, key, default=0):
        try:
            return int(float(self._opt(key, default)))
        except (TypeError, ValueError):
            return int(default)

    # -- persistence -------------------------------------------------------
    def _load_state(self):
        state = {}
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r") as handle:
                    state = json.load(handle) or {}
            elif os.path.exists(LEGACY_STATE_FILE):
                with open(LEGACY_STATE_FILE, "r") as handle:
                    state = json.load(handle) or {}
                logging.info("%s migrated state from mad_hatter.py's state "
                            "file (%s)", LOG, LEGACY_STATE_FILE)
            elif os.path.exists(LEGACY_CYCLE_FILE):
                with open(LEGACY_CYCLE_FILE, "r") as handle:
                    state = {"cycles": int((handle.read().strip() or "0"))}
                logging.info("%s migrated legacy cycle count", LOG)
        except Exception as exc:
            logging.debug("%s state load failed: %s", LOG, exc)
        return state

    def _save_state(self):
        if not self.ups:
            return
        payload = {
            "cycles": self.ups.cycle_counter.cycles,
            "discharged_mah": round(self.ups.cycle_counter.discharged_mah, 3),
            "draw": self.ups.draw_estimator.state(),
            "saved_at": int(time.time()),
        }
        try:
            directory = os.path.dirname(STATE_FILE) or "."
            handle = tempfile.NamedTemporaryFile(
                "w", dir=directory, prefix=".mad_hatter_ng.", delete=False)
            with handle:
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, STATE_FILE)
        except Exception as exc:
            logging.debug("%s state save failed: %s", LOG, exc)

    # -- lifecycle ---------------------------------------------------------
    def on_loaded(self):
        # pwnagotchi 2.9.5.x assigns `plugin.options = config['main']['plugins'][name]`
        # outright - it does NOT merge __defaults__ for you. Any plugin that
        # indexes self.options[...] directly will KeyError on every key the
        # user did not spell out in config.toml. Merge them ourselves.
        user_options = self.options if isinstance(self.options, dict) else {}
        merged = dict(self.__defaults__)
        merged.update(user_options)
        self.options = merged

        self.history = HistoryLog(
            self._opt("history_path", HISTORY_FILE),
            self._opt_int("history_max_points", 720))
        self.history.load()

        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop,
                                        name="mad_hatter_ng", daemon=True)
        self._thread.start()
        logging.info("%s plugin v%s loaded", LOG, self.__version__)

    def on_ready(self, agent):
        # Kept only to hand a real `agent` reference to the notification
        # backends (apprise_notify_ng's _queue_notification takes one) - the
        # original mad_hatter.py has no on_ready at all, this is additive.
        self._agent = agent
        self._log_notify_sibling_status()

    def _log_notify_sibling_status(self):
        """Post-cluster-review addition: this integration previously
        degraded to a silent no-op (a debug-level log line, easy to
        miss) if apprise_notify_ng/discord_ng were renamed, disabled,
        or never installed. Runs once at on_ready (after all plugins
        have had a chance to load) and logs a clear, visible INFO/
        WARNING line stating whether each configured sibling was
        actually found - so a misconfiguration shows up in the normal
        log, not just when a threshold notification is first due."""
        if not self._opt("notify_on_threshold", False):
            return
        backend = str(self._opt("notify_backend", "auto")).strip().lower()
        checks = []
        if backend in ("apprise", "auto"):
            checks.append(("apprise", self._opt("apprise_plugin_name", "apprise_notify_ng")))
        if backend in ("discord", "auto"):
            checks.append(("discord", self._opt("discord_plugin_name", "discord_ng")))
        for kind, name in checks:
            if plugins.loaded.get(name) is not None:
                logging.info("%s notify_backend=%s: sibling plugin '%s' found",
                             LOG, kind, name)
            else:
                logging.warning(
                    "%s notify_backend=%s: sibling plugin '%s' not found in "
                    "plugins.loaded - threshold notifications via %s won't "
                    "fire until it's installed/enabled, or "
                    "%s_plugin_name is corrected if it was renamed",
                    LOG, kind, name, kind, kind)

    def _try_init(self):
        try:
            self.ups = MadHatterUPS(self.options)
        except Exception as exc:
            self.ups = None
            self._init_error = str(exc)
            # Exponential backoff so a permanently-absent UPS (empty I2C bus)
            # doesn't log a WARNING every 60s forever. Keep retrying - the HAT
            # may be plugged in later - but widen the gap 60s -> ... -> 30min
            # cap, and only log at WARNING when the interval actually changes
            # (the repeats in between drop to DEBUG). A later success resets
            # this back to the short interval.
            self._init_fail_count += 1
            prev_delay = self._init_retry_delay
            delay = min(60 * (2 ** (self._init_fail_count - 1)), 1800)
            self._init_retry_delay = delay
            self._next_init_retry = time.monotonic() + delay
            with self._lock:
                self._status_text = "NO UPS"
            if delay != prev_delay:
                logging.warning("%s init failed (retry in %ds): %s", LOG, delay, exc)
            else:
                logging.debug("%s init still failing (retry in %ds): %s", LOG, delay, exc)
            return False

        # init succeeded - clear the backoff so a later unplug/replug starts
        # fresh at the short retry interval again.
        self._init_fail_count = 0
        self._init_retry_delay = 0
        state = self._load_state()
        self.ups.cycle_counter.cycles = int(state.get("cycles", 0) or 0)
        self.ups.cycle_counter.discharged_mah = float(state.get("discharged_mah", 0) or 0)
        draw = state.get("draw") or {}
        try:
            self.ups.draw_estimator.samples = [list(x) for x in (draw.get("samples") or [])]
            self.ups.draw_estimator.value_ma = draw.get("value_ma")
        except Exception:
            pass
        self._init_error = None
        return True

    def _publish(self, text):
        with self._lock:
            changed = text != self._status_text
            self._status_text = text
            ui = self._ui
        if changed and ui is not None:
            try:
                ui.set("mad_hatter_ng", text)
            except Exception:
                pass

    def _poll_loop(self):
        while not self._stop.is_set():
            interval = max(2, self._opt_int("poll_interval", 10))

            if self.ups is None:
                if time.monotonic() < self._next_init_retry or not self._try_init():
                    self._stop.wait(min(interval, 5))
                    continue

            if self._pause_poll.is_set():
                self._stop.wait(1)
                continue

            try:
                reading = self.ups.read()
                with self._lock:
                    self._reading = reading
                self._publish(self._render(reading))
                if self._opt("debug_mode"):
                    logging.debug("%s %.2fV %.1f%% %s %s", LOG,
                                  reading["voltage"], reading["soc"],
                                  "chg" if reading["charging"] else "dis",
                                  "%.0fmA" % (reading["current"] * 1000)
                                  if reading["current"] is not None else "n/a")
                self._log_history(reading)
                self._maybe_notify(reading)
                self._check_shutdown(reading)
            except UPSError as exc:
                if self.ups and self.ups.consecutive_errors > 5:
                    self._publish("UPS ERR")
                logging.debug("%s poll failed: %s", LOG, exc)
            except Exception as exc:
                logging.error("%s unexpected poll error: %s", LOG, exc)

            now = time.monotonic()
            if now - self._last_state_save > 60:
                self._last_state_save = now
                self._save_state()

            self._stop.wait(interval)

    # -- rendering ---------------------------------------------------------
    def _icons(self, soc, charging):
        if not self._opt("show_icon", True):
            return "", ""
        if self._opt("use_emoji", False):
            return ("\U0001FAAB" if soc < 20 else "\U0001F50B",
                    "⚡" if charging else "")
        return "", "+" if charging else ""

    def _estimate_minutes(self, reading):
        soc = reading["soc"]
        capacity = self._opt_float("battery_mah", 2000)
        if capacity <= 0 or soc <= 0 or soc >= 100:
            return None

        current_ma = None
        if reading["current"] is not None:
            current_ma = abs(reading["current"]) * 1000.0
            if current_ma < self._opt_float("charging_threshold_ma", 30):
                current_ma = None

        if reading["charging"]:
            if current_ma is None:
                return None
            remaining = (100.0 - soc) / 100.0 * capacity
            taper = 1.0 if soc < 80 else 1.6
            return remaining / current_ma * 60.0 * taper

        draw = current_ma
        if not draw:
            draw = reading.get("estimated_draw_ma")
        if not draw:
            draw = self._opt_float("avg_current_ma", 200)
        if not draw or draw <= 0:
            return None
        return soc / 100.0 * capacity / draw * 60.0

    def _render(self, reading):
        soc = reading["soc"]
        charging = reading["charging"]
        battery_icon, charge_icon = self._icons(soc, charging)

        parts = []
        if self._opt("show_voltage", False):
            parts.append("%.2fV" % reading["voltage"])
        parts.append("%s%d%%%s" % (battery_icon, int(round(soc)), charge_icon))

        if self._opt("show_time_estimate", True):
            minutes = self._fmt_estimate(reading)
            if minutes:
                parts.append(minutes)

        if self._opt("debug_mode", False):
            parts.append("C%d" % reading["cycles"])
            if reading["current"] is not None:
                parts.append("%dmA" % int(reading["current"] * 1000))
            elif reading.get("estimated_draw_ma"):
                parts.append("~%dmA" % int(reading["estimated_draw_ma"]))
            if reading["errors"]:
                parts.append("E%d" % reading["errors"])

        return " ".join(parts)

    def _fmt_estimate(self, reading):
        minutes = self._estimate_minutes(reading)
        text = _fmt_minutes(minutes) if minutes else None
        if not text:
            return None
        return ("^" if reading["charging"] else "~") + text

    # -- NEW: history logging (feature 2) -----------------------------------
    def _log_history(self, reading):
        if self.history is None:
            return
        now_wall = time.time()
        every = max(5, self._opt_int("history_log_interval_seconds", 60))
        if now_wall - self._last_history_log < every:
            return
        self._last_history_log = now_wall

        current_ma = None
        if reading.get("current") is not None:
            current_ma = reading["current"] * 1000.0
        elif reading.get("estimated_draw_ma"):
            current_ma = -reading["estimated_draw_ma"]  # discharging = negative

        self.history.add(now_wall, reading["voltage"], reading["soc"],
                         current_ma, reading["charging"])

        if now_wall - self._last_history_save > 60:
            self._last_history_save = now_wall
            self.history.save()

    # -- NEW: threshold notifications (feature 1) ---------------------------
    def _notify_level(self, reading):
        """Independent of shutdown_enabled - notifications are useful even
        when auto-shutdown itself is off."""
        if reading["charging"]:
            return None
        soc = reading["soc"]
        critical = self._opt_float("critical_threshold", 2)
        shutdown = self._opt_float("shutdown_threshold", 5)
        warning = self._opt_float("warning_threshold", 15)
        if soc < critical:
            return "shutdown_imminent"
        if soc < shutdown:
            return "critical"
        if soc < warning:
            return "warning"
        return None

    def _maybe_notify(self, reading):
        if not self._opt("notify_on_threshold", False):
            return

        level = self._notify_level(reading)
        if level is None:
            # Recovered (charging, or SoC back above warning_threshold):
            # clear every crossing flag so the NEXT dip fires again.
            for lvl in NOTIFY_LEVELS:
                self._notified[lvl] = False
            return

        if self._notified[level]:
            return  # already notified for this crossing - don't repeat every poll

        order = NOTIFY_LEVELS.index(level)
        for i, lvl in enumerate(NOTIFY_LEVELS):
            if i <= order:
                self._notified[lvl] = True

        self._fire_notification(level, reading)

    def _fire_notification(self, level, reading):
        title = "MadHatterNG: battery %s" % level.replace("_", " ")
        body = "%.1f%% (%.2fV), backend %s" % (
            reading["soc"], reading["voltage"], reading["backend"])
        backend = str(self._opt("notify_backend", "auto")).strip().lower()

        tried = []
        if backend in ("apprise", "auto"):
            tried.append("apprise")
            if self._notify_via_apprise(title, body):
                return
        if backend in ("discord", "auto"):
            tried.append("discord")
            if self._notify_via_discord(title, body):
                return

        logging.info("%s %s crossing: no notification backend available/"
                     "loaded (tried: %s) - on-screen icon still updated",
                     LOG, level, ", ".join(tried) or backend)

    def _notify_via_apprise(self, title, body):
        """apprise_notify_ng.py has no public `notify()` method - its real
        entry point is `_queue_notification(title, body, agent)`, which
        needs `self._apobj`/`self._queue` to already exist (both stay None
        if `apprise` failed to import, or on_loaded never ran). Guarded so
        a plugin that's loaded-but-not-ready degrades to a skipped, logged
        notification instead of an AttributeError."""
        try:
            target = plugins.loaded.get(self._opt("apprise_plugin_name", "apprise_notify_ng"))
            if target is None:
                return False
            queue_fn = getattr(target, "_queue_notification", None)
            if queue_fn is None or getattr(target, "_queue", None) is None:
                logging.debug("%s apprise_notify_ng is loaded but not ready "
                              "(no urls/config_path configured, or the "
                              "apprise package isn't installed)", LOG)
                return False
            queue_fn(title, body, self._agent)
            return True
        except Exception as exc:
            logging.debug("%s apprise notification failed: %s", LOG, exc)
            return False

    def _notify_via_discord(self, title, body):
        """discord_ng.py's real entry point is also `_queue_notification`,
        with a different signature (content, embed) - it queues onto its
        own worker thread rather than blocking this poll loop on an HTTP
        call."""
        try:
            target = plugins.loaded.get(self._opt("discord_plugin_name", "discord_ng"))
            if target is None:
                return False
            queue_fn = getattr(target, "_queue_notification", None)
            if queue_fn is None or not getattr(target, "webhook_url", None):
                logging.debug("%s discord_ng is loaded but not configured "
                              "(no webhook_url)", LOG)
                return False
            queue_fn(content="**%s**\n%s" % (title, body), embed=None)
            return True
        except Exception as exc:
            logging.debug("%s discord notification failed: %s", LOG, exc)
            return False

    # -- shutdown ----------------------------------------------------------
    def _check_shutdown(self, reading):
        if not self._opt("shutdown_enabled", False):
            self._low_since = None
            self._low_polls = 0
            return

        soc = reading["soc"]
        discharging = not reading["charging"]
        warning = self._opt_float("warning_threshold", 15)
        threshold = self._opt_float("shutdown_threshold", 5)
        critical = self._opt_float("critical_threshold", 2)

        now = time.monotonic()

        if soc < warning and discharging and now - self._last_warning_log > 60:
            self._last_warning_log = now
            logging.warning("%s low battery: %.1f%% (%.2fV)",
                            LOG, soc, reading["voltage"])

        if not (discharging and soc < threshold):
            self._low_since = None
            self._low_polls = 0
            return

        if soc < critical:
            logging.critical("%s battery critical (%.1f%%) - shutting down now",
                             LOG, soc)
            self._do_shutdown()
            return

        if self._low_since is None:
            self._low_since = now
            self._low_polls = 0
        self._low_polls += 1

        need_polls = max(1, self._opt_int("shutdown_grace", 3))
        need_seconds = max(0, self._opt_int("shutdown_grace_period", 30))

        if self._low_polls >= need_polls and (now - self._low_since) >= need_seconds:
            logging.critical("%s battery at %.1f%% for %ds - safe shutdown",
                             LOG, soc, int(now - self._low_since))
            self._do_shutdown()

    def _do_shutdown(self):
        with self._lock:
            self._status_text = "SHUTDOWN"
        self._save_state()
        if self.history is not None:
            self.history.save()
        try:
            pwnagotchi.shutdown()
        except Exception as exc:
            logging.error("%s pwnagotchi.shutdown() failed: %s", LOG, exc)
            os.system("sync && shutdown -h now")

    # -- UI ----------------------------------------------------------------
    def on_ui_setup(self, ui):
        try:
            configured_x = self._opt_int("ui_position_x", -80)
            # Negative x means "this many pixels in from the right edge", which
            # keeps the element on screen on 128px and 250px displays alike.
            # PRESERVED EXACTLY from mad_hatter.py - do not change this logic.
            pos_x = ui.width() + configured_x if configured_x < 0 else configured_x
            pos_x = _clamp(pos_x, 0, max(0, ui.width() - 10))
            pos = (pos_x, self._opt_int("ui_position_y", 0))

            font_name = str(self._opt("ui_font", "medium")).lower()
            text_font = {"small": fonts.Small, "medium": fonts.Medium,
                         "bold": fonts.Bold}.get(font_name, fonts.Medium)

            label = str(self._opt("label", "UPS") or "").strip() or None

            with self._lock:
                initial = self._status_text

            ui.add_element("mad_hatter_ng", LabeledValue(
                color=BLACK,
                label=label,
                value=initial,
                position=pos,
                label_font=fonts.Bold,
                text_font=text_font,
            ))
            self._ui = ui
            self._ui_ready = True
        except Exception as exc:
            logging.error("%s UI setup failed: %s", LOG, exc)

    def on_ui_update(self, ui):
        # Deliberately does no I/O: the display thread must never block on I2C.
        if not self._ui_ready:
            return
        with self._lock:
            text = self._status_text
        try:
            ui.set("mad_hatter_ng", text)
        except Exception:
            pass

    def on_unload(self, ui):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        self._save_state()
        if self.history is not None:
            self.history.save()

        if self.ups:
            self.ups.close()
            self.ups = None

        if self._ui_ready:
            try:
                with ui._lock:
                    ui.remove_element("mad_hatter_ng")
            except Exception:
                try:
                    ui.remove_element("mad_hatter_ng")
                except Exception:
                    pass
            self._ui_ready = False
        self._ui = None

        logging.info("%s unloaded", LOG)

    # -- diagnostic (web) ----------------------------------------------------
    def _find_doctor(self):
        """Locate mad_hatter_doctor.py so the web UI runs the REAL diagnostic
        rather than a second, weaker copy of the same logic. Kept from
        mad_hatter.py unchanged - if you copy the original's
        mad_hatter_doctor.py alongside this plugin, it still works."""
        configured = self.options.get("doctor_path")
        candidates = [configured] if configured else []
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            candidates.append(os.path.join(here, "mad_hatter_doctor.py"))
        except Exception:
            pass
        candidates += [
            "/usr/local/bin/mad_hatter_doctor.py",
            "/usr/local/sbin/mad_hatter_doctor.py",
            "/root/mad_hatter_doctor.py",
            "/opt/mad_hatter/mad_hatter_doctor.py",
        ]
        plugin_dirs = [
            "/etc/pwnagotchi/custom-plugins/mad_hatter_doctor.py",
            "/usr/local/share/pwnagotchi/custom-plugins/mad_hatter_doctor.py",
        ]

        for path in candidates:
            if path and os.path.isfile(path):
                return path
        for path in plugin_dirs:
            if os.path.isfile(path):
                logging.warning(
                    "%s mad_hatter_doctor.py is in a plugin directory (%s). "
                    "pwnagotchi imports every .py there as a plugin, so it "
                    "shows up as a broken entry on the plugins page. Move it: "
                    "sudo mv %s /usr/local/bin/", LOG, path, path)
                return path
        return None

    @staticmethod
    def _classify(line):
        match = re.match(r"^\s*\[\s*(OK|WARN|FAIL|INFO)\s*\]\s*(.*)$", line)
        if match:
            return ({"OK": "ok", "WARN": "warn", "FAIL": "fail",
                     "INFO": "info"}[match.group(1)], match.group(2))
        stripped = line.strip()
        if not stripped or set(stripped) <= set("-="):
            return None
        if re.match(r"^(\d+\.\s|Summary)", stripped):
            return ("head", stripped)
        return ("cont", stripped)

    def _run_doctor_script(self, script, watch_seconds):
        import subprocess

        cmd = [sys.executable, "-u", script, "--bus",
               str(self._opt_int("i2c_bus", 1))]
        if watch_seconds > 0:
            cmd += ["--watch", str(watch_seconds)]

        self._diag_log("info", "Running %s" % os.path.basename(script))

        self._pause_poll.set()
        config_lines, in_config = [], False
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    bufsize=1)
            budget = watch_seconds + 120
            deadline = time.monotonic() + budget
            for raw in proc.stdout:
                if time.monotonic() > deadline or self._stop.is_set():
                    proc.kill()
                    self._diag_log("fail", "Diagnostic timed out")
                    break
                line = raw.rstrip("\n")

                if "Suggested config.toml" in line:
                    in_config = True
                elif in_config:
                    if line.strip().startswith(("[main", "enabled", "ups_type",
                                                "charging_gpio", "battery_",
                                                "avg_current", "show_",
                                                "shutdown_", "invert_",
                                                "shunt_", "internal_")):
                        config_lines.append(line.strip())
                    elif config_lines and line.strip().startswith("["):
                        in_config = False

                item = self._classify(line)
                if item:
                    self._diag_log(item[0], item[1])
                with self._diag_lock:
                    self._diag["progress"] = min(
                        95, int(100 * (budget - max(0, deadline - time.monotonic()))
                                / budget))
            proc.wait(timeout=10)
        except FileNotFoundError:
            self._diag_log("fail", "Could not execute %s" % script)
            return False
        except Exception as exc:
            self._diag_log("fail", "Diagnostic failed: %s" % exc)
            return False
        finally:
            self._pause_poll.clear()

        if config_lines:
            with self._diag_lock:
                self._diag["config"] = "\n".join(config_lines)
        return True

    def _diag_log(self, level, text):
        with self._diag_lock:
            self._diag["lines"].append({"level": level, "text": text})

    def _diag_worker(self, watch_seconds):
        try:
            script = self._find_doctor()
            if script:
                if self._run_doctor_script(script, watch_seconds):
                    return
                self._diag_log("warn", "Falling back to the built-in check")
            else:
                self._diag_log("warn", "mad_hatter_doctor.py not found next to "
                                       "the plugin - running the built-in "
                                       "check, which covers less.")

            self._diag_log("info", "Built-in check starting")
            ups = self.ups
            if ups is None:
                self._diag_log("fail", "No UPS initialised: %s"
                               % (self._init_error or "unknown"))
                return

            backend = ups.backend
            self._diag_log("ok", "Backend %s at 0x%02X"
                           % (backend.name, backend.address))
            self._diag_log("info", "Bus i2c-%d, %d cell(s) detected"
                           % (ups.bus_number, ups.cells))

            for addr, (name, why) in KNOWN_UNSUPPORTED.items():
                if ups._probe(addr):
                    self._diag_log("warn", "Also on the bus: %s at 0x%02X "
                                           "- not supported (%s)"
                                   % (name, addr, why))

            reading = ups.read()
            self._diag_log("ok", "Pack %.3f V, %.1f%%, %s"
                           % (reading["voltage"], reading["soc"],
                              "charging" if reading["charging"] else "discharging"))

            if reading["current"] is None:
                self._diag_log("info", "No current sensor on this chip - "
                                       "charge state comes from the voltage "
                                       "trend, and runtime uses avg_current_ma")
                watch_seconds = min(watch_seconds, 60)

            samples = []
            deadline = time.monotonic() + watch_seconds
            total = max(1, watch_seconds)
            while time.monotonic() < deadline and not self._stop.is_set():
                try:
                    r = ups.read()
                    samples.append((time.monotonic(), r["voltage"], r["current"]))
                except Exception:
                    pass
                with self._diag_lock:
                    elapsed = total - max(0, deadline - time.monotonic())
                    self._diag["progress"] = int(100 * elapsed / total)
                time.sleep(2)

            if len(samples) >= 5:
                volts = [s[1] for s in samples]
                drift = (volts[-1] - volts[0]) / max(
                    1e-6, (samples[-1][0] - samples[0][0]) / 3600.0)
                self._diag_log("info", "Voltage trend %+.4f V/hour over %d samples"
                               % (drift, len(samples)))
                currents = [s[2] for s in samples if s[2] is not None]
                if currents:
                    mean_ma = sum(currents) / len(currents) * 1000.0
                    self._diag_log("info", "Mean current %+.0f mA" % mean_ma)
                    if abs(mean_ma) > 15 and abs(drift) > 0.005:
                        if (drift > 0) != (mean_ma > 0):
                            self._diag_log("fail", "Polarity inverted - set "
                                                   "invert_current = true")
                        else:
                            self._diag_log("ok", "Current polarity is correct")

            with self._diag_lock:
                self._diag["config"] = self._suggested_config(reading)
            self._diag_log("ok", "Diagnostic complete")
        except Exception as exc:
            self._diag_log("fail", "Diagnostic error: %s" % exc)
        finally:
            with self._diag_lock:
                self._diag["running"] = False
                self._diag["progress"] = 100

    def _suggested_config(self, reading):
        lines = ["[main.plugins.MadHatterNG]", "enabled = true",
                 'ups_type = "auto"', "charging_gpio = -1"]
        if reading.get("cells", 1) > 1:
            lines.append("battery_cells = %d" % reading["cells"])
        lines.append("battery_mah = %d      # CONFIRM your pack"
                     % self._opt_int("battery_mah", 2000))
        lines.append("avg_current_ma = %d   # tune after a real discharge"
                     % self._opt_int("avg_current_ma", 200))
        lines.append("show_voltage = true")
        return "\n".join(lines)

    # -- NEW: /history and /sanity web pages ---------------------------------
    def _history_page(self, points):
        socs = [p["soc"] for p in points]
        volts = [p["v"] for p in points]

        activity_times = _read_timer_activity_timestamps(
            self._opt("timer_csv_path"))
        window_seconds = self._opt_int("activity_correlation_window_minutes", 5) * 60
        drain = correlate_drain_rate(points, activity_times, window_seconds)

        rows = []
        for p in list(reversed(points))[:200]:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p["t"]))
            ma_text = "%.0f" % p["ma"] if p.get("ma") is not None else ""
            rows.append(
                "<tr><td>%s</td><td>%.2f</td><td>%.1f</td><td>%s</td>"
                "<td>%s</td></tr>"
                % (ts, p["v"], p["soc"], ma_text, "yes" if p.get("chg") else ""))
        table = "".join(rows) or "<tr><td colspan=5><i>no history yet</i></td></tr>"

        active_txt = ("%.1f%%/hr" % drain["active_pct_per_hour"]
                      if drain["active_pct_per_hour"] is not None else "n/a")
        idle_txt = ("%.1f%%/hr" % drain["idle_pct_per_hour"]
                   if drain["idle_pct_per_hour"] is not None else "n/a")

        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>MadHatterNG history</title>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<style>body{font-family:ui-monospace,Menlo,Consolas,monospace;"
            "background:#14161a;color:#d7dbe0;margin:0;padding:1.2rem;"
            "line-height:1.5}h2{color:#fff}.muted{color:#7c848f;font-size:.85rem}"
            "a{color:#8ab4f8}table{border-collapse:collapse;width:100%%}"
            "td,th{border:1px solid #2a2f38;padding:.3rem .5rem;font-size:.85rem}"
            "</style></head><body>"
            "<h2>MadHatterNG - history</h2>"
            "<p><a href='../'>&laquo; status</a> &nbsp; "
            "<a href='history.json'>raw JSON</a> &nbsp; "
            "<a href='../sanity'>sanity check</a></p>"
            "<p>Estimated time remaining (flat calc) is on the status page. "
            "Recent drain rate: <b>%s</b> during active epochs, <b>%s</b> "
            "idle (activity source: %s, &plusmn;%d min window)</p>"
            "<h3>SoC %%</h3>%s<h3>Voltage</h3>%s"
            "<table><tr><th>Time</th><th>V</th><th>SoC%%</th><th>mA</th>"
            "<th>Chg</th></tr>%s</table>"
            "</body></html>"
            % (active_txt, idle_txt, self._opt("timer_csv_path"),
               window_seconds // 60,
               _svg_sparkline(socs, stroke="#4ec27f"),
               _svg_sparkline(volts, stroke="#2d6cdf"),
               table)
        )

    def _sanity_page(self):
        checks = sanity_checks(self.options, self.ups)
        rows = "".join(
            "<div class='line %s'>[%s] %s</div>"
            % (c["level"], c["level"], html.escape(c["text"]))
            for c in checks
        )
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>MadHatterNG sanity</title>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<style>body{font-family:ui-monospace,Menlo,Consolas,monospace;"
            "background:#14161a;color:#d7dbe0;margin:0;padding:1.2rem;"
            "line-height:1.6}h2{color:#fff}a{color:#8ab4f8}"
            ".line{padding:.15rem 0}.ok{color:#4ec27f}.warn{color:#e0b341}"
            ".fail{color:#e8615a}.info{color:#8f98a3}</style></head><body>"
            "<h2>MadHatterNG - config sanity check</h2>"
            "<p><a href='../'>&laquo; status</a></p>"
            "%s</body></html>"
            % (rows or "<p>no checks available</p>")
        )

    # -- web -----------------------------------------------------------------
    def on_webhook(self, path, request):
        # Called directly on a Flask worker thread, NOT via the plugin event
        # queue - so every branch here must return immediately. The diagnostic
        # runs in its own thread and is polled via /report.
        from flask import jsonify, abort

        with self._lock:
            reading = dict(self._reading) if self._reading else None
            text = self._status_text

        if path in ("status", "json"):
            return jsonify({"ok": reading is not None, "display": text,
                            "error": self._init_error, "reading": reading})

        if path == "diagnose":
            with self._diag_lock:
                if self._diag["running"]:
                    return jsonify({"started": False, "reason": "already running"})
                try:
                    watch = int(request.args.get("watch", 60))
                except Exception:
                    watch = 60
                watch = _clamp(watch, 0, 900)
                self._diag = {"running": True, "progress": 0, "lines": [],
                              "config": None, "started_at": time.time()}
            threading.Thread(target=self._diag_worker, args=(watch,),
                             daemon=True).start()
            return jsonify({"started": True, "watch": watch})

        if path == "report":
            with self._diag_lock:
                return jsonify(dict(self._diag))

        # NEW: feature 2 - history/graph webhook
        if path in ("history.json", "history_json"):
            points = self.history.to_list() if self.history else []
            return jsonify({"points": points})

        if path == "history":
            points = self.history.to_list() if self.history else []
            return self._history_page(points)

        # NEW: feature 4 - config sanity webhook
        if path in ("sanity", "check"):
            return self._sanity_page()

        if path in (None, "", "/", "index"):
            return _DIAG_PAGE

        abort(404)


# ---------------------------------------------------------------------------
# Web UI served at /plugins/MadHatterNG (the plugin's real registered name
# is its file basename - see the module docstring's naming note)
# ---------------------------------------------------------------------------

_DIAG_PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>MadHatterNG</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:ui-monospace,Menlo,Consolas,monospace;background:#14161a;
      color:#d7dbe0;margin:0;padding:1.2rem;line-height:1.5}
 h1{font-size:1.1rem;margin:0 0 .2rem;color:#fff}
 .sub{color:#7c848f;font-size:.8rem;margin-bottom:.6rem}
 .nav{margin-bottom:1.2rem;font-size:.85rem}
 .nav a{color:#8ab4f8;margin-right:.8rem}
 .card{background:#1c1f25;border:1px solid #2a2f38;border-radius:6px;
       padding:1rem;margin-bottom:1rem}
 .big{font-size:1.8rem;color:#fff}
 button{background:#2d6cdf;color:#fff;border:0;border-radius:5px;
        padding:.55rem 1rem;font:inherit;cursor:pointer}
 button:disabled{background:#3a3f48;color:#7c848f;cursor:not-allowed}
 select{background:#14161a;color:#d7dbe0;border:1px solid #2a2f38;
        border-radius:5px;padding:.5rem;font:inherit;margin-right:.5rem}
 #bar{height:4px;background:#2a2f38;border-radius:2px;overflow:hidden;
      margin:.9rem 0;display:none}
 #fill{height:100%;width:0;background:#2d6cdf;transition:width .4s}
 .line{padding:.12rem 0;white-space:pre-wrap}
 .ok{color:#4ec27f}.warn{color:#e0b341}.fail{color:#e8615a}.info{color:#8f98a3}
 .tag{display:inline-block;width:3.6rem}
 pre{background:#14161a;border:1px solid #2a2f38;border-radius:5px;
     padding:.8rem;overflow-x:auto;color:#a9d5a0;font-size:.82rem}
 .muted{color:#7c848f;font-size:.8rem}
</style></head><body>
<h1>MadHatterNG</h1>
<div class="sub">UPS diagnostic &amp; battery status</div>
<div class="nav"><a href="history">history / graph</a><a href="sanity">sanity check</a></div>

<div class="card">
  <div class="big" id="disp">...</div>
  <div class="muted" id="meta">reading...</div>
</div>

<div class="card">
  <select id="watch">
    <option value="0">Quick check</option>
    <option value="60" selected>Sample 1 min</option>
    <option value="300">Sample 5 min</option>
    <option value="600">Sample 10 min</option>
  </select>
  <button id="run">Run diagnostic</button>
  <div id="bar"><div id="fill"></div></div>
  <div id="out" style="margin-top:.8rem"></div>
  <div id="cfg"></div>
</div>

<script>
const $=i=>document.getElementById(i);
const BASE=location.pathname.replace(/\/+$/,'');
const url=p=>BASE+'/'+p;

function say(msg,cls){
  $('out').innerHTML='<div class="line '+(cls||'fail')+'">'+
    '<span class="tag">['+(cls||'fail')+']</span>'+msg+'</div>';
}
async function getJSON(p){
  const r=await fetch(url(p),{headers:{'Accept':'application/json'}});
  if(!r.ok) throw new Error(p+' -> HTTP '+r.status);
  return r.json();
}
async function poll(){
  try{
    const r=await getJSON('status');
    $('disp').textContent=r.display||'--';
    if(r.reading){const d=r.reading;
      $('meta').textContent=`${d.backend} · ${d.voltage.toFixed(3)} V · `+
        `${d.soc.toFixed(1)}% · ${d.charging?'charging':'discharging'} · `+
        `${d.cells}S · ${d.cycles} cycles`;
    } else $('meta').textContent=r.error||'no reading yet';
  }catch(e){$('meta').textContent='status unavailable: '+e.message;}
}
function render(l){
  return `<div class="line ${l.level}"><span class="tag">[${l.level}]</span>`+
         `${l.text.replace(/</g,'&lt;')}</div>`;
}
function done(){
  $('run').disabled=false;$('run').textContent='Run diagnostic';
  $('bar').style.display='none';
}
async function report(){
  let r;
  try{ r=await getJSON('report'); }
  catch(e){ say('Lost contact with the plugin: '+e.message+
    '. If pwnagotchi restarted (check the log for "restarting"), the '+
    'diagnostic was killed with it.'); done(); return; }
  $('out').innerHTML=r.lines.map(render).join('');
  $('fill').style.width=(r.progress||0)+'%';
  if(r.config){$('cfg').innerHTML='<p class="muted">Suggested config.toml '+
    '(review before pasting):</p><pre>'+r.config.replace(/</g,'&lt;')+'</pre>';}
  if(!r.running){done();return;}
  setTimeout(report,1000);
}
$('run').onclick=async()=>{
  $('run').disabled=true;$('run').textContent='Running...';
  $('cfg').innerHTML='';$('out').innerHTML='';$('bar').style.display='block';
  try{
    const r=await getJSON('diagnose?watch='+$('watch').value);
    if(!r.started){say('Not started: '+(r.reason||'unknown'),'warn');done();return;}
  }catch(e){ say('Could not start: '+e.message); done(); return; }
  report();
};
poll();setInterval(poll,5000);
</script></body></html>
"""
