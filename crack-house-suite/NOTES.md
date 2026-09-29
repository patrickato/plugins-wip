# Notes: CrackHouseNG

## Why this one needed more than the originally documented fix

Cluster 34's original table flagged `crack_house.py` for two things:
direct-indexing `KeyError` risk on `self.options`, and a hardcoded
`iwconfig wlan0` interface. Reading the source in full for the rebuild
surfaced something much bigger: `on_ui_setup` starts by calling
`ui.is_waveshare_v2()`, `ui.is_waveshare_v1()`, `ui.is_waveshare144lcd()`,
`ui.is_inky()`, `ui.is_lcdhat()`, and `ui.is_waveshare27inch()` to pick
a position based on display model.

Confirmed against the real cloned `jayofelony/pwnagotchi` framework:
these `is_*` methods are defined on `pwnagotchi/ui/display.py`'s
`Display` class - but `pwnagotchi/ui/view.py`'s `View.__init__` calls
`plugins.on('ui_setup', self)` with itself, and `View.update()` calls
`plugins.on('ui_update', self)` the same way. Every plugin's
`on_ui_setup`/`on_ui_update` hook always receives a plain `View`
instance, never a `Display`, and `View` has none of these methods
(confirmed by grepping - the only `is_*` method on `View` is
`is_normal`). So `ui.is_waveshare_v2()` was never going to succeed on
this fork, on any hardware, ever - this plugin could not load, full
stop, before even reaching the bug the audit table already knew about.

## Bugs found

- **Fatal on-load crash (new finding, supersedes the earlier note)**:
  `AttributeError` on the very first display-detection call in
  `on_ui_setup`, on every display type, unconditionally.
- **A second crash hiding behind the first (new finding)**: `s_pos`
  (used for the stats element's position) is only ever assigned inside
  the `is_lcdhat()` branch. Had the first crash somehow not happened,
  enabling `display_stats` on any other display model would still hit
  `UnboundLocalError: local variable 's_pos' referenced before
  assignment`.
- **Crash on a missing configured file (new finding)**: `on_loaded`
  opens every entry in `files` with a bare `open()`; any missing file
  (a very plausible first-run state - e.g. no OnlineHashCrack export
  yet) crashes plugin load entirely rather than being skipped.
- **Direct-`self.options[...]` indexing** (originally flagged): this
  fork's loader never merges `__defaults__` into `self.options`, so a
  config.toml missing any of `files`/`saving_path`/`orientation`/
  `display_stats` would `KeyError`-crash on load or update.
- **Hardcoded `iwconfig wlan0`** (originally flagged): ignores
  whatever interface is actually configured in pwnagotchi's own
  `config.toml`.
- **Inconsistent fallback source (new finding)**: when nothing nearby
  matches, `on_ui_update` shells out to `tail`+`awk` against one
  specific hardcoded file, `/root/handshakes/wpa-sec.cracked.potfile`
  - independent of whatever the user actually configured in `files`/
  `saving_path`. If that one specific file doesn't exist or is empty,
  the fallback silently shows nothing, even if the user's other
  configured potfiles have plenty of cracked networks in them.

## What this rebuild keeps, drops, and fixes

- **Keeps**: both original file-format parsers (`.potfile`'s
  `BSSID:STAMAC:ESSID:password` and `.cracked`'s
  `datetime,ESSID,BSSID,STAMAC,password,note`), the
  vertical/horizontal orientation concept, the nearby/total stats
  element.
- **Drops**: all `ui.is_*()` display-detection calls entirely - they
  never worked on this fork, so there was nothing working to preserve.
- **Fixes**: replaces display detection with configurable
  `position_x`/`position_y` and `stats_position_x`/`stats_position_y`
  (defaulting to the original's own fallback position values, so
  behavior is unchanged for anyone who never touched a
  `ui.is_*()`-only code path anyway); wraps each configured file's
  read in its own try/except so one missing file no longer takes down
  the whole plugin; reads the interface from `pwnagotchi.config['main']
  ['iface']` by default (still overridable via an `iface` option); the
  "nothing nearby" fallback now shows the most recently learned crack
  from the plugin's own merged, deduplicated list instead of shelling
  out to a single hardcoded external file.
- Also switched `CRACK_MENU`/`BEST_RSSI`/`BEST_CRACK`/`TOTAL_CRACK`
  from the original's module-level globals to plain instance
  attributes - functionally equivalent for a singleton plugin, but
  removes any risk of state bleeding between instances and makes the
  code easier to unit-test in isolation (which is exactly how the
  tests below exercise it).

## Testing

16 tests in `tests/test_crack_house_ng.py`, all passing against the
real cloned `jayofelony/pwnagotchi` framework: real plugin
registration; `on_loaded` skipping a missing file without crashing
and still writing the (now empty) `saving_path` backup; correct
`.potfile` and `.cracked` parsing; `on_ui_setup` succeeding against a
bare UI object with none of the original's `is_waveshare_*`-style
methods at all (proving the fatal bug is actually gone, not just
avoided in a specific case); the stats element correctly
included/excluded by `display_stats`; configured `position_x`/
`position_y` being honored; clean unload even when the stats element
was never created; nearest-cracked-network selection by RSSI; the
configured `iface` actually being used in the `iwconfig` call; not
re-scanning while already associated; the "nothing nearby" fallback
using the plugin's own data instead of a hardcoded external file; and
the webhook rendering without crashing on an empty plugin.

## Still open

- No real-hardware verification yet of on-screen position or the
  `iwconfig` text-parsing approach - see README's "Still open"
  section.
