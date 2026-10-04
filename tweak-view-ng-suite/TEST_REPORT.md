# Tweak View NG 0.1.0-alpha2 — Test Report

## Result

**Desktop/simulated status: PASS**

- Python compile: PASS
- `compileall`: PASS
- Pytest, offline unit suite (`tests/`): **59 passed**
- Pytest, real-Jayofelony integration suite (`tests/integration/`):
  **16 passed, 1 skipped** (the skip is a loader helper added after the 2.9.5.8
  tag)
- Embedded browser JavaScript syntax (`node --check`): PASS
- Shell script syntax (`sh -n`): PASS
- HTML duplicate-id check: PASS

The integration suite binds to the genuine Jayofelony 2.9.5.8 framework (pinned
to the `v2.9.5.8` tag) through the headless `DummyDisplay`, so the results below
marked **(real)** were verified against real framework code and real Flask/Jinja,
not stubs. Anything needing a physical display/touch panel is still unverified
(see "NOT YET PHYSICALLY TESTED").

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
- Parallel snapshot + edit loop: PASS (also run against the **real** View lock).

### Harvest-hardening coverage (new in alpha2)

- Layout preload + validation in `on_loaded()`; `on_loaded()` tolerant of an
  unreadable layout file.
- Per-entry corruption recovery for both NG-schema files and legacy imports,
  with an explicit skip/recover report (verified against the malformed and mixed
  fixtures).
- Readiness phase machine; `api/ready` readable before `on_ui_setup`; mutating
  APIs return a structured `503` when not ready **(real)**.
- Transactional edit rollback: a forced save failure leaves neither runtime nor
  persisted state changed; a deliberate `4xx` is not treated as a rollback.
- HTML/DOM injection: hostile names/labels/values stay data in the JSON API and
  are entity-escaped by the recovery page under real Jinja autoescaping **(real)**.
- Recovery editor: renders when ready, shows a not-ready page otherwise, applies
  and persists a validated edit, and rejects unknown elements / unsafe props.

### Real-framework integration (real)

- NG's private-API assumptions (`_state`, `_lock`, `_changes`,
  `update(force=…)`, `add_element`/`remove_element`) hold on real 2.9.5.8.
- Snapshot reads the real stock widget set (face/status/channel/aps/…); edits
  mutate real widgets; NG shapes add/remove as real `Line`/`Rect`/`FilledRect`;
  custom `Ellipse`/`FilledEllipse` draw on a real Pillow canvas.
- Resolution independence at several sizes against the real `View`.
- NG imports and subclasses the real `plugins.Plugin` under the genuine
  importlib loader (`load_from_file`).
- Full lifecycle (`on_loaded` → `on_ui_setup` → update → undo → unload) against
  a real `View` inside a real Flask app context.

## NOT YET PHYSICALLY TESTED

Partially verified against the real framework in software, but still requiring a
real Pwnagotchi image/display before they can be marked PASS:

1. Jayofelony 2.9.5.8 plugin loader import **on a real booted image** (the
   importlib load path itself is now exercised in CI against the tagged source).
2. Flask/**CSRF** behavior through the live Jay web UI (NG's webhook + Jinja
   render are exercised with real Flask, but live CSRF tokens and routing under
   the running daemon are not).

Not covered at all without hardware:

3. Physical framebuffer/e-ink refresh after forced updates.
4. Your Pi 4 + 480×320 ILI9486/XPT2046 display (and touch).
5. Pi Zero 2W performance/memory behavior.
6. Pi 3 / Pi 5 behavior.
7. Multiple actual display drivers and rotations.
8. Interaction with a large real-world set of third-party plugins adding/removing widgets dynamically.
9. Long-duration operation while Pwnagotchi is actively updating its UI.

Until those are checked, this package is **alpha**, not a stable release.

## How to reproduce

```sh
cd tweak-view-ng-suite
python3 -m pytest                       # 59 offline unit tests
python3 -m pytest tests/integration     # 16 real-framework tests (auto-clones v2.9.5.8)
# or point at a local checkout / the Pi itself:
PWNAGOTCHI_SRC=/path/to/pwnagotchi python3 -m pytest tests/integration
```
