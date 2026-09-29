# Notes: SigStrNG

## Why this one was a bugfix + feature-upgrade, not a redesign

`sigstr.py` does one useful thing (show live WiFi signal strength as a
bar on-screen) with a small, focused design that was fundamentally
sound - the bugs were entirely in the implementation (a crashing
unload signature, a call to a framework function that doesn't exist,
and a redundant background thread that existed only to make that
broken call). The job here was to fix the implementation, remove the
unneeded thread entirely, and layer the four approved improvements on
top of the resulting simpler design.

## Bugs found in sigstr.py (all source-verified)

1. **`on_unload(self)` missing the required `ui` parameter.** This
   fork's real loader calls every plugin's unload hook as
   `on_unload(self, ui)` (see the framework-facts section below). The
   original defined `on_unload(self)` - one positional parameter short
   - so `TypeError: on_unload() takes 1 positional argument but 2 were
   given` was raised immediately, **before the method body ever ran**.
   That means `self.timer.cancel()`, the original's only actual
   cleanup step, never executed either: every unload crashed loudly
   AND leaked the running `threading.Timer` background thread on top
   of it. Fixed: the correct `on_unload(self, ui)` signature - and
   since bug #3 below removes the timer thread entirely, there is
   nothing left to leak in the first place.

2. **`refresh()` called `pwnagotchi.plugins.notify(...)`, a function
   that does not exist.**
   ```python
   plugins.notify(f"{TAG} Refreshing signal strength")
   ```
   `pwnagotchi.plugins` has no `notify` attribute anywhere in this
   fork's real framework (confirmed directly against
   `pwnagotchi/plugins/__init__.py`, the same file every other suite
   in this repo's fixes have been verified against). Since `refresh()`
   was itself a self-rescheduling `threading.Timer` callback running
   every `REFRESH_INTERVAL` (2) seconds forever in a background
   thread, this raised `AttributeError` on a fixed 2-second cycle,
   silently, for as long as the plugin was loaded - visible only as
   thread-exception spam in the log, never surfaced to the user or the
   UI. Fixed: the call is removed entirely (a `logging.debug(...)`
   line is used in this rebuild anywhere a "just log this" call is
   actually wanted).

3. **A redundant self-rescheduling `threading.Timer` loop whose only
   real job was making the now-broken call in bug #2.** Stripping out
   the `plugins.notify(...)` call left `refresh()` doing nothing but
   rescheduling itself - the actual signal-read-and-display logic
   always lived entirely in `on_ui_update(self, ui)`, which this
   fork's own UI refresh loop already calls periodically on its own.
   A plugin does not need a second, independent timer thread just to
   get periodic execution for something the UI loop already drives.
   Fixed: the timer thread is removed entirely - `SigStrNG` has no
   `.timer` attribute and never imports `threading`. This also fully
   resolves the thread-leak half of bug #1 (there's no longer a thread
   for `on_unload` to need to cancel). The one real thing
   `REFRESH_INTERVAL` was doing - not hammering `iw dev ... link` on
   every single UI redraw - is kept, but as a plain timestamp
   comparison inside `on_ui_update`/`_measure` (the `refresh_interval`
   option), never as a background thread.

4. **Hardcoded interface name `"wlan0"`.** Some builds/USB adapters
   enumerate as `wlan1`, `wlx...`, etc. - a plugin that only ever reads
   `wlan0` silently shows nothing useful on those builds, with no
   indication why. Fixed: `interface` is a config option, resolved at
   measurement time via `list_wireless_interfaces()` (parses `iw dev`
   output). If the configured interface isn't among what's actually
   detected, the plugin falls back to the first detected wireless
   interface and logs a clear warning explaining the fallback
   (`_resolve_interface`) - it never silently reads the wrong
   interface without saying so.

5. **`generate_signal_bar`'s fill/empty characters were backwards.**
   ```python
   bar_segments = '░' * bar_length        # used for the FILLED portion
   empty_segments = '█' * (self.symbol_count - bar_length)  # used for EMPTY
   ```
   `'█'` (a solid block) reads as "full" and `'░'` (a light shade)
   reads as "empty" to virtually anyone glancing at a bar meter - the
   original had these swapped, so a *strong* signal rendered as a
   bar that looked mostly *empty*, and vice versa. This is a purely
   visual bug, but a meaningfully misleading one for a plugin whose
   entire purpose is an at-a-glance readout. Fixed: swapped in
   `generate_signal_bar()` - `'█'` is now filled/strong, `'░'` is
   empty/weak.

## Framework facts this rebuild relies on

Same verified facts as `bluetooth-recon-suite`/`mad-hatter-suite`/
`fix-region-suite` in this repo (`pwnagotchi/plugins/__init__.py`, the
cloned `jayofelony/pwnagotchi` fork):
- `load_from_file()` registers a plugin as
  `plugin_name = os.path.basename(filename.replace(".py", ""))` - the
  file's exact basename, case-sensitive. `load()` looks up both the
  `enabled` flag and the options table in
  `config['main']['plugins'][name]` under that SAME key.
- `plugins.load()` does `plugin.options = config['main']['plugins'][name]`
  - a raw assignment of the parsed TOML table. `__defaults__` is never
  merged by the framework itself, so every option read in this file
  goes through `_opt()`/`_opt_int()`/`_opt_float()` against the
  module-level `DEFAULTS` dict, never bare `self.options[...]` -
  the same pattern `crack_house_ng.py`/`timer_ng.py`/
  `gps_tagger_ng.py`/`bluetooth_recon_ng.py`/`fix_region_ng.py` use.
- Real hook signatures relevant here: `on_loaded(self)`,
  `on_ui_setup(self, ui)`, `on_ui_update(self, ui)`,
  `on_unload(self, ui)` (the required `ui` parameter is exactly what
  the original got wrong - bug #1 above), `on_webhook(self, path,
  request)`. There is no `pwnagotchi.plugins.notify()` function
  anywhere in the real framework (bug #2 above).
- `on_ui_update(self, ui)` is already invoked periodically by this
  fork's own UI refresh loop on its own - the reason bug #3's separate
  timer thread was never actually necessary.

## A naming note (same convention as fix-region-suite/bluetooth-recon-suite)

Unlike `MadHatterNG.py` (an explicit one-off capital-NG rename request
for that suite only), this suite follows the same convention as
`fix_region_ng.py`/`bluetooth_recon_ng.py`: the plugin file is
`sigstr_ng.py` (snake_case), and its config section is
`[main.plugins.sigstr_ng]` - matching the file's exact basename, NOT
the class name (`SigStrNG`), and NOT the original's section name
(`sigstr`). This is a verified framework fact, not a stylistic
choice - see the framework-facts section above. A config section
spelled `sigstr` (the original's name) or `SigStrNG` (the class name)
for a file named `sigstr_ng.py` would never appear in the framework's
`enabled` list, so the plugin would silently never load at all - not
an options bug, a total no-load. If you'd rather rename the file
itself, that's fine - just make sure the config section header is
changed to match, exactly, including case (see `MadHatterNG.py`'s own
NOTES.md for a full worked example of doing exactly this).

## What was preserved from the original design, and why

**Kept:** reading RSSI via `iw dev <interface> link` and rendering it
as a `|████░░░░░░|`-style bar with a `LabeledValue` UI element at a
fixed default position - the original's core mechanism and on-screen
presentation are sound, so neither was replaced, only fixed (bug #5)
and made configurable (position, bar length). The default position
`(0, 205)` is kept as the shipped default, matching the original
exactly, even though it's now configurable.

## What's new (all four approved improvements)

1. **Signal history sparkline.** A bounded ring buffer
   (`collections.deque(maxlen=history_length)`, default 30 points - it
   can never grow without limit) of recent RSSI-percent readings,
   rendered as a compact unicode sparkline (`render_sparkline()`,
   using the standard 8-level block-height set `▁▂▃▄▅▆▇█`) so trend -
   not just the instantaneous reading - is visible at a glance. Shown
   both on-screen (the most recent `sparkline_display_points`,
   default 12, kept short for small e-ink displays) and in full on the
   webhook status page. The sparkline is rendered on a **fixed 0-100
   scale**, not a dynamic per-window min/max, so consecutive
   sparklines stay directly comparable to each other rather than each
   one silently rescaling to its own data range.
2. **Threshold-coded reading (strong/medium/weak).**
   `classify_signal()`, with configurable `strong_threshold_dbm`
   (default -60) / `weak_threshold_dbm` (default -80), maps the
   current dBm reading to a short tag (`STR`/`MED`/`WEAK`/`N/A`)
   prefixed onto the on-screen bar - a fast at-a-glance read on the
   small e-ink displays this project targets, instead of requiring the
   user to mentally convert a raw dBm or percent number into "is this
   actually good or bad". (The original suggestion talked about
   "color-coding" - this project's supported displays are monochrome
   e-ink, so a short text tag is the equivalent that actually works on
   the real hardware, the same reasoning `MadHatterNG.py`'s icon-based
   status indicators already follow.)
3. **RSSI-vs-handshake-capture correlation logging.** Optional
   (`correlate_handshakes`, default `false`), best-effort, matching
   the established pattern (`bluetooth_recon_ng.py`/`MadHatterNG.py`)
   for reading another suite's output file and degrading silently if
   it's missing or malformed. If `timer-suite`'s per-handshake CSV
   (default path `/etc/pwnagotchi/timer_ng.csv`, confirmed against
   `timer_ng.py`'s own `output_path` default and `FIELDNAMES`) is
   present, each logged handshake timestamp is paired with the closest
   RSSI reading from this plugin's own history within
   `correlation_window_minutes` (default 5), shown as a table on the
   webhook page - "how strong was my signal when I actually got a
   capture." An unmatched handshake is reported with `rssi_dbm: None`
   rather than silently dropped, so a coverage gap is visible instead
   of looking like a clean miss. Never crashes or blocks anything if
   `timer-suite`'s file doesn't exist or is malformed.
4. **On-screen positioning**, matching `MadHatterNG.py`'s exact
   convention: `ui_position_x`/`ui_position_y`, with the same
   negative-x-means-"this many pixels in from the right edge"
   behavior, copied character-for-character from `MadHatterNG.py`'s
   `on_ui_setup` (clamped so the element always stays on-screen on any
   display width). The original hardcoded a fixed `(0, 205)`; this
   rebuild keeps `(0, 205)` as the *default* but makes both axes
   user-configurable.

## Also added (matches this repo's established conventions)

- **A webhook status page** (`/plugins/sigstr_ng/`, plain HTML, every
  interpolated value passed through `html.escape()` - including the
  interface name, which comes from external `iw dev` command output
  and is therefore not fully trusted) showing: the current reading and
  bar, the full retained sparkline history, the strong/medium/weak
  classification and its thresholds, which interface is configured vs.
  actually in use (and whether that was auto-detected or a fallback),
  and the handshake-correlation table when that feature is enabled.
- **`enabled = false` shipped by default**, matching this repo's
  established safety/opt-in convention for every suite here.

## Testing

Run with (from this directory):
```
python3 tests/test_sigstr_ng.py
```
(`pytest` isn't installed in this particular sandbox - the test file
is written as a self-contained script with its own `check()`/pass-fail
harness, same as `bluetooth_recon_ng.py`/`fix_region_ng.py`'s test
files, so it runs directly with plain `python3` wherever `pytest`
isn't available.)

Covers:
- `on_unload(self, ui)` actually removes the UI element and never
  raises, both when the element exists and when it was never added
  (bug #1).
- The code never calls `plugins.notify(...)` anywhere, and
  `pwnagotchi.plugins` genuinely has no `notify` attribute in the
  stubbed framework used by the tests - directly confirming bug #2 is
  fixed and stays fixed (not just "no exception observed").
- The code never imports `threading` and never instantiates
  `threading.Timer(...)`, and a `SigStrNG` instance has no `.timer`
  attribute at all (bug #3).
- `generate_signal_bar()`'s fill direction at 0%, 50%, and 100%
  (bug #5), plus a `None` input rendering fully empty instead of
  crashing.
- `_resolve_interface()`'s fallback behavior (bug #4): configured
  interface present -> used as-is; configured interface absent from a
  real detected list -> falls back to the first detected interface and
  logs a warning; `iw dev` enumeration itself failing/empty -> trusts
  the configured value rather than refusing to try it.
- `read_signal_strength()`/`list_wireless_interfaces()` never raise -
  missing `iw`, a not-associated interface (no `signal:` line), and
  unparseable output are all handled by returning `None`/`[]`.
- The bounded history ring buffer: stays capped at `history_length`
  even after many more measurements than the cap, keeping only the
  most recent points.
- `render_sparkline()`: empty input, a single low value, a single high
  value, and one character per input value.
- `classify_signal()`: exact-boundary values at both thresholds, one
  step better/worse than each boundary, a mid-range "medium" value,
  and `None` mapping to "unknown".
- On-screen positioning: negative `ui_position_x` resolves as
  "pixels in from the right edge", a non-negative value is used
  as-is, an extreme negative value is clamped to stay on-screen, and
  the shipped default matches the original's hardcoded `(0, 205)`.
- The webhook status page renders without crashing both before any
  reading exists and after one does, HTML-escapes the interface name
  (no raw `<script>` tag survives), and returns a 404-style response
  for an unrecognized path.
- Handshake correlation: a handshake close in time to a reading gets
  paired with that reading's RSSI; one far outside the window is
  still reported (not dropped) with `rssi_dbm: None`; the feature
  degrades to `[]` (no crash) when `timer_csv_path` doesn't exist; and
  it stays off (`[]`) when `correlate_handshakes` is `false` even with
  a real, valid CSV present - reflected correctly on the webhook page
  either way.

## Still open / needs real-hardware testing

- `iw dev <interface> link`'s exact output format is assumed to match
  the standard `iw` CLI's real-world format (the `signal: -NN dBm`
  line) - this sandbox has no real WiFi radio to confirm parsing
  against a live association on real hardware, only against
  hand-constructed sample output in the tests.
- The strong/medium/weak thresholds (-60 / -80 dBm) are reasonable,
  commonly-cited rules of thumb for WiFi RSSI, not something tuned
  against this specific project's real adapters - worth adjusting
  after some real-world observation if they feel off.
- The handshake-correlation feature has never been run against a real
  `timer_ng.csv` produced by an actual capture session - only against
  hand-built sample rows in the tests.
