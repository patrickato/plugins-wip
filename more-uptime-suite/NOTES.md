# Notes: MoreUptimeNG

## Why this one

Flagged in Cluster 34's fix-candidate table for a real indentation bug
that defeats its one configurable option. Small, self-contained fix.

## Bugs found (both already flagged in the cluster review, confirmed and fixed)

- **Indentation bug**: in `on_ui_setup`,
  ```python
  if "position" in self.options:
      pos = self.options["position"].split(",")
      pos = [int(x.strip()) for x in pos]
  else:
      pos = (ui.width() - 58, 12)
      ui.add_element("more_uptime", Text(..., position=pos, ...))
  ```
  `ui.add_element(...)` sits inside the `else` branch - so setting a
  custom `position` (the entire point of that option) means the
  element is *never created at all*, while `on_ui_update` still
  unconditionally tries to update it every tick.
- **Masked-error bug**: `on_ui_update`'s `except` handler logs
  `repr(uiItems)`, but `uiItems` is only ever assigned inside the
  `override`-is-true branch of the try block. Any exception raised
  earlier (e.g. `open("/proc/uptime")` failing) crashes the handler a
  second time with `NameError: name 'uiItems' is not defined`,
  swallowing the real error entirely - confirmed by reproducing it in
  a test that makes `open()` raise and checking a NameError doesn't
  fire in its place.

## What this rebuild keeps, drops, and fixes

- **Keeps**: the three-state cycle (instance/process/system uptime),
  the 5-second cycle timer, and `override` mode's direct relabeling of
  the stock "uptime" element via `ui._state._state`.
- **Drops**: the unused `scapy` pip dependency declaration (never
  imported anywhere in the original file - the same stray-dependency
  pattern found repeatedly elsewhere in this audit).
- **Fixes**: moves `add_element` out of the conditional so the element
  is always created (using either the configured `position_x`/
  `position_y` or the original's default corner position); simplifies
  the update handler's exception logging so it can never itself throw.
- Switched the comma-separated `position = "10,20"` string option to
  two plain numeric options (`position_x`, `position_y`) for
  consistency with the rest of this audit's rebuilds, and because a
  TOML array under the same key name would have broken the original's
  `.split(",")` call outright.

## Testing

10 tests in `tests/test_more_uptime_ng.py`, all passing against the
real cloned `jayofelony/pwnagotchi` framework: real plugin
registration; the element being created even when a custom position
IS configured (directly reproducing and confirming the fix for the
original bug); the default position fallback; `override=true`
correctly skipping its own element and instead relabeling the stock
"uptime" element; the three-state cycle producing a labeled value;
`on_ui_update` not crashing (and not masking the real error with a
second `NameError`) when `/proc/uptime` is unreadable; and clean
unload behavior in both `override` states.

## Still open

- `override` mode's reach into `ui._state._state` internals is
  unchanged from the original and hasn't been independently
  re-verified beyond what the sandbox tests can exercise - see
  README's "Still open" section.
