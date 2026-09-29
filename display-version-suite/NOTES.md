# Notes: DisplayVersionNG

## Why this one

`display_version.py` was initially kept-as-is in Cluster 34 (no bugs
found - it's about as minimal as a plugin gets: one `LabeledValue`
element, updated with `f"v{pwnagotchi.__version__}"` on every tick).
When the user asked for improvement suggestions on the remaining
kept-as-is plugins in this cluster, the two small gaps that remained
were a hardcoded position and a stray unused dependency - both
approved and built here.

## Bugs found

None. The original is correct and about as simple as this audit
gets - this rebuild is a pure cleanup/feature addition, not a bugfix
one.

## What this rebuild keeps, drops, and adds

- **Keeps**: the one-line update logic reading the real
  `pwnagotchi.__version__`, unchanged.
- **Drops**: the unused `scapy` pip dependency declaration.
- **Adds**: `position_x`/`position_y` (configurable position,
  replacing the hardcoded `(185, 110)`, still the default).

## Testing

7 tests in `tests/test_display_version_ng.py`, all passing against
the real cloned `jayofelony/pwnagotchi` framework: real plugin
registration; the default position matching the original's hardcoded
value; a configured position being honored; `on_ui_update` correctly
showing the real `pwnagotchi.__version__`; clean `on_unload` behavior
both with and without a prior `on_ui_setup` call; and the webhook not
crashing.

## Still open

- No real-hardware verification yet of on-screen position - see
  README's "Still open" section.

## Original config preserved

A real original config file for `display_version.py` was found (exact match) at
`itsdarklikehell/pwnagotchi-plugins/configs/display_version.toml`. Preserved verbatim in this suite's own folder as
`config.original.toml`, per the project's standing config-preservation requirement,
for reference/troubleshooting if the rebuilt `config.toml` above ever
needs comparing against the source.
