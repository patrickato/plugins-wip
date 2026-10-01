# Notes: MadHatterNG

## Why this one was a feature upgrade, not a bugfix

`mad_hatter.py` (AlienMajik, v2.3.0) has **no known bugs** - its own
CHANGELOG documents a prior 2.0.0 bugfix pass (INA219 calibration,
MAX170xx byte-swap, PiSugar 2 vs 2 Pro vs 3 register maps, shutdown
grace counting UI refreshes instead of polls, `on_unload` crashing on a
failed init, and more), and this rebuild's own read of the current
source didn't turn up anything new. This is also the **one plugin in
the whole audit confirmed to match hardware actually owned** - a
Waveshare UPS 3S HAT (INA219-based, matched via `ups_type = "waveshare"`,
aliased to the `ina219` backend). The single-purpose PiSugar/PiBat/
PiVoyager plugins reviewed alongside this one were all removed for not
matching that hardware; this one was kept and upgraded specifically
because it does.

So the job here was purely additive: carry every existing chip
backend, calibration formula, auto-detection rule, and the existing
on-screen positioning behavior over **unchanged**, and add four
user-approved features on top.

## What was preserved unchanged (verified line-by-line against the
## original)

- **Every backend's math**: MAX170xx's 78.125uV/LSB VCELL and raw/256.0
  SOC formulas, its MODE-register quick-start and CONFIG-register
  alert-threshold writes; INA219's shunt-voltage-based (not
  CURRENT-register-based) current calculation with configurable
  `shunt_ohms`; PiSugar 2's two's-complement-with-a-sign-bit voltage
  decode at 0xA2/0xA3; PiSugar 2 Pro's simpler unsigned decode at
  0x64/0x65; PiSugar 3's big-endian millivolt read at 0x22/0x23. None
  of this was touched - see `MadHatterNG.py`'s test suite for
  spot-checks against known-good encoded register values for each.
- **The `ups_type` alias table**, including `"waveshare": "ina219"`,
  and the INA219-vs-INA226/230/237/238/260 manufacturer-ID/die-ID
  disambiguation (`identify_ina_variant`) that refuses to drive a
  chip whose registers scale differently rather than silently
  misreading it.
- **`__defaults__`/`on_loaded` merge pattern.** This fork's real loader
  (`pwnagotchi/plugins/__init__.py`, `load()`) does
  `plugin.options = config['main']['plugins'][name]` - a RAW
  assignment of the parsed TOML table, never merged with
  `__defaults__`. The original already worked around this correctly
  (`self.options = dict(self.__defaults__)` in `__init__`, then
  `merged = dict(self.__defaults__); merged.update(user_options);
  self.options = merged` in `on_loaded`) - this rebuild keeps that
  exact pattern, verbatim, rather than switching to the module-level
  `DEFAULTS`+`_opt()` pattern used by some other suites in this repo
  (`crack_house_ng.py`/`timer_ng.py`/`bluetooth_recon_ng.py`). Both
  patterns are correct; the original's own was preserved because the
  task explicitly asked not to "fix" things that already work.
- **On-screen positioning**, `ui_position_x`/`ui_position_y`, including
  the negative-x-means-"this many pixels in from the right edge"
  convention (`pos_x = ui.width() + configured_x if configured_x < 0
  else configured_x`, then clamped to stay on-screen) - copied
  character-for-character from `on_ui_setup`, with only the UI element
  key renamed (`mad_hatter` -> `mad_hatter_ng`, an internal string,
  unrelated to the framework's plugin *registration* name - see the
  naming section below).
- **The diagnostic webhook** (`/status`, `/json`, `/diagnose`,
  `/report`, and the standalone `mad_hatter_doctor.py` integration via
  `_find_doctor`/`_run_doctor_script`) - kept working exactly as
  before, with three NEW paths added alongside it (`/history`,
  `/history.json`, `/sanity`), not replacing anything.
- **The background polling thread / lock discipline** - I2C reads
  still happen off the UI thread through `bus_lock`, `on_ui_update`
  still does no I/O at all (just reads the last cached string).

## The naming decision - a verified framework fact, not just a style
## note

The task asked for the plugin file to be named exactly `MadHatterNG.py`
(capital NG, no underscore - unlike every other suite in this repo,
which uses `_ng.py`), with the config section as
`main.plugins.mad_hatter_ng` (snake_case), reasoning that TOML keys
need to be "identifier-ish."

Two things are worth stating plainly here, both verified against the
real cloned `jayofelony/pwnagotchi` fork rather than assumed:

1. **TOML doesn't actually require snake_case.** A bare TOML key can
   contain letters, digits, underscores, and dashes in any case -
   `MadHatterNG` is perfectly valid TOML on its own. That specific
   part of the original reasoning doesn't hold up.
2. **More importantly: the real loader doesn't use a "config
   namespace" separate from the file name at all.** In
   `pwnagotchi/plugins/__init__.py`:
   - `load_from_path`/`load_from_file` derive `plugin_name` as
     `os.path.basename(filename.replace(".py", ""))` - the file's
     basename, exact case.
   - `Plugin.__init_subclass__` registers the instance as
     `loaded[cls.__module__.split('.')[0]]`, and `cls.__module__` is
     set from that same `plugin_name` at import time.
   - `load()`'s `enabled` list, and the per-plugin options assignment
     (`plugin.options = config['main']['plugins'][name]`), both look
     `config['main']['plugins']` up **by that exact same key**.
   - A class body's `__name__ = "..."` (which the *original*
     `mad_hatter.py` also has, set to `"mad_hatter"`) has **zero
     effect** on any of this - Python does not let a class-body
     assignment override `type.__name__`, confirmed empirically
     (`class Foo: __name__ = "bar"` still has `Foo.__name__ ==
     "Foo"`). It's cosmetic only, on both the original and this
     rebuild.

   Concretely: I ran the REAL `load_from_file()` against this file in
   this project's cloned framework and confirmed
   `pwnagotchi.plugins.loaded` ends up keyed `"MadHatterNG"` (capital
   NG, matching the filename) - never `"mad_hatter_ng"`.

   **Consequence:** a config section spelled `[main.plugins.mad_hatter_ng]`
   for a file named `MadHatterNG.py` would never even appear in the
   framework's `enabled` list. The plugin would not partially work or
   silently use defaults - it would **never load at all**, with no
   error anywhere.

   Given that, this rebuild ships `config.toml` with the section
   spelled `[main.plugins.MadHatterNG]` - matching the file's real,
   verified registration key - rather than the `mad_hatter_ng` spelling
   originally asked for, which would have shipped a config that
   silently never enables the plugin. The Python **class** is still
   named `MadHatterNG` and the **file** is still named exactly
   `MadHatterNG.py`, per the explicit request; only the config
   section's spelling was corrected to what the verified framework
   mechanism actually requires. This is called out prominently in both
   `MadHatterNG.py`'s module docstring and `config.toml`'s header
   comment, and in the README's "A naming gotcha" section, so it isn't
   a silent deviation.

   If a snake_case config section (`mad_hatter_ng`) is genuinely
   wanted, the fix is to rename the FILE to `mad_hatter_ng.py` and
   update `config.toml`'s section to match - the two must always agree
   exactly, capitalization included. That's a rename, not a code
   change - none of `MadHatterNG.py`'s logic depends on its own
   filename anywhere except the two `_suggested_config`/module-docstring
   strings that print the config section as user-facing text.

   Internal identifiers that are NOT the framework registration key -
   the on-screen UI element name (`"mad_hatter_ng"`), the poll thread's
   name, the state/history file paths - were left as
   `mad_hatter_ng`-flavored strings for readability; none of these
   need to match the plugin's registration name and changing them
   doesn't affect loading.

## New feature 1: threshold notifications

`notify_on_threshold` (default `false`) + `notify_backend` (`"apprise"`
/ `"discord"` / `"auto"`, default `"auto"`).

Neither `apprise_notify_ng.py` nor `discord_ng.py` expose a stable
public `notify()`-style method - both funnel every outbound message
through an internal `_queue_notification(...)` method (different
signatures: apprise's takes `(title, body, agent)`, discord's takes
`(content, embed=None)`). This rebuild looks the target plugin up via
`pwnagotchi.plugins.loaded.get(name)` (confirmed to be a plain
`{plugin_name: instance}` dict, populated by the same loader described
above) and calls that internal method directly, guarded three ways:

1. `target is None` (plugin not loaded at all) -> log and skip.
2. The method/attribute it needs doesn't exist, or the target is
   loaded-but-not-ready (apprise's `self._queue` is `None` until
   `on_loaded` successfully imports the `apprise` package and builds a
   queue; discord's `self.webhook_url` is `None` until configured) ->
   log and skip.
3. Anything else raises -> caught, logged, skipped.

None of these ever crash `MadHatterNG`'s own poll loop - "if the
configured/detected backend plugin isn't loaded, log and skip
gracefully, never crash" was the explicit requirement, and this covers
both "not loaded" and "loaded but not actually able to send" (a
distinction the original spec didn't call out but that's necessary in
practice - a fresh `apprise-notify-suite` install with no `urls`
configured yet is "loaded" but would otherwise `AttributeError` on
`self._queue.put_nowait`).

**Fires once per crossing, not every poll**: `_notify_level()` maps a
non-charging reading to `None` / `"warning"` / `"critical"` /
`"shutdown_imminent"` using the existing `warning_threshold`/
`shutdown_threshold`/`critical_threshold` options (independent of
`shutdown_enabled`, so notifications work even with auto-shutdown
turned off). `_maybe_notify()` tracks a `_notified` flag per level;
crossing into a level marks it (and every lower level) as already-
notified so a bounce around inside the same band doesn't refire, and
recovering (charging, or SoC back above `warning_threshold`) clears
every flag so the next real dip notifies again. Crossing straight from
"fine" to "shutdown_imminent" (skipping past warning/critical) still
fires exactly once, for the highest level actually reached - not three
separate notifications.

## New feature 2: history logging + webhook graph

`HistoryLog` is a `collections.deque(maxlen=history_max_points)` (default
720, at `history_log_interval_seconds` = 60s, i.e. 12 hours of
1-minute samples) - genuinely bounded, not a list that grows forever
and gets truncated occasionally; the deque itself drops the oldest
point the instant a new one pushes it over the cap. Persisted to
`history_path` (default `/root/.mad_hatter_ng_history.json`) via the
same atomic write-to-tempfile-then-`os.replace()` pattern the original
already uses for its own state file, on the same 60-second cadence as
that state save, plus on `on_unload`.

`/plugins/MadHatterNG/history` renders it as an HTML page: two
dependency-free inline-SVG sparklines (`_svg_sparkline` - a plain
`<polyline>`, no charting library, no external request) for SoC% and
voltage, a table of the most recent 200 points, and the active/idle
drain-rate line from feature 3. `/plugins/MadHatterNG/history.json`
returns the same points as raw JSON for anyone who wants to graph it
themselves externally.

## New feature 3: drain-rate / activity correlation

`_read_timer_activity_timestamps()` reads `timer_csv_path` (default
`/etc/pwnagotchi/timer_ng.csv`, matching `timer-suite`'s own
`output_path` default and its real CSV schema - confirmed by reading
`timer_ng.py`'s own `FIELDNAMES`) into a sorted list of epoch-second
timestamps, using the exact same optional-source degrade pattern
already established by `bluetooth-recon-suite`'s
`_nearby_wifi_networks`/`_cracked_hostnames`: a missing or unreadable
file just means no correlation (an empty list), never a crash, logged
at `debug` level.

`correlate_drain_rate(points, activity_times, window_seconds)` walks
consecutive history points, skips any interval that's charging (a
rising SoC isn't a "drain") or spans more than an hour (a restart/
sleep gap, not real elapsed time), and buckets each remaining SoC-drop
interval as "active" (its midpoint timestamp falls within
`activity_correlation_window_minutes` of a timer-suite activity
timestamp) or "idle," accumulating percent-dropped and hours-elapsed
per bucket to compute a %/hour rate for each. This is shown on the
`/history` page alongside - not in place of - the existing flat
mAh/`avg_current_ma` time-remaining estimate, e.g.:

> Recent drain rate: **3.2%/hr** during active epochs, **1.1%/hr** idle

exactly the kind of extra, genuinely-informative data point the spec
asked for, without touching `_estimate_minutes()`'s existing
calculation at all.

## New feature 4: config sanity webhook

`sanity_checks(options, ups)` returns a flat list of `{level, text}`
entries (`ok`/`warn`/`fail`/`info`), rendered at
`/plugins/MadHatterNG/sanity`. What it checks, and why each one was
picked - all inspired by validation the original *already* does
defensively during backend selection, extended into an explicit report
rather than just a log line:

- **Configured `ups_type` vs. what actually got selected/detected**,
  plus every chip the original's own detection logic saw but
  deliberately declined to drive (`ups._rejected` - e.g. an INA226
  sharing an INA219 address range).
- **`shunt_ohms <= 0`** - the INA219 backend already silently falls
  back to `0.1` internally when this happens
  (`float(options.get("shunt_ohms", 0.1)) or 0.1`); the sanity page
  makes that otherwise-invisible fallback visible as a `fail`.
- **A fixed `battery_cells` that disagrees with the auto-detected pack
  voltage's implied cell count** (`ups.cells`, from the original's own
  `_resolve_cells` voltage-range heuristic) - a `warn`, since this is
  exactly the kind of thing that silently skews every SoC/time-
  remaining estimate without ever producing an error.
- **`charging_gpio` also listed in `reserved_gpios`** - an
  unambiguous `fail`, since it can never work.
- **`charging_gpio` colliding with a built-in reserved line or the
  configured display's known pins** (reusing the original's own
  `gpio_conflict()` function directly, not a reimplementation) - a
  `warn`, since the plugin already handles this gracefully at runtime
  (falls back to voltage-trend detection) but a user staring at "why
  is charge detection unreliable" deserves to see the real reason.

## Testing

60 checks in `tests/test_MadHatterNG.py`, all passing, run against the
real cloned `jayofelony/pwnagotchi` framework, the real `flask`
library (with a real `Flask` app context pushed, since `on_webhook`'s
`jsonify()` calls need one outside of an actual request), and the
real `pwnagotchi.plugins` loader. Covers:

- Real plugin registration, and specifically confirming the FILE-
  basename-is-the-real-key naming fact above against the actual
  loader (not just asserted in prose).
- The `__defaults__`/`on_loaded` merge pattern never `KeyError`s on an
  empty options dict, and never clobbers a user-supplied override.
- Preserved chip/calibration math: MAX170xx VCELL (78.125uV/LSB) and
  SOC (raw/256.0) formulas, INA219 bus-voltage and shunt-voltage/
  `shunt_ohms` current formulas (plus `invert_current`), PiSugar2's
  voltage decode, and the Li-ion OCV curve's boundary behavior - each
  checked against a hand-encoded fake I2C register value, not just
  "does it run."
- Preserved positioning: negative `ui_position_x` resolved from the
  right edge, a non-negative value used as-is, and clamping on a small
  (128px) display.
- Notifications: no notification below every threshold; exactly one
  notification on crossing `warning_threshold`; no re-fire while
  staying in the same band across repeated polls; a second,
  higher-level notification on crossing into `shutdown_imminent`;
  crossing-state reset on recovery so a later dip notifies again;
  graceful skip with nothing loaded; graceful skip with a
  loaded-but-not-ready backend (`_queue is None`); `notify_backend =
  "auto"` falling through to discord when apprise isn't loaded; a
  charging reading never notifying; and `notify_on_threshold = False`
  (the default) never calling anything.
- History: the deque never grows past `history_max_points` and keeps
  the most recent points; persistence round-trips through save/load;
  a missing history file loads as empty rather than crashing; the
  plugin-level `_log_history` respects `history_log_interval_seconds`
  (no double-logging within the window); the `/history` and
  `/history.json` webhook routes render/serialize correctly and
  include an inline SVG (never an external chart request).
- Correlation: a constructed two-interval history with one activity
  timestamp correctly produces a higher active-rate than idle-rate; a
  charging interval is excluded entirely; empty history degrades to
  `None`/`None` rather than a division error; a real-format
  `timer_ng.csv` fixture parses correctly; a missing CSV degrades to
  an empty timestamp list rather than crashing.
- Sanity checks: a fully-consistent config reports no `fail`; no
  initialised UPS is a `fail`; `shunt_ohms <= 0` is a `fail`; a
  mismatched fixed `battery_cells` is a `warn` (and a matching one is
  NOT flagged); `charging_gpio` colliding with a reserved line is
  flagged; `charging_gpio` also listed in `reserved_gpios` is a
  `fail`; and the `/sanity` webhook page renders and surfaces a real
  flagged issue.
- The pre-existing `/status` and index webhook routes still work
  unchanged, and an unrecognized path still raises a real Flask 404
  (`abort(404)`, exactly as the original does) rather than a 500 or a
  silent 200.
- `on_unload` persists the history log, and that persisted history
  survives a full unload/reload cycle.

Run with (from this directory):
```
python3 -m pytest tests/test_MadHatterNG.py -v
```
(or `pytest`, if your environment's `pytest` binary is on a different
interpreter than the one with `flask`/`Pillow`/etc. installed, point
`PYTHONPATH` or that venv's site-packages at wherever those live first
- this sandbox's default `pytest` binary needed exactly that.)

## Still needs real-hardware testing

This is the one plugin in the whole audit confirmed to target hardware
actually owned (a Waveshare UPS 3S HAT) - a real-hardware pass here is
disproportionately valuable compared to the rest of the audit:

- **The Waveshare UPS 3S HAT specifically** - confirm `shunt_ohms =
  0.1` is actually correct for this exact board revision (check the
  board's schematic/silkscreen if the current readings look scaled
  wrong), confirm `invert_current` isn't needed, and confirm the
  auto-detected `battery_cells` (should read 3S from a resting ~11-
  12.6V pack) matches the real pack.
- **GPIO charge detection** - untested against a real charging-status
  line on real hardware (the original's own `gpio_conflict()`/reserved-
  pin logic is preserved and unit-tested, but no physical GPIO was
  available to test against in this sandbox).
- **Threshold notifications against a real Discord webhook / Apprise
  target** - the plugin-to-plugin lookup and call are unit-tested
  against fakes standing in for `apprise_notify_ng`/`discord_ng`'s real
  classes; an actual message arriving in a real Discord channel or
  through a real Apprise URL hasn't been confirmed end-to-end.
- **A real discharge cycle** - to confirm `avg_current_ma`'s default
  (200) is in a sane ballpark for this specific HAT+pack combination,
  and that the drain-rate/activity correlation numbers on `/history`
  look sensible against genuine pwnagotchi activity (not just the
  synthetic fixtures in the test suite).
- **Config section naming** - the naming-gotcha fix above (shipping
  `[main.plugins.MadHatterNG]`) was verified against this project's
  cloned framework source and by actually running `load_from_file()`
  against `MadHatterNG.py` in that framework; it has not been
  confirmed against a live `pwnagotchi.service` restart on real
  hardware picking the plugin up from `config.toml`.

## Post-cluster-review fix: hardcoded sibling name made configurable

The post-cluster-review conflict pass found that `_notify_via_apprise`
and `_notify_via_discord` hardcoded `plugins.loaded.get("apprise_notify_ng")`
and `plugins.loaded.get("discord_ng")` - if either sibling suite's `.py`
file were ever renamed from its shipped name, the lookup would fail
silently (no log line, just a threshold notification that quietly never
arrives). Fixed by adding two new options, `apprise_plugin_name` and
`discord_plugin_name` (defaulting to the shipped names, so behavior is
unchanged out of the box), and looking those up via `self._opt(...)`
instead of the hardcoded strings. Also added `_log_notify_sibling_status()`,
called from `on_ready` (after all plugins have had a chance to load, to
avoid a false "not found" from load-order races), which logs an INFO
line naming whichever sibling(s) `notify_backend` would actually use, or
a WARNING telling you the configured name wasn't found and to check
`apprise_plugin_name`/`discord_plugin_name`. This only runs when
`notify_on_threshold` is enabled - it doesn't add log noise for the
common case where threshold notifications are off.

## Real-hardware fix (2026-10-01, Pi batch-3a)

Live-load on the Pi (no UPS HAT connected, empty I2C bus): `_try_init` retried
every 60s and logged a WARNING each time - indefinite log spam. Fixed: the retry
now uses exponential backoff (60s -> ... -> 30min cap) and only logs at WARNING
when the interval actually changes (repeats drop to DEBUG); a later successful
init resets the backoff so a plug/replug starts fresh at the short interval. It
still auto-detects the HAT when it appears, just quietly.
