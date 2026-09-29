# CrackHouseNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done.

A fixed rebuild of `crack_house.py` (itsdarklikehell/V0rT3x) - shows
the closest nearby cracked network and its password from your
potfiles, falling back to the last one learned when nothing nearby
matches.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Requirements & dependencies

- Python: none beyond the standard library.

## What's fixed vs. the original (all crash-on-load bugs)

1. **Guaranteed crash on load, on every display type.** `on_ui_setup`
   called `ui.is_waveshare_v2()`, `ui.is_inky()`, `ui.is_lcdhat()`, and
   similar methods to pick a position. Those methods only exist on
   this fork's `Display` class - the object plugin `on_ui_setup`/
   `on_ui_update` hooks actually receive is always the plain `View`
   (confirmed in `pwnagotchi/ui/view.py`, which calls
   `plugins.on('ui_setup', self)` with itself, never a `Display`).
   `View` has none of these methods, so the very first line of
   `on_ui_setup` always raised `AttributeError` - this plugin could
   never load successfully on this fork, on any hardware. Replaced
   entirely with configurable `position_x`/`position_y` (and
   `stats_position_x`/`stats_position_y`), defaulting to the
   original's own fallback values.
2. **A second, independent crash behind the first.** Even setting
   the display-detection issue aside, `s_pos` (the stats element's
   position) was only ever assigned inside the `is_lcdhat()` branch -
   enabling `display_stats` on any other display type would have hit
   `UnboundLocalError` on `s_pos` regardless.
3. **Crash on a missing configured file.** `on_loaded` opens every
   file in `files` with a plain `open()` and no error handling - a
   very likely first-run state (e.g. no `OnlineHashCrack.cracked` file
   yet) crashed the plugin entirely instead of just skipping that one
   file.
4. **Hardcoded interface.** `on_wifi_update` always ran
   `iwconfig wlan0` to check association state, regardless of the
   interface actually configured in pwnagotchi's own `config.toml`.
5. **Inconsistent fallback data source.** When no nearby cracked
   network matched, the UI fell back to shelling out to
   `tail`+`awk` against one specific hardcoded upstream file
   (`/root/handshakes/wpa-sec.cracked.potfile`), not whatever the user
   actually configured in `files`/`saving_path` - so the fallback
   could show stale or wrong data, or nothing at all, independent of
   the plugin's own settings.

## What's added (beyond the original bug fixes)

1. **Case-insensitive hostname matching.** `on_wifi_update` used to
   compare an access point's hostname to a potfile entry with a plain
   `==`, so a network seen over the air as `"MyLab"` would never match
   an entry saved as `"mylab"` or `"MYLAB"` - even though wpa-sec
   exports, `.cracked` files, and live scan results don't reliably
   agree on ESSID casing. Matching is now case-insensitive; the
   potfile's own original casing is still what's displayed.
2. **Cracked list now survives across reboots even if the source
   files don't.** `on_loaded` used to rebuild `_crack_menu` from
   `files` alone on every load - if a source file was rotated,
   cleared, or a wpa-sec export just wasn't re-downloaded before a
   reboot, any crack it once contributed silently disappeared even
   though it was already learned. `on_loaded` now also reads its own
   previous `saving_path` output first and merges those entries in,
   so a cracked network stays known once it's been seen, regardless
   of what the currently-configured `files` happen to contain on any
   given boot.

## What's kept

- The core idea and both original potfile/`.cracked` parsing formats.
- The "vertical"/"horizontal" orientation option and its original
  fallback positions (now the defaults instead of the crashing
  auto-detect path).
- The nearby-vs-total stats element.

## Install

1. Copy `crack_house_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
   Every default works out of the box, except double-checking your
   `files` paths point at potfiles you actually have.
3. If you were running the original `crack_house.py`, disable/remove
   it first.
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Element never shows anything | No cracked networks matched any configured `files` yet - check the webhook page (`/plugins/crack_house_ng/`) for how many were loaded |
| Position looks wrong on your screen | Set `position_x`/`position_y` explicitly - there's no more automatic per-display-model positioning (see fix #1 above, it never worked on this fork anyway) |

## Still open / needs real-hardware testing

- Real on-screen position for the 3.5" TFT hasn't been checked yet -
  the defaults are inherited from the original's own fallback values,
  not verified against this specific display.
- The `iwconfig`-based association check is text-parsing based and
  hasn't been verified against this fork's actual `iwconfig` output
  format on real hardware.
