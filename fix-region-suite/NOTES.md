# Notes: FixRegionNG

## Why this one was a bugfix, not a redesign

`fix_region.py` does one small, useful thing (force the WiFi
regulatory domain via `iw reg set`, persisted across reboots with a
systemd service) and does it with real, source-verified bugs - not a
design that needed rethinking. The job here was to keep the design
(systemd-persistence-across-reboots, applied once immediately via a
one-time restart) and fix the implementation. See "What was preserved"
below.

## Bugs found in fix_region.py (all source-verified)

1. **Import-time `KeyError` crash risk.**
   ```python
   REGION = pwnagotchi.config["main"]["plugins"]["fix_region"]["region"]
   ```
   ran at MODULE IMPORT TIME - not inside `__init__`, `on_loaded`, or
   any method - directly indexing three levels of the config dict with
   no `.get()` fallback anywhere. A user who set `enabled = true` but
   left out `region` (a reasonable mistake: the original's own README
   only shows an example with both keys set, and its `__defaults__`
   dict *does* list `"region": "NL"` - but that dict is never actually
   consulted, because this fork's loader does a raw assignment onto
   `plugin.options` and never merges `__defaults__`, and this line
   doesn't even read `self.options`/`__defaults__` at all, it reads
   the raw global `pwnagotchi.config`) would hit `KeyError` while the
   module was still being imported - before a plugin instance even
   exists, at a point in the framework's `load_from_file()` /
   `importlib.util.spec_from_file_location` exec path where an
   exception is more disruptive than a normal per-hook exception (that
   path catches and logs; the module-level import failing is a
   different, earlier failure mode entirely). Fixed: **no config
   access happens at module import time at all** in the rebuild. The
   region is read via `_opt()` (see the framework-fact section below)
   and validated entirely inside `on_loaded`, with a documented,
   always-valid fallback default (`"US"`) and a real ISO 3166-1
   alpha-2 format check - two letters, case-normalized to uppercase -
   before the value is used for anything. An invalid value is logged
   as a clear error and never applied; the plugin refuses rather than
   guessing or crashing.

2. **Command injection surface via raw string concatenation +
   `os.system`.** Every shell interaction in the original was built by
   string concatenation and run through `os.system(...)`:
   ```python
   os.system("sudo iw reg set " + REGION)
   os.system("sudo systemctl enable network-fix")
   os.system("rm " + SERV_PATH)
   ```
   and the region string was written directly into the generated shell
   script via `NETFIX_SH + REGION`. Since `REGION` only ever comes from
   local `config.toml` (not request input, not anything network-facing)
   this was low real-world severity - but it's still bad practice
   worth fixing on its own merits, and the plugin does write a
   root-owned file that systemd later executes, which raises the bar
   for "be careful here" even without an external attacker in the
   picture. Fixed: **every** command in the rebuild runs via
   `subprocess.run([...])` with an argument list - `shell=True` and
   string-interpolated command lines are not used anywhere in this
   file. The region string written into the generated shell script
   content is validated against the same 2-uppercase-letter ISO
   alpha-2 pattern (`_normalize_region()`) both before it's used as a
   `subprocess.run` argument AND immediately before it's written to a
   file that systemd will later execute as root
   (`_apply_region`'s belt-and-braces re-check).

3. **Config changes silently ignored after the first load.**
   `on_loaded` only wrote the shell script / systemd service
   `if not os.path.exists(SH_PATH)` / `if not os.path.exists(SERV_PATH)`.
   Once those two files existed - which they do, permanently, after the
   very first successful load - changing `region` in config.toml and
   restarting pwnagotchi did **nothing at all**: the stale region stayed
   applied forever, with no error, no log line, nothing to tell the
   user why their change didn't take effect, until they manually found
   and deleted the generated files themselves. Fixed: a small JSON
   state file (`state_path`, default
   `/root/.fix_region_ng_state.json`) tracks `applied_region` (and
   `applied_at`) every time a region is actually applied.
   `on_loaded` compares the configured, normalized region against
   `state["applied_region"]` (and also confirms the script/service
   files still exist, in case they were deleted out from under the
   plugin) and only rewrites the files / re-runs `iw reg set` /
   restarts pwnagotchi when something has genuinely changed. This
   fixes both halves of the bug at once: a real config change now
   actually takes effect, AND a boot where nothing changed no longer
   triggers a needless extra restart (which the original's
   `if not os.path.exists(...)` gate accidentally also avoided, but
   only because it never re-checked anything at all, not because it
   was designed to detect "no change").

4. **`on_unload` unconditionally shelled out to remove/stop things
   that may not exist.**
   ```python
   os.system("rm " + SERV_PATH)     # no -f: errors to stderr if already gone
   os.system("rm " + SH_PATH)
   os.system("sudo systemctl stop network-fix")
   os.system("sudo systemctl disable network-fix")
   ```
   ran unconditionally on unload, regardless of whether the service was
   ever actually created/enabled (e.g. if `on_loaded` failed partway,
   or the plugin was toggled off before ever successfully applying a
   region). `rm` without `-f` on a missing file writes a "No such file
   or directory" error to stderr (visible in the pwnagotchi log as
   noise, not a crash, but still wrong), and stopping/disabling a
   systemd unit that doesn't exist likewise logs a spurious systemctl
   error. Fixed: file removal uses `os.remove()` wrapped in
   `try/except FileNotFoundError` (silently doing nothing if the file's
   already gone) instead of shelling out to `rm` at all. The systemd
   service is only stopped/disabled if `systemctl is-enabled
   network-fix` (checked via `subprocess.run`, never assumed from
   "the file exists") actually reports it as `enabled` - a real check,
   not a guess based on file presence alone.

5. **Bogus dependency declaration.** `__dependencies__` claimed
   `{"pip": ["scapy"]}`, but `scapy` is never imported or referenced
   anywhere in `fix_region.py` - this plugin has nothing to do with
   packet crafting/sniffing at all, it shells out to `iw`/`systemctl`.
   Removed entirely in the rebuild. The plugin's real dependency is the
   `iw` command-line tool, which is standard on every pwnagotchi image
   - documented as an `apt: ["iw"]` expectation (for completeness, not
   because it's normally missing) rather than a pip package, and called
   out explicitly in README.md so nobody goes looking for a `pip
   install scapy` step this plugin never needed.

## Framework facts this rebuild relies on

Same verified facts as `bluetooth-recon-suite`/`mad-hatter-suite` in
this repo (`pwnagotchi/plugins/__init__.py`, the cloned
`jayofelony/pwnagotchi` fork):
- `plugins.load()` does `plugin.options = config['main']['plugins'][name]`
  - a **raw assignment** of the parsed TOML table. `__defaults__` is
  never merged by the framework itself. This rebuild uses the same
  module-level `DEFAULTS` dict + `_opt()`-reader pattern as
  `bluetooth_recon_ng.py` (rather than `MadHatterNG.py`'s
  `self.options = dict(self.__defaults__)` approach) - a reasonable
  choice either way; this one was picked because it's the closer match
  in scope/size to `bluetooth_recon_ng.py`.
- `on_unload(self, ui)` requires the `ui` parameter - present in the
  rebuild's signature even though this plugin has no on-screen element
  and never touches `ui`.
- `pwnagotchi.plugins.loaded` is a plain `{plugin_name: instance}` dict,
  keyed by the plugin file's basename - used by the optional
  GPS-suggestion feature to look up `gps_tagger_ng` (this repo's own
  GPS suite) or a similarly-shaped sibling plugin, the same
  `plugins.loaded.get(name)` pattern `MadHatterNG.py` already uses to
  look up `apprise_notify_ng`/`discord_ng`.
- There is no `pwnagotchi.plugins.notify()` function in the real
  framework; this plugin never calls anything like it (the original
  didn't either).

## A naming note (adapted from MadHatterNG.py's - read this before
## renaming anything)

Unlike `MadHatterNG.py` (an explicit one-off capital-NG rename request
for that suite only), this suite follows the **same convention as
`bluetooth_recon_ng.py`**: the plugin file is `fix_region_ng.py`
(snake_case), and its config section is
`[main.plugins.fix_region_ng]` - matching the file's exact basename,
NOT the class name (`FixRegionNG`), and NOT the original's section
name (`fix_region`). This is a verified framework fact, not a
stylistic choice: `pwnagotchi/plugins/__init__.py` registers every
plugin under `loaded[<file basename, exact case, ".py" dropped>]`
(`load_from_path`/`load_from_file`/`Plugin.__init_subclass__`'s
`cls.__module__.split('.')[0]`), and `load()` looks up both the
`enabled` flag and the options table in `config['main']['plugins']`
under that SAME key - a class body's `__name__ = "..."` assignment (as
this rebuild's `__name__ = "FixRegionNG"` also has, purely cosmetic)
does NOT override `type.__name__` in Python and has zero effect on
this lookup. A config section spelled `fix_region` (the original's
name) or `FixRegionNG` (the class name) for a file named
`fix_region_ng.py` would never even appear in the `enabled` list, so
the plugin would silently never load at all - not a merge/options bug,
a total no-load. If you'd rather rename the file itself (e.g. back to
`fix_region.py`, or something else entirely), that's fine - just make
sure the config section header matches whatever you rename it to,
exactly, including case.

## What was preserved from the original design, and why

**Kept:** the core mechanism - a small root-owned shell script
(`/root/network-fix.sh`, `iw reg set <region>`) plus a systemd service
(`/etc/systemd/system/network-fix.service`) that re-runs it on every
boot, so the regulatory-domain change survives a reboot without
pwnagotchi needing to reapply it itself every time; and triggering one
`_thread.start_new_thread(restart, (self.mode,))` after first applying
a region, so the change takes effect immediately rather than requiring
the user to restart pwnagotchi by hand a second time. Both of these are
sound design decisions on the original's part - the bugs were entirely
in the *implementation* (raw config access, string-built shell
commands, no change detection, unguarded cleanup), never in this
overall approach, so neither was second-guessed or replaced here.

## What's new (all approved - the user approved "all improvements" for
## this plugin)

1. **Real ISO 3166-1 alpha-2 format validation** (`_normalize_region`,
   `REGION_RE = re.compile(r"^[A-Za-z]{2}$")`) - a real regex check, not
   just "doesn't crash". Covered in bug-fix #1 above.
2. **All shell interaction via `subprocess.run([...])` argument lists**
   - covered in bug-fix #2 above. No `shell=True`, no string-built
   command lines, anywhere in `fix_region_ng.py`.
3. **Current regulatory domain detected and logged before any change.**
   `_log_current_domain()` runs `subprocess.run(["iw", "reg", "get"], ...)`
   once in `on_loaded`, parses the `country XX:` line
   (`REG_GET_RE = re.compile(r"^country\s+([A-Za-z]{2}):", re.MULTILINE)`),
   and logs it - so there's a record in the pwnagotchi log of what the
   regulatory domain was *before* this plugin touched anything. The
   same live lookup (not cached - re-run fresh) is also shown on the
   `on_webhook` status page.
4. **Optional GPS-based region auto-suggestion**
   (`gps_region_suggestion`, off by default):
   - Looks up a GPS-providing sibling plugin via
     `pwnagotchi.plugins.loaded.get(name)` for each name in
     `GPS_SIBLING_PLUGIN_NAMES = ("gps_tagger_ng", "gps_tagger", "gps")`
     - `gps_tagger_ng` (this repo's own `GPSTaggerNG` suite) is the
     confirmed, real integration: its instance exposes
     `pn_gps_coords` (a `{"Latitude": .., "Longitude": ..}` dict, or
     `None`) and `gps_hot` (whether that fix is currently considered
     good) - read via `getattr`, never assumed to exist. The other two
     names are best-effort generic-attribute-name guesses
     (`lat`/`latitude`, `lon`/`longitude`) for some other GPS plugin
     that might be loaded instead; if none of the three names resolve
     to a loaded plugin, or none expose usable coordinates, this is a
     no-op.
   - A small, explicitly-not-exhaustive offline lat/lon -> ISO country
     code lookup (`GPS_COUNTRY_BBOXES`, ~24 entries: simple rectangular
     bounding boxes for the US, Canada, GB, Ireland, Netherlands,
     Belgium, Germany, France, Spain, Italy, Switzerland, Austria,
     Poland, Sweden, Norway, Denmark, Finland, Australia, New Zealand,
     Japan, Brazil, Mexico, India, South Africa) - checked in order,
     first match wins. **This is genuinely a small hand-built table,
     not a real geo database** - a real implementation of this feature
     would want a proper offline reverse-geocoding library (e.g.
     `reverse_geocode` or `geopy` with a local dataset) for actual
     border accuracy; that's deliberately not pulled in as a dependency
     here because the task explicitly called this a "nice to have",
     off-by-default, logged-suggestion-only feature, not something
     worth adding a new dependency for. A point near a shared land
     border or coastline can easily match the wrong neighboring
     country in this simple bounding-box approach - that's an accepted,
     documented limitation, not a bug, given what this feature is for
     (a rough hint in the log, never applied to anything).
   - **It NEVER auto-applies a region change.** The suggestion is
     purely informational - one `logging.info(...)` line (only when the
     GPS-suggested code differs from the configured one) plus a row on
     the webhook status page. The `region` config value is the only
     thing `_apply_region()` ever actually calls `iw reg set` with, and
     nothing in `_maybe_suggest_gps_region()` touches `_apply_region()`
     or any state file at all.
   - **Degrades silently by design.** The entire suggestion path is
     wrapped in `try/except Exception` (in addition to the individual
     `getattr`/dict-`.get()` guards inside it) - no GPS plugin loaded,
     no coordinates available, a malformed coordinate value, or any
     other lookup failure all result in a single `logging.debug(...)`
     line and `self._last_gps_suggestion = None`, never a crash and
     never something that blocks `on_loaded` from finishing and
     applying the actual configured region.

## Also added (matches this repo's established conventions)

- **`on_webhook` status page** (`/plugins/fix_region_ng/`), plain HTML
  with every interpolated value passed through `html.escape()`: the
  configured region (and whether it's valid), the last-applied region
  and when, the live `iw reg get` domain, whether the persistence
  service is installed and separately whether it's actually enabled
  (via `systemctl is-enabled`, not inferred from file presence), and -
  when it fired - the GPS-suggested region.
- **`enabled = false` shipped by default** in config.toml, matching
  this repo's established safety convention (every suite here ships
  disabled so the user opts in deliberately) - a change from the
  original's own `config.toml` example, which shipped `enabled = true`.
- **`region = "US"` as the shipped default value** - chosen because
  it's a safe, always-parses-as-valid placeholder (rather than e.g. an
  empty string, which would fail validation and log an error on a
  completely untouched config), not because it's a recommendation for
  where any particular user actually is; the header comment above it in
  config.toml says so explicitly and tells the user to set their real
  location's code. This is a deliberate change from the original
  config's `region = "NL"` default - "US" was picked to match this
  plugin's shipped fallback in `DEFAULTS["region"]`, keeping the
  config.toml example and the code's own fallback consistent with each
  other rather than having them silently disagree the way the original
  effectively did (`__defaults__["region"] = "NL"` in the class body,
  vs. the module-level `REGION` line that never consulted it at all -
  see bug-fix #1).
- **A full explanatory header comment in config.toml** covering: (a)
  that this plugin writes root-owned systemd files and runs commands as
  root via sudo/systemctl - inherent to what regulatory-domain-setting
  requires, not a bug; (b) the exact file-basename/config-section-name
  requirement (see the naming note above); (c) what `iw reg get`
  output looks like, so the user can sanity-check manually without
  reading this file's source.

## Testing

Run with (from this directory):
```
python3 -m pytest tests/test_fix_region_ng.py -v
```
(Matching the command documented by `bluetooth-recon-suite`/
`mad-hatter-suite`'s own NOTES.md. `pytest` isn't installed in this
particular sandbox - the test file is written as a self-contained
script with its own `check()`/pass-fail harness, same as
`bluetooth_recon_ng.py`'s test file, so it also runs directly with
`python3 tests/test_fix_region_ng.py` wherever `pytest` isn't
available; both invocations execute the exact same assertions.)

Covers:
- Real plugin registration as a `pwnagotchi.plugins.Plugin` subclass
  under the file-basename key `fix_region_ng` (the naming-convention
  fact this whole suite is built around).
- Region-format validation (`_normalize_region`): valid 2-letter codes
  (including lowercase, normalized to uppercase), and invalid values
  (empty string, one letter, three letters, digits, `None`, a
  non-string) all correctly rejected.
- No shell command is ever built via string interpolation: `iw reg
  set`, `systemctl enable/start/stop/disable/is-enabled`, and `iw reg
  get` are all verified (with `subprocess.run` mocked out) to be called
  with a real argument **list**, and never with `shell=True`.
- The "only reapply if the region actually changed" logic (bug-fix #3):
  a fresh `on_loaded` with no prior state writes the script/service and
  applies the region and restarts exactly once; a second `on_loaded`
  with the same configured region and the files still present does
  neither (no re-write, no re-apply, no restart); changing the
  configured region between the two does trigger a fresh apply.
- `on_unload`'s existence-guarded cleanup (bug-fix #4): removing files
  that exist works; calling `on_unload` when nothing was ever created
  doesn't raise and doesn't call `systemctl stop`/`disable` at all
  (`systemctl is-enabled` mocked to report not-enabled/not-found).
- The webhook status page renders without crashing, includes the
  expected labeled rows, and HTML-escapes interpolated values (no raw
  `<script>` tag survives for a maliciously-crafted region/state value).
- The GPS-suggestion feature: firing correctly (right suggested code,
  right log line) with a stub GPS-providing plugin present in
  `pwnagotchi.plugins.loaded` with in-bounding-box coordinates: a
  match not equal to the configured region logs the suggestion, a
  match equal to the configured region doesn't; and silently doing
  nothing (`_last_gps_suggestion` stays `None`, no exception) with no
  GPS plugin loaded, a loaded plugin with no/`None` coordinates, and
  coordinates outside every known bounding box.
- An invalid configured `region` is refused (no files written, no
  `iw`/`systemctl` calls made, no restart triggered) and logged as an
  error.

## Still open / needs real-hardware testing

- The `network-fix.service`/`iw reg set` mechanics are carried over
  unchanged in design from the original (which is itself reportedly
  in real-world use upstream) - this sandbox has no real WiFi radio to
  confirm `iw reg set` actually changing the live regulatory domain
  end-to-end, or that channels 12/13 actually appear in `iwlist wlan0
  channel` output afterward. The new change-detection logic and
  guarded `on_unload` are unit-tested against mocked `subprocess`
  calls, not a real `systemctl`/`iw` on real hardware.
- The GPS-suggestion bounding-box table's accuracy is exactly what's
  documented above - a small, explicitly-approximate rectangle-based
  lookup, not verified against a real GPS fix in this sandbox (no GPS
  hardware available here either).
