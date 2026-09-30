# BirthdayNG

Shows your Pwnagotchi's age (or birth date) on screen. Rebuilt from
`birthday.py` (`Birthday`).

## What was actually broken

- `__defaults__` only set `enabled: False`. Every other option
  (`show_age`, `show_birthday`, `age_x_coord`, `age_y_coord`) was
  accessed with direct `self.options[...]` indexing and no fallback -
  a fresh install with any of those unset in `config.toml` (or a config
  that just forgot one) raised a `KeyError` the moment `on_ui_setup` or
  `on_ui_update` ran.
- `__dependencies__` declared `"pip": ["none"]`, but the module-level
  `from dateutil.relativedelta import relativedelta` import requires the
  `python-dateutil` package. The declared dependency and the actual
  import didn't match.
- `load_data()` did `self.born_at = data["born_at"]` - direct indexing
  on the real `/root/brain.json`. If that file existed but genuinely had
  no `born_at` key (not an unusual state - it's written by the core
  framework, not this plugin), this raised (or, depending on framework
  version/call site, could silently leave `self.born_at` at its
  `__init__` default of `0`, i.e. the Unix epoch) - producing either a
  crash or a wildly wrong "50+ year old" age display instead of an
  honest "we don't know yet".

## What this rebuild adds

- Real defaults for every option via `.get()`/a `DEFAULTS` dict, so a
  fresh install never `KeyError`s.
- `__dependencies__` now correctly declares `python-dateutil`.
- Missing/unreadable `born_at` is now handled explicitly: the age/
  birthday elements show `"unknown"` instead of crashing or showing a
  bogus decades-old age.
- **`birthday_message`** - a configurable message shown in place of the
  normal age string on the actual anniversary of `born_at` (same
  month+day as today). Defaults to `"Happy Birthday to me! I am {age}
  old today!"`; `{age}` is replaced with the current age string.
- **Self-healing `born_at`** - if `/root/brain.json` genuinely has no
  `born_at` key, the plugin creates its own small fallback state file
  (`/root/birthday_ng_fallback.json`) with a fresh timestamp the first
  time this happens, and reuses that same fallback timestamp on every
  later load. This is deliberately **not** written into `/root/
  brain.json` itself - that file is owned by the core framework (and
  potentially other plugins), and writing into it risks clobbering a
  `born_at` that shows up there later from elsewhere. Keeping our own
  side file means the age display eventually becomes meaningful again
  without ever touching a file this plugin doesn't own. If `brain.json`
  *does* get a real `born_at` later, it takes priority over the
  fallback file automatically (it's checked first, every load).

- **Convention-named position options** - `position_x` / `position_y`
  now set the on-screen spot for whichever element is shown (age or
  birthday), matching the other NG suites. The original's
  `age_x_coord` / `age_y_coord` names are still honored when
  `position_x` / `position_y` are left unset, so existing configs keep
  working unchanged. Default is `(0, 0)`, the original's own default.
- **`on_webhook` now returns a real body.** The original returned
  `None`, which makes Flask raise a 500 on the bare plugin index
  (`GET /plugins/birthday_ng/`); it now serves a small status page with
  the current age/birth date.

No on-screen countdown-to-next-birthday feature was added - explicitly
declined for this round.

## Configuration

See `config.toml`. `show_age` and `show_birthday` are mutually
exclusive in practice (age wins if both are true, matching the
original's if/elif). Position is set via `position_x` / `position_y`
(preferred, matching the other NG suites); the legacy
`age_x_coord` / `age_y_coord` names still work when the preferred pair
is left unset.

## Still open

- No real-device test against an actual populated `/root/brain.json` or
  a real display driver - the sandbox test suite exercises the plugin's
  logic (defaults, missing-key handling, fallback-file self-heal, age
  formatting, birthday-message substitution) directly against the real
  cloned framework classes, with the filesystem mocked.
