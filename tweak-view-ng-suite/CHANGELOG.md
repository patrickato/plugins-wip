# Changelog

## 0.1.0-alpha5

Bugfix for the alpha4 editor upgrades, found in on-hardware testing.

### Fixed

- **Drag was dead in alpha4.** `startDrag` called `drawBoxes()`, which rebuilt
  the overlay and detached the very element being grabbed, so pointer events
  never fired (the browser did text-selection instead). Now it marks the box
  selected in place without rebuilding, preventDefaults the pointerdown, and
  captures the pointer reliably. Added `user-select:none`/`touch-action:none`
  to boxes and `pointer-events:none` to the name labels so they can't steal the
  drag. Real-time drag works.

## 0.1.0-alpha4

Editor UX upgrade pass (see `EDITOR_UPGRADES.md`). Makes positioning feel like a
real design tool while keeping everything one-click and fully undoable.

### Added

- **Real-time drag** — the overlay box follows the cursor during a drag (commit
  still on release), instead of jumping only when you let go.
- **Arrow-key nudge** — selected element moves 1px per arrow key, 10px with
  Shift.
- **Border/overlap warning** — the dragged box turns red when it crosses a
  divider line (`line1`/`line2`) or the screen edge; a warning, not a block
  (element-overlap warning available behind a flag).
- **Auto-align strips** — `Align`/`Distribute` buttons for the top and bottom
  status strips. Strip membership is auto-detected from the divider lines; a new
  `api/align` endpoint computes positions server-side and applies them as one
  undoable transaction. Align = shared median baseline; distribute = even gaps.
- **Snap-to-guides** (`Snap` toggle, default on) — dragging snaps to the divider
  lines and screen edges within 3px.
- **Safe-zone overlay** (`Zones` toggle, default on) — faintly shades the top and
  bottom strips.
- **Element name labels** on the preview boxes, and a "moved from default"
  indicator in the element list, the title, and the box border.

### Tests

- 68 unit (was 61) + 16 integration, all green; `api/align` covered by unit
  tests and a real-framework check; `node --check` clean on both JS copies.

## 0.1.0-alpha3

First on-hardware validation pass (Pi 4 + 480x320 ILI9486, Jayofelony
**2.9.5.9** — one patch past the 2.9.5.8 target, loads clean). Verified live:
plugin-loader startup, the web UI + `api/ready`/`api/health`/`api/state`
endpoints, edit -> atomic persist -> **reboot -> restore** (12 properties
re-applied on boot, confirmed on the physical TFT), undo/redo, and the
JavaScript-free recovery editor (101 editable rows).

### Fixed

- **Editor web UI: graceful handling of non-JSON responses.** The `api()`
  fetch helper now checks the response content-type before parsing, so a stale
  CSRF token (e.g. a page left open across a reboot — flask-wtf answers those
  POSTs with an HTML 400) shows "Session expired — reload the page" instead of
  the cryptic `Unexpected token '<' … is not valid JSON`. Applies to both the
  main editor and the recovery form; 401/403 and other non-JSON responses get a
  clear message too. Found during on-hardware validation.

### Notes

- Still alpha pending the remaining checklist items (legacy import on hardware,
  profile reset, long-run/third-party-widget churn). The critical gates
  (load, persist/restore across reboot, live display) have passed on-device.

## 0.1.0-alpha2

Suite-harvest hardening pass. All eight harvest items from
`SEPARATION_AND_SUITE_HARVEST.md` are implemented and covered by tests.
Still alpha: real-hardware validation on the Pi has not yet been done, which
remains the gate to `0.2.0-beta1`.

### Added

- **Real Jayofelony integration test layer** (item 1): `tests/integration/`
  runs NG against the genuine Jayofelony 2.9.5.8 `View`/`State`/components/fonts
  and the real plugin loader (pinned to the `v2.9.5.8` tag), via the headless
  `DummyDisplay`. Skips cleanly when no real checkout is available.
- **Per-entry corruption recovery + import report** (item 3): a malformed
  profile/element/property/shape is dropped with an explicit reason instead of
  invalidating a healthy layout; a structured load/import report is exposed in
  the state and readiness payloads. New `sanitize_layout()`,
  `LayoutStore.load_report()` and `import_legacy_report()`.
- **Explicit startup/readiness state** (item 4): an `init -> loaded ->
  waiting_ui -> ready` phase machine; a new `api/ready` endpoint readable at any
  phase; mutating APIs return a structured `503` (not a `500`) before the UI
  adapter exists.
- **HTML/DOM injection regression tests** (item 6): hostile element names,
  labels and values stay data (JSON API) and are entity-escaped by the recovery
  page, verified against real Jinja autoescaping.
- **Historical configuration fixtures** (item 7): original Tweak View, Tweak
  View 2, malformed, mixed valid/invalid, and current/corrupt NG-schema fixtures
  under `tests/fixtures/`, with conversion + recovery regression tests.
- **Minimal server-rendered recovery editor** (item 8): a dependency-light,
  JavaScript-free fallback editor at `/plugins/tweak_view_ng/recovery`.

### Changed

- **Earlier layout preload** (item 2): the layout is loaded and validated in
  `on_loaded()` (before any UI object), with UI-dependent application still in
  `on_ui_setup()`. `on_loaded()` never raises on a bad file.
- **Transactional edit/save rollback** (item 5): every mutating route runs
  through a transaction that snapshots layout + runtime and rolls both back if
  the handler or its atomic save fails, so runtime and persisted state never
  diverge. Deliberate `4xx` responses are not treated as failures.

### Tests

- 59 offline unit tests (was 32) + 16 real-framework integration tests.
- `pytest.ini` keeps the two suites in separate invocations (their conftests
  are mutually exclusive).

## 0.1.0-alpha1

First Tweak View NG implementation.

### Added

- Jayofelony v2.9.5.8 compatibility adapter.
- Resolution-independent editor.
- Offline web UI and actual `/ui` preview.
- Drag-to-position overlay.
- Safe property validation.
- Legacy Tweak View JSON import.
- Atomic persistence and backups.
- Named profiles.
- Undo/Redo.
- Per-element revert and profile reset.
- Custom line/rectangle/ellipse widgets.
- Deferred application for late-created plugin UI elements.
- Forced refresh for zero-FPS configurations.
- Unload restoration.
- Automated compatibility, lifecycle, persistence, concurrency and resolution tests.
