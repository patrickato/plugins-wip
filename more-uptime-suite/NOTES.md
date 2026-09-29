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

## What's added, on top of the bug fixes (user-approved)

Suggested after the initial rebuild, and both approved for building
(#1 and #2 of 2 suggested):

1. **Configurable `cycle_interval`.** The 5-second interval between
   state changes was a hardcoded literal in `on_ui_update`. Pulled out
   into an option, validated by a new `_cycle_interval()` helper that
   falls back to the same 5-second default on anything non-numeric or
   `<= 0`, so a typo in `config.toml` can't turn this into a tight
   busy-loop advancing the state every single update.
2. **Configurable `states` list.** The three-way `IN -> PR -> UP`
   cycle and its fixed order used to be baked directly into
   `on_ui_update`'s `if self._state == 2 / elif == 1 / else` chain.
   That's now driven by a `states` option (a list of any subset/order
   of `"IN"`/`"PR"`/`"UP"`), validated by a new `_states()` helper.
   `on_ui_update` now looks up the current state's label from that
   list instead of hardcoded numeric branches, and dispatches on the
   label string (`"UP"`/`"PR"`/else `"IN"`) rather than a magic index.
   An empty list, or one with no recognized entries, logs a warning
   and falls back to the original default order rather than raising
   `ZeroDivisionError` (from `% len(states)` on an empty list) or
   silently freezing on the wrong label.

## Testing

20 tests in `tests/test_more_uptime_ng.py`, all passing against the
real cloned `jayofelony/pwnagotchi` framework. The original 10 cover:
real plugin registration; the element being created even when a
custom position IS configured (directly reproducing and confirming
the fix for the original bug); the default position fallback;
`override=true` correctly skipping its own element and instead
relabeling the stock "uptime" element; the three-state cycle
producing a labeled value; `on_ui_update` not crashing (and not
masking the real error with a second `NameError`) when `/proc/uptime`
is unreadable; and clean unload behavior in both `override` states.
10 new tests cover the additions above: a configured `cycle_interval`
being honored (state advances once, then holds through an immediate
second call); an invalid `cycle_interval` (`0`) falling back to the
default instead of crashing or spinning; a single-entry `states` list
locking the display to just that state across multiple cycles; a
custom `states` order/subset being respected; and both an all-invalid
and an empty `states` list falling back to the default instead of
crashing (covering the `ZeroDivisionError` risk specifically).

## Still open

- `override` mode's reach into `ui._state._state` internals is
  unchanged from the original and hasn't been independently
  re-verified beyond what the sandbox tests can exercise - see
  README's "Still open" section.
