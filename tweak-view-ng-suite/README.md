# Tweak View NG — 0.1.0-alpha1

A hardened, resolution-independent successor to the original **Tweak View / Tweak View 2** concept for **Jayofelony Pwnagotchi 2.9.5.8 64-bit**.

## Target and hardware policy

The Pwnagotchi API target is intentionally specific: **Jayofelony 2.9.5.8**. Hardware is intentionally *not* hard-coded. The editor reads `ui.width()` and `ui.height()` at runtime, so the same plugin is intended to work across the 64-bit image's supported Raspberry Pi models and display drivers/resolutions.

This alpha has automated/simulated validation only. Physical Pi/display validation is still required before calling it a stable release.

## What is already implemented

- One compatibility adapter contains all intentional private Jayofelony UI-state access.
- Lock ordering follows Jay's `View._lock -> State._lock` rendering path.
- Runtime screen-size discovery; no 250×122 or 480×320 hard-coding.
- Safe property allow-list and validation.
- Pixel coordinates, including imported negative-from-edge coordinates.
- Font selection using Pwnagotchi fonts plus available DejaVu Mono sizes.
- Lines, rectangles, filled rectangles, ellipses and filled ellipses.
- Atomic JSON persistence (`fsync` + `os.replace`).
- Automatic `.bak` backup before overwriting a layout.
- Automatic import of legacy `/etc/pwnagotchi/tweak_view.json` `VSS.*` layouts when no NG layout exists.
- Named profiles.
- Undo / Redo history.
- Revert element and reset profile.
- Actual `/ui` frame preview.
- Drag-to-position overlay in the web editor.
- Forced redraw after committed edits, important when `ui.fps = 0`.
- Missing-element deferral: layouts can reference plugin widgets that appear after Tweak View NG loads.
- Restore touched original properties and remove NG custom shapes when the plugin unloads.
- No external web fonts/CDNs; editor works offline.

## Install (alpha / test system)

Copy `tweak_view_ng.py` to:

```bash
sudo cp tweak_view_ng.py /etc/pwnagotchi/custom-plugins/
```

Add to `/etc/pwnagotchi/config.toml`:

```toml
[main.plugins.tweak_view_ng]
enabled = true
filename = "/etc/pwnagotchi/tweak_view_ng.json"
legacy_filename = "/etc/pwnagotchi/tweak_view.json"
auto_import_legacy = true
backup = true
history_limit = 50
strict_version = false
```

Restart Pwnagotchi, then open:

```text
http://<pwnagotchi-host>:8080/plugins/tweak_view_ng/
```

### Why `strict_version = false` by default?

The alpha reports the runtime Pwnagotchi version but does not hard-stop on a mismatch. Set it to `true` if you want the plugin to refuse to initialize on anything except 2.9.5.8 while testing.

## Files

- `tweak_view_ng.py` — deployable single-file plugin.
- `tests/` — desktop compatibility/safety tests using a simulated Jay UI.
- `config.toml.example` — ready-to-paste config.
- `install.sh` — simple installer for a Pwnagotchi.
- `uninstall.sh` — disables/removes the plugin while preserving layout JSON.
- `TEST_REPORT.md` — exact automated vs physical-test status.

## Safety / rollback

The new plugin writes to `/etc/pwnagotchi/tweak_view_ng.json`; it does **not** overwrite the original `tweak_view.json`. If auto-import runs, the legacy file remains untouched.

Disable the plugin and restart Pwnagotchi to return to the base layout. `on_unload` also attempts to restore every property NG touched during the current process.

## Alpha limitations

- Physical e-ink/TFT validation has not happened yet.
- Bounding boxes for text widgets are intentionally approximate in the browser overlay; the real display preview remains authoritative.
- Changes made by another plugin to the same widget property after NG captures its original value can create ownership ambiguity. This needs explicit conflict-policy testing on hardware.
- This build intentionally does not edit Pwnagotchi core files.

## Lineage

Tweak View NG is a new implementation inspired by the original Tweak View work by NurseJackass/Sniffleupagus and the later Tweak View 2 work credited to Sniffleupagus/BraedenP232. Legacy layout import exists specifically to avoid stranding those users.
