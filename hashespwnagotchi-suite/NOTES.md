# Research notes: HashesPwnagotchiNG

Source: `hashespwnagotchi.py` (itsdarklikehell/pwnagotchi-plugins,
originally meow@hashes.pw). Reviewed as part of the `test-plugins`
Cluster 31 (cloud-crack-upload destinations, revisiting Group 19).

## The security issue (why this plugin was not safe to run as shipped)

`_writeEAPOL()`/`_writePMKID()` built `hcxpcapngtool` commands with
Python string formatting and ran them via `subprocess.getoutput()`
(which runs the string through `/bin/sh -c`). `_repairPMKID()`'s
fallback path was worse: it built a `tcpdump ... | sed ...` command with
raw string **concatenation** (not even `.format()`) and ran it with
`subprocess.check_output(..., shell=True)`.

Confirmed via the fork's own documented capture-file naming convention
(`{ESSID}_{BSSID}.pcapng`) and this plugin's own `_essid_from_path()`/
`_bssid_from_path()` helpers (which assume exactly that shape) that the
AP's ESSID ends up directly in the filename these commands are built
from. An ESSID is attacker-controlled - any device can broadcast any
SSID string, including shell metacharacters, with zero authentication.
A maliciously-named AP could therefore inject arbitrary shell commands,
executed as root, the moment the pwnagotchi captured a handshake from it
and this plugin tried to convert the file.

**Fix**: every external command now runs via `subprocess.run([...],
shell=False)`, with each argument (including any filename) passed as
its own list element. Nothing is ever handed to a shell for
interpretation, so there is no injection surface regardless of what an
AP names itself. The `tcpdump | sed` pipeline was replaced with `tcpdump`
run directly (no shell) plus the same BSSID/name extraction done with
Python's `re` module - identical result, no shell, no pipe, nothing to
inject into.

## Other bugs found (source-verified)

1. **`on_config_changed()` referenced `self.status`**, which is never
   assigned anywhere in the class - only `self.report` (the actual
   `StatusFile` instance) is. Setting the `interval` option in
   config.toml made this method raise `AttributeError` every single
   time it ran, which silently killed it before it could log anything
   or run the startup batch-conversion pass. Without `interval` set,
   Python's `or` short-circuit evaluation hid the bug entirely (the
   right-hand `self.status.newer_then_hours(...)` expression never got
   evaluated) - so the bug only appeared for anyone actually trying to
   use the option meant to make this more efficient. This is likely the
   deeper reason the batch pathway "never worked" even before
   considering the `.pcap`/`.pcapng` issue below - anyone who set
   `interval` would never even reach the buggy filter.

2. **The batch-conversion scan filtered for `.pcap`** - this fork only
   ever writes `.pcapng`. Fixed throughout with `os.path.splitext()`
   (see #3 below, same fix covers both).

3. **Filename/extension parsing used `path.split(".")[0]`** in several
   places - this takes everything before the *first* dot in the whole
   string, not the real extension. An ESSID containing a period (legal:
   "Motorola.5G" is a real, common pattern for dual-band routers) would
   break this. Fixed with `os.path.splitext()`, which splits at the
   *last* dot, everywhere a "no extension" path was needed.

4. **Whitelist exclusion was present in the source but commented out**
   (`# handshake_paths = remove_whitelisted(...)`) - unlike every
   sibling plugin in the original Cluster 6/19 review, this meant once
   the other bugs were fixed, it would upload every captured handshake
   with zero exclusions. Re-enabled.

5. **A Python-2-only `.encode("hex")` call** in `_repairPMKID`'s
   dict-based branch (used when a full AP dict is available, i.e. from
   `on_handshake`) - this codec doesn't exist in Python 3 and raises
   `LookupError`. The function's own fallback branch (used when no AP
   dict is available) already does this correctly a few lines later
   (`.encode().hex()`) - the first branch just never got the same fix
   when the rest of the file was ported to Python 3. Made consistent.

6. **`self.skip` was permanent, with nothing ever clearing it** - one
   transient upload failure (a network blip, a momentary hashes.pw
   outage) permanently blacklisted that handshake from ever being
   retried again until the whole plugin (and pwnagotchi) restarted.
   Fixed with bounded retries: a handshake is retried up to
   `max_upload_attempts` times (with `upload_retry_delay` seconds
   between attempts) before finally being skipped for the rest of that
   run.

7. **`_validate_or_fetch_token()` read `response["token"]` with bare
   indexing** - if hashes.pw's API ever returned a differently-shaped
   response (an error body, a future API version), this would raise an
   uncaught `KeyError` instead of the `ValueError` the surrounding
   upload code already knows how to handle and log cleanly. Fixed to
   use `.get()` and raise that same, already-handled exception type.

## What changed in this rewrite (summary)

All seven fixes above, plus:
- `hcxpcapngtool_timeout` / `upload_timeout` config options (the
  original had no timeout on either external call or HTTP request).
- A startup check for `hcxpcapngtool` on `PATH`, logged clearly at
  `on_loaded()` instead of failing confusingly on first real use.
- Bounded upload retries (fix #6 above), configurable.

Deliberately unchanged: the overall shape of the plugin (local
conversion via `on_handshake` for new captures, a batch catch-up pass on
config change, upload via `on_bored`/`on_internet_available`, GPS
coverage reporting for captures that couldn't be converted at all) - all
of that logic was sound, it just needed the bugs above fixed.

## Testing done (sandbox, no real hardware)

18 tests in `tests/test_hashespwnagotchi_ng.py`, run against the REAL
cloned jayofelony framework (`pwnagotchi.plugins`, confirmed genuine
`Plugin.__init_subclass__` registration - not a mock). `hcxpcapngtool`
and `tcpdump` themselves aren't installed in this sandbox, so every test
involving them mocks `subprocess.run` at the boundary and asserts on
*how* it would have been called (a real list, never `shell=True`, the
exact argv) rather than on real tool output - this is exactly the right
boundary for proving the security fix, since the vulnerability was
entirely about *how the command gets built and run*, not what
`hcxpcapngtool` itself does with valid input.

Specifically covered: a crafted "ESSID" containing `$()`, backticks, and
a semicolon is proven to reach `subprocess.run` as a single, literal
argv element, never as shell-interpreted text; `on_config_changed` no
longer raises when `interval` is set; the batch scan finds `.pcapng` and
ignores `.pcap`; a dot inside an ESSID doesn't break extension handling;
the whitelist is actually applied; the Python-2 `.encode("hex")` path no
longer raises; a failed upload is retried before being permanently
skipped, and is skipped only after `max_upload_attempts` real failures;
and a malformed token response raises `ValueError`, not `KeyError`.

Only `prctl` and `tomlkit` (both native/pure-Python dependencies this
sandbox can't install, and both untouched by anything this plugin
actually calls) are stubbed - `pwnagotchi.plugins`, `pwnagotchi.utils`
(`StatusFile`, `remove_whitelisted`), and `requests` are all the real
things.

## Still open / needs real-hardware testing

- The hashes.pw account/API side is entirely untestable from here - a
  valid `api_key`, the exact current API shape, and whether uploads are
  actually accepted all need a real run against the live service.
- `hcxpcapngtool`/`tcpdump`'s actual output on a real capture (as
  opposed to the mocked subprocess boundary used in these tests) hasn't
  been exercised - worth watching the logs on the first few real
  handshakes after install.
- The PMKID-repair tcpdump fallback path in particular is the least
  commonly hit code path (it only runs when a raw PMKID was extracted
  but no AP name info was available at all) - worth keeping an eye on
  if you ever see a "PMKID could not be repaired" log line.

## Config verified against upstream (2026-09-28)

Compared `config.toml` against the real upstream sample
(`itsdarklikehell/pwnagotchi-plugins/configs/hashespwnagotchi.toml`,
which just sets `enabled = true`) and the original's `__defaults__`
(`enabled`, `api_key` only - everything else upstream was either
hardcoded or missing entirely). Checked every `self.options.get(...)`/
`self.options[...]` call in `hashespwnagotchi_ng.py`: `api_key`,
`api_url`, `whitelist`, `interval`, `hcxpcapngtool_timeout`,
`upload_timeout`, `max_upload_attempts`, `upload_retry_delay` are all
present and documented. Renamed from `config.toml.example` to
`config.toml`.
