# Tweak View NG — real-framework integration tests

These tests run Tweak View NG against the **actual Jayofelony Pwnagotchi**
framework (real `View` / `State` / widgets / plugin loader), not the
hand-written stubs used by the offline unit suite in `../`.

They correspond to suite-harvest item #1 ("Real Jayofelony integration test
layer"): tier 3 of the intended stack

1. pure unit tests (`tests/`)
2. simulated Jay `View`/`State`/widgets (`tests/`)
3. **real Jayofelony plugin loader + Flask/Jinja/CSRF conventions** ← here
4. physical Raspberry Pi validation (on-device only)

## What is real vs. shimmed

Real: `pwnagotchi.ui.view.View`, `pwnagotchi.ui.state.State`,
`pwnagotchi.ui.components.*`, `pwnagotchi.ui.fonts`, the headless
`pwnagotchi.ui.hw.dummydisplay.DummyDisplay`, and `pwnagotchi.plugins` (the
importlib-based loader). NG is imported and driven exactly as the Pi would.

Shimmed (sandbox artifacts only, no behavioral effect on NG):

- `prctl` — a libcap C-extension the loader uses only to set a cosmetic thread
  name; replaced with a no-op module.
- `PIL.ImageFont.FreeTypeFont.getsize` — removed in Pillow ≥ 10;
  `DummyDisplay.layout()` still calls it. Restored from `getbbox` so a modern
  Pillow can build the real `View`. On the Pi's pinned Pillow the method exists
  natively and the shim does nothing.

## Pinned target

`v2.9.5.8` (the tag, not the moving default branch). The default branch has
drifted past 2.9.5.8 — e.g. it adds `plugins.get_plugin_metadata`, which does
not exist at the tag — so the harness pins the tag to stay faithful to the
documented target.

## Running

From the suite root (`tweak-view-ng-suite/`):

```sh
# Offline unit suite only (never touches the network):
python3 -m pytest tests/ --ignore=tests/integration

# Integration suite. Locates the real framework in this order:
#   1. $PWNAGOTCHI_SRC (a jayofelony/pwnagotchi checkout)
#   2. an already-importable pwnagotchi package (e.g. on the Pi itself)
#   3. a prior clone at tests-cache (.cache/jayo-pwnagotchi)
#   4. a fresh shallow clone of the v2.9.5.8 tag (needs git + network)
python3 -m pytest tests/integration

# Point at your own checkout (fastest, and what you'd do on the Pi):
PWNAGOTCHI_SRC=/path/to/pwnagotchi python3 -m pytest tests/integration
```

If no real framework can be located (offline, no checkout), every test here
**skips** with a clear reason — the unit suite still validates the plugin.

## On-device note

On the Raspberry Pi the real `pwnagotchi` package is already installed and
importable, so `python3 -m pytest tests/integration` will bind to the live
framework with no clone and no shims needed (native `prctl` and the pinned
Pillow are present). That makes this suite the natural first step of the
on-hardware validation pass.
