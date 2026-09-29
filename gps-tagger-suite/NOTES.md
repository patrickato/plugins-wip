# Research notes: GPSTaggerNG

Source: `privacy-nightmare.py` (itsdarklikehell/pwnagotchi-plugins,
originally by glenn@pegden.com). Reviewed as part of the `test-plugins`
category-by-category pass (Attack/Capture category, the cluster covering
`privacy-nightmare.py`, `wd_honey_Pot.py`, `neurolyzer.py`).

## Bugs found in the original (source-verified)

1. **`self.gps_hot` referenced before being set.** It's only ever
   assigned inside `get_gps()`, which `aps_update()` only calls `if
   agent != None`. But `aps_update()` can be reached with `agent=None`
   from `on_event`'s `"wifi.ap.new"` handler - the very first AP ever
   seen could hit this path first, raising `AttributeError` on
   `self.gps_hot`.

2. **`latlong` used outside the branch that defines it.** `latlong` is
   only assigned `if self.gps_hot == True:`, then read unconditionally a
   few lines later in the per-AP logging loop. Any time GPS isn't locked
   - which for most people most of the time is the *normal* state, not
   an edge case - this is a guaranteed `NameError`.

3. **Bare-indexed config options**: `self.options["pn_output_path"]` and
   `self.options["gps_speed"]` - `KeyError` if either was ever left
   unset in `config.toml`.

4. **A second, independent bettercap websocket connection**, run in its
   own thread (`hook_ws_events`/`_event_poller`), just to catch
   `"wifi.client.probe"` and `"wifi.ap.new"` events. The author's own
   comment: "OK adding a second websocket listener is an ugly approach,
   but without modifying the core code, I cant think of a better way".
   That workaround isn't needed on this fork: `pwnagotchi/agent.py`'s
   real `_on_event` already does
   `plugins.on('bcap_%s' % re.sub(r"[^a-z0-9_]+", "_", jmsg['tag'].lower()), self, jmsg)`
   for every bettercap event, which dispatches to a plugin's own
   `on_bcap_<tag_with_underscores>` method exactly like any other hook.
   Confirmed directly in the fork's `agent.py` source. This also means
   the original's thread was never cleaned up on unload (`on_unload`
   never touched it, and the `while True` loop inside `_event_poller`
   didn't even check `self.running`) - it would have run forever in the
   background even after the plugin was disabled.

5. **New-AP handler passed the wrong shape.**
   `self.aps_update("NE", None, jmsg["data"])` passes a single AP dict
   where `aps_update` expects a *list* of AP dicts. `for ap in
   access_points:` then iterates over the dict's own keys (plain
   strings), and the very next line, `ap["vendor"]` on a string, raises
   `TypeError: string indices must be integers`. This meant the
   "wifi.ap.new" path - genuinely new APs specifically - could never
   actually be logged without crashing.

6. **Assumed AP/station arguments are always full dicts.** Confirmed by
   reading `pwnagotchi/agent.py`'s real `_on_event` in detail: on a
   handshake, if the AP/station can't be found in the current bettercap
   session at that exact moment, the framework calls
   `plugins.on('handshake', self, filename, ap_mac, sta_mac)` with bare
   MAC **strings** instead of dicts (the dict form is only used when a
   match is found). `aps_update`'s `ap["hostname"]` / `ap["mac"]` /
   `ap["vendor"]` would raise `TypeError` on the bare-string case. This
   applies to `on_association`/`on_deauthentication` too, which pass
   `access_point` straight through unchanged.

7. **Invalid JSON output files.** `json.dump(ap, fp); json.dump(self.
   pn_gps_coords, fp)` writes two separate JSON values into one file
   with no separator - not something a normal JSON parser (including
   anything we'd want to build to read these files later) can load as a
   single document.

8. **Filename collisions.** `pn_filename = "%s/pn_ap_%s.json" %
   (self.options["pn_output_path"], hostname)` keys the file purely by
   hostname. Two different APs sharing an SSID - extremely common
   ("NETGEAR", "xfinitywifi", any hidden AP, which all fall back to the
   same `"Unknown-<vendor>"` placeholder if the vendor also matches) -
   silently overwrite each other's file with no warning.

9. **Cosmetic counter bug.** `ui.set("pn_count", "%s/%s" % (self.
   pn_count, self.pn_count))` always shows the same number on both sides
   of the slash.

## What changed in this rewrite

- All nine bugs above fixed - see the inline docstring in
  `gps_tagger_ng.py` for the fix mapped to each numbered item.
- `.gps.json` sidecar written on `on_handshake`, using the filename the
  real framework hands the plugin, in the same
  `{"Latitude": .., "Longitude": ..}` schema `handshakes_dl_ng.py`
  already reads (that plugin was built first, in the previous cluster,
  specifically supporting this schema, so the interop was designed in
  rather than bolted on).
- `min_regap_distance_feet` (default 50 ft) distance filter. Reasoning
  on the default, per the user's request to base it on "normal/regular
  conditions" rather than this specific test setup: typical
  non-RTK, consumer-grade GPS (the kind normally paired with a
  pwnagotchi over USB/UART, not a survey-grade receiver) drifts roughly
  10-20 feet under a clear sky, and more under tree cover or near
  buildings. 50 feet gives that jitter a healthy margin while still
  being tight enough that two genuinely different locations for the
  same AP (e.g. a business's AP seen from the parking lot on two
  different visits) don't get treated as "no real movement" and merged.
  It's exposed as a config option specifically so it can be tuned up or
  down for a noisier or more precise GPS setup without touching code.
- Rate-limited "no GPS fix" logging (`no_gps_log_interval_seconds`,
  default 300s) instead of either spamming a line per event (the
  original, once fixed literally, would have) or silently doing
  nothing.
- `manage_gps` flag (default `False`): the original always tried to
  drive bettercap's `gps.device`/`gps.baudrate`/`gps on` itself whenever
  `gps_device` was set in its config, with zero awareness that this
  fork already ships its own `[main.plugins.gps]` that does the exact
  same thing. Running both against the same serial device at once is a
  real, avoidable conflict - flagged during the original cluster review
  for `privacy-nightmare.py` as a design concern, and now addressed
  directly: this plugin defaults to *not* touching GPS hardware config
  at all, and only takes it over if you explicitly opt in.

## Testing done (sandbox, no real hardware)

24 tests in `tests/test_gps_tagger_ng.py`, run against the REAL cloned
jayofelony framework, not a mock: `pwnagotchi.plugins` (confirmed
`GPSTaggerNG` actually registers itself via the real
`Plugin.__init_subclass__`, keyed in the real `plugins.loaded` dict,
the moment the module is imported - not something a stand-in class could
fake), `pwnagotchi.ui.fonts`, and `pwnagotchi.ui.components.LabeledValue`
are all the genuine source. Only `prctl` (a native module this sandbox
can't install) is stubbed with a one-line no-op, and only
`pwnagotchi.ui.view` is faked - just its `BLACK` constant, copied
verbatim from that file's own source (`0xFF`) - because importing it for
real pulls in `pwnagotchi.utils` -> `tomlkit`, which also isn't
installable here and has nothing to do with anything under test.

Covered: every one of the nine original bugs has a dedicated
regression test proving the fixed code takes the same input the
original would have crashed on and does not raise; the distance filter
is tested both ways (small jitter well under 50 ft does not rewrite the
stored fix, genuine movement well past it does); the `.gps.json` sidecar
is verified to match the exact schema; the filename-collision fix is
verified by tagging two different APs sharing a hostname and checking
two separate, individually-valid JSON files result; the `gps_up`
gate (nothing is read before `on_ready()` sets it) is tested explicitly;
and the on-screen UI elements are exercised against the real
`LabeledValue` widget, not a stand-in.

## Still open / needs real-hardware testing

- Not yet tested against a live pi + real GPS module + jayofelony
  64-bit image - all GPS behavior here is simulated via a fake `agent`
  object returning canned coordinates.
- The `min_regap_distance_feet` default (50 ft) is a general
  recommendation based on typical consumer GPS accuracy, not measured
  against your specific GPS hardware - worth checking your GPS's actual
  real-world jitter once it's running and adjusting the value if needed.
- `manage_gps = true`'s actual bettercap `gps on`/`set gps.device`
  command sequence is unchanged from the original (only the config
  access around it was hardened) and hasn't been exercised against a
  real GPS device in this sandbox.

## Config verified against upstream (2026-09-28)

Compared `config.toml` against the real upstream sample
(`itsdarklikehell/pwnagotchi-plugins/configs/privacy-nightmare.toml`,
which just sets `enabled = false` - no other options documented
upstream) and against every `self.options.get(...)`/`self.options[...]`
call in `gps_tagger_ng.py`. All options the code actually reads
(`pn_output_path`, `min_regap_distance_feet`,
`no_gps_log_interval_seconds`, `manage_gps`, `gps_device`, `gps_speed`)
are present and documented in `config.toml`. Renamed from
`config.toml.example` to `config.toml` - it's a complete, ready-to-edit
config, not a stub.

## Original config preserved

A real original config file for `privacy-nightmare.py` was found (exact match) at
`itsdarklikehell/pwnagotchi-plugins/configs/privacy-nightmare.toml`. Preserved verbatim in this suite's own folder as
`config.original.toml`, per the project's standing config-preservation requirement,
for reference/troubleshooting if the rebuilt `config.toml` above ever
needs comparing against the source.
