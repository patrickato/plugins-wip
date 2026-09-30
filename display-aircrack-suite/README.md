# DisplayAircrackNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done.

An extended rebuild of `display-aircrack.py` (itsdarklikehell/
7h30th3r0n3) - shows a small UI element indicating whether
`aircrack-ng` is currently running.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Requirements & dependencies

- Python: none beyond the standard library. The original also
  declared an unused `scapy` pip dependency (never imported anywhere
  in the file) - dropped here, the same stray-dependency pattern
  found repeatedly elsewhere in this audit.
- System: `aircrack-ng` itself, if you actually want this element to
  ever show "running" (same as the original).

## What's changed vs. the original

No crash bugs were found in the original - this is a pure
efficiency/feature rebuild, not a bugfix one.

1. **Throttled process check.** The original shelled out to `ps -A`
   on every single call to `on_ui_update` - i.e. every UI render
   tick, typically several times a second - just to check one boolean
   status that doesn't need to be that fresh. Now only actually
   re-checks every `check_interval` seconds (default 3); the
   displayed value still updates every tick from the last known
   state, so the UI itself doesn't feel any less responsive.
2. **Configurable position**, replacing the hardcoded
   `(ui.width() // 2 - 10, 0)` (still the default).
3. **Configurable status text.** The original showed a bare
   `"(1)"`/`"(0)"`. Now `running_text`/`stopped_text`, defaulting to
   the clearer `"AC:ON"`/`"AC:OFF"`.
4. **Dropped the unused `scapy` dependency declaration.**
5. **`on_webhook` returns a real page.** The original returned `None`,
   which makes Flask raise a 500 on the bare plugin index
   (`GET /plugins/display_aircrack_ng/`); it now serves a small status
   page showing the current running/stopped state.

## What's kept

- The core idea: a substring check for `"aircrack-ng"` in `ps -A`'s
  output. This is inherently a bit fragile (it would also match, say,
  a process whose own command line happens to contain that string),
  but that's unchanged from the original and wasn't part of what was
  approved to fix here.

## Install

1. Copy `display_aircrack_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
3. If you were running the original `display-aircrack.py`, disable/
   remove it first.
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Always shows "stopped" even when aircrack-ng is running | Confirm `aircrack-ng` is actually installed and its process name in `ps -A` genuinely contains the substring "aircrack-ng" |
| Status feels laggy after starting/stopping aircrack-ng | Expected - it only re-checks every `check_interval` seconds now (default 3), not on every render tick; lower `check_interval` if you want fresher status at the cost of more frequent `ps` calls |

## Still open / needs real-hardware testing

- Real on-screen position for the 3.5" TFT hasn't been checked yet.
