# Research notes: BanthexNG

Source: `banthex-de.py` (itsdarklikehell/pwnagotchi-plugins, originally
adi1708). Reviewed as part of the `test-plugins` Cluster 31
(cloud-crack-upload destinations, revisiting Group 19).

## Why `banthex-de.py` and not `banthex.py`

Both are near-identical wpa-sec-compatible upload plugins for
banthex.de, by two different contributors forking the same original.
`banthex.py` (dadav's fork, v2.1.0) has its own extra bug on top of the
shared one: its `__init__`'s corrupted-state-file recovery path deletes
`/root/.wpa_sec_uploads` (a *different* plugin's state file entirely)
instead of its own `/root/.banthex_uploads`, then immediately tries to
reopen the still-corrupt original file - which fails again, uncaught
this time, so the plugin fails to load at all if that ever happens.
`banthex-de.py` (adi1708's fork, v1.5.0) fixes this correctly. Since the
two are otherwise functionally identical, `banthex-de.py` was chosen as
the one to fix and keep; `banthex.py` was removed from the master list
as the redundant, more-buggy twin.

## Bugs found in `banthex-de.py` (source-verified)

1. **`.pcap`-only filter**: `filename.endswith('.pcap')` never matches
   this fork's real `.pcapng` captures - the recurring bug across this
   entire project. Fixed.
2. **Permanent skip list**: `self.skip.append(handshake)` on any upload
   failure, with nothing ever removing an entry - a single transient
   network hiccup permanently blacklisted that handshake from ever being
   retried again until the whole plugin reloaded. Fixed with bounded
   retries (`max_upload_attempts`, default 3) - the identical fix
   already applied to `hashespwnagotchi_ng.py` earlier in this same
   cluster, kept consistent across both.

Everything else checked out fine: the whitelist exclusion
(`remove_whitelisted`) is present and correctly called (unlike
`hashespwnagotchi.py`, which had this commented out); the cracked-results
download throttle (don't re-download more than once an hour) works as
intended, just hardcoded rather than configurable; `on_loaded()`
correctly validates both required options before setting `ready = True`.

## `on_webhook()`'s cookie-based auth - not a bug, worth documenting

`on_webhook()` redirects to banthex.de with your `api_key` set as a
plaintext cookie. This is intentional and matches how the real wpa-sec
service (which banthex.de is designed to be compatible with) handles
browser-based auth - there's no way to "fix" this without breaking
compatibility with the service itself. Documented clearly in README.md
so it's a known, accepted design point rather than a discovered surprise
later.

## What changed in this rewrite (summary)

Both fixes above, plus: `upload_timeout` / `download_timeout` config
options (the original hardcoded 30 seconds for both), and
`download_check_interval_hours` (the original hardcoded 1 hour). No
behavior changes beyond making these already-reasonable defaults
configurable.

## Testing done (sandbox, no real hardware)

11 tests in `tests/test_banthex_ng.py`, run against the REAL cloned
jayofelony framework (`pwnagotchi.plugins`, confirmed genuine
`Plugin.__init_subclass__` registration) and a real Flask
app/request context for `on_webhook()` (confirms the actual redirect +
cookie-setting behavior, not just that the function doesn't crash).
Covered: `.pcapng` is uploaded, `.pcap` never is; the whitelist is
actually applied; a failed upload is retried before being permanently
skipped, and only skipped after `max_upload_attempts` real failures; a
successful upload is recorded and never retried/skipped; the
cracked-results download respects the configurable interval; and
`on_webhook()` returns a real redirect with the `key` cookie set.

Only `prctl` and `tomlkit` (both native/pure-Python dependencies this
sandbox can't install, and both untouched by anything this plugin
actually calls) are stubbed - `pwnagotchi.plugins`, `pwnagotchi.utils`
(`StatusFile`, `remove_whitelisted`), `requests`, and `flask` are all
the real things.

## Still open / needs real-hardware testing

- The banthex.de account/API side is entirely untestable from here - a
  valid `api_key` and whether uploads/downloads actually work against
  the live service both need a real run.
- The cookie-based `on_webhook()` flow (visiting the plugin's web page to
  get redirected into banthex.de logged in) hasn't been exercised in a
  real browser.

## Config verified against upstream (2026-09-28)

Compared `config.toml` against the real upstream sample
(`itsdarklikehell/pwnagotchi-plugins/configs/banthex-de.toml`, which
includes real `api_key`/`api_url`/`download_results`/`whitelist`
values - the API key and whitelist entries in that sample belong to the
original author, not this user, so they were not copied forward; this
plugin's own `config.toml` uses clearly-marked placeholder values
instead) and the original's `__defaults__`
(`enabled`, `api_key`, `api_url`, `download_results`, `whitelist`).
Checked every `self.options.get(...)`/`self.options[...]` call in
`banthex_ng.py`: `api_key`, `api_url`, `whitelist`, `download_results`,
`download_check_interval_hours`, `upload_timeout`, `download_timeout`,
`max_upload_attempts` are all present and documented. Renamed from
`config.toml.example` to `config.toml`.
