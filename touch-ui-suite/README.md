# TouchUING

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done. **This is the plugin most directly relevant to your
actual MPI3501 touchscreen** - please test it carefully before relying
on it.

A fixed rebuild of `Touch_UI.py` (itsdarklikehell/Sniffleupagus) -
reads touch input from `/dev/input/event*` via `evtest`/`ts_print` and
dispatches press/release/move events to other plugins that implement
`on_touch_press`/`on_touch_release`/`on_touch_move`.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen (Inland/MPI3501,
resistive touch, ILI9486/ADS7846), jayofelony 64-bit pwnagotchi image.

## Requirements & dependencies

- Python: `RPi.GPIO`, `numpy` (both likely already present on a
  standard pwnagotchi image).
- System: `evtest` and `libts-bin` (`sudo apt install evtest
  libts-bin`) - same as the original.
- This plugin does nothing visible by itself; it dispatches touch
  events for *other* plugins to react to (see the callback docstring
  at the top of `touch_ui_ng.py`).

## What's fixed vs. the original

1. **A drawing exception could crash the render thread.**
   `Touch_Button.draw()`'s `except` handler called `logging(repr(e))`
   - `logging` is the imported module object, not callable, so any
   real exception while drawing a button (a bad font, an odd
   image size, anything) raised a brand-new, unhandled `TypeError`
   from inside the except block itself, instead of just being logged.
   Fixed to `logging.error(...)`.
2. **`on_internet_available` always crashed when packages were
   pending.** `check_output(["apt", "install", "-y"].extend(self.needsAptPackages))`
   - `list.extend()` mutates in place and returns `None`, so this
   always evaluated to `check_output(None)`, crashing every time
   connectivity came up with any packages flagged as needed. Fixed to
   `check_output(["apt", "install", "-y"] + self.needsAptPackages)`.
3. **A momentary+reverse button crashed on touch.** `process_touch`
   checked a bare `if reverse:` - `reverse` was never defined
   anywhere in that scope (not `self.reverse`, not `button.reverse`),
   so touching any momentary button configured with `reverse=True`
   raised `NameError` immediately. Fixed to the clearly-intended
   `button.reverse`.
4. **The "missing evtest binary" auto-detect never actually worked.**
   The original's check was `if not evtest:` right after
   `evtest = Popen(...)` - a `Popen` object is always truthy, so this
   condition could never be true. If `evtest` genuinely wasn't
   installed, `Popen(...)` would instead raise `FileNotFoundError`
   immediately, which was only caught much further out by the
   method's single broad try/except wrapping the whole handler -
   meaning the handler just silently exited without ever recording
   that `evtest`/`libts-bin` needed installing, so the "auto apt
   install once online" feature could never actually trigger from a
   missing-evtest state. Fixed by catching `FileNotFoundError` right
   at the `Popen()` call (for both `evtest` and `ts_print`) and
   setting `needsAptPackages` there.

## What's added (beyond the original bug fixes)

1. **A real webhook status page.** `on_webhook` used to just log that
   it was hit and return nothing at all - visiting the plugin's
   webhook page showed a blank response, with no way to check on the
   touchscreen's state remotely. It now returns an HTML page reporting
   whether the reader thread is running, whether a touchscreen process
   is currently active, when the last touch was seen, and any apt
   packages (`evtest`/`libts-bin`) still pending install.
2. **Long-press detection.** There was previously no way for another
   plugin to react to a deliberately-held touch specifically - only
   the existing `touch_press`/`touch_release`/`touch_move` events,
   none of which distinguish a quick tap from a long hold. A new
   `longpress_seconds` option (default 0.6s) sets how long a press
   must be held; if a release follows a press held at least that long,
   an additional `touch_longpress` event is dispatched right alongside
   the normal `touch_release` one - using the exact same
   `plugins.one()`-targeted-vs-`plugins.on()`-broadcast dispatch logic
   as every other event here, so a button's `event_handler` still
   controls who gets notified.

## What's kept

- The whole touch-dispatch design: press/move/release detection,
  per-button bounding-box hit testing, momentary vs. toggle button
  behavior, `event_handler`-targeted dispatch via `plugins.one()` vs.
  broadcast dispatch via `plugins.on()` when a button has no specific
  handler, and the GPIO-button (ok/next/back/prev) support for
  cycling/selecting elements without a touchscreen.
- `init_gpio`'s four near-identical button blocks were consolidated
  into one small loop over a name->callback map - a readability
  cleanup, not a behavior change; each button is still set up and
  guarded independently, exactly as before.
- The vestigial `@singleton` decorator on the original class was
  dropped - it did nothing useful in the first place, since this
  fork's plugin loader already instantiates exactly one instance
  automatically the moment the class is defined (via
  `Plugin.__init_subclass__`, which fires before any decorator on the
  class ever runs).

## Install

1. `sudo apt install evtest libts-bin` if you don't already have them.
2. Copy `touch_ui_ng.py` into your custom plugins folder.
3. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
4. If you were running the original `Touch_UI.py`, disable/remove it
   first.
5. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Nothing happens when touching the screen | Check `evtest`/`libts-bin` are actually installed; check the logs for "Found touchscreen device" - if it's never logged, `ts_device` was never detected |
| Touches register at the wrong X/Y | The rotation-based X/Y flip logic is inherited unchanged from the original and depends on your display's configured rotation in `config.toml` - see `touchScreenHandler`'s rotation handling |
| GPIO buttons don't respond | Confirm the BCM pin numbers in `gpios` match your actual wiring |

## Still open / needs real-hardware testing

- This plugin's core loop (spawning `evtest`/`ts_print`, parsing their
  output, detecting the actual touchscreen device) cannot be exercised
  at all without real touchscreen hardware and cannot be verified in
  this sandbox beyond the specific bugs fixed above - please test
  carefully on your real MPI3501 setup before depending on it.
- The rotation-based X/Y coordinate flip was not changed and not
  independently re-verified against your specific display's actual
  rotation setting.
