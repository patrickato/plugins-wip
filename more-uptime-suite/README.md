# MoreUptimeNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done.

A fixed rebuild of `more_uptime.py` (itsdarklikehell/evilsocket) -
cycles a small UI element between system uptime, pwnagotchi's own
process uptime, and time-since-plugin-loaded.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Requirements & dependencies

- Python: none beyond the standard library. The original declared an
  unused `scapy` pip dependency (never imported anywhere in the file)
  - dropped here.

## What's fixed vs. the original

1. **The element was never created when a custom position was set.**
   `on_ui_setup`'s `ui.add_element(...)` call was nested one level too
   deep - inside the `else` branch of `if "position" in self.options`.
   So the element only ever got created when the user had *not* set a
   position; setting one (the whole point of the option) meant
   `on_ui_update` tried to update an element that was never created.
   Depending on the pwnagotchi UI framework version this either
   silently no-ops or raises, but either way the feature the option
   exists for never worked. Fixed by moving element creation out of
   the conditional entirely.
2. **A masked-error bug in the update handler.** `on_ui_update`'s
   `except` block referenced `uiItems`, a variable that only ever got
   assigned inside the `override` branch of the try block above it.
   Any exception raised *before* reaching that point (e.g.
   `/proc/uptime` briefly unreadable) crashed the exception handler
   itself with a second, unrelated `NameError`, completely hiding
   whatever the real problem was. Fixed by logging just the actual
   exception.
3. **Unused `scapy` dependency declaration**, dropped.

## What's added (beyond the original bug fixes)

1. **Configurable cycle interval.** How long each state is shown
   before cycling to the next one was hardcoded to 5 seconds. Now set
   with `cycle_interval` (seconds); an invalid value (zero, negative,
   non-numeric) falls back to the same 5-second default rather than
   crashing or spinning through states every update.
2. **Configurable state subset/order.** The original always cycled
   all three states (instance uptime, process uptime, system uptime)
   in a fixed `IN -> PR -> UP` order, with no way to change either.
   The `states` option now takes a list of any subset of `"IN"`,
   `"PR"`, `"UP"`, in whatever order you want - list just one to stop
   cycling entirely and show a single fixed state, or reorder/drop
   ones you don't care about. An empty list, or one with no valid
   entries, falls back to the original default order.

## What's kept

- The three-way cycle (instance/process/system uptime) and its timing
  as the *default* behavior - both are now configurable rather than
  hardcoded (see "What's added" above).
- The `override` mode that hijacks the stock "uptime" element's label
  instead of adding a new one - still a direct reach into
  `ui._state._state`, same as the original, wrapped in the same
  defensive try/except.

## Install

1. Copy `more_uptime_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
3. If you were running the original `more_uptime.py`, disable/remove
   it first.
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Element never appears | Check `override` isn't accidentally `true` - in that mode there's no separate element, it relabels the stock "uptime" one instead |
| Position looks wrong | Set `position_x`/`position_y` explicitly |

## Still open / needs real-hardware testing

- `override` mode's reach into `ui._state._state` is inherited as-is
  from the original and hasn't been re-verified against this fork's
  current UI internals beyond what the sandbox tests can exercise.
