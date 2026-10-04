# Tweak View NG — Project Status

## Identity

This is a **separate project** from the existing `tweak-view-suite` in this repository. Do not merge, overwrite, delete, join, or treat that older suite as the working tree for this project.

Project directory: `tweak-view-ng-suite/`
Current release: `0.1.0-alpha2`
Primary software target: **Jayofelony Pwnagotchi 2.9.5.8 64-bit**

### Permanent separation rule

`tweak-view-suite/` and `tweak-view-ng-suite/` must remain independent projects with their own source, tests, config, documentation, release history, and development paths.

While working on NG, do not modify files under `tweak-view-suite/` unless the repository owner explicitly requests separate work on that project.

NG may study behavior or lessons from Suite and independently reimplement useful ideas, but the projects are not to be merged or overlaid.

See `SEPARATION_AND_SUITE_HARVEST.md` for the full rule and the approved NG-native ideas identified from comparison with Suite.

## Goal

Build a hardened, modern successor to Tweak View / Tweak View 2 that is specific to the current Jayofelony 64-bit API while remaining Raspberry-Pi-model and display-resolution independent.

The plugin should adapt to any display the Jayofelony image exposes through the normal `View` interface rather than hard-coding 250x122, 480x320, or any other resolution.

## Current design decisions

- Jayofelony 2.9.5.8 is the authoritative API target for this alpha.
- Pi model is not hard-coded.
- Display model/resolution is not hard-coded; use runtime `ui.width()` / `ui.height()`.
- Version-sensitive private UI state access is isolated in one compatibility adapter.
- Use Jay's render/state lock order rather than directly mutating `_state._state` from arbitrary web requests.
- Prefer public `View` methods for add/remove/redraw where available.
- Keep the original `/etc/pwnagotchi/tweak_view.json` untouched; NG uses `/etc/pwnagotchi/tweak_view_ng.json`.
- Legacy `VSS.*` layouts may be imported automatically.
- No Pwnagotchi core-file modifications.
- Keep `strict_version = false` by default so future compatible Jay builds can be tried, but only claim versions physically verified.

## Alpha capability set

Implemented in 0.1.0-alpha1:

- compatibility adapter
- runtime resolution/orientation discovery
- drag positioning and direct property editing
- safe property allow-list / validation
- Pwnagotchi and DejaVu Mono font selection
- lines, rectangles, filled rectangles, ellipses, filled ellipses
- named profiles
- Undo / Redo
- per-element revert
- full profile reset
- atomic JSON writes and `.bak` backup
- legacy Tweak View `VSS.*` import
- live `/ui` frame preview
- forced redraw after committed edits, including `ui.fps = 0`
- deferred layout application for plugin widgets that appear later
- unload restoration and NG custom-shape cleanup
- offline editor with no CDN dependency

Added in 0.1.0-alpha2 (harvest hardening):

- layout preload + validation in `on_loaded()` (before the UI exists)
- per-entry corruption recovery with a structured load/import report
- explicit readiness phases, an `api/ready` endpoint, and a not-ready `503`
- transactional edits that roll back runtime + persisted state on save failure
- a minimal, JavaScript-free recovery editor at `/recovery`
- a real-Jayofelony integration test layer

## Suite-harvest gate before hardware validation

Useful lessons from the separate `tweak-view-suite/` were documented for
independent NG implementation. This did **not** authorize merging the projects,
and nothing under `tweak-view-suite/` was modified.

All eight NG-native additions are now implemented in `tweak-view-ng-suite/` and
covered by independent tests (see `TEST_REPORT.md`):

1. ✅ real Jayofelony framework/plugin-loader integration tests
   (`tests/integration/`, pinned to the `v2.9.5.8` tag);
2. ✅ earlier layout/config preload and validation (now in `on_loaded()`);
3. ✅ per-entry corruption recovery and explicit import reports
   (`sanitize_layout()`, `LayoutStore.load_report()`, `import_legacy_report()`);
4. ✅ deliberate startup/not-ready API and editor state (phase machine +
   `api/ready` + structured `503`);
5. ✅ stronger transactional edit/save rollback behavior
   (`_mutating_route()` rolls back runtime **and** persisted state on failure);
6. ✅ HTML/DOM injection regression tests (unit + real-Jinja);
7. ✅ historical Tweak View/Tweak View 2 configuration fixtures
   (`tests/fixtures/`);
8. ✅ minimal server-rendered recovery editor
   (`/plugins/tweak_view_ng/recovery`).

The harvest gate is therefore **cleared in software**. The remaining gate to
`0.2.0-beta1` is real-hardware validation (below), which has **not** been done.

## Test state

Desktop/simulated result: **PASS**.

- **Unit (stubbed framework): 59/59 pytest tests** (`tests/`, was 32).
- **Integration (real Jayofelony 2.9.5.8): 16 passed, 1 skipped** (`tests/integration/`).
  The skip is a loader helper that exists only after the 2.9.5.8 tag.

Also passing:

- Python compile / compileall
- embedded JavaScript syntax (`node --check`)
- shell syntax (`sh -n`)
- duplicate HTML-ID check
- 1,000 sequential edit stress test
- concurrent snapshot/edit stress test

The integration layer exercises NG against the genuine Jayofelony `View`,
`State`, components, fonts, plugin loader and real Flask/Jinja — so the
private-API assumptions in `JayUIAdapter`, the readiness `503`, the recovery
page's autoescaping and the transactional update path are all verified on the
real stack, not just stubs. What it does **not** cover is anything that needs
a physical display/touch panel (see below).

Simulated screen matrix:

- 250x122
- 296x128
- 400x300
- 480x320
- 800x480
- 320x480
- 480x800

## Next release gate

The project remains **alpha** until the Suite-harvest hardening items and real-hardware validation are complete.

First physical target:

- Raspberry Pi 4
- Jayofelony Pwnagotchi 2.9.5.8 64-bit
- 3.5-inch 480x320 ILI9486 display
- XPT2046/ADS7846 touch hardware

Physical checks still required:

1. real plugin-loader import/startup
2. real Flask/CSRF webhook behavior
3. actual framebuffer/display redraw after forced updates
4. edit -> persist -> restart -> restore cycle
5. Undo/Redo and reset on hardware
6. original Tweak View legacy import on hardware
7. interaction with live third-party widgets appearing/disappearing
8. long-running operation while the Pwnagotchi UI updates
9. Pi Zero 2W / Pi 3 / Pi 5 performance and compatibility when hardware becomes available
10. multiple actual display drivers/rotations

## Release path

`0.1.0-alpha1` -> Suite-harvest hardening (**done → `0.1.0-alpha2`**) ->
Pi 4/480x320 validation -> fixes -> `0.2.0-beta1` -> broader Pi/display
validation -> stable `1.0.0`.

We are at `0.1.0-alpha2`: the harvest hardening is complete in software. The
next step is the real-hardware validation pass listed above; `0.2.0-beta1` is
not tagged until that passes.

Do not mark stable merely because sandbox tests pass. Maintain explicit `PASS`, `SIMULATED PASS`, and `NOT TESTED` labels in future reports.
