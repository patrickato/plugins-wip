# Tweak View NG 0.1.0-alpha1 — Test Report

## Result

**Desktop/simulated status: PASS**

- Python compile: PASS
- `compileall`: PASS
- Pytest: **32 passed**
- Embedded browser JavaScript syntax (`node --check`): PASS
- Shell script syntax (`sh -n`): PASS
- HTML duplicate-id check: PASS

## Exact software target

Primary API target: **Jayofelony Pwnagotchi v2.9.5.8, 64-bit**.

The implementation was designed against these v2.9.5.8 behaviors:

- `View.width()` / `View.height()` expose runtime display dimensions.
- `View.add_element()` / `View.remove_element()` are used for custom elements.
- `View.update(force=True)` forces a redraw, important when `ui.fps = 0`.
- `View._lock` protects rendering.
- `State._lock`, `State._state`, and `State._changes` are the private compatibility surface isolated inside `JayUIAdapter`.
- Plugin configuration is assigned directly to `plugin.options`, so NG merges its own defaults in `on_loaded()`.
- Web plugins use `on_webhook(self, path, request)` and are routed under `/plugins/<name>/<path>`.

## Automated test coverage

### UI adapter

- Runtime display dimensions.
- State snapshot and property serialization.
- Safe property allow-list.
- 2-point widget positioning.
- 4-point line/shape positioning.
- Negative-from-edge coordinate conversion.
- Out-of-bounds clamping.
- Font validation and application.
- Original-property capture and restoration.
- Custom shape add/replace/remove.
- Concurrent snapshot/edit access.

### Persistence and recovery

- Atomic JSON save.
- Backup creation.
- Round-trip load/save.
- Wrong-schema rejection.
- Legacy `VSS.element.property` conversion.
- Legacy custom-shape conversion.
- Automatic legacy import without modifying the old file.

### Plugin lifecycle

- Default-option merge.
- Saved-layout application during `on_ui_setup`.
- Deferred application when another plugin's widget appears later.
- Unload restoration.
- Custom-shape cleanup.

### API/edit workflow

- Update -> persist.
- Revert one element without reverting others.
- Add/update/delete custom shape -> persist.
- Reset profile.
- Undo.
- Redo.
- Profile switching with restoration/application.
- `max_length` wrapper rebuild.
- Unknown import schema rejection without changing the active layout.

### Screen/resolution matrix

Simulated successfully at:

- 250×122
- 296×128
- 400×300
- 480×320
- 800×480
- 320×480
- 480×800

The engine does not contain per-resolution layout constants.

### Stress

- 1,000 sequential position edits: PASS.
- Parallel snapshot + edit loop: PASS.

## NOT YET PHYSICALLY TESTED

These cannot honestly be marked PASS until the plugin runs on real Pwnagotchi hardware:

1. Jayofelony 2.9.5.8 plugin loader import on a real image.
2. Flask/CSRF behavior through the live Jay web UI.
3. Physical framebuffer/e-ink refresh after forced updates.
4. Your Pi 4 + 480×320 ILI9486/XPT2046 display.
5. Pi Zero 2W performance/memory behavior.
6. Pi 3 / Pi 5 behavior.
7. Multiple actual display drivers and rotations.
8. Interaction with a large real-world set of third-party plugins adding/removing widgets dynamically.
9. Long-duration operation while Pwnagotchi is actively updating its UI.

Until those are checked, this package is **alpha**, not a stable release.
