# GPSTaggerNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs real-hardware/real-GPS testing before
it's considered done.

A rebuild of `privacy-nightmare.py`. GPS-tags every AP the pwnagotchi
sees (one JSON file per AP, in a folder you choose), and writes a
`.gps.json` sidecar next to each handshake capture using the exact
schema `handshakes_dl_ng.py` already reads - so a tagged capture shows
up with a GPS download link on that suite's web page automatically,
with nothing extra to configure.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Requirements & dependencies

- Hardware: any pwnagotchi running the jayofelony 64-bit image (verified
  against a Pi 4 + 3.5" TFT setup), plus a working GPS source. That can
  be this fork's own built-in `main.plugins.gps` (recommended - see
  below) or any other setup that ends up populating bettercap's
  `gps.device`/session data.
- No extra apt or pip packages - only the Python standard library
  (`json`, `os`, `time`, `math`).
- No secrets or accounts needed. The only `>>> USER INPUT REQUIRED <<<`
  item is `gps_device` in `config.toml`, and only if you set
  `manage_gps = true` (see below) - leave it commented out otherwise.

## What's fixed vs. the original (`privacy-nightmare.py`)

All nine items below were confirmed by reading the original source and
this fork's real framework source side by side - see `NOTES.md` for the
full detail on each, including the two (filename collisions and
invalid-JSON output files) found newly during this rebuild rather than
in the original review.

1. `self.gps_hot` was read before it was ever set - `AttributeError` on
   the very first AP seen via a certain event path.
2. `latlong` was used outside the branch that defined it - guaranteed
   `NameError` any time GPS wasn't locked yet (the common case).
3. Two config options were read with bare `self.options[...]` indexing -
   `KeyError` risk if either was left unset.
4. A hand-rolled second bettercap websocket connection, run in its own
   background thread, replaced with this fork's real
   `on_bcap_<event>` hook mechanism - no more duplicate connection or
   leaked thread.
5. A new-AP event handler passed a single dict where a list was
   expected, guaranteeing a `TypeError` every time a genuinely new AP
   appeared.
6. This fork can hand a plugin a bare MAC string instead of a full AP
   dict in some cases - the original assumed a dict always, guaranteed
   `TypeError` on that path.
7. The per-AP output file was two concatenated `json.dump()` calls, not
   one valid JSON document.
8. The output filename was built from the AP's hostname alone - two
   different APs sharing an SSID (very common) would overwrite each
   other's file.
9. A copy/paste bug always displayed the same number twice on-screen
   (e.g. "5/5").

## What's added

- **`.gps.json` sidecar per handshake** - written next to the capture
  file itself, in the `{"Latitude": .., "Longitude": ..}` schema
  `handshakes_dl_ng.py` already understands.
- **Distance filter (`min_regap_distance_feet`, default 50 ft)** - a
  restart forgets which APs were already tagged this run; without this,
  every AP would get its file rewritten again on every restart even if
  you haven't moved. 50 feet is a general-purpose default sized around
  typical consumer (non-RTK) GPS drift, not tuned to any specific
  location - see `NOTES.md` for the reasoning. Raise it if your GPS is
  noisier, lower it for a higher-precision receiver.
- **Rate-limited "no GPS fix" logging** (`no_gps_log_interval_seconds`,
  default 300s) instead of one log line per event.
- **`manage_gps` flag (default `false`)** - see "GPS setup" below.

**Deliberately not added:** its own GPS hardware driver - it reads
whatever GPS source you already have configured, it doesn't replace one.

## GPS setup — read this before enabling

This plugin needs *something* to be populating bettercap's GPS session
data. You have two options, and you should only ever use one at a time:

1. **Recommended: use this fork's own built-in `[main.plugins.gps]`**
   (or `gps_listener` / `gsmfake`, if that's what you're already
   running). Leave `manage_gps = false` (the default) in this plugin's
   config - it will just read whatever GPS source is already active.
2. **Let this plugin manage GPS itself**: set `manage_gps = true` and
   fill in `gps_device` (`>>> USER INPUT REQUIRED <<<`) and `gps_speed`.
   Only do this if you are **not** also running the built-in `gps`
   plugin (or anything else) against the same device - two things
   driving the same serial port will conflict.

## Install

1. Copy `gps_tagger_ng.py` into your custom plugins folder
   (`custom_plugins` in `config.toml`, typically
   `/etc/pwnagotchi/custom-plugins/`).
2. Add the block from `config.toml` to
   `/etc/pwnagotchi/config.toml`, and decide on the GPS setup above.
3. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```
4. Tagged AP files show up under `pn_output_path`; a `.gps.json` file
   shows up next to each new handshake capture once GPS has a fix.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| No `.gps.json` files ever appear | Check GPS actually has a fix - `manage_gps=false` and no other GPS plugin running means this plugin has nothing to read |
| AP files keep reappearing/updating even though you haven't moved | Your GPS may be noisier than the 50 ft default assumes - check the actual jitter and raise `min_regap_distance_feet` if needed |
| Two AP files for what looks like the same network | Expected if two physically different APs share the same SSID (fix #8) - check the MAC in the filename |
| `on_ready` warning about `manage_gps` and no `gps_device` | You set `manage_gps = true` but didn't fill in `gps_device` - either fill it in or leave `manage_gps = false` |
