"""
sigstr_ng.py - pwnagotchi plugin (runs ON the pi)

SigStrNG is a bugfix + feature-upgrade rebuild of sigstr.py (bryzz42o,
Pwnagotchi-fsociety-plugins, v1.0.6) - source at
plugins_archive/bryzz42o/Pwnagotchi-fsociety-plugins/sigstr.py. The
original reads the current WiFi signal strength (RSSI) for whatever AP
pwnagotchi is associated with, and renders it on-screen as a text bar.

Confirmed bugs in the original (all source-verified, see NOTES.md for
the full writeup):

  1. `on_unload(self)` is missing the required `ui` parameter. This
     fork's real loader calls `on_unload(self, ui)` (see the framework
     facts below) - `TypeError: on_unload() takes 1 positional argument
     but 2 were given` is raised BEFORE the function body runs, so
     `self.timer.cancel()` never executes either: the background timer
     thread leaks on every unload, on top of the crash itself. Fixed:
     the correct `on_unload(self, ui)` signature - and, per bug #3
     below, there's no longer a timer thread to leak in the first
     place.
  2. `refresh()` (the original's timer callback) calls
     `plugins.notify(f"{TAG} Refreshing signal strength")`.
     `pwnagotchi.plugins.notify` does not exist anywhere in this fork's
     real framework (confirmed against
     `pwnagotchi/plugins/__init__.py`) - this raised `AttributeError`
     on every single timer tick (every `REFRESH_INTERVAL` seconds,
     forever) in a background thread, from the moment the plugin
     loaded. Fixed: the call is removed entirely (this rebuild uses
     `logging.debug(...)` where a log line is actually wanted).
  3. A redundant self-rescheduling `threading.Timer` loop
     (`refresh()`), whose only real purpose in the original was to
     call the now-broken `plugins.notify(...)` above. The actual
     signal-strength read-and-display logic already lived entirely in
     `on_ui_update(self, ui)`, which this fork's own UI refresh loop
     already calls periodically on its own - a plugin does not need a
     second, separate timer thread just to get periodic execution for
     UI purposes. Fixed: the timer thread is removed entirely (this
     also fully resolves the thread-leak half of bug #1 - there is no
     longer a thread to leak on unload). The "how often do we actually
     re-measure RSSI vs. how often the UI merely redraws" distinction
     the original's `REFRESH_INTERVAL` was trying to express is kept,
     but as a lightweight timestamp comparison inside
     `on_ui_update`/`_measure` itself (`refresh_interval` option) -
     never as a background thread.
  4. Hardcoded interface name `"wlan0"`. Some builds/adapters enumerate
     differently. Fixed: `interface` is now a config option, with a
     fallback that lists available wireless interfaces (`iw dev`
     output, parsed by `list_wireless_interfaces()`) and picks the
     first one if the configured interface isn't among them, logging a
     clear warning when it falls back (`_resolve_interface`).
  5. `generate_signal_bar` used `'░'` (a light/"empty-looking" shade)
     for the FILLED portion of the bar and `'█'` (a solid/"full-
     looking" block) for the EMPTY portion - visually backwards from
     how a signal bar should read at a glance. Fixed: swapped, so
     `'█'` is filled/strong signal and `'░'` is empty/weak, in
     `generate_signal_bar()` below.

Framework facts this rebuild relies on (verified against the cloned
jayofelony/pwnagotchi fork, `pwnagotchi/plugins/__init__.py` -
identical facts already documented and relied on by
`bluetooth_recon_ng.py` and `MadHatterNG.py` in this same repo):
  - `load_from_file()` registers a plugin as
    `plugin_name = os.path.basename(filename.replace(".py", ""))` -
    the file's exact basename, case-sensitive. `load()` looks up both
    the `enabled` flag and the options table in
    `config['main']['plugins'][name]` under that SAME key. So the
    config section for this file MUST be
    `[main.plugins.sigstr_ng]` (matching `sigstr_ng.py`, see the
    naming section below), or the plugin silently never loads.
  - `plugins.load()` does `plugin.options =
    config['main']['plugins'][name]` - a RAW assignment of the parsed
    TOML table. `__defaults__` is NEVER merged by the framework
    itself. This rebuild uses the module-level `DEFAULTS` dict +
    `_opt()`-per-read pattern already established in this repo by
    `crack_house_ng.py`/`timer_ng.py`/`gps_tagger_ng.py`/
    `bluetooth_recon_ng.py`, rather than `bluetooth_recon_ng.py`'s
    sibling `__defaults__`-in-`__init__` pattern used by
    `MadHatterNG.py` - either is correct; this file follows the
    snake_case-filename suites' convention since `sigstr_ng.py` is
    itself snake_case (see `bluetooth_recon_ng.py`'s own docstring for
    the same reasoning).
  - Real hook signatures: `on_loaded(self)`, `on_ready(self, agent)`,
    `on_ui_setup(self, ui)`, `on_ui_update(self, ui)`,
    `on_unload(self, ui)` (note the required `ui` parameter - the
    exact thing the original got wrong, bug #1 above),
    `on_webhook(self, path, request)`. There is no
    `pwnagotchi.plugins.notify()` function (bug #2 above).
  - `on_ui_update(self, ui)` is already called periodically by this
    fork's own UI refresh loop - see bug #3 above for why this plugin
    does not run a separate timer/thread of its own.

Naming note: unlike `MadHatterNG.py` (which keeps a capital-NG,
no-underscore filename per an explicit request), this plugin follows
`bluetooth_recon_ng.py`'s and every other `_ng.py` suite's convention:
snake_case file `sigstr_ng.py`, config section
`[main.plugins.sigstr_ng]` - matching the file's basename exactly, per
the verified framework fact above.

What's new on top of the fixed original (all approved - see NOTES.md
for the full design writeup):
  1. **Signal history sparkline.** A small, bounded ring buffer
     (`collections.deque(maxlen=history_length)`, default 30 points -
     so this can never grow without limit) of recent RSSI-percent
     readings, rendered as a compact unicode sparkline
     (`render_sparkline()`, using the standard block-height character
     set `▁▂▃▄▅▆▇█`) so the user can see whether signal is trending
     stronger or weaker, not just its instantaneous value. Shown both
     on-screen (the most recent `sparkline_display_points`) and in
     full on the webhook status page.
  2. **Threshold-coded reading.** Classified into strong/medium/weak
     bands (`classify_signal()`, configurable
     `strong_threshold_dbm`/`weak_threshold_dbm`) and reflected as a
     short tag (`STR`/`MED`/`WEAK`) prefixed onto the on-screen bar, so
     it reads fast on the small e-ink displays this project's hardware
     uses, rather than requiring the user to mentally convert a raw
     dBm/percent number into "is this actually good or bad".
  3. **RSSI-vs-handshake-capture correlation logging.** Optional
     (`correlate_handshakes`, default `false`), best-effort, matching
     the pattern `bluetooth_recon_ng.py`/`MadHatterNG.py` already use
     for reading another suite's output file and degrading silently if
     it's missing/malformed. If `timer-suite`'s per-handshake CSV
     (default path `/etc/pwnagotchi/timer_ng.csv`, confirmed against
     `timer_ng.py`'s own `output_path` default and `FIELDNAMES` -
     `timestamp,network,time_to_deauth,time_to_handshake,
     time_between_deauth_and_handshake`) is present, each handshake
     timestamp in it is paired with the closest RSSI reading from this
     plugin's own history (within `correlation_window_minutes`), so
     the user can see "how strong was my signal when I actually got a
     capture" on the webhook page. Never crashes or blocks if the
     other suite's file doesn't exist or is malformed - wrapped in the
     same graceful-degradation pattern (`try`/`except FileNotFoundError`
     + a broad `except Exception` with a debug log line, never fatal).
  4. **On-screen positioning**, matching `MadHatterNG.py`'s exact
     convention: `ui_position_x`/`ui_position_y`, with the same
     negative-x-means-"this many pixels in from the right edge"
     behavior (`pos_x = ui.width() + configured_x if configured_x < 0
     else configured_x`, clamped to stay on-screen), copied from
     `MadHatterNG.py`'s `on_ui_setup`. The original hardcoded a fixed
     `(0, 205)` position; this rebuild keeps `(0, 205)` as the
     *default* but makes both axes configurable, including the
     negative-x convention.
  5. **A webhook status page** (`on_webhook`/`_status_page`, plain
     HTML, `html.escape()` on every interpolated value - including
     the interface name, which comes from external `iw dev` command
     output and is therefore not fully trusted input) showing: the
     current reading, the sparkline history, the strong/medium/weak
     classification and its thresholds, which interface is configured
     vs. actually detected-and-in-use, and the handshake-correlation
     table when that feature is enabled and data is available.

`enabled = false` is the shipped `config.toml` default, matching this
repo's convention of shipping every suite disabled-by-default for
opt-in.
"""

import csv
import html
import logging
import subprocess
import time
from collections import deque
from datetime import datetime, timedelta

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK

# This fork's plugin loader does NOT merge a plugin's __defaults__ into
# self.options (see module docstring) - every option must be read via
# _opt() with a real fallback here, never bare self.options[...].
DEFAULTS = {
    "enabled": False,
    # Wireless interface to read RSSI from. If this interface isn't
    # among what `iw dev` actually enumerates, the plugin falls back to
    # the first detected wireless interface and logs a warning - see
    # list_wireless_interfaces()/_resolve_interface().
    "interface": "wlan0",
    # Minimum seconds between actual RSSI measurements (an `iw dev ...
    # link` shell-out). The UI refresh loop may call on_ui_update more
    # often than this - between measurements, the last known reading is
    # simply redrawn, no shell-out repeated. This replaces the original
    # plugin's separate threading.Timer reschedule loop (see the
    # module docstring's bug #3) with a plain timestamp comparison.
    "refresh_interval": 2,
    # Width (in bar characters) of the on-screen signal bar.
    "bar_length": 10,
    # How many recent readings to keep in the bounded sparkline
    # history ring buffer (collections.deque(maxlen=...) - this can
    # never grow past this cap).
    "history_length": 30,
    # Whether to append a compact sparkline after the bar on-screen.
    # The webhook status page always shows the full retained history
    # regardless of this setting.
    "sparkline_enabled": True,
    # How many of the most recent history points to show in the
    # on-screen sparkline (kept short so the element stays readable on
    # small displays); the webhook page shows the full history.
    "sparkline_display_points": 12,
    # Strong/medium/weak classification thresholds, in dBm. A reading
    # >= strong_threshold_dbm is "strong"; <= weak_threshold_dbm is
    # "weak"; anything in between is "medium". Typical WiFi RSSI runs
    # roughly -30 (excellent, very close) to -90 (unusable).
    "strong_threshold_dbm": -60,
    "weak_threshold_dbm": -80,
    # On-screen label and position. Negative ui_position_x means "this
    # many pixels in from the right edge" - see MadHatterNG.py's
    # on_ui_setup for the exact same convention this copies.
    "label": "Signal",
    "ui_position_x": 0,
    "ui_position_y": 205,
    # Optional, best-effort correlation against timer-suite's
    # per-handshake CSV. Off by default - see the module docstring's
    # "new feature 3".
    "correlate_handshakes": False,
    "timer_csv_path": "/etc/pwnagotchi/timer_ng.csv",
    "correlation_window_minutes": 5,
    "correlation_log_max_points": 200,
}

ELEMENT_NAME = "sigstr_ng"

# Standard 8-level block-height sparkline character set (low -> high).
SPARK_CHARS = "▁▂▃▄▅▆▇█"  # ▁▂▃▄▅▆▇█

TAG_FOR_CLASS = {
    "strong": "STR",
    "medium": "MED",
    "weak": "WEAK",
    "unknown": "N/A",
}


# ---------------------------------------------------------------------------
# Pure/testable helpers - no framework or subprocess dependency in the
# math itself, only in the two I/O functions below.
# ---------------------------------------------------------------------------

def generate_signal_bar(percent, bar_length=10):
    """Renders `percent` (0-100) as a `|████░░░░░░|`-style bar.

    Fixed vs. the original (module docstring bug #5): '█' now means
    filled/strong, '░' means empty/weak - the original had these two
    characters backwards.
    """
    bar_length = int(bar_length) if bar_length else 10
    if bar_length <= 0:
        bar_length = 10
    percent = 0.0 if percent is None else max(0.0, min(100.0, float(percent)))
    filled = int(percent / (100.0 / bar_length))
    filled = max(0, min(bar_length, filled))
    empty = bar_length - filled
    return "|" + ("█" * filled) + ("░" * empty) + "|"


def render_sparkline(percent_values):
    """Renders a list of 0-100 percent values as a compact unicode
    sparkline. Uses a fixed 0-100 scale (not a dynamic min/max over the
    window) so consecutive sparklines stay directly comparable to each
    other, rather than each one silently rescaling to its own data."""
    if not percent_values:
        return ""
    chars = []
    top = len(SPARK_CHARS) - 1
    for value in percent_values:
        value = 0.0 if value is None else max(0.0, min(100.0, float(value)))
        idx = int(round((value / 100.0) * top))
        idx = max(0, min(top, idx))
        chars.append(SPARK_CHARS[idx])
    return "".join(chars)


def classify_signal(dbm, strong_threshold_dbm, weak_threshold_dbm):
    """-> "strong" / "medium" / "weak" / "unknown" (dbm is None)."""
    if dbm is None:
        return "unknown"
    if dbm >= strong_threshold_dbm:
        return "strong"
    if dbm <= weak_threshold_dbm:
        return "weak"
    return "medium"


def list_wireless_interfaces():
    """Parses `iw dev` output into a list of interface names. Returns
    [] (never raises) if `iw` is missing, times out, or errors - the
    caller treats an empty list as "couldn't enumerate", not "no
    interfaces exist"."""
    try:
        output = subprocess.check_output(
            ["iw", "dev"], stderr=subprocess.DEVNULL, timeout=5
        ).decode("utf-8", errors="replace")
    except (FileNotFoundError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, OSError) as exc:
        logging.debug("[SigStrNG] couldn't list wireless interfaces via "
                      "'iw dev': %r", exc)
        return []
    interfaces = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Interface "):
            interfaces.append(line.split("Interface ", 1)[1].strip())
    return interfaces


def read_signal_strength(interface):
    """-> {"dbm": int, "percent": float} or None (not associated /
    interface missing / iw failed / unparseable output). Never raises."""
    try:
        output = subprocess.check_output(
            ["iw", "dev", interface, "link"], stderr=subprocess.DEVNULL, timeout=5
        ).decode("utf-8", errors="replace")
    except (FileNotFoundError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, OSError) as exc:
        logging.debug("[SigStrNG] couldn't read signal for %s: %r", interface, exc)
        return None
    if "signal:" not in output:
        return None
    try:
        dbm = int(output.split("signal: ", 1)[1].split(" dBm", 1)[0].strip())
    except (IndexError, ValueError) as exc:
        logging.debug("[SigStrNG] couldn't parse 'iw dev %s link' output: %r",
                      interface, exc)
        return None
    percent = max(0.0, min(100.0, (dbm + 100) / 50 * 100))
    return {"dbm": dbm, "percent": percent}


def read_handshake_events(path):
    """Best-effort parse of timer_ng.csv's timestamp/network columns
    into a list of {"timestamp": <raw iso string>, "network": str,
    "dt": datetime}. Same optional-source degrade pattern as
    MadHatterNG.py's _read_timer_activity_timestamps /
    bluetooth_recon_ng.py's _nearby_wifi_networks: a missing or
    unreadable file just means no correlation data, never a crash."""
    events = []
    try:
        with open(path, newline="") as handle:
            for row in csv.DictReader(handle):
                raw_ts = row.get("timestamp")
                if not raw_ts:
                    continue
                try:
                    dt = datetime.fromisoformat(raw_ts)
                except (TypeError, ValueError):
                    continue
                events.append({
                    "timestamp": raw_ts,
                    "network": row.get("network") or "",
                    "dt": dt,
                })
    except FileNotFoundError:
        logging.debug("[SigStrNG] timer_csv_path %s not found, skipping "
                      "handshake correlation", path)
    except Exception as exc:
        logging.debug("[SigStrNG] couldn't read timer_csv_path %s: %r", path, exc)
    return events


def correlate_handshakes(history_points, events, window_seconds, max_points=200):
    """Pairs each handshake `events` entry with the closest reading in
    `history_points` (each a dict with "t" epoch-seconds and "dbm",
    ascending or not - order doesn't matter here) within
    `window_seconds`. Returns a list of {"timestamp", "network",
    "rssi_dbm", "delta_seconds"} dicts, most recent handshake first,
    capped at `max_points`. `rssi_dbm`/`delta_seconds` are None when no
    reading falls inside the window for that handshake - this is
    reported, not silently dropped, so a gap in coverage is visible on
    the webhook page rather than looking like a clean miss."""
    results = []
    for event in events:
        try:
            event_epoch = event["dt"].timestamp()
        except (OverflowError, OSError, ValueError):
            continue
        best = None
        best_delta = None
        for point in history_points:
            delta = abs(point["t"] - event_epoch)
            if delta <= window_seconds and (best_delta is None or delta < best_delta):
                best = point
                best_delta = delta
        results.append({
            "timestamp": event["timestamp"],
            "network": event["network"],
            "rssi_dbm": best["dbm"] if best is not None else None,
            "delta_seconds": best_delta,
        })
    results.sort(key=lambda r: r["timestamp"], reverse=True)
    return results[:max(0, int(max_points))]


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class SigStrNG(plugins.Plugin):
    __author__ = ("bugfix + feature-upgrade rebuild for this project's plugin "
                 "audit, of sigstr.py (bryzz42o, Pwnagotchi-fsociety-plugins)")
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = ("Signal-strength bar with a trend sparkline, "
                       "strong/medium/weak classification, and optional "
                       "handshake-capture correlation.")
    __name__ = "SigStrNG"
    __help__ = __description__
    __dependencies__ = {
        # `iw` is already part of the base OS image this project targets
        # (same as bluetooth_recon_ng.py's `bluez` dependency being
        # already-present-but-declared-for-clarity) - nothing extra to
        # install beyond stdlib for this plugin itself.
        "apt": ["iw"],
        "pip": [],
    }

    def __init__(self):
        self.history = None
        self._last_reading = None
        self._last_measure_ts = 0.0
        self._configured_interface = None
        self._active_interface = None
        self._interface_detected = False

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    def _opt_int(self, key, default=0):
        try:
            return int(self._opt(key))
        except (TypeError, ValueError):
            return int(default)

    def _opt_float(self, key, default=0.0):
        try:
            return float(self._opt(key))
        except (TypeError, ValueError):
            return float(default)

    # ------------------------------------------------------------------
    # Lifecycle

    def on_loaded(self):
        length = max(1, self._opt_int("history_length", 30))
        self.history = deque(maxlen=length)
        logging.info("[SigStrNG] plugin loaded")

    def on_unload(self, ui):
        # Bug #1 fix: the real framework calls on_unload(self, ui) - the
        # original defined on_unload(self) and would have crashed with a
        # TypeError before this body ever ran. There is also no
        # background timer thread to cancel here (bug #3 fix) - nothing
        # left to clean up besides the UI element.
        logging.info("[SigStrNG] plugin unloading")
        with ui._lock:
            try:
                ui.remove_element(ELEMENT_NAME)
            except KeyError:
                pass

    # ------------------------------------------------------------------
    # UI

    def on_ui_setup(self, ui):
        try:
            configured_x = self._opt_int("ui_position_x", 0)
            # Negative x means "this many pixels in from the right edge" -
            # copied character-for-character from MadHatterNG.py's
            # on_ui_setup, which itself preserves mad_hatter.py's original
            # convention. Keeps the element on-screen on any display width.
            pos_x = ui.width() + configured_x if configured_x < 0 else configured_x
            pos_x = max(0, min(pos_x, max(0, ui.width() - 10)))
            pos_y = self._opt_int("ui_position_y", 205)

            label = str(self._opt("label") or "").strip() or None

            ui.add_element(ELEMENT_NAME, LabeledValue(
                color=BLACK,
                label=label,
                value="",
                position=(pos_x, pos_y),
                label_font=fonts.Bold,
                text_font=fonts.Medium,
            ))
        except Exception as exc:
            logging.error("[SigStrNG] UI setup failed: %r", exc)

    def on_ui_update(self, ui):
        now = time.time()
        interval = self._opt_float("refresh_interval", 2.0)
        if self._last_reading is None or interval <= 0 or (now - self._last_measure_ts) >= interval:
            self._measure(now)
        try:
            ui.set(ELEMENT_NAME, self._render_text())
        except Exception:
            pass

    def _render_text(self):
        reading = self._last_reading
        if reading is None:
            return "n/a"

        strong_th = self._opt_int("strong_threshold_dbm", -60)
        weak_th = self._opt_int("weak_threshold_dbm", -80)
        tag = TAG_FOR_CLASS[classify_signal(reading["dbm"], strong_th, weak_th)]

        bar = generate_signal_bar(reading["percent"], self._opt_int("bar_length", 10))
        text = "%s %s" % (tag, bar)

        if self._opt("sparkline_enabled"):
            n = max(0, self._opt_int("sparkline_display_points", 12))
            values = [p["percent"] for p in list(self.history)[-n:]] if n else []
            spark = render_sparkline(values)
            if spark:
                text += " " + spark
        return text

    # ------------------------------------------------------------------
    # Measurement

    def _resolve_interface(self):
        configured = str(self._opt("interface") or "wlan0")
        interfaces = list_wireless_interfaces()
        if not interfaces:
            # Bug #4 fix (partial): we couldn't enumerate at all (iw
            # missing/failed) - trust the configured value rather than
            # refusing to try it.
            return configured, configured, False
        if configured in interfaces:
            return configured, configured, True
        fallback = interfaces[0]
        logging.warning(
            "[SigStrNG] configured interface %r not found among detected "
            "wireless interfaces (%s) - falling back to %r",
            configured, ", ".join(interfaces), fallback)
        return configured, fallback, True

    def _measure(self, now):
        configured, active, detected = self._resolve_interface()
        self._configured_interface = configured
        self._active_interface = active
        self._interface_detected = detected
        self._last_measure_ts = now

        reading = read_signal_strength(active)
        self._last_reading = reading
        if reading is not None and self.history is not None:
            self.history.append({
                "t": now, "dbm": reading["dbm"], "percent": reading["percent"],
            })

    # ------------------------------------------------------------------
    # Handshake correlation (feature 3 - optional, off by default)

    def _correlate_handshakes(self):
        if not self._opt("correlate_handshakes"):
            return []
        events = read_handshake_events(self._opt("timer_csv_path"))
        if not events or self.history is None:
            return []
        window_seconds = max(0.0, self._opt_float("correlation_window_minutes", 5) * 60)
        max_points = self._opt_int("correlation_log_max_points", 200)
        return correlate_handshakes(list(self.history), events, window_seconds, max_points)

    # ------------------------------------------------------------------
    # Webhook: status page

    def _status_page(self):
        reading = self._last_reading
        dbm = reading["dbm"] if reading else None
        percent = reading["percent"] if reading else None

        strong_th = self._opt_int("strong_threshold_dbm", -60)
        weak_th = self._opt_int("weak_threshold_dbm", -80)
        tag = TAG_FOR_CLASS[classify_signal(dbm, strong_th, weak_th)]

        bar = generate_signal_bar(percent, self._opt_int("bar_length", 10)) \
            if percent is not None else "n/a"

        spark_values = [p["percent"] for p in self.history] if self.history else []
        spark = render_sparkline(spark_values) or "(no data yet)"

        configured_iface = html.escape(str(self._configured_interface or self._opt("interface")))
        active_iface = html.escape(str(self._active_interface or "n/a"))
        detect_note = ("auto-detected via 'iw dev'" if self._interface_detected
                       else "'iw dev' listing unavailable - using configured value as-is")

        if self._opt("correlate_handshakes"):
            points = self._correlate_handshakes()
            if points:
                rows = "".join(
                    "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (
                        html.escape(p["timestamp"]),
                        html.escape(p["network"]),
                        ("%d dBm" % p["rssi_dbm"]) if p["rssi_dbm"] is not None else "n/a",
                        ("%ds" % int(p["delta_seconds"])) if p["delta_seconds"] is not None else "n/a",
                    )
                    for p in points
                )
                correlation_html = (
                    "<table border='1' cellpadding='4'>"
                    "<tr><th>Handshake time</th><th>Network</th>"
                    "<th>RSSI at capture</th><th>Time delta</th></tr>"
                    "%s</table>" % rows
                )
            else:
                correlation_html = (
                    "<p><i>no correlation data yet - either %s doesn't exist "
                    "yet, or no signal reading fell within "
                    "correlation_window_minutes of a logged handshake</i></p>"
                    % html.escape(str(self._opt("timer_csv_path")))
                )
        else:
            correlation_html = "<p><i>disabled (correlate_handshakes = false)</i></p>"

        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>SigStrNG</title>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "</head><body style='font-family: sans-serif;'>"
            "<h2>SigStrNG</h2>"
            "<p>Current: <b>%s</b> (%s%%) &nbsp; [%s]</p>"
            "<p>Bar: <code>%s</code></p>"
            "<p>Sparkline (%d point(s) retained): <code>%s</code></p>"
            "<p>Thresholds: strong &ge; %d dBm, weak &le; %d dBm</p>"
            "<p>Interface: configured=<code>%s</code>, in use=<code>%s</code> (%s)</p>"
            "<h3>Handshake correlation</h3>%s"
            "</body></html>"
            % (
                ("%d dBm" % dbm) if dbm is not None else "n/a",
                ("%.0f" % percent) if percent is not None else "n/a",
                html.escape(tag),
                html.escape(bar),
                len(spark_values), html.escape(spark),
                strong_th, weak_th,
                configured_iface, active_iface, html.escape(detect_note),
                correlation_html,
            )
        )

    def on_webhook(self, path, request):
        path = (path or "").strip("/")
        if path in ("", "index"):
            return self._status_page()
        return "Not found", 404
