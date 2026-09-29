# BluetoothReconNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done (see "Still open" below).

A single merged rebuild of two originals:

- `blemon_plugin.py` (itsdarklikehell, edited from evilsocket's example
  plugin) - BLE device tracking via bettercap's `ble.recon` module.
- `bluetoothsniffer.py` (itsdarklikehell/diytechtinker, fixed by
  Jayofelony) - classic Bluetooth (BR/EDR) scanning via `hcitool`.

Both had real, source-verified bugs (see NOTES.md for the full list)
and enough overlap in purpose that merging them into one plugin, one
config block, one persisted device table, and one webhook page was the
approved design - not two separate plugins duplicating "what Bluetooth
devices are nearby" with two different data models.

## Requirements & dependencies

- **bettercap**, already running with its BLE module, for the BLE half
  (`ble.recon`). This ships with the jayofelony image already.
- **bluez** / **bluez-tools** (`hcitool`) on the OS, for the classic
  half. Also already part of the jayofelony image.
- Python: nothing beyond the standard library (`flask` is only used via
  its already-bundled `Response` class for the JSON export route).

No extra `pip` packages are required. (The original `blemon_plugin.py`
declared a `pip: ["scapy"]` dependency it never actually used anywhere
in the file - see NOTES.md, fix #4.)

## Install

1. Copy `bluetooth_recon_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
   Every option has a working default - there's nothing you're
   required to fill in.
3. If you were running the original `blemon.py` and/or
   `bluetoothsniffer.py`, disable/remove both first - this plugin
   replaces them entirely, including their on-screen elements and any
   `devices_file`/similar output they used to write (their old output
   files are left alone/untouched, just no longer updated).
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## What you get

- Two on-screen elements, clearly labeled `BLE` and `BT`, each showing
  a live count from the unified device table. Positions are
  independently configurable (see config.toml).
- A webhook page at `/plugins/bluetooth_recon_ng/` showing every known
  device (MAC, radio type, name, vendor, tracker flag, known flag,
  first/last seen, RSSI, and any WiFi correlation), sorted by most
  recently seen.
- A JSON export at `/plugins/bluetooth_recon_ng/export` - a direct
  download of the full persisted device table. It always serves
  exactly that one file; it does not accept any path/filename from the
  request (see NOTES.md for why that specifically matters here).
- Persistence across reboots (`device_table_path`), with configurable
  retention/expiry pruning of old devices (`retention_hours`).
- Best-effort BLE tracker flagging for Apple Find My (AirTag and other
  Find My accessories), Tile, and Samsung SmartTag/SmartTag+ devices.
  See NOTES.md for exactly what's matched and its honest caveats - this
  is reverse-engineered pattern matching, not an official API, and
  hasn't been checked against a real tracker.
- A curated (not exhaustive) built-in OUI vendor lookup, extensible via
  `oui_extra_path`.
- Optional, best-effort correlation against three other `plugins-wip`
  plugins' own output files (`crack_house_ng.py`, `timer_ng.py`,
  `gps_tagger_ng.py`) - none of these are required; each is skipped
  cleanly if its file/directory isn't present.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `BT` count never moves | `hcitool` isn't installed, or classic scanning is failing - check the pwnagotchi log for `[BluetoothReconNG] hcitool not found` or `hcitool scan exited non-zero` |
| `BLE` count never moves | bettercap's `ble` module may not be available/enabled, or `ble.recon` failed to start - check the log around `starting ble.recon` |
| Webhook page is empty | Nothing's been seen yet, or every option scan is being filtered out by too aggressive an `rssi_threshold` |
| A device you'd expect to see is missing | Check `rssi_threshold` isn't filtering it out, and that `retention_hours` hasn't pruned it if it's genuinely been that long since it was last seen |
| `correlated_networks` is always empty | One or more of `crack_house_potfile_path`/`timer_csv_path`/`gps_tagger_dir_path` doesn't point at a real file/dir on your setup, or those plugins aren't installed/enabled - this is expected and non-fatal, it's a best-effort extra, not a requirement |

## Still open / needs real-hardware testing

- **BLE recon event data.** This sandbox doesn't have bettercap's own
  Go source available to confirm the exact JSON shape of a
  `ble.recon` device event's manufacturer-specific advertisement data.
  `_extract_manufacturer_info` makes a best-effort attempt at a couple
  of plausible key names and degrades gracefully if none match - the
  actual field names need verifying against a real bettercap session's
  event log on real hardware.
- **`hcitool scan` output parsing.** Verified against `hcitool`'s
  documented `MAC\tName` output format, not against a live scan's real
  output on this specific hardware/BlueZ version.
- **Tracker-flagging signatures specifically.** The AirTag/Tile/
  SmartTag byte patterns in `match_tracker()` are unit-tested against
  known-good sample byte sequences constructed from publicly-discussed
  reverse-engineering write-ups, but this project has no real AirTag,
  Tile, or SmartTag to scan and confirm against. See NOTES.md for the
  exact bytes used and the honest caveat on how reliable this can be
  expected to be.
