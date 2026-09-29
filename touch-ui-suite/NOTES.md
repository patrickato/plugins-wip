# Notes: TouchUING

## Why this one, carefully

Flagged in Cluster 34's fix-candidate table with 2 real bugs, and
called out as especially relevant since the user's real hardware
includes an MPI3501 touchscreen. Given how hardware-dependent this
plugin is (GPIO, external `evtest`/`ts_print` processes, real touch
input), this rebuild deliberately fixes only concrete, provable bugs
rather than attempting a broader redesign of the touch-detection
pipeline - anything more speculative would risk breaking the one
plugin actually tied to the user's physical device, with no way to
verify a bigger rewrite against real hardware from here.

## Bugs found

- **`Touch_Button.draw()`'s except handler calls the module, not a
  function** (originally flagged): `logging(repr(e))` - `logging` is
  the imported module object, not callable. Any real drawing
  exception (a bad font, a bad image, anything) raised a second,
  unhandled `TypeError` from inside the except block itself, which
  could kill the UI render thread. Fixed to `logging.error(...)`.
- **`on_internet_available`'s apt-install call always crashed**
  (originally flagged): `check_output(["apt", "install",
  "-y"].extend(self.needsAptPackages))` - `list.extend()` mutates the
  list in place and returns `None`, so this always evaluated to
  `check_output(None)`, raising a `TypeError` every single time
  connectivity came up with `needsAptPackages` set. Fixed to
  `check_output(["apt", "install", "-y"] + self.needsAptPackages)`.
- **A `NameError` waiting for any momentary+reverse button** (new
  finding, found while reading the file in full for this rebuild):
  `process_touch` has
  ```python
  if button.momentary:
      ...
      button.state = self._beingTouched
      if reverse:
          button.state = not button.state
  ```
  `reverse` is never defined anywhere in this method or class scope -
  not `self.reverse`, not `button.reverse` - so touching any momentary
  button configured with `reverse=True` would have raised `NameError`
  immediately, the very first time it happened. Fixed to the clearly
  intended `button.reverse`.
- **The missing-evtest auto-detect could never actually fire** (new
  finding): `evtest = Popen("/usr/bin/evtest", ...)` followed by
  `if not evtest: self.needsAptPackages = [...]`. A `Popen` instance
  is always truthy (no `__bool__` override), so `not evtest` can never
  be `True` - if `evtest` genuinely doesn't exist, `Popen(...)` itself
  raises `FileNotFoundError` immediately, which was only caught by the
  single broad `try/except Exception` wrapping the *entire*
  `touchScreenHandler` method. That swallowed the exception and just
  logged a generic "Handler: ..." message and returned, without ever
  reaching the line that would have set `needsAptPackages` - so the
  plugin's own "auto-install evtest/libts-bin once online" feature
  could never trigger from that specific failure mode. The same
  problem existed for the `ts_print` Popen call further down. Fixed by
  catching `FileNotFoundError` at each specific `Popen()` call site and
  setting `needsAptPackages` there.

## What this rebuild keeps, drops, and fixes

- **Keeps**: the entire touch-dispatch design as originally written -
  press/move/release detection via depth, bounding-box hit testing
  against live UI elements, momentary-vs-toggle button semantics,
  `event_handler`-targeted dispatch (`plugins.one`, confirmed real
  against `pwnagotchi/plugins/__init__.py`) falling back to
  broadcast dispatch (`plugins.on`) when a button has no specific
  handler, and GPIO-button support for non-touch navigation.
- **Drops**: the `@singleton` decorator on the original class - dead
  weight, not a bug. Confirmed via `pwnagotchi/plugins/__init__.py`:
  `Plugin.__init_subclass__` fires (and calls `cls()` to create the
  one instance the loader ever uses) the moment the class body
  finishes executing, which happens *before* any decorator on that
  class runs. So `@singleton` never had any effect on how this plugin
  actually got instantiated by the framework - it only would have
  mattered if something else in the module called `Touch_Screen()`
  again later, which nothing did.
- **Fixes**: the four bugs above. `init_gpio`'s four repetitive,
  near-identical blocks were also consolidated into one small loop
  over a name -> (press callback, release callback) map, purely for
  readability - each button is still configured and guarded
  independently, with the exact same behavior as before.

## Testing

18 tests in `tests/test_touch_ui_ng.py`, all passing against the real
cloned `jayofelony/pwnagotchi` framework (confirming `plugins.on` and
`plugins.one`'s real signatures directly against
`pwnagotchi/plugins/__init__.py`): real plugin registration;
`Touch_Button.draw()` logging a real exception instead of raising a
second, unhandled one (directly reproducing and confirming the fix);
the apt-install call receiving a real, non-`None`, correctly merged
argument and clearing `needsAptPackages` on success, and doing nothing
when none is pending; a momentary+reverse button not raising
`NameError` and correctly inverting its state (directly reproducing
and confirming that fix); broadcast-vs-targeted dispatch choosing
`plugins.on` vs. `plugins.one` correctly based on whether a button has
an `event_handler`; `pointInBox` correctness; a missing `evtest`
binary now correctly setting `needsAptPackages` instead of the
original's dead check; `init_gpio` wiring up exactly the configured
buttons (and doing nothing when none are configured); and clean
`on_unload` behavior with nothing set up yet.

Two Anthropic-provided dependency stand-ins were needed just to import
and exercise this module in a sandbox with no real Raspberry Pi:
`tests/stub_deps/RPi/GPIO.py` (a no-op stand-in for the real
`RPi.GPIO`, which only exists on actual Pi hardware) and the existing
`prctl`/`tomlkit` stubs shared with every other suite in this project.
`numpy` is a real, genuinely installed dependency here, not stubbed.

## Still open

The touch-detection pipeline itself - spawning and parsing
`evtest`/`ts_print`, finding the real device path, the rotation-based
coordinate flip - is exactly as hardware-dependent as the original and
was deliberately left unchanged beyond the four bugs above; none of it
can be exercised without a real touchscreen. **Please test this one
carefully on the actual MPI3501 setup** before relying on it - see
README's "Still open" section.
