# Notes: ScreenRefreshNG

## Why this one

Flagged in Cluster 34's fix-candidate table: calls a method that
doesn't exist on the object it receives, so it can never do the one
thing it's for.

## Bug found (confirmed, matches the original flag exactly)

- `on_ui_update` calls `ui.init_display()`. Confirmed via
  `pwnagotchi/ui/display.py`: `init_display()` is defined only on
  `Display`, and calls `self._implementation.initialize()` plus fires
  `plugins.on('display_setup', ...)`. Confirmed via
  `pwnagotchi/ui/view.py`: `View.update()` (the method that eventually
  fires the `ui_update` plugin event) calls
  `plugins.on('ui_update', self)` - always with itself, a plain
  `View`, never a `Display`. `View` defines no `init_display` method
  anywhere. So every firing of this plugin, on every display type,
  raised `AttributeError: 'View' object has no attribute
  'init_display'` and did nothing - identical in shape to the
  `crack_house.py` bug found in this same batch (a plugin assuming it
  has a `Display`-only method on what is actually always a `View`).

## What this rebuild keeps, drops, and fixes

- **Keeps**: the `refresh_interval` counter-based trigger and the
  "Screen cleaned" status message.
- **Drops**: the unused `scapy` pip dependency.
- **Fixes**: there is no supported, public `View` method for forcing a
  hardware refresh on this fork - the *only* real one, `initialize()`,
  lives on the driver object at `ui._implementation`, which happens to
  be present on `View` too (`Display` inherits from `View` and reuses
  the same attribute set in `View.__init__`). This rebuild reaches
  into that attribute directly, wrapped in a try/except and a
  presence check, and documents plainly in the README that this is a
  private-attribute reach made only because no public alternative
  exists - not something to imitate elsewhere without the same
  justification.

## Testing

12 tests in `tests/test_screen_refresh_ng.py`, all passing against the
real cloned `jayofelony/pwnagotchi` framework: real plugin
registration; no crash against a bare object shaped like the real
`View` (no `init_display` at all) - directly reproducing and
confirming the fix for the original crash; the implementation's
`initialize()` actually being called to perform the refresh; the
status message being set (and skippable); the interval counter
correctly gating and resetting; `refresh_interval=0` disabling the
feature entirely; a missing `_implementation` attribute not crashing;
and a failing `initialize()` call being caught rather than fatal.

## Still open

This is the one plugin in the whole audit whose *actual effect*
(does re-running `initialize()` visibly clear ghosting on the user's
real driver) genuinely cannot be verified without physical hardware -
see README's "Still open" section. What's verified here is only that
the guaranteed crash is gone and the call reaches the framework's own
refresh mechanism.
