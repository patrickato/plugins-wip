# Notes: BirthdayNG

## Bugs found

- `__defaults__` only covered `enabled`. `show_age`, `show_birthday`,
  `age_x_coord`, `age_y_coord` were all read via direct `self.options[...]`
  indexing in `on_ui_setup`/`on_ui_update` with no fallback -
  `KeyError` on any install missing one of them in `config.toml`.
- `__dependencies__` declared `"pip": ["none"]` while the module
  imports `dateutil.relativedelta`, which needs `python-dateutil`
  (confirmed genuinely installed in this sandbox, and genuinely needed
  at import time regardless).
- `load_data()` used `data["born_at"]` (direct indexing) against the
  real `/root/brain.json`. A brain.json that exists but lacks
  `born_at` either raises or leaves `self.born_at` at the `__init__`
  default of `0` (Unix epoch, 1970) - producing a nonsensical ~55+
  year-old age display instead of failing visibly.

## What this build does

- Real defaults for every option (`DEFAULTS` dict + `_opt()` helper
  using `.get()`), so nothing `KeyError`s on a fresh/partial config.
- `__dependencies__` fixed to declare `python-dateutil`.
- `_read_born_at_from_brain()` uses `.get("born_at")` and catches
  file/JSON errors, returning `None` on any failure instead of
  raising or defaulting to epoch-0.
- When brain.json has no `born_at`, `_load_or_create_fallback()`
  writes a fresh `time.time()` timestamp into the plugin's own
  `/root/birthday_ng_fallback.json` (created once, then reused on
  every later load) instead of writing into `/root/brain.json`
  itself. Design rationale: `brain.json` is owned by the core
  framework/other plugins; writing a possibly-wrong `born_at` there
  risks conflicting with a real one that shows up later from
  elsewhere. The plugin always re-checks the real `brain.json` first
  on every `load_data()` call, so if it ever does get a real
  `born_at`, that takes priority automatically and the fallback file
  is simply no longer consulted.
- `on_ui_update` shows `"unknown"` for age/birthday when `born_at` is
  still `None` (only possible if even the fallback-file write failed),
  rather than crashing or showing a bogus age.
- New `birthday_message` config option (default: `"Happy Birthday to
  me! I am {age} old today!"`), shown in place of the normal age
  string when today matches the month+day of `born_at`. `{age}` is
  substituted with the current formatted age string.
- On-screen position normalized to the repo-wide convention:
  `position_x` / `position_y` are preferred and, via `_resolve_position()`,
  fall back to the original `age_x_coord` / `age_y_coord` names (then to
  `(0, 0)`) so older configs keep working. The Age and Birthday elements
  are mutually exclusive, so one resolved pair covers whichever is shown.
- `on_webhook` fixed to return an HTML body instead of `None`. Returning
  `None` makes Flask raise a 500 on the bare index path
  (`GET /plugins/birthday_ng/`); the body now reports the current
  age/birth date (or "born_at unknown").
- Declined this round: on-screen countdown to next birthday - not
  built.

## Testing

Tests in `tests/test_birthday_ng.py`, run against the real cloned
`jayofelony/pwnagotchi` framework. Covers: real plugin registration;
missing options fall back to real defaults instead of `KeyError`;
`__dependencies__` correctly lists `python-dateutil`; `load_data`
correctly reads a real `born_at` from a mocked brain.json; a
brain.json with no `born_at` key falls back to creating
`birthday_ng_fallback.json` with a fresh timestamp; a second load
reuses the existing fallback file's timestamp rather than overwriting
it; a real `born_at` appearing later in brain.json takes priority over
an existing fallback file; a missing/corrupt brain.json is handled
without raising; age formatting (years/months/days singular-plural and
the <1-year "days" vs "d" branch); `is_birthday_today` matching and
non-matching dates; `birthday_message` substitution on the matching
date; `on_ui_update` showing `"unknown"` when `born_at` is `None`
(fallback file write itself failed); `on_ui_setup`/`on_unload` running
without crashing; `position_x`/`position_y` (and the legacy
`age_x_coord`/`age_y_coord` fallback) landing on the drawn element; and
`on_webhook` returning a non-None HTML body in both the known and
unknown-`born_at` cases.

## Still open

- No real-device test against a live, populated `/root/brain.json` or
  an actual e-paper display driver.
- Countdown-to-next-birthday was explicitly declined this round, not
  built.

## Original config preserved

A real original config file for `birthday.py` was found (exact match) at
`itsdarklikehell/pwnagotchi-plugins/configs/birthday.toml`. Preserved verbatim in this suite's own folder as
`config.original.toml`, per the project's standing config-preservation requirement,
for reference/troubleshooting if the rebuilt `config.toml` above ever
needs comparing against the source.
