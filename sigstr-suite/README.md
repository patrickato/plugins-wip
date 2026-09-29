# SigStrNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done (see "Still open" below).

A bugfix + feature-upgrade rebuild of `sigstr.py` (bryzz42o,
`Pwnagotchi-fsociety-plugins`, v1.0.6). The original reads the current
WiFi signal strength (RSSI) for whatever AP pwnagotchi is associated
with/targeting, and renders it on-screen as a text bar so you can see
connection quality at a glance during a session.

The original had five real, source-verified bugs (see NOTES.md for the
full list, including a background timer thread that threw an uncaught
`AttributeError` every 2 seconds, forever, because it called a
framework function - `pwnagotchi.plugins.notify()` - that doesn't
exist). This rebuild fixes all five and adds four approved
improvements on top.

## Requirements & dependencies

- **`iw`** (the CLI tool), already part of the base OS image this
  project targets - same as `bluetooth_recon_ng.py`'s already-present
  `bluez` dependency. Nothing extra to install.
- Python: nothing beyond the standard library. No `pip` packages
  required at all - the original declared none either, and correctly
  so (unlike some other plugins in this audit's originals that
  declared a dependency they never actually used).

## Install

1. Copy `sigstr_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
   Every option has a working default - there's nothing you're
   required to fill in. It ships `enabled = false`, matching this
   repo's convention of shipping every suite disabled-by-default.
3. If you were running the original `sigstr.py`, disable/remove it
   first (`enabled = false` under its own `[main.plugins.sigstr]`
   section, or delete the file) - this plugin replaces it entirely,
   including its on-screen element.
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## What you get on screen

A single on-screen element (default position `(0, 205)`, matching the
original's hardcoded position, but now fully configurable including
the negative-x-from-right-edge convention) showing:

```
STR |████████░░| ▃▄▅▇█▇▆▅▄▃▂▁
```

- A short strong/medium/weak tag (`STR`/`MED`/`WEAK`), configurable
  thresholds, so you don't have to mentally convert a raw dBm number.
- The signal bar itself - fixed vs. the original, which had the fill
  and empty characters backwards (`█` now means filled/strong, `░`
  now means empty/weak - see NOTES.md, bug #5).
- An optional compact trend sparkline of the last several readings, so
  you can see at a glance whether signal is getting stronger or
  weaker, not just its instantaneous value.

## Webhook status page

`/plugins/sigstr_ng/` (plain HTML, everything interpolated into it is
`html.escape()`-d, including the interface name since it comes from
external `iw dev` command output) shows:

- The current reading (dBm + percent) and its strong/medium/weak tag.
- The full retained sparkline history (not just the on-screen-length
  slice) as both a compact sparkline string and a point count.
- The configured thresholds.
- Which interface is configured vs. which one is actually in use (see
  "Interface auto-fallback" below) and whether that was auto-detected.
- The handshake-capture correlation table, when `correlate_handshakes`
  is enabled and data is available (see below).

## Interface auto-fallback

The original hardcoded `"wlan0"` with no fallback - on a build where
the WiFi adapter enumerates differently, the plugin would just never
read a signal at all. This rebuild lists available wireless interfaces
via `iw dev` and, if the configured `interface` isn't among them, falls
back to the first one it finds and logs a clear warning naming which
interface it picked instead.

## Handshake-capture correlation (optional, off by default)

Set `correlate_handshakes = true` to have the webhook status page show
a table pairing each handshake-capture timestamp from `timer-suite`'s
CSV (`timer_csv_path`, default `/etc/pwnagotchi/timer_ng.csv`, matching
`timer-suite`'s own `output_path` default) with the closest RSSI
reading from this plugin's own history, within
`correlation_window_minutes` of that capture - so you can see "how
strong was my signal when I actually got a capture." This is entirely
optional and best-effort: if `timer-suite` isn't installed/configured,
or the CSV doesn't exist yet, the page just says so and everything
else keeps working normally.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| On-screen element always shows `n/a` | No reading has succeeded yet - check the pwnagotchi log for `[SigStrNG] couldn't read signal for ...`; most likely pwnagotchi isn't currently associated with any AP, or `interface` (after auto-fallback) doesn't match a real wireless interface. |
| Webhook page shows the wrong interface under "in use" | Check the "Interface" line on the status page - it shows both `configured` and `in use`; if they differ, your configured `interface` wasn't found by `iw dev` and the fallback kicked in (a warning is also logged). |
| `Handshake correlation` always says "no correlation data yet" | Either `correlate_handshakes` is still `false` (check the page - it says so explicitly when disabled), or `timer_csv_path` doesn't point at a real file on your setup, or no reading happened to fall within `correlation_window_minutes` of a logged capture - all expected/non-fatal. |
| Bar/sparkline look "inverted" from what you remember of the original | That's the fix, not a regression - see NOTES.md bug #5. `█` (filled) now means strong signal, `░` (empty) now means weak. |

## Still open / needs real-hardware testing

- **`iw dev ... link` output parsing.** Verified against the
  documented `signal: <N> dBm` line format and a hand-constructed
  sample of real-looking output, not against a live `iw` invocation on
  this specific hardware/driver/`iw` version.
- **`iw dev` interface-listing output parsing**, same caveat - verified
  against the documented `Interface <name>` line format, not a live
  capture from real hardware.
- **Handshake-correlation timestamp interpretation.** `timer-suite`
  writes tz-naive local-time ISO timestamps
  (`datetime.datetime.now().isoformat()`); this plugin's own history
  timestamps come from `time.time()` (a real UTC epoch). Pairing them
  relies on Python's naive-`datetime.timestamp()` behavior (interprets
  a naive datetime as local time, matching how it was generated) being
  consistent with the system's timezone not changing between the two
  events - the same assumption `MadHatterNG.py`'s own drain-rate
  correlation against the same CSV already makes, not something new to
  this rebuild.
