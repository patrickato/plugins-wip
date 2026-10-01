# Notes: CrackPipelineNG

## What this merges, and why as one suite

Four plugins, all authored by `patrickato` (the repo owner) for his own
fork - real, working plugins he wrote himself, not upstream community
plugins:

- `ClaudeCrackAuto.py` (`test-plugins/pwnagotchi-plugins/claudecrackauto/`)
- `RuleMutationCrack.py` (`test-plugins/pwnagotchi-plugins/rulemutationcrack/`)
- `HashFormatDetector.py` (`test-plugins/pwnagotchi-plugins/hashformatdetector/`)
- `best_quickdic.py` (`test-plugins/pwnagotchi-plugins/best_quickdic/`)

Because all four are the repo owner's own work, there is **no "original
community config" to preserve** in the `config.original.toml` sense used
elsewhere in this repo (e.g. `handshaker-suite`, `apprise-notify-suite`) -
this suite deliberately does not ship one. This paragraph is that
documentation, as instructed.

They were merged, rather than kept as four separate plugins, because:

1. All four do some variant of "convert a handshake with hcxpcapngtool, then
   run hashcat" - keeping them separate meant up to four independent
   `threading.Thread`s (and four independent `threading.Lock`s) potentially
   firing hashcat/hcxpcapngtool subprocesses concurrently against the same
   handshake-producing event, fighting over one Pi's CPU. See bug #6 below.
2. Three different, inconsistent names for essentially the same "allowlist
   of networks I'm allowed to act on" concept (`whitelist`, `targets`, and no
   concept at all in the other two) - a real support/consistency problem on
   its own even before getting to the next point.
3. **One of the four (`best_quickdic.py`) had no allowlist at all**, and its
   own docstring argued this was fine. Merging was the cleanest way to
   guarantee the fix (a single shared gate) actually applies to every code
   path that invokes hashcat, rather than patching that one file in
   isolation and hoping a future similar plugin doesn't reintroduce the same
   gap.

## THE MOST IMPORTANT FIX: best_quickdic's missing scoping

`best_quickdic.py`'s docstring states:

> Not SSID-scoped. It never touches the radio or targets a network - it only
> ever operates on a handshake file that's already been captured and is
> sitting on disk. That's pure local post-processing, not active behavior
> against any network, so it doesn't carry the same "own SSIDs only" gating
> that active attack/assoc/deauth plugins need.

**This reasoning does not hold, and was overridden rather than preserved.**
A plugin that automatically runs hashcat - a real password-cracking attack -
against the handshake of every single network a pwnagotchi happens to pick
up, with zero regard for whether the user owns that network, is an
unauthorized password-cracking attempt against other people's networks. The
fact that it operates on a file already sitting on disk rather than
transmitting radio frames does not change what the *action* is: attempting
to recover someone else's WiFi password without authorization. The
"active behavior against a network" framing conflates *how a plugin
interacts with the radio* with *what the plugin's output is used for or
against* - a distinction that matters for e.g. a passive packet logger, but
not for a plugin whose entire purpose is attempting to recover a specific
network's password.

**The fix, applied in this merge:** the exact same `authorized_networks`
allowlist (same name, same semantics, same empty-by-default behavior as
`wifi-jammer-suite`'s `wifi_jammer_ng.py` in this repo) gates every actual
hashcat invocation in the merged pipeline - the plain wordlist pass AND the
rule-based mutation pass, with no exceptions and no "but this part is just
local file processing" carve-out anywhere in the code. An empty
`authorized_networks` (the default) means hashcat is **never** invoked by
this plugin, against anything, full stop. Classification (`.route` sidecar
writing, from `HashFormatDetector`'s own unscoped-by-design behavior) still
runs for every capture - that step is read-only and genuinely doesn't run
hashcat or any other cracking tool, so it was left unscoped, matching
`HashFormatDetector`'s original intent for *that specific step only*. The
allowlist gate sits strictly between classification and the actual
hcxpcapngtool/hashcat invocations.

If you are reading this because you're extending this plugin or adding a
similar one: do not reintroduce an unscoped-by-design cracking path on the
theory that "it's just local post-processing." That reasoning was examined
here and explicitly rejected.

## Bugs found in the four originals (all source-verified)

### 1. `access_point` bare-MAC-string crash (all four)

Every one of the four originals accessed the AP argument like this:
```python
ssid = (access_point or {}).get('hostname', '') or ''
```
This fork's `agent.py` can call `on_handshake` with a bare MAC string
instead of a full dict for `access_point` when it can't match the AP in the
live bettercap session at that exact moment (same confirmed behavior
`wifi-jammer-suite`'s `wifi_jammer_ng.py` already documents and handles via
its own `_as_ap_dict()`). `(access_point or {})` only protects against
`access_point` being `None` or falsy - a non-empty bare string is still
truthy, so `.get()` is called directly on a `str`, raising `AttributeError`
and silently dropping the handshake entirely (the whole `_process`/
`on_handshake` call aborts with the exception swallowed by this fork's
per-plugin event-queue worker, which just logs and moves on).

**Fixed** with this plugin's own `_as_ap_dict()`, directly modeled on
`wifi_jammer_ng.py`'s version, applied to every single AP-argument read in
this merged plugin (classification, allowlist matching, path building).
Tested directly (`_as_ap_dict` unit tests, plus an end-to-end
`on_handshake` call with a bare-string AP confirming no crash and correct
"unknown" classification).

### 2. Untrusted SSID flowing into filesystem paths (RuleMutationCrack, best_quickdic)

```python
# RuleMutationCrack.py
hc_path = os.path.join(self.export_dir, f"{ssid}_{int(time.time())}.hc22000")
# best_quickdic.py
hc_path = os.path.join(self.export_dir, f"{ssid}_{int(time.time())}.hc22000")
```
Both built an export path directly from the raw SSID string, with zero
sanitization. A WiFi SSID is an arbitrary, attacker-settable (or just
weird/malformed) string up to 32 bytes - nothing stops a nearby AP from
being named something containing `/`, `..`, or other path-meaningful
characters. `HashFormatDetector.py`'s own BSSID-based naming
(`bssid.replace(':', '')`) is incidentally reasonably safe since it only
ever operates on a MAC-shaped string, but that's not true of the two
SSID-based originals above. `ClaudeCrackAuto.py` was the only one of the
four that actually sanitized anything (`_safe_name()`, stripping to
alnum-or-underscore).

**Fixed** by reusing `ClaudeCrackAuto`'s exact `_safe_name()` approach for
every SSID-derived path component in this merged plugin - there is exactly
one place in the whole file that builds an export filename from an SSID
(`_process_job`'s `hc_path` construction), and it always goes through
`_safe_name()` first. Tested directly: a malicious SSID
(`"../../etc/passwd"`) is confirmed to never let the resulting path escape
`export_dir` (`os.path.commonpath` check against the real built path).

### 3. `os.makedirs` crash on an empty dirname (all four)

```python
os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
```
in all four originals' `on_loaded`. `os.path.dirname("results.log")` (a
`log_file` with no directory component - a very plausible user config
mistake, or even a reasonable choice if someone wants the log next to the
plugin) returns `""`, and `os.makedirs("", exist_ok=True)` raises
`FileNotFoundError`. Combined with the framework fact below, this silently
aborts the rest of `on_loaded` - including, in every one of the four
originals, the line `self.ready = True` that came after it. The plugin
loads, logs one easy-to-miss exception (this fork's per-plugin
`on_loaded`-runner just does `logging.exception(...)` and moves on - see
`pwnagotchi/plugins/__init__.py`'s `run_once()`), and then never does
anything else, ever, with `self.ready` permanently `False`.

**Fixed**: every `os.makedirs(dirname, ...)` call in this merged plugin is
guarded with `if dirname:` before calling it. More importantly, `on_loaded`
itself is restructured so each independent setup step (loading
`authorized_networks`, creating `export_dir`, creating `log_file`'s
directory, checking for `hcxpcapngtool`/`hashcat` on PATH, starting the
worker thread) is in its own `try`/`except` that logs a warning on failure
and lets every subsequent step still run - matching this repo's established
"one failing step shouldn't take the rest down" pattern (see
`handshaker-suite`'s `_safe_system()`/per-step-try-except approach for the
same idea applied to `os.system` calls). `self.ready = True` is set
**unconditionally** at the very end of `on_loaded`, regardless of which
individual steps above it succeeded. Tested directly: a `log_file` with no
directory component (`"results.log"`, run from a temp cwd) confirms
`on_loaded()` doesn't raise and `self.ready` ends up `True`.

### 4. ClaudeCrackAuto's `on_ui_setup` bug (font-as-color, hardcoded float position)

```python
ui.add_element('claudecrackauto', LabeledValue(
    color=fonts.Small,                       # a FONT where a COLOR belongs
    ...
    position=(ui.width() / 2 + 10, 0),       # hardcoded, and '/' => a float
    ...
))
```
Two separate bugs in one call: `color=` was given a font object
(`fonts.Small`) instead of a real color constant - every other suite in this
repo correctly imports `BLACK` from `pwnagotchi.ui.view` and passes
`color=BLACK`. Separately, the element's position was a hardcoded,
non-configurable expression using `/` (true division), which produces a
`float` - this repo's convention is always plain `int` pixel positions, and
this was never made overridable the way every other suite's on-screen
element position is.

**Fixed**: this merge's single on-screen element uses the real `BLACK`
import, and `position_x`/`position_y` config options (defaulting to the
plain-int `(0, 150)`, checked against this repo's other suites' own default
positions at the time this was built - see the `DEFAULTS` dict's comment in
`crack_pipeline_ng.py` for the full list checked).

### 5. No `on_config_changed` in any of the four

None of the four originals could reload their allowlist (`whitelist` /
`targets` / none at all) without a full plugin or process restart.

**Fixed**: `on_config_changed(self, config)` calls the same
`_load_targets()` helper `on_loaded` uses, exactly matching
`wifi_jammer_ng.py`'s own pattern. Tested directly: a target added to
`authorized_networks` after load, followed by `on_config_changed`, is
picked up without restarting anything.

### 6. Resource contention - no shared worker queue

Each of the four originals spun up its own `threading.Thread` per matching
handshake event (`ClaudeCrackAuto`, `RuleMutationCrack`, `best_quickdic`) or
ran its classification synchronously inline (`HashFormatDetector` - cheap
enough that this was fine for that one specific plugin, but not a pattern
that generalizes to the cracking steps). If more than one of these four
plugins were ever enabled at once - or even just two handshakes landing
close together under one of them - nothing prevented multiple concurrent
`hashcat`/`hcxpcapngtool` subprocesses from running simultaneously on one
Pi's CPU, each slowing the others down and potentially the whole device.

**Fixed architecturally**, not just patched: a real single-worker job queue,
matching `apprise_notify_ng.py`'s own `queue.Queue()` + one background
`threading.Thread` + `threading.Event()` stop-flag pattern exactly.
`on_handshake` only classifies and (if authorized and WPA-like) enqueues a
job - it's fast and never blocks the caller. The ONE worker thread
(`_worker_loop`) pulls jobs off the queue and processes them strictly one at
a time; a second job sits in the queue until the first one's entire
conversion+crack pipeline finishes. This is what actually makes it a queue
rather than a pile of uncoordinated background threads - the direct answer
to the "hashcat-queue" idea this suite was commissioned from. Tested
directly: five jobs enqueued at once against a worker whose job-processing
function tracks concurrent-entry count confirms the max concurrency
observed is exactly 1, never more.

### 7. best_quickdic's missing scoping

Covered in its own section above - the single most important fix in this
merge.

### 8. Inconsistent/scattered defaults across the four

- Allowlist option name: `whitelist` (ClaudeCrackAuto), `targets`
  (RuleMutationCrack), none at all (HashFormatDetector, best_quickdic).
- Default wordlist path: `/root/wordlists/rockyou.txt` (ClaudeCrackAuto),
  `/home/pi/wordlists/rockyou.txt` (RuleMutationCrack),
  `/home/pi/wordlists` folder (best_quickdic).
- Default export/log directories: four different `/home/pi/<plugin-name>/`
  folders, one per plugin.

**Fixed**: unified under one set of option names across the merged plugin,
with `authorized_networks` as the canonical allowlist name - matching
`wifi_jammer_ng.py`, so this repo now has exactly one name for "the
allowlist of networks a plugin is allowed to act on" (see
`CONVENTIONS.md`'s new "The `authorized_networks` allowlist" section, added
as part of this merge). `wordlist_folder` defaults to
`/etc/pwnagotchi/wordlists`, and `export_dir`/`log_file` default under
`/etc/pwnagotchi/crack_pipeline_ng/` - matching this repo's established
`/etc/pwnagotchi/...`-rooted path convention (see `crack_house_ng.py`'s
`saving_path`, `bluetooth_recon_ng.py`'s `device_table_path`). None of the
four originals' own default paths were preserved verbatim, since there were
three mutually-inconsistent sets to choose from and no reason to prefer one
plugin's choice over another's.

## Architecture: the merged pipeline

```
on_handshake(agent, filename, access_point, client_station)
        |
        v
 _as_ap_dict() normalize  (fixes bug #1 for every path below)
        |
        v
   _classify(ap)  -- read-only, inspects `encryption` only, never
        |            touches hcxpcapngtool/hashcat
        |
   +----+----+----+-------------+
   |    |    |    |             |
  wep  open unk  wpa      (write .route sidecar for wep/open/unknown,
   |    |    |    |        log, done - these three never reach the
   |    |    |    |        allowlist gate at all)
   |    |    |    v
   |    |    |  _is_authorized(ap)?  (authorized_networks gate - THE
   |    |    |    |         |         fix for bug #7/best_quickdic)
   |    |    |    no        yes
   |    |    |    |         |
   |    |    |  write     enqueue job (self.processed dedup)
   |    |    |  .route      |
   |    |    |  "unauth"    v
   |    |    |         [ queue.Queue() ]
   |    |    |              |
   |    |    |              v
   |    |    |      single worker thread (_worker_loop)
   |    |    |      processes ONE job at a time:
   |    |    |        a. hcxpcapngtool convert -> .hc22000
   |    |    |        b. nothing usable? stop (log + notify)
   |    |    |        c. run_local=false? stop (export + notify)
   |    |    |        d. plain wordlist pass (every *.txt in
   |    |    |           wordlist_folder, capped, first hit wins)
   |    |    |        e. still nothing + rules_file exists?
   |    |    |           rule-mutation pass, same wordlists
   |    |    |        f. log outcome, update on-screen status,
   |    |    |           notify (sibling-lookup, see below)
   v    v    v
 write  write write
 .route .route .route
("aircrack-ng"/"none"/"unknown" respectively)
```

Everything to the left of the allowlist gate (classification) is
unconditional and read-only, per `HashFormatDetector`'s original design
intent for *that step specifically* - it's genuinely safe to always run,
since it never invokes a cracking tool. Everything from the allowlist gate
onward is exactly what best_quickdic's original design incorrectly left
ungated.

## Notification integration: why delegate instead of reimplementing

`ClaudeCrackAuto.py` was the only one of the four with any notification
code at all, and it reimplemented raw `requests.post()` calls to ntfy/
Discord webhook URLs directly inside the plugin:

```python
import requests
if self.notify_method == 'ntfy' and self.ntfy_url:
    requests.post(self.ntfy_url, data=message.encode('utf-8'), timeout=10)
elif self.notify_method == 'discord' and self.discord_webhook:
    requests.post(self.discord_webhook, json={'content': message}, timeout=10)
```

This repo already has `apprise-notify-suite` (`apprise_notify_ng.py`),
which fans a single notification out to dozens of services (ntfy, Discord,
Pushover, email, SMS gateways, and more) from one configured list of URLs,
with its own background worker thread, rate limiting, and screenshot
attachment support already built and tested. Reimplementing a second,
narrower (`ntfy`/Discord-only) notification path inside this plugin would
mean: a second place to configure webhook URLs, a second place that could
leak those URLs or mishandle a failed request, and zero benefit over just
calling into the sibling plugin that already does this correctly.

**This merge follows CONVENTIONS.md's sibling-plugin-lookup pattern
exactly** (the same pattern `mad-hatter-suite`'s `_notify_via_apprise`
established): a configurable `apprise_plugin_name` option (default
`"apprise_notify_ng"`), looked up via `plugins.loaded.get(...)`, calling
that plugin's real `_queue_notification(title, body, agent)` method if
found - wrapped in `try/except (AttributeError, TypeError, Exception)` so a
missing or internally-changed sibling degrades to a skipped, logged
notification rather than crashing this plugin. `_log_notify_sibling_status()`
(called once from `on_ready`, gated behind `notify_enabled`) logs INFO if
the sibling is found and WARNING with actionable guidance if not - matching
`mad-hatter-suite`'s own implementation of this exact convention. The whole
integration is gated behind `notify_enabled` (default `false`): nothing
happens, not even the `plugins.loaded.get(...)` lookup, for a user who
hasn't opted in.

## Judgment calls made that weren't fully spelled out in the task

1. **A fourth classification route, `"unknown"`, for an AP with no
   `encryption` field at all** (the bare-MAC-string shape from
   `_as_ap_dict()`). None of the four originals handled this shape at all -
   they would have crashed on `.get()` before ever reaching any
   classification logic (bug #1). The task's spec described three routes
   (WEP / open / WPA-proceed-to-gate); this merge adds a fourth so the
   bare-string case has a well-defined, non-crashing, non-silently-wrong
   outcome. The alternative - falling through to "open, nothing to crack" -
   was deliberately rejected: it would permanently misclassify a
   potentially-crackable WPA network as nothing-to-crack, which is a worse
   failure mode than a `.route` sidecar saying "couldn't classify, check by
   hand." This is directly tested (`route: unknown` sidecar content,
   confirmed non-crashing, confirmed never enqueues a job).
2. **History ring buffer result categories**: the task listed
   cracked/not-found/exported/skipped-WEP/skipped-open/skipped-unauthorized
   explicitly. This merge adds `skipped-unknown` (for the judgment call
   above) and `no_handshake` (conversion produced nothing usable - step 3b)
   as two additional categories, since both are real, distinct outcomes a
   user would want to see on the status page and neither fit cleanly into
   the listed six.
3. **On-screen element default position** `(0, 150)`: not specified
   numerically in the task. Chosen after checking every other suite's own
   default position in this repo (`crack_house_ng.py`, `bluetooth_recon_ng.py`,
   `handshaker-suite`, `sigstr-suite`, `mad-hatter-suite`, `weather-suite`,
   `timer-suite`, `fortune-thoughts-suite`, `dossier-suite`) - no collision
   found with any of them, documented inline in the `DEFAULTS` dict's own
   comment in `crack_pipeline_ng.py`.
4. **`show_on_screen` defaults to `true`**: the task left this as "your
   call, document the choice." Chosen `true` since the element is a small
   text badge (not a scan indicator or anything that risks misleading the
   user about radio state), consistent with most other status-badge suites
   in this repo defaulting to visible-by-default.
5. **Notification calls on every pipeline outcome, not just the three the
   task named explicitly** (cracked / not-found / exported-offline). This
   merge also fires a (still `notify_enabled`-gated) notification for
   "no crackable material after conversion," "exported because hashcat is
   missing," and "exported because no wordlists were found" - all genuinely
   distinct outcomes a user watching notifications would want surfaced,
   rather than silently falling back to only-the-log for those cases.
6. **`_classify()` is a pure module-level function**, not a method, taking
   an already-normalized AP dict and returning `(route, detail)`. This was
   done specifically so it's directly unit-testable without constructing a
   full plugin instance - matching the "pure, testable helper" pattern this
   repo already uses elsewhere (e.g. `handshaker-suite`'s
   `scan_handshakes()`, `bluetooth-recon-suite`'s `match_tracker()`).

## Testing

Run with (from this directory):
```
python3 tests/test_crack_pipeline_ng.py
```
Against the REAL cloned `jayofelony/pwnagotchi` framework (`pwnagotchi.plugins`,
confirmed genuine `Plugin.__init_subclass__` registration) with `prctl`/
`tomlkit` stubbed (both native/unavailable in this sandbox, same stubs as
every other suite's test file in this repo). All tests pass as of this
writing (`All tests passed.`).

Covers: `_as_ap_dict` handling both a real dict and a bare-MAC-string AP
(and `None`); `_classify` routing WEP/open/unknown/WPA correctly;
`on_handshake` writing the correct `.route` sidecar content for WEP, open,
unauthorized-WPA, and unknown classifications, and recording the matching
history entry for each; an empty `authorized_networks` never enqueuing a
single job across multiple WPA captures; BSSID-form and (case-insensitive)
SSID-form authorized-target matching; `_safe_name` sanitization, and a
malicious SSID (`"../../etc/passwd"`) confirmed to never let the built
export path escape `export_dir`; the empty-dirname `os.makedirs` guard
(a `log_file` with no directory component, confirming `on_loaded` completes
and sets `self.ready = True`); the worker queue processing jobs strictly
one at a time (concurrency tracking confirms max concurrency of 1 across 5
simultaneously-queued jobs); plain-pass-then-rule-pass sequencing in both
directions (plain finds nothing -> rule pass attempted; plain finds a
password -> rule pass never attempted); `run_local=false` stopping after
export and never calling hashcat; `max_wordlists_per_run` capping the
number of wordlists actually attempted; the notification sibling-lookup
(found -> `_queue_notification` called with the right args; not found ->
warning path, no crash; `notify_enabled=false` -> no lookup at all);
`on_config_changed` reloading the allowlist and picking up a newly-added
target; `on_unload` stopping the worker thread cleanly (confirmed via
`Thread.is_alive()` before/after); and the webhook status page rendering,
including HTML-escaping a malicious SSID in both the recent-history table
and the "currently processing" line.

## Known limitations

- **No real-hardware hashcat/hcxpcapngtool run was possible in this
  sandbox** - neither tool is installed here. Every test that exercises the
  conversion/cracking steps mocks `_convert`/`_run_hashcat` directly rather
  than invoking the real binaries. The `subprocess.run([...])` invocation
  shape itself is unchanged from the four originals (already confirmed
  correct/safe there - real argument lists, never `shell=True`), but the
  actual on-device behavior of a real hashcat/hcxpcapngtool run through this
  pipeline has not been verified end-to-end.
- **GPU-accelerated cracking elsewhere is still the recommended path** for
  anything beyond a toy wordlist - a Raspberry Pi's CPU-only `hashcat` run
  is dramatically slower than even a modest discrete GPU. `run_local =
  false` (exporting the `.hc22000` file for offline cracking) is the
  practical choice for any wordlist larger than a quick sanity check,
  exactly as `ClaudeCrackAuto.py`'s original design already recognized.
- **The notification integration's actual Apprise delivery has not been
  tested end-to-end** in this sandbox (that's `apprise-notify-suite`'s own
  test suite's job) - this plugin's own tests confirm the sibling-lookup and
  `_queue_notification` call shape, with a fake stand-in object, not a real
  Apprise delivery.
- **The bare-MAC-string "unknown" classification is a judgment call** (see
  above) that has not been observed against a real live bettercap session
  producing that exact AP shape for a WPA network - only confirmed against
  this fork's documented/established behavior for that shape
  (`wifi_jammer_ng.py`'s own `_as_ap_dict` docstring and `NOTES.md`).
