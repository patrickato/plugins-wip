# Tweak View NG — test fixtures

Representative layout/config files used by the regression tests (suite-harvest
item 7). These are **independent NG fixtures**: they illustrate the real-world
shapes NG must ingest without copying anything from `tweak-view-suite/`.

| File | Represents | Used to verify |
|---|---|---|
| `legacy_tweak_view.json` | An original Tweak View `VSS.*` layout | Legacy auto-import converts edits + status wrap/max_length |
| `legacy_tweak_view_2.json` | A Tweak View 2 layout with `__custom_shapes__` | Custom line/rect/ellipse shapes map to NG shape types |
| `legacy_malformed.json` | A badly broken legacy file | Per-entry recovery keeps valid entries, skips the rest with reasons |
| `legacy_mixed_valid_invalid.json` | A legacy file mixing good and bad entries | Valid entries survive; invalid ones are reported, none abort the import |
| `ng_schema_current.json` | A healthy current-schema NG layout | Round-trips through `LayoutStore.load_report()` unchanged |
| `ng_schema_corrupt_entries.json` | A current-schema NG file with corrupt entries | Healthy profiles/elements/shapes preserved; corrupt ones dropped + reported; missing active profile falls back to `default` |

Every NG release should keep the legacy-conversion and corruption-recovery
regression tests green against these fixtures. When the schema evolves, add a
new `ng_schema_*` fixture rather than editing the existing ones, so older
formats stay covered.
