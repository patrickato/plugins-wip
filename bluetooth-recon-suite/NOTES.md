# Notes: BluetoothReconNG

## Why this one was a merge, not two separate rebuilds

This was a user-directed design decision (merge `blemon_plugin.py` and
`bluetoothsniffer.py` into one plugin, `BluetoothReconNG`), not
something this rebuild independently decided or second-guesses here.
Both originals answer essentially the same question ("what Bluetooth
devices are nearby") over two different radios with two entirely
separate, non-interoperating data models (an in-memory counter for
BLE, a hostname-keyed JSON file for classic) - merging them into one
unified, deduplicated-by-MAC device table with one persistence format,
one config block, and one webhook page removes that duplication
instead of shipping two plugins that both partially answer the same
question in incompatible ways.

## Bugs found in blemon_plugin.py (all source-verified)

1. **Dead hooks.** `on_ai_ready`, `on_ai_policy`,
   `on_ai_training_start/step/end`, `on_ai_best_reward`,
   `on_ai_worst_reward`, and `on_free_channel` are all defined as
   pass-through no-ops. None of these are hooks this fork's
   `plugins.on()` dispatcher (confirmed against
   `pwnagotchi/plugins/__init__.py` and every `plugins.on(...)` call
   site in `pwnagotchi/agent.py`) ever actually calls - they're
   artifacts of the "example plugin that implements all the available
   callbacks" this file was based on, written for a version of the
   framework (or an entirely different, AI-training-capable fork) that
   isn't this one. Removed entirely in the rebuild; this fork's real
   hook list is documented in the plugin's module docstring.
2. **UI key mismatch.** `on_ui_setup` registers the element as
   `"blemon_count"`, but `on_bcap_ble_device_lost` calls
   `ui.set("blecount", ...)` - a different string. That call would have
   raised (or, depending on the `State`/`View` implementation, silently
   no-op'd) every time a device was "lost", meaning the count could
   drift stale after every lost-device event and never catch up until
   the next "new"/"connected" event happened to fire. Fixed in the
   rebuild by not maintaining a hand-incremented/decremented counter at
   all - the BLE count shown is computed live from the unified device
   table on every `on_ui_update`, so there's no separate counter state
   to ever drift out of sync with the table in the first place.
3. **`name is ""` identity comparison.** Used twice
   (`on_bcap_ble_device_new`, `on_bcap_ble_device_lost`). Works today
   only because of CPython's small-string interning implementation
   detail for the empty string, not because of any language guarantee
   - and it's flagged as a `SyntaxWarning` on modern Python. Not
   ported into the rebuild at all (this rebuild doesn't do
   name-based `display.set("status", ...)` messaging - see "Dropped"
   below - but the underlying lesson applies to any future string
   comparison in this codebase: always `==`, never `is`, for value
   equality).
4. **Wrong declared dependency.** `__dependencies__ = {"pip":
   ["scapy"]}`, but `scapy` is never imported anywhere in the file -
   every BLE interaction goes through `agent.run("ble.recon ...")`,
   bettercap's own API, exactly like `wifi_jammer_ng.py`'s
   `agent.run("wifi.deauth ...")` and `gps_tagger_ng.py`'s
   `agent.run("gps on")` already do in this project. Fixed: no `pip`
   dependency declared for the BLE half at all.

## Bugs found in bluetoothsniffer.py (all source-verified)

1. **Guaranteed `KeyError` on load in the common case.** `__init__`
   sets `self.options = {...a dict of defaults...}`, but this fork's
   real loader (`pwnagotchi/plugins/__init__.py`, `load()`) does
   `loaded[name].options = pwnagotchi.config['main']['plugins'][name]`
   - a **raw assignment** of the user's parsed TOML table onto
   `plugin.options`, which runs *after* `__init__` and overwrites
   whatever `__init__` set completely. `__defaults__` is never merged
   either. Every one of `on_loaded`'s five direct
   `self.options["key"]` reads (`devices_file`, twice for
   `timer`/`count_interval` in other methods, `bt_x_coord`,
   `bt_y_coord`) was one missing config key away from a real
   `KeyError` crash - and since the original never told the user they
   needed to set every one of those keys explicitly in config.toml
   (it looked, from the source, like they were optional with sane
   built-in defaults), this was a near-guaranteed first-run crash for
   anyone who just enabled the plugin per its own apparent design.
   Fixed with the `_opt()`-reads-a-module-level-`DEFAULTS`-dict pattern
   already established in this repo by `crack_house_ng.py`/
   `timer_ng.py`/`gps_tagger_ng.py`.
2. **UI key mismatch on unload.** `on_ui_setup` registers the element
   as `"BtS"`; `on_unload` calls `ui.remove_element("BluetoothSniffer")`
   - a different string that was never a real element key. Per the
   confirmed framework fact that `State.remove_element()` does a bare
   `del self._state[key]` with no guard, this always raised `KeyError`
   - masked only by the surrounding broad `except Exception`, so the
   element itself was never actually removed from the UI, and the user
   never got an error telling them why. Fixed: uses the real element
   key, and (matching this project's established convention) wraps
   each `remove_element` call individually in `try/except KeyError` so
   removing a key that genuinely was never added (e.g. `on_unload`
   firing before `on_ui_setup` ever ran) can't crash unload either.
3. **Unguarded missing-binary crash.** `scan()`'s
   `subprocess.check_output(cmd_inq.split())` call only catches
   `subprocess.CalledProcessError` - a missing `hcitool` binary (BlueZ
   tools not installed) raises `FileNotFoundError`, which was never
   caught and would propagate out of the scan path entirely. Fixed:
   the rebuild's `_classic_scan()` explicitly catches
   `FileNotFoundError` (with a clear log message pointing at the
   missing dependency) alongside `CalledProcessError` and a general
   `(subprocess.SubprocessError, OSError)` fallback.

## What was kept vs. dropped from the originals

**Kept:** the core idea of both radios (bettercap `ble.recon` for BLE,
`hcitool` for classic), persistence to a JSON file across reboots, an
on-screen BLE count and an on-screen classic-BT summary, a webhook
page.

**Dropped, deliberately:**
- blemon_plugin.py's per-device `ble.enum <mac>` auto-enumeration and
  its `display.set("status", ...)` "chatty" status messages
  ("Something blue!!!", "Bye <name>", etc.) - cute, but out of scope
  for a recon/tracking tool, and not something the merge spec asked
  for. The rebuild logs device events instead of narrating them on the
  main status line.
- bluetoothsniffer.py's `hcitool name`/`hcitool info ... | grep
  Manufacturer` per-device shell-outs for name/manufacturer lookup.
  These were slow (a `time.sleep(0.1)` poll loop with a 7-second
  timeout, per device, per scan) and manufacturer info is now handled
  by the built-in OUI table instead (instant, no per-device shell-out,
  works for BLE too where `hcitool info` doesn't apply at all). Device
  *names*, when broadcast, still come from the scan itself
  (`hcitool scan`'s own output includes the name when the remote
  device advertises one; `ble.recon`'s device event `name` field for
  BLE).

## What's new (all approved - see the task spec this was built from)

- **Unified device schema**, deduplicated by MAC across both radios:
  `mac`, `radio_type`, `name`, `vendor`, `is_tracker`+`tracker_type`,
  `first_seen`, `last_seen`, `rssi`, plus `is_known` and the three
  correlation fields below.
- **Persistence + retention.** Full table as JSON
  (`device_table_path`, default `/root/handshakes/bluetooth_recon_ng.json`
  - same directory convention as `crack_house_ng.py`'s `saving_path`),
  reloaded on `on_loaded`. `retention_hours` (default 168 = 7 days)
  prunes devices whose `last_seen` is older than that, in memory and in
  the persisted file, checked cheaply once per scan cycle
  (`_prune_expired`, also run once up front in `on_loaded` and once per
  webhook hit so the status page never shows already-expired entries).
- **Filtering.** `rssi_threshold` (default `None`/disabled) drops
  weak-signal sightings from being recorded at all, only when RSSI is
  actually available for that event. `known_devices` (default `[]`)
  flags matching MACs with `is_known=True` without ever removing or
  hiding their data - it's a highlight, not a filter, per the spec.
- **OUI vendor lookup.** `OUI_TABLE` is a curated, hand-picked subset -
  roughly 90 entries covering common consumer-electronics, mobile, and
  IoT vendors (Apple, Samsung, Google, Amazon, Microsoft, Sony, LG,
  Huawei, Xiaomi, Fitbit, Garmin, Sonos, Bose, Tile, Espressif,
  Raspberry Pi Foundation, Nordic Semiconductor, TI, Broadcom, Intel,
  TP-Link, NETGEAR, D-Link, Ubiquiti, Belkin, Logitech, Philips/Signify,
  Ring, Nest, Roku, ASUS, Dell, HP, Lenovo, Anker, GoPro, DJI, Withings,
  Polar, Jabra, Plantronics/Poly, Sennheiser, and a few more). **This is
  explicitly NOT the full IEEE OUI registry** (which is several
  megabytes and genuinely not appropriate to embed in a plugin file) -
  it's a best-effort, hand-assembled subset from commonly-referenced
  OUI listings, not verified line-by-line against the live IEEE
  registry in this sandbox (no practical way to do that offline here).
  Treat any `vendor` value as informative, not authoritative - an
  unmatched prefix falls back to `"Unknown"` rather than guessing.
  Extend or correct it via `oui_extra_path`, a JSON file of
  `{"AA:BB:CC": "Vendor Name"}` entries that's merged on top of (and
  takes precedence over, on a collision) the built-in table.
- **Tracker flagging.** `match_tracker(company_id, payload,
  service_uuids)` in `bluetooth_recon_ng.py` is a small, independently
  unit-testable function (decoupled from bettercap's own event
  parsing - see the "still needs real-hardware testing" note below) that
  checks:
  - **Apple Find My** (AirTag + other Find My network accessories):
    BLE manufacturer company ID `0x004C` (Apple), with the
    manufacturer-specific payload's first two bytes being `0x12`
    (Find My advertisement type) and `0x19` (length, 25 decimal) -
    this specific `(type=0x12, len=0x19)` pair is the commonly-cited
    signature for a Find My "separated/offline" advertisement in
    public reverse-engineering write-ups of the protocol (Apple has
    not published this format officially).
  - **Tile**: BLE manufacturer company ID `0x0136`, the Bluetooth SIG
    company identifier publicly assigned to Tile, Inc. Tile has not
    published a more specific payload-level signature, so this matches
    on company ID alone - meaning it will flag ANY BLE advertisement
    carrying Tile's company ID, which in practice should only be Tile
    products, but is a broader net than the Apple/Samsung checks.
  - **Samsung SmartTag/SmartTag+**: either the advertised service UUID
    containing `fd5a` (a service UUID publicly associated with Samsung
    SmartThings/SmartTag advertisements in community write-ups), OR
    BLE manufacturer company ID `0x0075` (Samsung Electronics'
    assigned company ID) with the manufacturer payload's first byte
    equal to `0x01`.
  - **Honest caveat, stated plainly (not overclaiming):** none of
    these three vendors officially document their tracker
    advertisement formats. These signatures come from public
    reverse-engineering discussion, can change with a firmware update
    on the vendor's side at any time without notice, and have NOT been
    verified against a real AirTag, Tile, or SmartTag device in this
    project (no such hardware is available here) - only against
    hand-constructed byte sequences in the test suite that assume the
    above description is accurate. Treat a match as a strong hint
    worth a closer look, never as certain identification.
- **WiFi<->Bluetooth correlation**, all three sources optional and
  independently best-effort (try/except + a debug log line if
  missing, never fatal, never required config):
  1. `crack_house_ng.py`'s `saving_path` output
     (`crack_house_potfile_path`, default
     `/root/handshakes/crack_house_ng.potfile`) - a plain-text
     `hostname:password` file for networks that have actually been
     cracked. Read as a set of lowercased hostnames for the
     `any_cracked` check.
  2. `timer_ng.py`'s `output_path` CSV
     (`timer_csv_path`, default `/etc/pwnagotchi/timer_ng.csv`) -
     `timestamp,network,time_to_deauth,time_to_handshake,
     time_between_deauth_and_handshake` rows. Read for
     `(timestamp, network)` pairs to find WiFi activity within
     `correlation_window_minutes` (default 10) of a Bluetooth
     sighting.
  3. `gps_tagger_ng.py`'s `pn_output_path` directory
     (`gps_tagger_dir_path`, default `/etc/pwnagotchi/gps_tagger_ng`) -
     one `pn_ap_<hostname>_<mac>.json` file per AP, each holding
     `{"ap": {...,"hostname":...}, "gps": {"Latitude":..,"Longitude":..},
     ...}` (that exact schema, confirmed by reading
     `gps_tagger_ng.py`'s own `aps_update`/`_write_gps_sidecar`
     source). Read to attach a `{"lat":..,"lon":..}` location for a
     correlated network, when one exists.

  For every recorded/updated device sighting, `_correlate()` looks up
  WiFi networks active within the configured window of that sighting's
  timestamp, sets `correlated_networks` (list of hostnames) and
  `any_cracked` (bool, true if any of those hostnames appear in
  crack_house_ng's cracked list), and `correlated_location`
  (`{lat, lon}` or `None`, from the first correlated network that has
  one via gps_tagger_ng). This is explicitly a nice-to-have
  cross-reference, never a hard dependency - a fresh install with none
  of the three other plugins configured works identically, just with
  those three fields always empty/`None`/`False`.
- **Webhook status page + export.** `/plugins/bluetooth_recon_ng/`
  renders the full device table (sorted by `last_seen` descending) with
  clearly-labeled `BLE: N` / `Classic: N` counts (not two bare
  numbers). `/plugins/bluetooth_recon_ng/export` returns the persisted
  device table as a downloadable JSON file via Flask's `Response` with
  a `Content-Disposition: attachment` header. This route takes **no**
  path/filename from the request at all - it always serves exactly the
  one file this plugin itself persists to `device_table_path`. This
  project's own earlier research into `pwndroid.py`
  (`jayofelony/pwnagotchi-torch-plugins`) found a real path-traversal
  bug in its download handler from accepting a user-supplied path
  without confining it to a safe directory; that's cited here only as
  the reason this export route was deliberately designed to accept no
  request-supplied path at all, not as something this plugin needed to
  fix (it's a different, unrelated plugin, untouched by this work).
- **On-screen display.** Two elements, `BLE` and `BT`, each
  independently positionable (`ble_position_x`/`ble_position_y`/
  `classic_position_x`/`classic_position_y`, each defaulting to `None`
  meaning "use the built-in default position"), following the same
  `_default_position()`-helper / `None`-means-auto pattern established
  by `crack_house_ng.py`'s `position_x`/`position_y`/
  `stats_position_x`/`stats_position_y`.

## Testing

35 tests in `tests/test_bluetooth_recon_ng.py`, all passing against the
real cloned `jayofelony/pwnagotchi` framework and the real `flask`
library. Covers:
- Real plugin registration as a `pwnagotchi.plugins.Plugin` subclass.
- `_opt()` returning the real default (not crashing) when a key is
  entirely absent from `self.options` - the bug-fix at the center of
  bluetoothsniffer.py's rebuild.
- Both on-screen elements are created under their real, distinct keys
  (`bluetooth_recon_ng_ble` / `bluetooth_recon_ng_bt`) and
  `on_ui_update` updates both by those same keys, live from the device
  table - the bug-fix at the center of blemon_plugin.py's key-mismatch
  rebuild.
- None of blemon_plugin.py's dead AI-training/`on_free_channel` hooks
  exist on `BluetoothReconNG` at all.
- `on_unload` never raises even when an element was never added (the
  `remove_element`-has-no-guard framework fact), and correctly stops
  `ble.recon` only when it was the one that started it.
- OUI lookup: a matched prefix, an unmatched prefix falling back to
  "Unknown", and `oui_extra_path` overriding the built-in table on a
  collision.
- `match_tracker()` against hand-constructed known-good byte sequences
  for all three signatures (Apple Find My type+length bytes, Tile
  company ID, Samsung service-UUID and company-ID+type-byte variants),
  plus confirming an ordinary non-tracker BLE advertisement is NOT
  flagged.
- Retention/expiry pruning: an old device is removed, a recent one is
  kept, and `retention_hours = 0` disables pruning entirely.
- RSSI filtering: a weak-signal sighting is dropped when
  `rssi_threshold` is set and the event's RSSI is below it; a sighting
  with no RSSI available is never filtered even when a threshold is
  configured.
- `known_devices` flags a matching MAC as `is_known` without affecting
  any other device's data.
- Correlation logic against constructed fixture files matching the
  real formats of `crack_house_ng.py`, `timer_ng.py`, and
  `gps_tagger_ng.py`: a sighting inside the correlation window picks up
  the right network(s) and cracked/location flags, a sighting outside
  the window doesn't, and correlation degrades to empty/`False`/`None`
  with no crash when none of the three source files/dirs exist at all.
- Webhook page rendering (device rows present, labeled `BLE:`/`BT:`
  counts, no crash on an empty table) and the export route returning
  the full persisted device table as a real Flask `Response` with the
  expected `Content-Disposition` header.

Run with (from this directory):
```
python3 -m pytest tests/test_bluetooth_recon_ng.py -v
```

## Still open / needs real-hardware testing

- **BLE recon event data shape.** This sandbox has no copy of
  bettercap's own Go source to confirm the exact JSON field names a
  real `ble.recon` device event uses for manufacturer-specific
  advertisement data (`_extract_manufacturer_info` guesses a couple of
  plausible key names and degrades gracefully otherwise) - needs
  checking against a live bettercap session's event log on real
  hardware.
- **`hcitool scan` output format** on the actual BlueZ version shipped
  on real hardware, vs. the documented `MAC\tName` format this rebuild
  parses against.
- **Tracker-flagging signatures**, as described above - no real
  AirTag/Tile/SmartTag was available to test against; only
  hand-constructed sample bytes.
- **OUI table accuracy.** Individual prefix->vendor entries were
  assembled from general knowledge/commonly-referenced listings, not
  cross-checked entry-by-entry against a live IEEE OUI registry lookup
  in this sandbox (no practical offline way to do that here) - treat
  as a best-effort convenience, not a certified-accurate source, and
  correct/extend via `oui_extra_path` as needed.

## Original configs preserved

Real original config files for both merged-in plugins were found (exact
match) at `itsdarklikehell/pwnagotchi-plugins/configs/blemon_plugin.toml`
and `itsdarklikehell/pwnagotchi-plugins/configs/bluetoothsniffer.toml`.
Preserved verbatim in this suite's own folder as
`blemon_plugin.config.original.toml` and
`bluetoothsniffer.config.original.toml`, per the project's standing
config-preservation requirement.
