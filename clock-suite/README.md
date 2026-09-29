# ClockNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done.

An extended rebuild of `clock.py` (LoganMD, redone by NeonLightning) -
shows the current date and time as two small UI elements.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Requirements & dependencies

- Python: none beyond the standard library.

## What's changed vs. the original

No bugs were found in the original - this is a pure feature rebuild,
not a bugfix one.

1. **Configurable positions.** Both the date and time elements'
   screen positions were hardcoded ((100, 0) and (100, 95)). Now
   configurable via `date_position_x`/`date_position_y` and
   `time_position_x`/`time_position_y`, defaulting to the original's
   own values so nothing changes for the common case.
2. **Configurable date/time format.** The original hardcoded
   `"%m/%d/%y"` for the date and `"%I:%M%p"` (12-hour) for the time,
   with no way to change either without editing the plugin's source -
   notably, no way to get a 24-hour clock. Both are now `strftime`
   format strings set via `date_format`/`time_format`. An invalid
   format string falls back to the original default instead of
   crashing the render loop.
3. **Defensive `on_unload`.** The original's `ui.remove_element(...)`
   calls had no error handling; if `on_ui_setup` somehow hadn't run
   yet, unload would raise. Now wrapped the same defensive way as the
   rest of this audit's rebuilds.

## What's kept

- The two-element (date + time) design and both original default
  positions/formats, unchanged unless you configure otherwise.

## Install

1. Copy `clock_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
3. If you were running the original `clock.py`, disable/remove it
   first.
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Time/date look wrong | Check the Pi's own system time/timezone (`date` at a shell) - this plugin just displays `datetime.datetime.now()`, it doesn't set the clock |
| Format string not doing what you expect | Confirm it's a valid Python `strftime` directive - an invalid one silently falls back to the default rather than showing anything broken |

## Still open / needs real-hardware testing

- Real on-screen position for the 3.5" TFT hasn't been checked yet -
  the defaults are inherited from the original's own values, not
  verified against this specific display.
