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

## What's added, on top of the bug fixes (user-approved)

Suggested after the initial rebuild, and approved for building
(options #2 and #3 of 3 suggested):

1. **Case-insensitive hostname matching** (`on_wifi_update`). The
   comparison between a live AP's hostname and a potfile entry's
   hostname was a plain `==`, so `"MyLab"` over the air would never
   match `"mylab"` in a potfile - a real risk since wpa-sec exports,
   manually-entered `.cracked` rows, and live scan ESSIDs don't
   reliably agree on casing for the same network. Now compared with
   `.lower()` on both sides; the potfile's stored casing is still what
   gets displayed, so this only widens what counts as a match, it
   doesn't change what's shown.
2. **Cross-reboot persistence via `saving_path`** (`on_loaded`).
   Previously, `_crack_menu` was rebuilt from `files` alone on every
   load - so if a source file got rotated, cleared, or a wpa-sec
   export just wasn't freshly re-downloaded before a reboot, any crack
   that file had contributed vanished from the list even though it
   had already been learned once. `on_loaded` now also reads its own
   previous `saving_path` output (the merged file it writes at the end
   of every `on_loaded` run) as an additional source, merged in before
   `files` are parsed. A network cracked once stays known from then on,
   independent of what the currently-configured `files` happen to
   contain on any given boot. (Suggestion #1 from the same batch -
   redacting passwords before display - was not approved and is not
   included.)

## Testing

20 tests in `tests/test_crack_house_ng.py`, all passing against the
real cloned `jayofelony/pwnagotchi` framework. The original 16 cover:
real plugin registration; `on_loaded` skipping a missing file without
crashing and still writing the (now empty) `saving_path` backup;
correct `.potfile` and `.cracked` parsing; `on_ui_setup` succeeding
against a bare UI object with none of the original's
`is_waveshare_*`-style methods at all (proving the fatal bug is
actually gone, not just avoided in a specific case); the stats element
correctly included/excluded by `display_stats`; configured
`position_x`/`position_y` being honored; clean unload even when the
stats element was never created; nearest-cracked-network selection by
RSSI; the configured `iface` actually being used in the `iwconfig`
call; not re-scanning while already associated; the "nothing nearby"
fallback using the plugin's own data instead of a hardcoded external
file; and the webhook rendering without crashing on an empty plugin.
4 new tests cover the additions above: case-insensitive matching
(an AP reporting `"mylab"` matches a `"MyLab:hunter2"` potfile entry,
with the original casing preserved in the result); persistence across
a simulated reboot (a `saving_path` file written by a prior run is
picked up by `on_loaded` even when `files` is empty/missing);
persisted entries merging with newly-parsed ones rather than being
replaced by them; and no crash on a genuine first-ever run where
`saving_path` doesn't exist yet.

## Still open

- No real-hardware verification yet of on-screen position or the
  `iwconfig` text-parsing approach - see README's "Still open"
  section.

## Original config preserved

A real original config file for `crack_house.py` was found (exact match) at
`itsdarklikehell/pwnagotchi-plugins/configs/crack_house.toml`. Preserved verbatim in this suite's own folder as
`config.original.toml`, per the project's standing config-preservation requirement,
for reference/troubleshooting if the rebuilt `config.toml` above ever
needs comparing against the source.

## Post-cluster-review fix: screen-position overlap

The post-cluster-review conflict pass found that this suite's
`orientation = "horizontal"` default position, (0, 91), is identical to
fortune-cookie-suite's own default position on non-Waveshare/non-Inky
displays - both would render at the same spot if a user ran both
suites with default settings and horizontal orientation. Changed the
horizontal-orientation default to (0, 71). No change to the
`orientation = "vertical"` default (180, 61), which never collided.
`position_x`/`position_y` remain fully user-configurable and override
either default regardless.

## Post-cluster-review fix: real handshake directory default

The post-cluster-review conflict pass confirmed (via discohash-suite's
and discord-suite's own code comments) that this fork's actual handshake
capture directory is `/etc/pwnagotchi/handshakes`, not the legacy/upstream
`/root/handshakes` path the original `crack_house.py` hardcoded. Updated
this suite's `files` list (the potfile/handshake-list paths it watches)
and `saving_path` default to `/etc/pwnagotchi/handshakes/...` to match.
Both remain fully overridable via config. Since bluetooth-recon-suite's
`crack_house_potfile_path` and viz-suite's `crack_house_saving_path`
both read this suite's output by convention (for the cross-suite
correlation features), their own defaults were updated to match in the
same pass - see their own NOTES.md entries.
