# Notes: FortuneCookieNG

## Bugs found

- No `__defaults__` dict existed anywhere in the class, yet
  `self.options['orientation']` (`on_ui_setup`) and
  `self.options['enabled']` (`on_ui_update`) were both directly
  indexed - guaranteed `KeyError` on a fresh install (the default state
  for a brand-new plugin config) the moment either hook ran.
- The `orientation` branch in `on_ui_setup` built identical
  `LabeledValue` elements regardless of the value - `orientation` was
  entirely decorative, never affecting the actual UI.
- The fortune list was a hardcoded 4-item list inline in
  `on_ui_update`, with no rotation timer - effectively static output.

## What this build does

- `DEFAULTS` dict + `_opt()` helper (`.get()`-based) covering every
  option: `enabled`, `orientation`, `fortunes`, `rotate_interval_seconds`,
  `fortune_command`.
- `orientation` now genuinely changes the rendered element: vertical
  gets a `Fortune:` label above the value; horizontal (default) has no
  separate label, single combined line.
- `on_ui_update` now tracks `self._last_rotate` and only re-picks a
  fortune once `rotate_interval_seconds` has elapsed (or on the very
  first update), rather than picking fresh every single call (which
  would rotate far too fast, effectively every UI tick) or never
  (the original's practical behavior).
- `fortunes` is now a full config-driven list (20 defaults, up from 4),
  read via `_opt("fortunes")` with a hardcoded `DEFAULT_FORTUNES`
  fallback if the option is present but empty.
- `fortune_command`: when set, `_run_fortune_command()` runs it via
  `subprocess.run(..., shell=True, capture_output=True, text=True,
  timeout=5)`, catching `subprocess.TimeoutExpired` and `OSError`
  (covers a missing binary), checking `returncode`, and treating empty
  stdout as a miss too - any of these fall back to
  `_pick_from_list()`. Nothing here can crash the daemon even with no
  `fortune` binary installed at all.

## Testing

Tests in `tests/test_fortune_cookie_ng.py`, run against the real cloned
`jayofelony/pwnagotchi` framework. Covers: real plugin registration;
`on_ui_setup`/`on_ui_update` running without `KeyError` on a
completely empty options dict (the original's exact crash scenario);
`orientation="vertical"` producing a labeled element vs.
`orientation="horizontal"`/default producing an unlabeled one (proving
the option now has a real effect, unlike the original where both
branches built identical elements); `enabled=False` showing the
disabled message; fortune selection picking only from the configured
list when `fortune_command` is unset; rotation timing - the fortune
does NOT change before `rotate_interval_seconds` has elapsed, and DOES
change (or at least is re-rolled) after; `fortune_command` success
path returning the command's stdout; `fortune_command` falling back to
the list on a non-zero exit, a `TimeoutExpired`, a missing-binary
`OSError`, and empty stdout - none of them raising; empty `fortunes`
list falling back to `DEFAULT_FORTUNES` instead of crashing
`random.choice` on an empty sequence.

## Still open

- No real-device test against a live e-paper display or an actually
  installed `fortune` binary - `subprocess.run` is mocked for the
  command-path tests.
