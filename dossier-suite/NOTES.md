# Notes: DossierNG

## Why this one

This is idea #1 from this project's post-cluster-review
"red-team-ideas" brainstorm: a "unified target dossier." Unlike every
other suite in this repo, there is no upstream `handshaker.py`/
`crack_house.py`/etc. this rebuilds or fixes - **this is a brand-new
plugin, built from scratch**, following the same precedent
showerthoughts-suite set before it was merged away (its NOTES.md: "The
master list carried [an] entry ... with no locatable source anywhere
... unlike every other plugin in this audit, there was no file to
read, diff, or fix.").

## No original config

There is no `config.original.toml` in this suite's folder, and there
never will be one - this is an original plugin for this repo, with no
upstream plugin's config to preserve. (Same situation showerthoughts-
suite documented before it was merged away.)

## Design decision: read bluetooth-recon-suite's correlation, don't re-derive it

This is the one design decision in the task that's worth spelling out
in full, since it's the crux of how this plugin avoids duplicating
real logic.

bluetooth-recon-suite already reads these same three files
(crack-house-suite's potfile, timer-suite's CSV, gps-tagger-suite's tag
directory) itself, purely to answer one question for its own purposes:
"was this specific Bluetooth device active near a WiFi network within
some time window?" Its `_correlate()`/`_nearby_wifi_networks()`/
`_cracked_hostnames()`/`_gps_locations()` methods do that work, and the
result is persisted on every device record as `correlated_networks`
(list of hostnames), `any_cracked` (bool), and `correlated_location`.

DossierNG's job is the mirror-image question: "for this WiFi network,
which Bluetooth devices were nearby?" Two ways to answer that were
considered:

1. **Re-implement the same time-window correlation logic directly in
   DossierNG** - read the raw Bluetooth event timestamps (there aren't
   even any persisted directly - only bluetooth-recon-suite's derived
   `last_seen`/`first_seen` per device) and the same three WiFi-side
   sources, and recompute matches independently.
2. **Read bluetooth-recon-suite's own device table and invert its
   already-computed `correlated_networks` field** - for each device
   record, for each hostname in its `correlated_networks`, that device
   is a "nearby" hit for that hostname's dossier.

Option 2 is what's built here, for two concrete reasons:

- **No duplicate logic to maintain or drift.** The time-window
  matching, the exact field name mismatches file formats can have, and
  any future refinement (e.g. a smarter distance-based match instead
  of a flat time window) all live in exactly one place. If
  bluetooth-recon-suite's correlation logic improves, DossierNG
  benefits automatically with zero code changes.
- **DossierNG genuinely doesn't need the raw timestamps.**
  bluetooth-recon-suite's device table doesn't expose the individual
  BLE/classic sighting timestamps that fed its correlation window
  decision anyway (only the derived `first_seen`/`last_seen` summary
  fields) - re-deriving the same match from a *summary* of the data
  bluetooth-recon-suite already used would be strictly less accurate
  than just reading its conclusion directly.

The cost of this choice, honestly stated: DossierNG's Bluetooth section
is only as good, and only as fresh, as bluetooth-recon-suite's own
persisted device table. If that suite isn't installed, hasn't run
recently, or its own correlation window/config is tuned differently
than a user might expect for this purpose, DossierNG's "nearby
Bluetooth devices" section reflects that as-is - it has no visibility
into (and doesn't second-guess) bluetooth-recon-suite's own matching
decisions.

## Known limitations (read this before trusting the Bluetooth section)

- **The Bluetooth correlation is an inherited, best-effort, TIME-WINDOW
  heuristic - not proven physical co-location.** A device merely being
  listed as "active nearby around the same time" as a network does
  NOT mean it is physically near that network's AP, does NOT mean it
  belongs to that network's owner, and does NOT mean any connection
  between the two exists at all beyond both having produced activity
  bettercap saw within bluetooth-recon-suite's configured
  `correlation_window_minutes`. This is stated plainly on both the
  index page's Bluetooth-column header context and, more fully, on
  every detail page directly above the nearby-device table - it is
  never phrased as "confirmed" or "belongs to" anywhere in this
  plugin's own code, config, or docs.
- **No real end-to-end test against genuinely-populated real source
  files on real hardware was possible in this sandbox.** All test
  coverage (see "Testing" below) is against constructed/fixture data
  written directly by the test file, in the exact real file formats
  each source suite documents (potfile lines, timer CSV rows,
  gps-tagger JSON records, bluetooth-recon-suite's JSON device table
  schema) - not against files actually produced by a live pwnagotchi
  running all four suites in the field. The parsing logic for each
  format is verified against that suite's own source code (read in
  full before writing this plugin), but a genuinely-in-the-wild
  malformed file shaped in a way none of these fixtures anticipated
  could still, in principle, hit an untested code path. Every source
  read is still wrapped in a broad `except Exception` with a WARNING
  log, specifically as a hedge against exactly that possibility - see
  "Defensive reads" below.
- **Hostname matching is case-insensitive but not otherwise
  normalized.** Two APs that are "the same" network but recorded under
  meaningfully different hostnames by different suites (e.g. one
  suite's `<hidden>`/MAC-address fallback vs. another suite's real
  SSID for the same AP) will show up as two separate, partially-
  documented dossiers rather than being merged. This matches
  crack-house-suite's own established hostname-matching convention
  (case-insensitive only), which this plugin follows for consistency,
  but it's a real limitation worth knowing about.
- **GPS "most recent" is by file mtime, not by a GPS-fix timestamp
  inside the file itself for ties.** gps-tagger-suite's own record
  does carry a `tagged_at` unix timestamp, but this plugin uses the
  file's on-disk mtime to pick the winning file when more than one
  `pn_ap_*.json` matches the same hostname (which can happen if
  gps-tagger-suite ever wrote files for the same hostname under
  different MACs) - mtime and `tagged_at` should normally agree since
  gps-tagger-suite writes the file at tag time, but they are not
  guaranteed identical in every edge case (e.g. a file copied/restored
  from a backup with a different mtime).

## Defensive reads

Every one of the four source reads (`_read_cracked_passwords`,
`_read_timer_rows`, `_read_gps_tags`, `_read_bluetooth_devices`) is
wrapped in its own independent try/except, mirroring the exact
defensive style bluetooth-recon-suite already established for these
same four file types:

- `FileNotFoundError` (and, for the GPS directory, a plain
  `os.path.isdir()` check) is caught separately and logged at
  **DEBUG** - a source simply not being installed, or not having
  produced any data yet, is completely normal and not worth a WARNING.
- Any other read/parse error is logged at **WARNING** - the file
  exists but couldn't be understood, which is worth a user's
  attention.
- Within the timer CSV and the GPS tag directory, an individual
  malformed row/file is skipped (with its own WARNING) without
  aborting the rest of that source's read - one bad CSV row or one
  corrupt `pn_ap_*.json` file never loses every other row/file in that
  same source.
- The Bluetooth-device inversion step similarly skips (with a WARNING)
  any individual device record that isn't shaped as expected, rather
  than letting one malformed record abort the whole inversion.

None of the four `_read_*` methods, nor `_assemble()` as a whole, ever
raises out to `on_loaded`/`on_webhook`/`on_ui_update` - this is
directly tested (see below) by making each of the four sources
missing, and separately malformed, one at a time, and confirming the
other three still populate correctly and nothing crashes.

## Caching (`refresh_interval_seconds`)

Re-reading and re-parsing four files/directories on every single UI
tick or every single webhook hit would be wasteful, especially for the
GPS tag directory (which can hold many small files). `_rebuild()` is
only called from `_maybe_rebuild()`, which checks whether at least
`refresh_interval_seconds` seconds have elapsed since the last build
(or whether no dossier has ever been built yet) before doing the
actual work; both `on_ui_update` and `on_webhook` go through
`_maybe_rebuild()`, so a webhook hit that arrives after the cache has
gone stale still gets a fresh rebuild on demand, but a rapid sequence
of hits within the interval reuses the cached result. This is tested
directly: a rebuild is confirmed to happen only once for repeated
`_maybe_rebuild()` calls inside the interval, and a forced-elapsed
timestamp is confirmed to trigger exactly one more rebuild.

## Routing (`on_webhook`)

Matches bluetooth-recon-suite's routing style - route on the stripped
path:

- `""` (index, also matched by a bare `?host=` with no path) - the
  sorted-by-completeness index page.
- `"target/<hostname>"` - that hostname's detail page. A bare
  `?host=<hostname>` query parameter is also accepted as an
  alternative to the path form, resolved from the raw path's query
  string via `urllib.parse` before path-matching, so either form
  reaches `_detail_page`.
- `"export"` - the full assembled dossier dict as JSON, via a real
  Flask `Response` (imported defensively, matching every other suite
  in this repo that might run without Flask installed, though Flask is
  always present on-device in practice).
- Anything else - `"Not found", 404`, matching this repo's established
  convention (see fix-region-suite's/bluetooth-recon-suite's own
  webhook 404 handling).

Every value that originated from scanned/untrusted network or
Bluetooth data (hostnames, device names, vendor strings, tracker
types, update types, GPS-record fields rendered as text) is passed
through `html.escape()` before being written into any HTML response -
directly mirroring bluetooth-recon-suite's `_status_page()`, since a
maliciously-named AP or BLE device is untrusted input reaching this
plugin exactly the same way it reaches that one. This is tested
directly: a hostname and a Bluetooth device name are both constructed
containing a literal `<script>` tag, and both the index and detail
pages are confirmed to never contain the raw unescaped tag while
containing its escaped form.

## Testing

Run with (from this directory):
```
python3 tests/test_dossier_ng.py
```
(`pytest` isn't installed in this particular sandbox - the test file
is written as a self-contained script with its own `check()`/pass-fail
harness, same as every other suite's test file in this repo, so it
runs directly with plain `python3` wherever `pytest` isn't available.)

All tests pass as of this writing (see the final `0 failure(s) out of
test run` line). Covers, against constructed fixture files in each
real source format and the real `pwnagotchi.plugins` loader:

- **Registration**: the plugin registers under the real loader as
  `dossier_ng`, and all required hooks exist.
- **`_opt()`** falls back to `DEFAULTS` for a key entirely absent from
  `options` (this fork's loader never merges `__defaults__`).
- **Assembly with all four sources present and matching by hostname** -
  a hostname appearing in all four sources assembles a dossier with
  `cracked_password`, `gps` (with a Google Maps URL in the exact
  `https://www.google.com/maps/search/?api=1&query=<lat>,<lon>` format
  this repo's discord-suite already uses), `timing`, and
  `nearby_bluetooth_devices` all populated, with `completeness == 4`.
- **Each of the four sources individually missing** (potfile absent,
  CSV absent, GPS directory absent, Bluetooth device table absent) -
  confirmed the other three sources still populate correctly for a
  hostname that has data in them, and nothing raises.
- **A malformed/corrupt file for each of the four sources** (an
  unparseable potfile line with no `:`, a CSV row missing/blank
  `network`, a `pn_ap_*.json` file that isn't valid JSON, a Bluetooth
  device table that isn't a JSON object at all) - confirmed the
  corrupt entry/file is skipped gracefully and doesn't prevent the
  rest of that same source, or any other source, from being read.
- **Case-insensitive hostname matching** across all four sources - a
  potfile entry for `"MyLab"`, a timer CSV row for `"mylab"`, a GPS tag
  for `"MYLAB"`, and a Bluetooth correlated-network entry for `"MyLab"`
  are all confirmed to merge into exactly one dossier.
- **"Most recent" selection**: for the GPS tag, two `pn_ap_*.json`
  files for the same hostname with different mtimes - the
  more-recently-modified one's coordinates win. For the timer CSV, two
  rows for the same hostname with different timestamps - the later
  timestamp's row is used as the primary record, and
  `historical_row_count` correctly reports 2.
- **Tracker devices sorted to the front** of `nearby_bluetooth_devices`
  - a fixture with one non-tracker device recorded before one tracker
  device in bluetooth-recon-suite's device table confirms the tracker
  still appears first in the assembled list.
- **`completeness` scoring** - explicit checks for 0, 1, and 4 (every
  combination in between follows directly from the same summation
  logic, exercised across the other assembly tests above).
- **`on_webhook`**: the index page (lists a known hostname, sorted by
  completeness), the detail page (including a maliciously-named
  hostname AND a maliciously-named Bluetooth device, both confirmed
  HTML-escaped), the `export` JSON route (valid JSON, contains the
  assembled data), a request for an unknown hostname's detail page
  (404), and a request for a completely unrecognized path (404).
- **`refresh_interval_seconds` caching**: repeated `_maybe_rebuild()`
  calls within the interval only rebuild once (confirmed via a call
  counter on the assembly method); forcing `_last_build` into the past
  by more than the interval triggers exactly one more rebuild on the
  next call.
