# Notes: ClockNG

## Why this one

`clock.py` was initially kept-as-is in Cluster 34 (no bugs found -
it's a small, self-contained plugin: two `LabeledValue` elements
updated from `datetime.datetime.now()` on every render tick). When
the user asked for improvement suggestions on the remaining
kept-as-is plugins in this cluster, two real gaps stood out even with
no bugs present: hardcoded positions, and a hardcoded, non-configurable
date/time format (notably no way to get a 24-hour clock without
editing the plugin's source). Both were approved and built here.

## Bugs found

None. The original is small and correct as far as it goes - this
rebuild is a pure feature addition, not a bugfix.

## What this rebuild keeps, drops, and adds

- **Keeps**: the two-element (date, time) design, and both of the
  original's default position/format values as the new defaults, so
  behavior is unchanged for anyone who doesn't configure anything.
- **Adds**: `date_position_x`/`date_position_y` and
  `time_position_x`/`time_position_y` (configurable positions,
  replacing the two hardcoded tuples); `date_format`/`time_format`
  (configurable `strftime` strings, replacing the two hardcoded
  literals) - validated by a small `_format()` helper that catches a
  bad format string and falls back to the original default rather
  than crashing `on_ui_update`; a defensive `on_unload` that no-ops
  cleanly if the elements were never created, matching the pattern
  used throughout this audit's other rebuilds.
- Renamed the UI elements from the original's `clock1`/`clock2` to
  `clock_ng_date`/`clock_ng_time` for clarity and to avoid any
  collision if someone were to run both plugins side by side during
  a transition.

## Testing

16 tests in `tests/test_clock_ng.py`, all passing against the real
cloned `jayofelony/pwnagotchi` framework: real plugin registration;
both elements created at the original's default positions; configured
positions being honored; both elements updating with the default
formats; a configured 24-hour `time_format` and a configured
`date_format` being honored; an unusual-but-valid format string not
crashing; a format string that actually raises falling back to the
default instead of propagating the exception; clean `on_unload`; and
`on_unload` not crashing when `on_ui_setup` never ran.

## Still open

- No real-hardware verification yet of on-screen position - see
  README's "Still open" section.

## Original config preserved

A real original config file for `clock.py` was found (exact match) at
`itsdarklikehell/pwnagotchi-plugins/configs/clock.toml`. Preserved verbatim in this suite's own folder as
`config.original.toml`, per the project's standing config-preservation requirement,
for reference/troubleshooting if the rebuilt `config.toml` above ever
needs comparing against the source.
