# FixRegionNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins`.

A bug-fix rebuild of `fix_region.py` (itsdarklikehell, edited from
@V0rT3x's original `network-fix` service idea, credit:
Dal/FikolmijReturns). It forces the WiFi radio's regulatory domain
(country code) via `iw reg set`, persisted across reboots with a small
root-owned shell script + systemd service, so channels the stock
regulatory domain blocks (e.g. 12/13 outside the US) become available.

The original had real, source-verified bugs (see NOTES.md for the full
list) - an import-time `KeyError` crash risk, shell commands built by
raw string concatenation, config changes that were silently ignored
after the first load, and an unguarded `on_unload`. All are fixed here
while keeping the original's design (systemd-persistence-across-reboots)
unchanged - see NOTES.md.

## Requirements & dependencies

- **`iw`**, the command-line tool `iw reg get`/`iw reg set` use. This
  is standard on every pwnagotchi image already - nothing extra to
  install for that.
- Python: nothing beyond the standard library.

**No pip or apt package is required beyond `iw`.** The original
declared a `pip: ["scapy"]` dependency it never actually used anywhere
in the file - see NOTES.md, fix #5.

## Install

1. Copy `fix_region_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`,
   setting `region` to your actual location's ISO 3166-1 alpha-2 code
   (e.g. `"NL"`, `"GB"`, `"DE"`). The section header must be exactly
   `[main.plugins.fix_region_ng]` - see "A naming note" below.
3. If you were running the original `fix_region.py`, disable/remove it
   first (`enabled = false` under its own `[main.plugins.fix_region]`
   section, or delete the file) - both plugins would otherwise manage
   the same `/root/network-fix.sh` / `network-fix.service` files.
4. Enable the plugin (`enabled = true`) and restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```
   The plugin itself will also trigger one more restart automatically
   the first time it actually applies a region, so the change takes
   effect immediately without you needing to restart twice by hand.

### A naming note

The plugin file is `fix_region_ng.py` (snake_case), so its config
section must be exactly `[main.plugins.fix_region_ng]` - matching the
file's basename, not the class name (`FixRegionNG`) and not the
original's section name (`fix_region`). This fork's plugin loader
registers and looks up a plugin - both whether it's "enabled" at all,
and its options table - by the plugin **file's exact basename**, never
by anything written inside the Python class. Get the section name
wrong and the plugin silently never loads at all - no error, it just
never appears as enabled. See NOTES.md for the full, verified
explanation.

## How to verify it worked

```
iw reg get
iwlist wlan0 channel
```
`iw reg get` should show `country <YOUR_REGION>:` after the plugin has
applied a change and pwnagotchi has restarted. `iwlist wlan0 channel`
lets you confirm channels 12/13 (or whatever your target region
unlocks) are now listed as usable.

The plugin's own webhook page at `/plugins/fix_region_ng/` also shows:
the configured region, whether it's a valid ISO 3166-1 alpha-2 code,
the last region actually applied (and when), the live `iw reg get`
domain, and whether the persistence service is installed/enabled - a
quick way to check all of the above without SSHing in, and without
guessing why nothing changed if `region` was invalid.

## What you get

- `region` forced via `iw reg set`, applied once immediately and
  persisted across reboots via a systemd service
  (`network-fix.service`) that re-runs `/root/network-fix.sh` on every
  boot - unchanged in spirit from the original design (see NOTES.md
  for why this was kept).
- Real change detection: editing `region` in config.toml and reloading
  the plugin now actually re-applies the new region (the original
  silently ignored config changes after the first run - see NOTES.md,
  fix #3). Nothing gets rewritten or restarted on a boot where nothing
  changed.
- Format-validated `region` (ISO 3166-1 alpha-2, 2 letters) - an
  invalid value is refused with a clear log message instead of being
  passed straight to a shell command or crashing plugin load.
- Every shell interaction goes through `subprocess.run([...])` with an
  argument list - no `shell=True`, no string-built command lines,
  anywhere in this file.
- The regulatory domain from `iw reg get` is logged once on load
  (before anything changes) and shown live on the webhook page.
- Optional, off-by-default GPS-based region suggestion
  (`gps_region_suggestion`) - a logged hint only, never auto-applied.
  See config.toml and NOTES.md.
- A guarded `on_unload`: files are removed with `os.remove()` inside
  `try/except FileNotFoundError` (never a bare `rm` shell-out), and the
  systemd service is only stopped/disabled if `systemctl is-enabled`
  actually reports it as enabled.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Plugin never appears as loaded at all | Check the config section is spelled exactly `[main.plugins.fix_region_ng]` - see "A naming note" above. |
| "configured region ... is not a valid ISO 3166-1 alpha-2 code" in the log | `region` isn't exactly two letters - check config.toml for a typo. Nothing is applied while this is the case. |
| Editing `region` and restarting doesn't seem to do anything | Check the log for "already applied - nothing to do" (means the new value didn't actually change from the last-applied one after normalization) vs. a validation error (see above). |
| `iw reg get` still shows the old country after a change | Give it a moment - the plugin restarts pwnagotchi once after applying a change; if it still doesn't stick, check `iw reg set <region>` works manually and that `network-fix.service` is enabled (`systemctl status network-fix`). |
| GPS suggestion never appears even with `gps_region_suggestion = true` | This is best-effort by design - it needs a GPS-providing plugin (e.g. `gps_tagger_ng`) loaded with a current fix, and your coordinates need to fall inside the plugin's small built-in bounding-box table (a handful of common countries, not a full geo database - see NOTES.md). No error is logged for this; it degrades silently. |

## Still open / needs real-hardware testing

- The `network-fix.service`/`iw reg set` mechanics themselves are
  carried over unchanged from the original design and are the same
  approach already in real-world use by the upstream plugin - the main
  things worth a real-hardware pass are the new change-detection logic
  (editing `region` a second time and confirming it actually re-applies
  and restarts exactly once) and the guarded `on_unload` cleanup.
- The GPS-suggestion bounding-box table is a small, hand-built,
  explicitly-not-exhaustive lookup - see NOTES.md for its exact scope
  and what a "real" implementation would want instead.
