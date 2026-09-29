# MadHatterNG

Universal UPS / battery-monitor plugin for pwnagotchi. A **feature-upgrade
rebuild** of `mad_hatter.py` (AlienMajik, v2.3.0) - the original has no
known bugs, so nothing about its chip backends, calibration math,
auto-detection, or on-screen positioning was "fixed"; it was carried
over unchanged. Four new features were added on top. See NOTES.md for
the full design writeup.

**Confirmed target hardware for this install: a Waveshare UPS 3S HAT**
(INA219-based). `config.toml` ships with `ups_type = "waveshare"` set
explicitly for that reason - see "Requirements & dependencies" below.

## What it supports

- **MAX17040/17048** fuel gauges (Geekworm X1200, UPS-Lite, and similar).
- **All INA219-based UPS HATs** - Waveshare, Seengreat, SB Components,
  EP-0136, and any other generic INA219 board (`ups_type = "ina219"`).
- **PiSugar 2 / 2 Pro / 3** (separate, correct register maps for each -
  they are NOT interchangeable despite two of them sharing an I2C
  address).
- Auto-detection (`ups_type = "auto"`, the framework default) via I2C
  scanning, or force a specific `ups_type` for a faster/more reliable
  startup.

## New in this rebuild

1. **Threshold notifications.** Optionally fires a push notification
   (through `apprise-notify-suite` or `discord-suite`, whichever is
   loaded/configured) when the battery crosses warning -> critical ->
   shutdown-imminent, once per crossing - not spammed every poll.
   Off by default (`notify_on_threshold = false`).
2. **Trend history + graph.** A bounded, persisted log of voltage/SoC/
   current samples, viewable at `/plugins/mad_hatterNG/history` (an
   HTML page with a dependency-free inline-SVG sparkline and a table)
   or as raw JSON at `/plugins/mad_hatterNG/history.json`.
3. **Drain-rate / activity correlation.** Best-effort correlation
   against `timer-suite`'s per-handshake CSV, splitting the recent
   drain rate into "active epochs" vs "idle" and showing both
   alongside the existing flat mAh/current time estimate on the
   `/history` page.
4. **Config sanity check.** `/plugins/mad_hatterNG/sanity` cross-checks
   your configured options against what was actually auto-detected on
   the I2C bus (mismatched `battery_cells`, a zero/negative
   `shunt_ohms`, a `charging_gpio` that's also in `reserved_gpios`,
   etc.) - a quick way to answer "why doesn't my SoC estimate look
   right" without reading the source.

Everything else - backends, calibration, auto-detection, positioning,
the `/status`/`/json`/`/diagnose`/`/report` diagnostic webhook, the
background polling thread - is the same design as `mad_hatter.py`.

## Requirements & dependencies

- **smbus2** (preferred) or **smbus** - `sudo apt install python3-smbus2`.
  Without either, the plugin logs a clear error and simply never
  initializes a UPS (the rest of pwnagotchi keeps running).
- **RPi.GPIO** or **rpi-lgpio** (Pi 5) - optional, only needed if you
  set `charging_gpio` to a real pin for hardware charge detection.
  Missing GPIO support just means charge detection falls back to
  current sensing or the voltage trend.
- Nothing beyond the standard library for the four new features
  (history/notifications/correlation/sanity) - no extra `pip` package
  required. Notifications are a soft dependency on
  `apprise-notify-suite`/`discord-suite` being separately installed
  and enabled; if neither is, `notify_on_threshold` just logs and
  no-ops.
- **Confirmed hardware for this install:** Waveshare UPS 3S HAT
  (INA219-based, `ups_type = "waveshare"`). This is the one plugin in
  this audit that matches hardware actually in hand - see NOTES.md for
  why a real-hardware pass on this one specifically is worth doing.

## Install

1. Copy `mad_hatterNG.py` into your custom plugins folder. **The
   filename must stay exactly `mad_hatterNG.py`** (capital NG, no
   underscore) - see "A naming gotcha" below for why this matters.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
   **The section header must be exactly `[main.plugins.mad_hatterNG]`**
   - again, see "A naming gotcha".
3. If you were running the original `mad_hatter.py`, remove or disable
   it first (`enabled = false` under its own `[main.plugins.mad_hatter]`
   section, or delete the file) - the two plugins would otherwise both
   try to talk to the same I2C chip and both draw a UI element.
   `mad_hatterNG.py` automatically migrates cycle-count/draw-estimate
   history from the original's state file on first load, so you don't
   lose that.
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

### A naming gotcha (read this before you rename anything)

The plugin file is named `mad_hatterNG.py` per an explicit request -
capital NG, no underscore, unlike every other suite in this repo. This
fork's plugin loader registers and looks up a plugin (both whether it's
"enabled" at all, and its options table) by the plugin **file's exact
basename**, case included - NOT by any name written inside the Python
class. That means the config section has to be
`[main.plugins.mad_hatterNG]`, matching the file, not the more
conventional-looking `mad_hatter_ng`. If the section name and the file
name don't match exactly, the plugin silently never loads at all - no
error, it just never appears as enabled. See NOTES.md for the full,
verified explanation (and what to change if you'd rather rename the
file to `mad_hatter_ng.py` and use a snake_case section instead - both
work, as long as they match each other).

## What you get on screen

The same single on-screen element as the original: SoC%, an optional
voltage prefix, an optional time-remaining estimate, and (in
`debug_mode`) cycle count / current / error count. Position is fully
configurable via `ui_position_x`/`ui_position_y`, including the
original's negative-x-means-"from the right edge" convention.

## Webhook pages

All served under `/plugins/mad_hatterNG/`:

| Path | What it shows |
|---|---|
| *(index)* | The original interactive diagnostic page (live status + on-demand watch-and-sample diagnostic). |
| `status`, `json` | Current reading as JSON. |
| `diagnose`, `report` | Start/poll the background diagnostic. |
| `history` | **New.** Trend graph (inline SVG) + table + active/idle drain-rate correlation. |
| `history.json` | **New.** Raw history points as JSON. |
| `sanity` | **New.** Config-vs-detected-hardware sanity report. |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Plugin never appears as loaded at all | Check the config section is spelled exactly `[main.plugins.mad_hatterNG]` (capital NG) - see "A naming gotcha" above. |
| `NO UPS` on screen | No supported chip responded on the I2C bus - check wiring/`i2c_bus`, and check `dmesg`/`i2cdetect -y 1`. The plugin retries every 60s. |
| SoC% jumps ~15% right when you plug in a charger | This is the exact bug the IR-compensation/smoothing in `SocEstimator` exists to prevent - if you still see it, check `internal_resistance` is set reasonably for your pack (default 0.12 ohm/cell). |
| Current always reads ~0 on an INA219 board | Check `shunt_ohms` matches your board's real shunt resistor (0.1 for the Waveshare UPS 3S HAT) - the `/sanity` page flags an obviously-wrong (<=0) value, but not a plausible-but-wrong one. |
| Current sign is backwards (charging shows as draining or vice versa) | Set `invert_current = true`. |
| `notify_on_threshold` is on but nothing ever arrives | Check `/plugins/mad_hatterNG/status` and the pwnagotchi log for `no notification backend available/loaded` - it means neither `apprise-notify-suite` nor `discord-suite` is loaded/configured, or `notify_backend` is pinned to one that isn't. |
| `/history` always says "n/a" for active/idle drain rate | `timer_csv_path` doesn't point at a real file (default `/etc/pwnagotchi/timer_ng.csv`, matching `timer-suite`'s own default) - this is optional/best-effort, everything else on the page still works. |
| Charging GPIO refused at boot with a "belongs to..." log line | That pin is already claimed by the display or another onboard function - pick a different `charging_gpio`, or leave it at `-1` (the default) to use current/voltage-based detection instead. |

## Still open / needs real-hardware testing

See NOTES.md's "Still needs real-hardware testing" section - this is
the one plugin in this whole audit confirmed to target hardware
actually owned (a Waveshare UPS 3S HAT), so a real-hardware pass here
specifically is worth prioritizing.
