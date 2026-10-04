# TimerNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done.

An extension of `timer.py` (itsdarklikehell/idoloninmachina) - the
original was already clean and genuinely useful (measures time-to-deauth
and time-to-handshake), kept as-is during Cluster 34's review. This
rebuild adds the four improvements discussed for it and fixes one small
real bug found while building them.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Requirements & dependencies

- Python: none beyond the standard library (`csv`, `datetime`). The
  original depended on `pandas` just to write a 3-column CSV - dropped
  entirely here in favor of the stdlib `csv` module, which also removes
  a real dependency-declaration bug (see below).
- No config values are required to get something useful - every
  default reproduces the original's timing/logging behavior, just at a
  different (configurable) output path.

## What's fixed vs. the original

1. **Hardcoded, possibly-wrong output path.** The original always
   wrote to `/home/pi/data/pwnagotchi_times.csv` - a path that doesn't
   exist on every setup (different user, different data layout).
   `output_path` is now a config option, defaulting to
   `/etc/pwnagotchi/timer_ng.csv`.
2. **Missing bare-MAC-string handling.** `on_handshake` can be called
   with a plain MAC string instead of a full AP dict
   (`pwnagotchi/agent.py`'s "couldn't match the session" branch,
   confirmed the same way this was found and fixed in `WifiJtest`
   earlier in this audit) - the original would have crashed trying to
   read a network name off a bare string. Fixed with the same
   `_as_ap_dict`-style normalizer.
3. **A dependency-declaration bug.** The original's `__dependencies__`
   listed `scapy` (a pip package it never actually imports anywhere in
   the file) but didn't declare `pandas` (which it does import and use
   to write the CSV) - so the one real dependency it needed was
   undocumented, and the one it declared was fictional. Moot now that
   `pandas` is dropped entirely in favor of the stdlib `csv` module.
4. **Unbounded, full-file rewrite on every handshake.** The original
   loaded its entire history into a pandas DataFrame and rewrote the
   whole file from scratch on every single handshake, with no size
   cap - see `max_rows` below.

## What's added

- **`output_path`** (configurable) - see fix #1.
- **`max_rows`** (default 5000) - CSV rotation. Once the log exceeds
  this many rows, the oldest entries are dropped so the file doesn't
  grow forever on a long-running device. Set to `0` to reproduce the
  original's unbounded behavior exactly.
- **Per-network best/worst tracking** - the original logged a flat,
  unlabeled sequence of three numbers per capture with no way to tell
  which network any given row was for. Every row now also records the
  network name (SSID, or the AP's MAC if hidden/unavailable), and the
  plugin keeps a running best/worst time-to-handshake per network in
  memory, viewable on the webhook page.
- **Optional on-screen element** (`show_on_screen`, default `false`) -
  the original had no visual output beyond the CSV. When enabled,
  shows either the last capture's time-to-handshake or a rolling
  average over `ui_average_window` captures (`ui_metric`).
- **A real webhook page** - the original's `on_webhook` just logged
  that it was pressed and returned nothing. Now renders a small table
  of every network seen, its capture count, and its best/worst
  time-to-handshake.

## Install

1. Copy `timer_ng.py` into your custom plugins folder (`custom_plugins`
   in `config.toml`, typically `/etc/pwnagotchi/custom-plugins/`).
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
   Every default works out of the box.
3. If you were running the original `timer.py`, disable/remove it
   first - both would otherwise log the same events twice.
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```
5. Visit `http://pwnagotchi.local:8080/plugins/timer_ng/` any time to
   see the per-network summary table.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| CSV file never appears | Every capture so far has been passive (no `on_deauthentication` fired before the handshake) - that's by design, matching the original: passive captures aren't timed |
| Network shows as a MAC address instead of a name | The AP's SSID was hidden (`<hidden>`) or unavailable at capture time - falls back to the MAC, same as the original's underlying data would have implied |
| On-screen element never shows | Check `show_on_screen = true` is actually set - it's off by default |

## Still open / needs real-hardware testing

- Real-world CSV growth rate and whether `max_rows = 5000`'s default
  is a sensible cap for actual usage patterns hasn't been measured
  outside a sandbox.
- Visual placement of the optional on-screen element on the real 3.5"
  TFT screen needs a look once installed.
