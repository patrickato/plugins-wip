# Cross-suite conventions

This file documents patterns that are shared on purpose across more than
one suite in this repo. If you're adding a new suite (or extending an
existing one) and it needs to do something another suite already does
below, follow the same pattern rather than inventing a new one - it keeps
behavior predictable and keeps `NOTES.md`/`config.toml` comments from
having to re-explain the same idea five different ways.

## Looking up a sibling plugin (`plugins.loaded.get(...)`)

**What it's for:** any suite that wants to optionally integrate with
another suite if it happens to be installed - e.g. mad-hatter-suite
pushing a battery-threshold alert through whichever notification suite
is available, or fix-region-suite reading GPS coordinates from whichever
GPS suite is loaded.

**The mechanism:** this fork's `pwnagotchi.plugins.loaded` is a plain
dict of `{plugin_name: instance}`, keyed by the plugin file's exact
basename (see `pwnagotchi/plugins/__init__.py`'s `load_from_file()`).
So `plugins.loaded.get("apprise_notify_ng")` returns that plugin's
instance if it's loaded and enabled, or `None` otherwise.

**Why a hardcoded name is a real bug, not just a style nitpick:** the
post-cluster-review conflict pass found two suites (mad-hatter-suite,
fix-region-suite) hardcoding the sibling's file basename as a plain
string. If a user ever renamed that sibling's `.py` file - or a future
suite shipped under a different name - the lookup returns `None` and
the integration silently stops working. Nothing crashes, nothing logs,
and there's no config option to point at the renamed file. That's a
guaranteed-eventually support headache with no way for a user to
self-diagnose it.

**The convention, now applied in mad-hatter-suite and fix-region-suite,
that any future sibling-lookup should copy:**

1. **Make the target name(s) a config option**, defaulting to the
   sibling's shipped name(s) so behavior is unchanged out of the box.
   A single name is a plain string option (see mad-hatter-suite's
   `apprise_plugin_name`/`discord_plugin_name`); if more than one name
   could plausibly apply (e.g. several GPS plugin variants), use a list
   option that overrides a built-in tuple of guesses when set (see
   fix-region-suite's `gps_sibling_names`, which overrides
   `GPS_SIBLING_PLUGIN_NAMES` only when the user has configured it).
2. **Do the lookup through `self._opt(...)`**, never a bare string
   literal, so the configured override actually takes effect:
   ```python
   plugins.loaded.get(self._opt("apprise_plugin_name", "apprise_notify_ng"))
   ```
3. **Log whether the sibling was actually found, once, at a sensible
   point** - not on every poll cycle (that's log spam), and not so
   early that a normal load-order race gives a false negative:
   - If the integration is checked repeatedly from a hook that runs
     early in the plugin lifecycle (fix-region-suite's `on_loaded()`),
     log from that same call site - there's no earlier point that would
     be more accurate anyway.
   - If the integration only fires later, once every plugin has had a
     chance to load (mad-hatter-suite's threshold-notification check),
     log from `on_ready(self, agent)` instead of `on_loaded(self)`,
     since `on_ready` runs after the whole plugin set has loaded and
     won't produce a false "not found" just because this suite happened
     to load before its sibling did.
   - Log **INFO** naming the sibling that was found (so a user can
     confirm the integration is wired up correctly from a normal log
     read), and **WARNING** naming every name that was checked and
     wasn't found (so a misconfiguration is immediately actionable -
     the user sees exactly which option to fix).
   - Gate the log behind the feature actually being enabled (e.g. only
     log mad-hatter-suite's notification-sibling status if
     `notify_on_threshold` is true) - a suite that never uses the
     integration shouldn't warn about a sibling it was never going to
     look for.
4. **Never let a missing or misbehaving sibling take down the suite
   doing the lookup.** The lookup and any resulting call must stay
   inside their own `try`/`except`, exactly as before this convention -
   this pattern only changes visibility and configurability, not the
   existing "a missing sibling degrades this one feature, never crashes
   the plugin" guarantee.

**Suites that could adopt this in the future:** any suite that starts
doing a `plugins.loaded.get(...)` lookup of its own should follow the
four steps above from the start, rather than shipping a hardcoded name
and needing this same fix applied to it later.

## The `authorized_networks` allowlist

**What it's for:** any suite whose actual effect - sending real frames,
or running a password-cracking tool against a captured handshake - must
never touch a network the user doesn't explicitly own or have
permission to test. This is the single safety mechanism for that whole
class of suite, not a style nicety.

**The mechanism, now applied in `wifi-jammer-suite` (`wifi_jammer_ng.py`)
and `crack-pipeline-suite` (`crack_pipeline_ng.py`):**

1. **One config option, `authorized_networks`, a list, empty by
   default.** Empty means total inaction for whatever the suite's real
   effect is - `wifi_jammer_ng.py` never calls `agent.run('wifi.deauth
   ...')`, `crack_pipeline_ng.py` never invokes `hcxpcapngtool`/
   `hashcat` - no matter what else the suite does (both still log/
   classify/display around that gate; only the actual effect is held
   back).
2. **Each entry can be a BSSID or an SSID**, auto-detected with one
   shared module-level regex:
   ```python
   _MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")
   ```
   A `_load_targets()` method splits the configured list once into two
   sets - `self._authorized_macs` (uppercase-normalized) and
   `self._authorized_ssids` (lowercase-normalized) - and a `_match`/
   `_is_authorized` helper checks an AP dict's `mac`/`hostname` against
   those sets. BSSID matching is the precise, unambiguous form; SSID
   matching is offered purely for convenience on hardware the user
   already controls.
3. **Call `_load_targets()` from both `on_loaded` and
   `on_config_changed`**, so editing the list and reloading config takes
   effect without a full plugin/process restart.
4. **Normalize the AP argument before matching it**, via each suite's
   own `_as_ap_dict(ap)` helper - `on_handshake`'s (and, for
   `wifi_jammer_ng.py`, `on_association`'s) AP argument can arrive as
   either a full dict or a bare MAC string on this fork, confirmed
   against the real cloned `agent.py`. Calling `.get()` on a bare string
   raises `AttributeError` and silently drops the event - every
   suite adopting this allowlist pattern must normalize first, on every
   single path that reads the AP argument, not just some of them.
5. **Log how many BSSIDs/SSIDs were loaded, once, at `on_loaded`** -
   WARNING if the list is empty (so a user who forgot to configure
   anything sees it immediately in the log, not just silent inaction),
   INFO with the counts otherwise.

**Suites that could adopt this in the future:** any new suite whose real
effect must be scoped to networks the user owns - not just active
radio-transmitting plugins, but also, as `crack-pipeline-suite`'s own
`NOTES.md` discusses at length, any plugin that runs a cracking tool
against an already-captured file. "It's just local post-processing, not
radio behavior" is explicitly not a valid reason to skip this pattern -
see that suite's NOTES.md for the full argument.

## Other established repo-wide conventions (for reference)

These predate this file and are documented in more depth in their own
suites' `NOTES.md`, but are worth listing here so they're discoverable
in one place:

- **`DEFAULTS` dict + `_opt()`/`_opt_int()`/`_opt_bool()`/`_opt_float()`
  reader pattern** - this fork's loader does `plugin.options =
  config['main']['plugins'][name]` with no `__defaults__` merging, so
  every suite in this repo reads its options through this helper
  pattern instead of assuming a key exists.
- **Config section name matches the plugin file's exact basename** -
  never the class name, never the original upstream plugin's own
  section name. See any suite's `config.toml` header comment for the
  full explanation (handshaker-suite's and mad-hatter-suite's are the
  most detailed write-ups of why this matters).
- **Screen position via `ui_position_x`/`ui_position_y` (or
  `position_x`/`position_y`)** - always user-overridable, with negative
  `x` conventionally meaning "this many pixels in from the right edge."
  Default positions are chosen to avoid known collisions with other
  suites' own defaults (see crack-house-suite's and handshaker-suite's
  `NOTES.md` for two documented examples), but a default is a starting
  point, not a promise - always check for overlap against whatever
  other suites you're actually running.
- **`bind_scope` for any suite running its own web server** -
  `"auto"`/`"tailscale"`/`"localhost"`/`"lan"`, with the exact URL
  always logged and shown on the page itself. See web2ssh-suite's or
  handshaker-suite's `config.toml` for the full option write-up. Note
  this is not yet adopted by every suite that runs a server (see the
  "three separate attack surfaces" observation in the post-cluster-
  review pass) - it's the recommended pattern for any *new* suite
  adding its own server, not a guarantee every existing one already
  follows it.
