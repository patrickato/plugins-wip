# ScreenRefreshNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done. This one's actual on-screen effect specifically
cannot be verified in a sandbox at all - see "Still open" below.

A fixed rebuild of `screen_refresh.py` (itsdarklikehell/rossmarks) -
periodically re-initializes the e-ink display driver to clear
ghosting/burn-in after a configurable number of UI updates.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

Note: this plugin exists mainly for e-ink/e-paper displays, which
ghost/burn-in over time. A TFT/LCD screen (like the 3.5" MPI3501 this
suite is targeted at) doesn't ghost the same way, so a periodic
`initialize()` call here mostly just re-inits the driver - there's
unlikely to be a visible reason to enable this on that specific
hardware, kept only because it was one of the queued fix candidates.

## Requirements & dependencies

- Python: none beyond the standard library. The original declared an
  unused `scapy` pip dependency (never imported anywhere in the file)
  - dropped here.

## What's fixed vs. the original

1. **Guaranteed crash on every firing.** The original called
   `ui.init_display()`. That method only exists on this fork's
   `Display` class (`pwnagotchi/ui/display.py`) - the object actually
   passed to `on_ui_update` is always the plain `View`
   (`pwnagotchi/ui/view.py`'s `View.update()` calls
   `plugins.on('ui_update', self)` with itself, never a `Display`).
   `View` has no `init_display` method at all, so every single firing
   (once every `refresh_interval` updates) raised `AttributeError` and
   did nothing - this plugin's entire purpose never once executed on
   this fork.
2. **No public replacement exists**, so this rebuild reaches into
   `ui._implementation` (the underlying hardware driver object) and
   calls `.initialize()` directly - the same call `Display.init_display()`
   itself makes internally. `_implementation` is set in `View.__init__`
   and is present on every `View` instance (Display just inherits from
   View and reuses the same attribute), so it's reachable from a
   plugin even though there's no *supported* public API for it. This
   is a deliberate, disclosed exception to normal "use the public API"
   practice, made only because no public alternative exists on this
   fork - see "Still open" below.
3. Dropped the unused `scapy` dependency declaration.

## Install

1. Copy `screen_refresh_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
3. If you were running the original `screen_refresh.py`, disable/
   remove it first.
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| No visible refresh on the real screen | `_implementation.initialize()` may behave differently (or not be a meaningful "refresh" at all) for your specific display driver - see "Still open" |
| Log shows "no display implementation available to refresh" | `ui._implementation` was `None`/missing at the time - shouldn't normally happen, but the plugin is defensive about it rather than crashing |

## Still open / needs real-hardware testing

- **This is the one item in this whole audit that a sandbox genuinely
  cannot verify.** The fix removes the guaranteed crash and calls the
  same underlying `initialize()` method the framework's own
  `Display.init_display()` uses, but whether re-running a given
  display driver's `initialize()` actually produces a visible
  ghosting-clearing refresh (versus, say, a brief blank flash, or
  nothing different at all) depends entirely on that specific
  hardware driver's implementation. Needs a direct look at the real
  3.5" TFT screen after installing.
