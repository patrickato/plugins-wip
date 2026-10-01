# Notes: FortuneThoughtsNG

## Why this merge happened

The post-cluster-review pass found `fortune-cookie-suite`
(`FortuneCookieNG`) and `showerthoughts-suite` (`ShowerThoughtsNG`) were
both small, closely-related "rotate a short text message on screen while
idle" plugins - same shape (one `LabeledValue` UI element, a rotation
timer, a content pool, reactive-state-driven immediate rotation), just
sourcing their text differently (a local list/shell command vs. a
fetched reddit pool). Worth combining into one suite with a single
`content_source` switch rather than maintaining two nearly-parallel
implementations side by side. Both old suite folders
(`fortune-cookie-suite/`, `showerthoughts-suite/`) have been deleted;
this suite (`fortune-thoughts-suite/`) replaces both entirely.

## What was preserved from fortune-cookie-suite, and where

All in `fortune_thoughts_ng.py`:

- The full per-hardware default position table (waveshare v1/v2/v3 →
  `(0, 95)`, waveshare144lcd → `(0, 92)`, inky → `(0, 83)`,
  waveshare27inch → `(0, 153)`, else → `(0, 91)`) - see `_position()`.
  Now the fallback used only when `position_x`/`position_y` aren't both
  explicitly set (see the position-resolution section below).
- `orientation` ("horizontal" default = no label, one combined line;
  "vertical" = a `Fortune:` label above the value via `LabeledValue`) -
  see `on_ui_setup()`, unchanged.
- `DEFAULT_FORTUNES` - the same 20-entry local content pool, used as the
  default `fortunes` list.
- `fortune_command`: shells out via `subprocess.run(command, shell=True,
  capture_output=True, text=True, timeout=5)`, falling back to the local
  list on any missing-binary/timeout/non-zero-exit/empty-output
  condition, never raising - see `_run_fortune_command()` and
  `_local_or_command()`, byte-for-byte the same safety behavior as the
  original.

## What was preserved from showerthoughts-suite, and where

All in `fortune_thoughts_ng.py`:

- The full reddit-fetch pipeline - `on_internet_available()` starts a
  background daemon thread (`_fetch_thoughts`, never runs on the main
  thread), guarded by `refresh_interval_seconds` and a
  "don't start a second fetch while one is already running" check.
  `_fetch_thoughts()` does `requests.get` against
  `https://www.reddit.com/r/<subreddit>/hot.json` with a real
  `User-Agent` header (updated to reference `fortune_thoughts_ng`
  instead of `showerthoughts_ng`), and handles: a missing `requests`
  import (logs and returns, no crash), HTTP 429 (logs and retries next
  cycle, existing pool untouched), non-200 status, malformed JSON
  (`KeyError`/`TypeError`/`ValueError` all caught), filters stickied
  posts, applies `min_score`, and truncates to `max_length` with an
  ellipsis. Nothing here assumes a field exists.
- The on-disk cache (`cache_file`, a JSON list of strings) - loaded in
  `on_loaded()` (regardless of `content_source`, so switching to
  `"reddit"`/`"auto"` later still has something cached) and saved after
  every successful fetch, with the same defensive try/except around
  both load and save.
- `reactive_states` (default `["bored", "lonely", "sad"]`) driving
  `on_bored`/`on_lonely`/`on_sad`. **Small behavior improvement over the
  original**: showerthoughts-suite only ever rotated the reddit pool on
  a reactive state; here, `_reactive_rotate()` calls the same
  `_rotate()`/`_get_message()` path `on_ui_update` uses, so a
  bored/lonely/sad state now immediately rotates a fresh message from
  whatever `content_source` is actually configured (including a fresh
  local fortune or command result) - there was no good reason to
  restrict "get a new one now" to reddit-sourced content only.
- `position_x`/`position_y` explicit override options - see "Position
  resolution" below for how they combine with fortune-cookie's table.
- A real `on_webhook(self, path, request)` - showerthoughts previously
  only logged a line and returned nothing. This suite instead returns a
  small HTML status page (current `content_source`, current pool size,
  the currently-displayed message, and last reddit refresh time),
  through pwnagotchi's own built-in web UI webhook mechanism - no new
  port, no new server, matching crack-house-suite's minimal-effort
  pattern rather than adding another independent attack surface (the
  post-cluster-review pass specifically flagged suites running their own
  servers as worth minimizing).

## Position resolution (combining both suites' positioning features)

`_position(ui)`: if `position_x` AND `position_y` are both explicitly
set, they win outright (showerthoughts' override feature). Otherwise,
falls back to fortune-cookie's per-hardware auto-detected table -
**not** showerthoughts' old plain `(0, ui.height() - 10)` bottom-line
default, which is dropped entirely: the per-hardware table is strictly
more precise, so it's the better universal fallback.

## content_source fallback chains

- `"local"` (default): `fortune_command` if configured and it succeeds,
  else the `fortunes` list. No network access.
- `"command"`: `fortune_command`, falling back to the `fortunes` list on
  any failure (unset command, missing binary, timeout, non-zero exit,
  empty output).
- `"reddit"`: the fetched reddit pool if it currently has content, else
  the `fortunes` list (so the display is never blank - e.g. before the
  first successful fetch, or if every fetch attempt has failed, or
  there's no internet).
- `"auto"`: reddit pool (if it has content) → `fortune_command` (if
  configured) → `fortunes` list. Any unrecognized/typo'd
  `content_source` value is also treated as `"local"`, so a
  misconfiguration degrades gracefully instead of crashing.

The background reddit-fetch machinery (`on_internet_available`) is only
ever started when `content_source` is `"reddit"` or `"auto"` - `"local"`/
`"command"` mode never spends a thread or a network call fetching a pool
they'll never read from.

## rotate_interval_seconds

Unified to a single default of **45 seconds**, replacing both originals'
separate values (fortune-cookie-suite: 60s, showerthoughts-suite: 30s) -
a reasonable middle ground between the two, still fully configurable.

## enabled defaults to true

fortune-cookie-suite's own historical default was `enabled = true` (it's
a benign display plugin, not a network scanner). showerthoughts-suite
defaulted to `enabled = false` only because that suite was an unverified
from-scratch build with no source to validate against. Now that it's
merged with fortune-cookie's default-on precedent, and the merged
suite's own default `content_source = "local"` requires zero network
access, defaulting to `enabled = true` is correct here - there's nothing
about the default configuration that reaches out to the network or does
anything riskier than the original fortune-cookie plugin did.

## Original config preserved

A real original config file for `fortune_cookie.py` was found (exact
match) at `itsdarklikehell/pwnagotchi-plugins/fortune_cookie.toml` and
is preserved verbatim in this suite's folder as `config.original.toml`,
per the project's standing config-preservation requirement, for
reference/troubleshooting if the rebuilt `config.toml` above ever needs
comparing against the source.

showerthoughts-suite never had a real original config to preserve -
its own `NOTES.md` documented that no source for the original
"Showerthoughts" plugin could be located anywhere in the archives
searched for this project, so there was never an upstream
`showerthoughts.toml` (or similar) to find in the first place. Nothing
from showerthoughts-suite is included in `config.original.toml`; it
contains only fortune-cookie's real original, as described above.

## Known limitations / not built this round

- No real-device test against a live e-paper display, an actually
  installed `fortune` binary, or a live network call to reddit -
  `subprocess.run` and `requests.get` are both mocked in the test suite,
  matching both originals' own honest caveats about this.
- Reddit's own rate-limiting behavior for unauthenticated requests can
  change without notice; if `refresh_interval_seconds` at its default
  (1 hour) still triggers frequent 429s in practice, raising it further
  is the straightforward mitigation - no code change needed (carried
  forward from showerthoughts-suite's own NOTES.md).

## Design decisions made that weren't fully spelled out in the merge spec

- `"local"` and `"command"` end up calling the same `_local_or_command()`
  helper - when `fortune_command` is unset, `"command"` mode's "always
  use fortune_command, falling back to the list" degrades to exactly
  "local"'s own priority order anyway, so there was no behavioral reason
  to duplicate the logic. `"local"` still exists as its own distinct,
  clearly-named option value primarily for config readability/intent
  ("I want local content, permitting an optional command override") vs.
  `"command"` ("I specifically want the command-sourced content").
- The webhook's "last refresh time" is tracked in a new
  `self._last_fetch_success` timestamp, separate from the existing
  `self._last_refresh` throttle timestamp (which updates the moment a
  fetch is *attempted*, not when one actually *succeeds*) - using the
  throttle timestamp for the status page would have shown a fetch time
  even when that fetch failed or returned nothing usable.
- `_load_cache()`/`_fetch_thoughts()` now take `self._lock` around every
  read/write of `self._pool` (showerthoughts' original only locked the
  write in `_fetch_thoughts`, not the cache load) - a small defensive
  tightening since `_get_message()` can now read `self._pool` from
  `on_ui_update`'s calling context, not just from `_rotate()` triggered
  by the same fetch thread.

## Testing

`tests/test_fortune_thoughts_ng.py`, run against the real cloned
`jayofelony/pwnagotchi` framework, using the real `requests` library
(available in this sandbox) with only the HTTP transport mocked -
matching showerthoughts-suite's own original testing approach, not a
hand-rolled `requests` stub module (none of the existing suites in this
repo that touch `requests` actually ship one; the real library plus a
mocked `.get`/mocked import is the established, working pattern). Covers:
real plugin registration; local-list rotation; `fortune_command`
success/timeout/non-zero-exit/empty-output/missing-binary fallback;
reddit fetch success/429/malformed-JSON/missing-`requests`-import (via a
patched `builtins.__import__`, same technique apprise-notify-suite's own
tests already use for its optional `apprise` dependency); cache
load/save round-trip across a fresh plugin instance; all four
`content_source` values and their fallback behavior when the preferred
source is empty/unconfigured; position resolution (explicit override
wins; the per-hardware table for several hardware flags plus the
default/unknown-hardware case); `orientation` vertical vs. horizontal
element construction; reactive rotation firing for bored/lonely/sad
regardless of `content_source`; `on_unload` removing the element cleanly
(and being safe if it was never added); and the `on_webhook` status page
rendering without crashing under both reddit and local content sources.

## Real-hardware fix (2026-10-01, Pi batch-3a)

Live-load on the Pi crashed plugin load: `on_ui_setup` -> `_position()` called
`ui.is_waveshare27inch()`, which does not exist on jayofelony's `Display`
(AttributeError, unhandled). Fixed: `_position()` now probes every `ui.is_*()`
hardware helper defensively via `getattr`, so a missing/raising helper returns
False and falls through to the generic `(0, 91)` instead of crashing. Also set an
explicit `position_x=0 / position_y=91` in `config.toml` for the 480x320 MPI3501
so placement is deterministic and skips auto-detect entirely. Regression test
added (`_BareUI` with no `is_*()` helpers must not raise, must land at (0,91)).
