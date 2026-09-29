# WifiJammerNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done.

**FOR YOUR OWN AUTHORIZED LAB HARDWARE ONLY.** This plugin sends real
802.11 deauthentication frames via bettercap's `wifi.deauth` module. It
will never fire against anything you have not explicitly listed in
`authorized_networks` in `config.toml` - that list is empty by default,
and an empty list means the plugin loads but does nothing at all. Read
the comments in `config.toml` before enabling this.

A rebuild of `wifi_jammer.py` (itsdarklikehell/pwnagotchi-plugins,
author "Deus Dust"). The original could never actually run on this fork
at all (see "What's fixed" below) and had no targeting scope whatsoever
- it would have fired at every AP that ever yielded a handshake, with no
whitelist. This rebuild keeps the "send a deauth burst at a target"
concept but makes it something you can actually point at your own gear
only, on purpose.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Requirements & dependencies

- Hardware: any pwnagotchi running the jayofelony 64-bit image with
  bettercap already running (the same bettercap session pwnagotchi
  itself uses - no separate tool or process needed).
- Python: none beyond what pwnagotchi/bettercap already provide - no new
  pip packages.
- **>>> USER INPUT REQUIRED <<<**: at least one entry in
  `authorized_networks` in `config.toml` - your own AP's BSSID and/or
  SSID. See `config.toml`'s comments for the exact format.

## What's fixed vs. the original

1. `from pwnagotchi.plugins import BasePlugin` - **`BasePlugin` does not
   exist anywhere in this fork** (confirmed by grepping the real cloned
   `pwnagotchi/plugins/__init__.py` source - the actual base class is
   `plugins.Plugin`). The original could never even be imported, so it
   could never load or run, period. Rebuilt on the real base class.
2. `access_point.bssid` (attribute access) - this fork hands plugins a
   plain dict (`access_point['mac']`), not an object with a `.bssid`
   attribute. Would have been an `AttributeError` even if fix #1 alone
   had been applied.
3. **No authorization/scoping at all.** The original fired at *every*
   AP that ever yielded a handshake, unconditionally, with an unbounded
   `aireplay-ng --deauth 0` (0 = send forever). Replaced entirely with
   the `authorized_networks` allowlist - see the big warning above and
   in `config.toml`.
4. Shelled out to `aireplay-ng` as a brand-new subprocess competing with
   bettercap for the same monitor interface, and needed a separate
   binary + hardcoded interface name (`wlan0`) that may not even be
   correct for your setup. Replaced with `agent.run('wifi.deauth <mac>')`
   - the exact same in-process bettercap API call this fork's own core
   `pwnagotchi/agent.py` already uses for its normal deauth behavior.
   No new dependency, no interface-name guessing, no risk of two
   processes fighting over the interface.

## What's added

- A per-target `cooldown_seconds` so an authorized target can't be
  hammered on every single association/handshake event in a tight loop.
- Fires on **both** `on_association` (as soon as bettercap interacts
  with the AP - doesn't require a captured handshake first) and
  `on_handshake` (the original's only trigger) - both on by default,
  so you can test against your own gear without waiting for a full
  handshake capture.
- A manual **"fire now" webhook page** for on-demand testing independent
  of any live event - visit `http://pwnagotchi.local:8080/plugins/wifi_jammer_ng/`
  to see your configured BSSID-form targets and fire a burst at any of
  them on demand, subject to the same cooldown.
- SSID matching in addition to BSSID matching in `authorized_networks` -
  list your own network by name if you don't have its MAC handy. BSSID
  is still the more precise, unambiguous match; SSID is offered purely
  for convenience on hardware you already control.

## Install

1. Copy `wifi_jammer_ng.py` into your custom plugins folder
   (`custom_plugins` in `config.toml`, typically
   `/etc/pwnagotchi/custom-plugins/`).
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`,
   filling in `authorized_networks` with your own gear
   (`>>> USER INPUT REQUIRED <<<` - do not skip this, the plugin does
   nothing until it's filled in).
3. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```
4. To fire manually at any time, visit
   `http://pwnagotchi.local:8080/plugins/wifi_jammer_ng/` in a browser
   on the same network and click "fire now" next to a listed BSSID.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Nothing ever fires | Check `authorized_networks` isn't empty and that the entry exactly matches your AP's real BSSID/SSID (check `pwnagotchi.log` at startup - it logs how many authorized BSSIDs/SSIDs it loaded) |
| Fires once, then goes quiet for a while against the same target | That's `cooldown_seconds` doing its job - lower it in `config.toml` if you want more frequent bursts during a test session |
| Webhook page shows "no BSSID-form authorized targets configured" | You only listed SSID-form entries - manual on-demand firing needs a BSSID, since there's no live-lookup from SSID to MAC on that page. SSID entries still fire automatically on association/handshake. |
| Webhook says "Not ready yet" | The plugin hasn't seen `on_ready` fire yet (very early in startup) - wait a few seconds and reload |
| `wifi.deauth` call logs a failure | bettercap itself rejected or errored on the command - check `pwnagotchi.log` around that line for bettercap's own error text |

## Still open / needs real-hardware testing

- Actual over-the-air behavior (does the target device visibly
  disconnect/reconnect) can't be verified from a sandbox - needs a real
  run against your own lab AP.
- `wifi.deauth`'s exact behavior when pointed at an AP's own MAC vs. a
  specific client station's MAC is bettercap's own, unchanged - this
  plugin always targets the AP's MAC (matching the original's intent of
  "deauth the whole AP"), not a specific client.
