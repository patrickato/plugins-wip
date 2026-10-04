# Tweak View NG — Separation Rule and Suite-Harvest Notes

## Hard separation rule

`tweak-view-ng-suite/` and `tweak-view-suite/` are separate projects.

This is a permanent project rule unless the repository owner explicitly changes it later.

Do **not**:

- merge the two projects;
- join their working trees;
- overwrite files in either project with files from the other;
- delete either project because the other exists;
- rename/move one project on top of the other;
- treat one project as a replacement working tree for the other;
- copy a whole implementation from one project into the other;
- share runtime/config files by accident;
- make changes to `tweak-view-suite/` while working on `tweak-view-ng-suite/`.

Both projects should retain their own source, documentation, tests, configuration, release history, and development path.

### Project identities

- `tweak-view-suite/` — conservative repaired/hardened descendant of the original Tweak View behavior.
- `tweak-view-ng-suite/` — independently developed next-generation editor architecture for current Jayofelony Pwnagotchi.

The older Suite remains useful as a reference implementation and behavioral comparison target. NG may study lessons from it, but those lessons should be independently implemented inside NG rather than merged wholesale.

## Runtime separation

The projects must also remain operationally distinct.

Future work should prevent accidental install-time collisions. In particular, both projects currently have historical naming overlap around `tweak_view_ng.py` / `TweakViewNG` / `tweak_view_ng.json`. This is a known issue to resolve carefully without modifying or deleting the older Suite while working on NG.

Any NG-side compatibility change must preserve existing Suite files untouched.

## Suite-harvest items for NG

The following ideas were identified as worth independently incorporating into NG. These are **design lessons**, not instructions to merge Suite code.

### 1. Real Jayofelony integration test layer

Add a test layer that exercises the real Jayofelony Python plugin framework in addition to NG's existing unit/simulated-UI tests.

Desired stack:

1. pure unit tests;
2. simulated Jay `View` / `State` / widgets;
3. real Jayofelony plugin loader and Flask/Jinja/CSRF conventions;
4. physical Raspberry Pi validation.

The Suite's framework-level tests are useful reference material for expected behavior.

### 2. Earlier layout preload

Move disk/config preload and validation as early as practical (`on_loaded`) while keeping UI-object-dependent application in `on_ui_setup`.

Goal: layout data should already be parsed and validated before the first UI application pass.

### 3. Per-entry corruption recovery

A malformed entry should not invalidate an otherwise healthy layout or legacy import.

Add explicit import/load reporting such as:

- imported entries;
- skipped entries;
- reason each entry was skipped;
- affected profile/element/property.

Preserve valid entries whenever safe.

### 4. Explicit startup/readiness behavior

NG should have a deliberate not-ready state rather than relying on a caught exception if the web UI/API is reached before `on_ui_setup` completes.

Desired behavior includes a small readiness payload and browser status such as:

- plugin loaded;
- layout loaded;
- waiting for UI adapter;
- ready.

### 5. Stronger transactional edit/save rollback

NG already uses atomic file replacement and backups. Extend this so a failed persistence operation cannot leave runtime state claiming a change that was not safely saved.

Desired transaction concept:

1. capture prior state;
2. validate requested edit;
3. prepare persisted state;
4. save atomically;
5. commit runtime mutation;
6. roll back cleanly on failure.

Exact ordering may be adjusted to match Jay's UI constraints, but runtime and persisted state should not silently diverge.

### 6. HTML/DOM injection regression tests

Retain Suite's defensive escaping philosophy and test hostile/odd widget metadata, labels, names, and values.

Examples should include HTML metacharacters, tags, quotes, ampersands, and script-like strings. The browser editor must display these as data, never execute them.

### 7. Historical configuration fixtures

Preserve representative real-world legacy layouts as NG test fixtures without altering the source Suite project.

Suggested fixture coverage:

- original Tweak View layout;
- Tweak View 2 layout;
- malformed legacy layout;
- mixed valid/invalid legacy layout;
- current NG schema layout;
- future schema-migration fixtures as formats evolve.

Every NG release should retain legacy-conversion regression tests.

### 8. Minimal recovery editor

Consider an intentionally simple server-rendered fallback page, separate from NG's main JavaScript editor.

Possible route:

`/plugins/tweak_view_ng/recovery`

Purpose: provide a basic element/property editor if the richer frontend is unavailable or broken. It should be simple, dependency-light, and independently testable.

## What NG should NOT inherit from Suite

Do not bring over the older architectural limitations merely for compatibility:

- direct raw `_state._state` access spread throughout the plugin;
- unrestricted object introspection as the primary editing API;
- plain non-atomic JSON writes;
- a single flat `VSS.*` storage model as NG's native schema;
- reapplying every tweak on every normal UI update;
- lack of profiles;
- lack of Undo/Redo;
- architecture coupled tightly to original Tweak View internals.

Legacy compatibility should be provided through import/adapters/tests rather than by reverting NG's architecture.

## Development rule going forward

When an idea is discovered in `tweak-view-suite/`:

1. document the behavior or lesson;
2. decide whether it benefits NG;
3. design the NG-native implementation independently;
4. add NG-specific tests;
5. modify files only under `tweak-view-ng-suite/` unless the repository owner explicitly requests work on Suite separately.

This keeps both projects intact and allows them to continue as independent comparison/reference implementations.
