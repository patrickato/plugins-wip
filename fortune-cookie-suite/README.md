# FortuneCookieNG

Displays a rotating fortune cookie message on screen. Rebuilt from
`fortune_cookie.py` (`FortuneCookiePlugin`).

## What was actually broken

- There was no `__defaults__` dict at all. `self.options['orientation']`
  (in `on_ui_setup`) and `self.options['enabled']` (in `on_ui_update`)
  were both accessed with direct indexing and no fallback - a brand-new
  install with a bare `config.toml` (the default state before anyone
  sets these) raised a `KeyError` on load and again on every UI update.
- The `orientation` option had zero actual effect: the `if
  self.options['orientation'] == "vertical": ... else: ...` branch in
  `on_ui_setup` built byte-for-byte identical `LabeledValue` elements in
  both branches. Setting `orientation` to anything did nothing visible.
- The fortune list itself was hardcoded to 4 entries inline in
  `on_ui_update`, picked once and then only re-picked if `on_ui_update`
  happened to run again with new logic - in practice the original had no
  rotation timer, so the message was effectively static for long
  stretches.

## What this rebuild adds

- Real defaults for `orientation`, `enabled`, and everything else via a
  `DEFAULTS` dict + `.get()`, so a fresh install never `KeyError`s.
- `orientation` now genuinely does something: `"vertical"` shows a
  `Fortune:` label above the message; the default `"horizontal"` shows
  just the message with no separate label, on one line.
- **Timed rotation** - `rotate_interval_seconds` (default 60) controls
  how often the displayed fortune changes while the daemon runs, driven
  from `on_ui_update` rather than being picked once and left static.
- **Configurable fortune list** - `fortunes` in `config.toml`, with a
  20-entry default set (up from the original's 4).
- **Optional external source** - `fortune_command` (default empty/
  disabled). If set, the plugin shells out to that command (e.g. the
  real Unix `fortune` binary) for a fresh fortune instead of using the
  configured list. Guarded with a 5-second timeout and full
  `subprocess`/`OSError` error handling; any failure (missing binary,
  timeout, non-zero exit, empty output) falls back to the configured
  list silently rather than crashing anything.

## Configuration

See `config.toml`. Leave `fortune_command` empty to just use the
`fortunes` list (the default). Set it to `"fortune"` (or any shell
command that prints a line of text) to pull from the real `fortune`
Unix utility instead, when installed.

## Still open

- No real-device test against an actual e-paper display or a real
  installed `fortune` binary - the sandbox test suite exercises the
  plugin's logic (defaults, orientation behavior, rotation timing, list
  vs. command selection, and command-failure fallback) directly against
  the real cloned framework, with `subprocess.run` mocked for the
  command-based path.
