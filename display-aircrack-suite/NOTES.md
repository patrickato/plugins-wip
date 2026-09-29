# Notes: DisplayAircrackNG

## Why this one

`display-aircrack.py` was initially kept-as-is in Cluster 34 (no
bugs found - it's a small, self-contained status indicator). When the
user asked for improvement suggestions on the remaining kept-as-is
plugins in this cluster, one real inefficiency stood out even with
no bugs present: it shells out to `ps -A` on every single UI render
tick just to check one boolean status. That, plus a hardcoded
position, a stray unused dependency, and a barely-readable status
text, were all approved and built here.

## Bugs found

None. The original works as intended - this rebuild is a pure
efficiency/feature addition, not a bugfix one.

## What this rebuild keeps, drops, and adds

- **Keeps**: the core "look for the substring 'aircrack-ng' in `ps
  -A`'s output" approach, unchanged. It's inherently a little
  imprecise (it would also match, say, a differently-named process
  that happens to embed that substring in its own command line), but
  that imprecision was already present in the original and wasn't
  part of what was approved to change here.
- **Drops**: the unused `scapy` pip dependency declaration.
- **Adds**: a `check_interval` option (default 3 seconds) that
  throttles how often the actual `ps -A` shell-out happens - the
  displayed value still updates on every render tick from the last
  known state, so the UI doesn't feel any less responsive, it's just
  not re-running a subprocess several times a second for no benefit;
  validated by a small `_check_interval()` helper that falls back to
  the default on anything non-numeric or `<= 0`, the same defensive
  pattern used for `cycle_interval` in `MoreUptimeNG` and
  `poll_interval_ms` in `VizNG` earlier in this batch. Also adds
  configurable `position_x`/`position_y` (replacing the hardcoded
  `ui.width() // 2 - 10, 0`, still the default) and configurable
  `running_text`/`stopped_text` (replacing the original's bare
  `"(1)"`/`"(0)"`, defaulting to the clearer `"AC:ON"`/`"AC:OFF"`).

## Testing

13 tests in `tests/test_display_aircrack_ng.py`, all passing against
the real cloned `jayofelony/pwnagotchi` framework: real plugin
registration; the default position matching the original's formula;
a configured position being honored; the running/stopped text
correctly reflecting a mocked `ps -A` output, both at the defaults
and with configured text; `check_interval` correctly throttling
repeated `ps` calls while still updating the displayed value from the
last known state every tick; an invalid `check_interval` falling back
to the default; the plugin not crashing (and keeping its last known
state) if the `ps` check itself raises; and clean `on_unload` behavior
both with and without a prior `on_ui_setup` call.

## Still open

- No real-hardware verification yet of on-screen position - see
  README's "Still open" section.
