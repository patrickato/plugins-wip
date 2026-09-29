# Notes: HandshakerNG

## Why this one needed a real rebuild, not just a small patch

`handshaker.py`'s stated purpose - "help access important pwnagotchi
information when the device cannot be accessed via SSH" - was
completely unimplemented: its `on_loaded()` was guaranteed to crash on
every single load (bug #1 below), and even if it hadn't,
`on_webhook()` (the only place that purpose could have lived) did
nothing but log and return `None` (bug #2). The plugin's only actually
-working code was its `on_ready`/`on_unload` boot-flow behavior, which
this rebuild kept intact.

## Bugs found in handshaker.py (all source-verified)

1. **`on_loaded()` calls `self.load_data(data_path)`, but `load_data`
   is never defined anywhere in the class.**
   ```python
   def on_loaded(self):
       data_path = "/root/handshakes"
       self.load_data(data_path)
       ...
   ```
   There is no `def load_data` anywhere in `handshaker.py` - this is a
   guaranteed `AttributeError` the instant `on_loaded()` runs, on
   every single plugin load, with no code path that avoids it. The
   plugin has never worked at all, on any device, with any config.
   Fixed: `load_data(self, data_path)` is a real method now, backed by
   the pure/testable `scan_handshakes(data_path)` helper (see "What's
   new" below) - it globs `data_path/*.pcapng` (never `*.pcap` - see
   the fork-fact section below), builds one record per capture, sorts
   newest-first, and sets `self.handshakes` to the count (the exact
   attribute name preserved - the original's own `on_loaded`/`on_ready`
   logging already read `self.handshakes`, and this rebuild's identical
   logging lines keep reading it) plus a separate `self.handshake_records`
   list.

2. **`on_webhook(self, path, request)` only logs and returns `None` -
   the plugin's entire stated purpose was completely unimplemented.**
   ```python
   def on_webhook(self, path, request):
       logging.info(f"[{self.__class__.__name__}] webhook pressed")
   ```
   No matter what path was requested, nothing was ever returned to the
   browser - the plugin's advertised "access important information
   without SSH" feature did not exist in any form. Fixed: this
   rebuild's real functionality (features 1/2/5 below - JSON status,
   HTML status/file-listing page, per-file downloads) is served
   through this plugin's own small dedicated Flask server instead of
   the shared pwnagotchi web UI's `on_webhook` route (see "New feature
   4" below for why a dedicated server, matching `web2ssh_ng.py`'s
   established pattern). `on_webhook` itself is still implemented -
   not left as a silent no-op - and now returns a short, honest string
   pointing at the dedicated server's actual URL (or a 503 explaining
   the server isn't running) instead of returning `None`.

3. **`__dependencies__` declared `pip: ["scapy"]`, but `scapy` is
   never imported or referenced anywhere in the file.**
   ```python
   __dependencies__ = {
       "apt": ["none"],
       "pip": ["scapy"],
   }
   ```
   A `grep -n scapy handshaker.py` beyond this one line finds nothing
   - a bogus dependency that would make a user install a real package
   for a feature the plugin never actually used. Fixed: removed. This
   rebuild's real dependencies are `flask`/`werkzeug` (for the new
   dedicated server, feature 4) - both normally already present on a
   stock pwnagotchi image, same note as `web2ssh_ng.py`/
   `handshakes_dl_ng.py`.

## Framework facts this rebuild relies on

Same verified facts as `web2ssh-suite`/`sigstr-suite` in this repo
(`pwnagotchi/plugins/__init__.py`, the cloned `jayofelony/pwnagotchi`
fork):
- `load_from_file()` registers a plugin as
  `plugin_name = os.path.basename(filename.replace(".py", ""))` - the
  file's exact basename, case-sensitive. `load()` looks up both the
  `enabled` flag and the options table in
  `config['main']['plugins'][name]` under that SAME key. So the config
  section for this file MUST be `[main.plugins.handshaker_ng]`
  (matching `handshaker_ng.py`), or the plugin silently never even
  appears in the "enabled" list.
- `plugins.load()` does `plugin.options =
  config['main']['plugins'][name]` - a RAW assignment of the parsed
  TOML table. `__defaults__` is NEVER merged by the framework itself.
  This rebuild uses the module-level `DEFAULTS` dict +
  `_opt()`/`_opt_int()`/`_opt_bool()`/`_opt_float()`-per-read pattern
  already established by `web2ssh_ng.py`/`sigstr_ng.py`.
- `Plugin.__init_subclass__` does `plugin_instance = cls()` - a
  ZERO-ARGUMENT construction. `__init__(self)` here takes no other
  arguments.
- Real hook signatures relevant here: `on_loaded(self)`, `on_ready(self,
  agent)`, `on_ui_setup(self, ui)`, `on_ui_update(self, ui)`,
  `on_unload(self, ui)` (the required `ui` parameter), `on_webhook(self,
  path, request)`.
- **This fork only ever writes `.pcapng` handshake capture files,
  never `.pcap`.** This has been a recurring, previously-found bug in
  this exact project - see `banthex_ng.py`/`hashespwnagotchi_ng.py`'s
  own docstrings for the history of plugins that filtered for `.pcap`
  and silently showed nothing as a result. `scan_handshakes()` below
  globs `*.pcapng` only (`CAPTURE_EXT = ".pcapng"`, used consistently
  everywhere a file is matched or a name is stripped of its
  extension) - a stray `.pcap` file is correctly ignored, and this is
  directly covered by a test that plants one and confirms it's
  excluded from the count.

## A naming note (same convention as web2ssh-suite/sigstr-suite)

The plugin file is `handshaker_ng.py` (snake_case), and its config
section is `[main.plugins.handshaker_ng]` - matching the file's exact
basename, NOT the class name (`HandshakerNG`), and NOT the original's
section name (`handshaker`). This is a verified framework fact, not a
stylistic choice (see the framework-facts section above) - a config
section spelled either of those other two ways for this file would
never appear in the framework's `enabled` list at all, a total
no-load, not an options bug.

## What was preserved from the original design, and how

**Kept, functionally unchanged:** `on_ready(self, agent)`'s boot-time
sync - rsyncing the handshakes directory to `/boot/handshakes`,
copying `/var/log/pwnagotchi.log` to `/boot/pwnagotchi-start.log`,
and - only if `/boot/custom_plugins` exists - moving its contents into
`/home/pi/custom_plugins/` and copying `/etc/pwnagotchi/config.toml`
to `/boot/config.toml` (this last copy is kept nested inside the same
`if os.path.exists("/boot/custom_plugins")` block as the original -
it was never meant to run unconditionally, and this rebuild doesn't
change that). `on_unload(self, ui)`'s copy of
`/var/log/pwnagotchi.log` to `/boot/pwnagotchi-end.log` is likewise
kept as-is. None of this was a bug - it's real, intentional
device-boot-flow behavior, unrelated to the (actually broken) web
feature, so it was left alone rather than "fixed."

**Three deliberate improvements were made on top of this otherwise-
unchanged behavior**, per the task's explicit judgment calls:

(a) **`data_path` is now a config option** instead of the hardcoded
    `"/root/handshakes"` string that appeared twice in the original
    (once in `on_loaded`, once in `on_ready`). The default value is
    still `/root/handshakes`, so out-of-the-box behavior for anyone
    who doesn't touch `config.toml` is unchanged. `on_ready`'s rsync
    source is now `self._opt("data_path")` instead of a second
    hardcoded copy of the same string.

(b) **Every `os.system(...)` call in `on_ready`/`on_unload` is now
    wrapped in its own try/except** (`_safe_system(command,
    description)`) - one failing step (e.g. `/boot/custom_plugins` not
    present on some installs despite the `os.path.exists` guard racing
    with something else, or a permissions error on `/boot`) logs a
    clear warning and lets every subsequent step still run, instead of
    an unexpected exception from `os.system` (rare, but possible on
    some platforms/argument combinations) being able to take down
    plugin load/unload entirely. This is tested directly: a test
    forces the very first `os.system` call to raise `OSError` and
    confirms all 5 subsequent-and-current calls (rsync, log copy, `mv`,
    `rm -rf`, config.toml copy) still get attempted and `on_ready`
    itself never raises.

(c) **A new boolean option `sync_to_boot`** (default `true`,
    preserving the original's always-on behavior exactly) lets a user
    set it `false` in `config.toml` to skip all of `on_ready`'s
    boot-sync behavior entirely, if they don't want the rsync/log/
    custom_plugins dance running on every boot. This is a pure opt-out
    gate at the very top of `on_ready` - when `false`, none of the 5
    `os.system` calls happen at all (also directly tested).

## What's new - the 5 approved features (of the original 7-item list)

The user was presented 7 possible improvement ideas and explicitly
approved only items 1, 2, 4, 5, and 7. Items 3 (HTTP Basic Auth) and 6
("since last check" delta tracking of new handshakes) were **not**
approved and are **not built here** - see "Not built this round"
below. This is a deliberate scope limit stated up front, not something
discovered as out-of-scope partway through.

1. **A real JSON status endpoint** (`/status.json`) -
   `build_status_payload(records)` is the single choke point that
   builds: `handshake_count`, `total_size_bytes` (summed across every
   capture), `most_recent_capture` (ISO-8601 timestamp of the newest
   file's mtime, or `null` if there are none), `capture_names` (a flat
   list of each capture's sanitized display name - see "What
   'SSID/BSSID list' means" below), and `captures` (the full per-file
   detail list, each with `name`, `filename`, `size_bytes`,
   `captured_at`, and `download_url`).

2. **A simple HTML status page** (`/`) as a phone-friendly alternative
   to raw JSON - summary numbers at the top, a banner showing the
   exact reachable URL (feature 4) and a loud "no authentication"
   notice, and a table of every capture with a download link. The same
   route also accepts `?format=json` and, via `_render_index()`
   checking `request.args.get("format")` before rendering HTML,
   returns the exact same `jsonify(payload)` response the dedicated
   `/status.json` endpoint returns - both routes go through the same
   `_current_status()` method, so there is exactly one code path that
   builds the payload, guaranteeing they can never drift apart. This
   is tested directly: a real HTTP request to `/status.json` and
   another to `/?format=json` are compared for byte-for-byte-equal
   parsed JSON.

3. *(This is item 4 in the original numbering - see the task's own
   list. Numbered here to match "the 5 approved features," not the
   original 7-item numbering, to avoid confusion.)*
   **`bind_scope`**, adapted from `web2ssh_ng.py`'s exact established
   implementation (`resolve_bind_plan()`/`detect_tailscale_ip()`/
   `get_local_lan_ip()` - reproduced here rather than imported across
   suites, since each suite in this repo is standalone) with the same
   4 values and semantics:
     - `"auto"` (default) - Tailscale IP if detected, else `127.0.0.1`
       with an explanatory warning.
     - `"tailscale"` - requires detection; fails safe (refuses to bind
       any socket) if not found.
     - `"localhost"` - always `127.0.0.1`.
     - `"lan"` - `0.0.0.0`, explicit opt-in, loud warning every start
       (this plugin's `"lan"` warning explicitly also mentions there is
       no authentication at all, since that's a materially bigger deal
       here than for `web2ssh_ng.py`, which at least has Basic Auth).
   Whichever scope is used, the URL is logged and shown as a banner at
   the top of the HTML page. The Flask app is served via
   `werkzeug.serving.make_server(...)` in a background daemon thread -
   `on_loaded()` starts the thread and returns immediately (never a
   blocking `.run()`/`.serve_forever()` call directly), and
   `on_unload(self, ui)` calls the server's `.shutdown()` (then
   `.join()`s the thread and calls `.server_close()`) to stop it
   cleanly. This is tested end-to-end: a real `make_server` instance is
   started on an ephemeral port, real HTTP requests are made against
   it, and `on_unload` is confirmed to actually close the port
   (`wait_for_port_closed`).

4. **List/download individual handshake capture files**
   (`/download/<filename>`) - `.pcapng` files only, each with
   filename/size/capture-time shown on the status page and a simple
   download link. **Deliberately scoped down** compared to the
   already-existing `handshakes_dl_ng.py` suite - see "Scope vs.
   handshakes-dl-suite" below. Downloads are served through
   `resolve_download_target(data_path, requested_filename)`, the
   single choke point every download request passes through:
     1. `os.path.basename()` strips any directory components from the
        requested name (defeats `../`-style traversal regardless of
        how it's encoded in the URL, since even a URL-decoded `../`
        segment gets stripped to just its trailing component here).
     2. The result must end in `.pcapng` (rejects `notes.txt`,
        `/etc/passwd`'s basename `passwd`, etc.).
     3. The candidate path is resolved with `os.path.abspath()` against
        the configured `data_path` and checked to actually be inside
        it (`candidate == data_dir_abs or candidate.startswith(data_dir_abs + os.sep)`) -
        a defense-in-depth check on top of step 1, not redundant with
        it: it's what actually catches anything that could otherwise
        resolve outside `data_path` (e.g. a `data_path` itself
        containing `..` segments, or platform path-separator quirks).
     4. The caller (`_handle_download`) additionally checks
        `os.path.isfile(target)` before ever calling
        `send_from_directory` - a name that resolves validly but
        doesn't exist (or isn't a regular file) is also a 404.
   This function is unit-tested directly (no server needed) against:
   a real filename inside `data_path` (accepted), `../etc/passwd`
   (rejected), an absolute path outside `data_path` (rejected), a
   non-`.pcapng` name (rejected), a bare `..` (rejected), empty input
   (rejected), and a syntactically-valid-but-nonexistent name (still
   *resolves* correctly - existence is deliberately not this
   function's job, so it stays testable without a filesystem for the
   safety logic itself) - and again end-to-end through a real HTTP
   server: a real download succeeds with the exact expected bytes, a
   URL-encoded traversal attempt and a request for a nonexistent file
   both get a genuine 404, and a request for a non-`.pcapng` filename
   also 404s.

5. **Live handshake count on-screen** - `on_ui_setup(self, ui)` /
   `on_ui_update(self, ui)` copied from `sigstr_ng.py`'s exact
   `LabeledValue` convention: configurable `ui_position_x`/
   `ui_position_y` (with the same negative-x-means-"from the right
   edge" behavior, clamped to stay on-screen), a `label` option,
   `BLACK` color, `fonts.Bold`/`fonts.Medium`. The data directory is
   only re-scanned once per `ui_refresh_interval` seconds (default 30
   - handshake counts change far less often than something like signal
   strength), tracked as a plain timestamp comparison
   (`self._last_ui_scan_ts`), not a re-glob on every single UI tick.
   The element is removed in `on_unload` via `ui.remove_element(...)`,
   inside a try/except (mirroring `sigstr_ng.py`'s `except KeyError`
   for "element was never added," wrapped in a broader `except
   Exception` per this task's explicit requirement so *any* UI
   teardown failure - not just a missing element - can't crash
   unload). Tested: setup/update don't raise, the UI value reflects the
   real scanned count, a second update within the refresh interval does
   *not* re-scan even when a new file appears on disk, forcing the
   interval to have elapsed *does* trigger a re-scan, and a
   `RuntimeError` raised from a mocked `ui.remove_element` is swallowed
   without `on_unload` itself raising.

## What "SSID/BSSID list" actually means here

Like `handshakes_dl_ng.py`'s own `_collect()` method, this plugin
treats the whole sanitized capture-file basename (minus `.pcapng`) as
the capture's display name/identifier, everywhere - the status page,
the JSON payload's `capture_names`/`captures[].name`, and download
links. It deliberately does **not** attempt to parse a strict
`SSID_BSSID`-shaped split back out of the filename: pwnagotchi capture
filenames vary in format across configurations/versions, and there's
no delimiter that's guaranteed safe to split on for every real
filename that shows up in practice. This keeps the display consistent
with the rest of this repo's suites rather than inventing a second,
potentially-fragile filename-parsing scheme just for this plugin.

## Scope vs. `handshakes-dl-suite` (deliberate, explained)

`handshakes_dl_ng.py` (already in this repo) already does a
fuller-featured job of the "list and download my handshakes" idea:
size/date/sort, per-file download links, companion hash-file
(`.2500`/`.16800`/`.22000`) and GPS-file (`.gps.json`/`.geo.json`)
surfacing, a size cap (`max_results`), and a bulk
"download all as ZIP" button - served through the pwnagotchi's own
shared web UI (`on_webhook`, reachable at
`/plugins/handshakes_dl_ng/`).

This plugin's own file listing/download (feature 5 above) is
**deliberately scoped down**, not a duplicate:
- Only the raw `.pcapng` capture files themselves - no hash-file or
  GPS-file surfacing (that stays `handshakes_dl_ng.py`'s job, per the
  task's explicit instruction).
- No ZIP bulk-download button - one capture at a time.
- Served through **this plugin's own separate dedicated server**
  (its own port + independent `bind_scope`), not through the shared
  pwnagotchi web UI. That's the actual point of building this here at
  all rather than just pointing people at `handshakes_dl_ng.py`: it's
  a channel that can be reached (or deliberately *not* reached, via
  `bind_scope = "localhost"`/`"tailscale"`) completely independently of
  whether the main pwnagotchi web UI is up, reachable, or exposed the
  way you want it to be - "a no-SSH-needed, separately-network-
  restrictable channel," as the task put it, distinct from the
  fuller-featured shared-web-UI page that already exists for the same
  underlying files.

Both plugins can run side-by-side without conflict - they read the
same on-disk `.pcapng` files but never write to or coordinate with
each other, and they're independently enableable/configurable.

## No authentication - the explicit tradeoff

Unlike `web2ssh_ng.py` (which refuses to start without real, non
-placeholder credentials), **this plugin's dedicated server has no
authentication of any kind.** This was a deliberate, explicit scope
decision for this round, not an oversight - see "Not built this round"
below.

That means **`bind_scope` is the only access control this plugin has.**
Anyone who can reach the bound address can:
- See the handshake count and the full list of captured
  network names (via `/`, `/status.json`, or `/?format=json`).
- Download every raw `.pcapng` capture file (via `/download/<name>`) -
  which can potentially be used offline to attempt to crack the WiFi
  password of networks you've captured a handshake for.

`bind_scope = "auto"` (the default) or `"tailscale"` keeps this off
the open LAN in the common case (falling back to localhost-only, or
refusing to start, respectively, if Tailscale isn't available) -
`"lan"` is an explicit, loudly-logged opt-in that a user has to choose
on purpose, with a warning that specifically calls out the lack of
authentication (not just the generic "reachable by your LAN" warning
`web2ssh_ng.py`'s equivalent gives, since that plugin at least still
has Basic Auth behind it). This tradeoff is documented in both
`config.toml` and `README.md` as instructed.

## Not built this round

Of the original 7 improvement ideas presented, 5 were approved (1, 2,
4, 5, 7 in the task's own numbering) and are covered above. Two were
explicitly **not** approved:

- **Item 3: HTTP Basic Auth.** A real, available idea for a future
  round (the pattern already exists in this exact repo -
  `web2ssh_ng.py`'s `validate_credentials()`/`_check_credentials()`
  using `hmac.compare_digest` could be adapted here close to
  verbatim) - not built this round because it was explicitly not
  approved. Building it anyway, or adding *any* form of authentication
  to this plugin on the assumption "well, it clearly should have some"
  would have gone beyond what was actually asked for, so it was left
  out entirely, on purpose - see "No authentication" above for the
  tradeoff this leaves in place instead.
- **Item 6: "since last check" delta tracking.** The idea of tracking
  which handshakes are new since a client last checked in (e.g. a
  "seen" marker, or a `?since=<timestamp>` query parameter that only
  returns captures newer than that) is a reasonable future addition
  once feature 1's JSON endpoint exists to build it on top of, but was
  not part of this round's approved scope and isn't implemented here.
  `most_recent_capture`/each capture's `captured_at` in the current
  JSON payload give a caller enough to implement this themselves
  client-side in the meantime (compare against a timestamp they stored
  from a previous poll), but the server does no state-tracking of what
  a particular client has or hasn't seen before.

Neither of these is a bug or a gap discovered late - both were known,
presented options that the user chose not to approve for this pass.

## Testing

Run with (from this directory):
```
python3 tests/test_handshaker_ng.py
```
(`pytest` isn't installed in this particular sandbox - the test file
is written as a self-contained script with its own `check()`/pass-fail
harness, same as `web2ssh_ng.py`/`sigstr_ng.py`'s test files, so it
runs directly with plain `python3` wherever `pytest` isn't available.)

All tests pass as of this writing (`0 failure(s) out of test run`).
Covers, against the real installed Flask/Werkzeug (no mocking of the
HTTP layer itself) and the real `pwnagotchi.plugins` loader:

- **Registration**: the plugin registers under the real loader as
  `handshaker_ng` (confirming the file-basename-based registration
  fact), all required hooks exist, `load_data` is a real defined
  method (directly guarding against bug #1's exact failure mode -
  `AttributeError: 'HandshakerNG' object has no attribute
  'load_data'` - regressing), `scapy` is never imported and never
  declared as a pip dependency (bug #3), and only `*.pcapng` (never
  `*.pcap`) is ever globbed (the fork-fact/recurring-bug-class check).
- **`load_data`/`scan_handshakes()`**: correct count and record list
  from a temp directory of real fake `.pcapng` files, a planted
  `.pcap` (non-`.pcapng`) file and a non-capture file are both
  correctly ignored, records are sorted newest-first, names/filenames/
  sizes are correct, and a missing directory is handled gracefully
  (returns `[]`, never raises).
- **`build_status_payload()`**: correct `handshake_count`,
  `total_size_bytes` (summed), `most_recent_capture`, `capture_names`,
  and per-capture `download_url`s from real scanned records, plus the
  empty-list edge case (count 0, `most_recent_capture: None`,
  `total_size_bytes: 0`).
- **`resolve_download_target()`** (the traversal-safety function,
  unit-tested directly without a filesystem/server needed for the
  logic itself): accepts a real filename inside `data_path`; rejects
  `../` traversal, an absolute path outside `data_path`, a non-
  `.pcapng` filename, a bare `..`, and empty input; and correctly
  *resolves* (without checking existence, which is the caller's job)
  a syntactically-valid name that doesn't exist yet.
- **`resolve_bind_plan()`/Tailscale detection**, all 4 scopes, with
  `detect_tailscale_ip`/`get_local_lan_ip` mocked: `"tailscale"` with
  and without detection (success case and fail-safe refusal),
  `"localhost"` unconditionally, `"lan"` binding `0.0.0.0` with a
  warning that specifically mentions the lack of authentication, and
  `"auto"` both with and without detection, plus an unrecognized value
  never crashing.
- **A real end-to-end server test**: `on_loaded()` starts a real
  `make_server` instance on an ephemeral port without blocking; the
  status page renders and shows the reachable-at banner, the no-auth
  warning, a real captured network's name, and a download link; the
  dedicated JSON endpoint returns the correct payload; `?format=json`
  on the HTML route returns the byte-identical payload; downloading a
  real temp file returns the exact expected bytes; a URL-encoded
  traversal attempt and a request for a nonexistent file both get a
  genuine 404; a request for a non-`.pcapng` filename also 404s; and
  `on_unload` genuinely closes the port (confirmed by a real socket
  connection attempt failing afterward).
- **`on_ui_setup`/`on_ui_update`**: neither raises, the UI element
  gets added, the displayed value reflects the real scanned handshake
  count, a second update within `ui_refresh_interval` does not
  re-scan even when a new file appears, forcing the interval to have
  elapsed does trigger a re-scan and updates the displayed count, and
  a UI teardown failure (`RuntimeError` from a mocked
  `ui.remove_element`) doesn't propagate out of `on_unload`.
- **`on_ready`/`on_unload` boot-sync, with `os.system` mocked**: all 5
  of the original's `os.system` calls (rsync, log-to-start-log copy,
  `mv custom_plugins`, `rm -rf custom_plugins`, config.toml-to-/boot
  copy) still happen by default when `/boot/custom_plugins` exists;
  `sync_to_boot = false` skips all of them entirely (zero `os.system`
  calls); a failing first step (mocked to raise `OSError`) doesn't
  stop the other 4 from still being attempted and doesn't propagate
  out of `on_ready`; and `on_unload` still copies the log to
  `pwnagotchi-end.log`.

## Still open / needs real-hardware testing

- The rsync/log-copy/`/boot` paths in `on_ready`/`on_unload` have
  never run against a real pwnagotchi's actual `/boot` partition/
  filesystem layout - only against a mocked `os.system` in this
  sandbox (which has no `/boot` mount, no `pwnagotchi.log`, etc. to
  test against for real).
- Tailscale detection and the LAN-IP guess (`get_local_lan_ip()`) are
  the same best-effort logic already flagged as needing real-hardware
  verification in `web2ssh-suite`'s own NOTES.md - this suite inherits
  that same caveat since the logic is adapted from there.
- `data_path`'s real-world default (`/root/handshakes`) should be
  double-checked against whatever bettercap/capture-plugin
  configuration is actually in use on a given install - some setups
  may write handshakes elsewhere, in which case `data_path` needs to
  be set to match (this rebuild makes that a config option instead of
  a hardcoded assumption specifically so it can be corrected without
  editing the plugin file).

## Original config preserved

A real original config file for `handshaker.py` was found (exact match) at
`itsdarklikehell/pwnagotchi-plugins/configs/handshaker.toml`. Preserved verbatim in this suite's own folder as
`config.original.toml`, per the project's standing config-preservation requirement,
for reference/troubleshooting if the rebuilt `config.toml` above ever
needs comparing against the source.
