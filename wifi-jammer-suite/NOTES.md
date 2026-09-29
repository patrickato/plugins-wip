# Research notes: WifiJammerNG

Source: `wifi_jammer.py` (itsdarklikehell/pwnagotchi-plugins, author
"Deus Dust"). Reviewed as part of `test-plugins` Cluster 33 (Network /
Security analysis category). Originally recommended for outright
removal alongside 9 sibling plugins by the same author that all shared
the same fatal defect (see `test-plugins:plugin-upgrade-proposals/cluster-33-network-security/NOTES.md`).
This one specifically was picked back up for a real rebuild at the
user's request, for supervised testing against their own lab hardware.

## Why this one, and not its 9 siblings

The other 9 (`dns_spoof_detector.py`, `mac_adress_logger.py`,
`mac_randomizer.py`, `network_intrusion_detector.py`,
`network_mapper.py`, `network_packet_sniffer.py`, `traffic_sniffer.py`,
`wifi_analyser.py`, `wifi_password_cracker.py`) are either fundamentally
non-functional in concept even once the import is fixed
(`network_intrusion_detector.py` logs "intrusion detected" for literally
every IP packet - there's no actual detection logic to save), redundant
with better plugins already on the list (`mac_randomizer.py` vs.
`neurolyzer.py`; `wifi_password_cracker.py` vs. the existing
crack-pipeline `_ng` plugins), or low-value duplicates of each other
(`network_packet_sniffer.py`/`traffic_sniffer.py` are the same
`print(packet.summary())` script twice). `wifi_jammer.py` was the one
genuinely-interesting one - a working deauth-burst concept - but it was
also the one with the most dangerous *lack* of scope, since as shipped
it would have fired unconditionally at every AP that ever yielded a
handshake. That's a design/safety fix worth actually doing, not a
"why bother" situation.

## Bugs found in `wifi_jammer.py` (source-verified)

1. **Fatal: `from pwnagotchi.plugins import BasePlugin`.** Confirmed by
   grepping the real cloned `jayofelony/pwnagotchi/pwnagotchi/plugins/__init__.py`
   - `BasePlugin` does not exist anywhere in this fork; the only base
   class is `plugins.Plugin`. This import fails immediately, so the
   plugin could never load at all, under any configuration.
2. **`access_point.bssid`** - attribute access on what this fork always
   hands plugins as a plain dict (`access_point['mac']`). Would have
   been a second, independent crash even with fix #1 alone applied.
3. **No authorization/scoping whatsoever.** `on_handshake` fired at
   whatever `access_point` it was given, no matter what network that
   was. Combined with...
4. **...`aireplay-ng --deauth 0`** - in aireplay-ng, a count of `0`
   means "send continuously, forever," not "send zero." So the original
   design was: any AP, unconditionally, forever, the moment a handshake
   was captured from it.
5. Shelled out to a brand-new `aireplay-ng` subprocess with a hardcoded
   `interface="wlan0"` default - both a portability problem (this
   fork's real monitor interface name isn't guaranteed to be `wlan0`)
   and a resource-contention risk (a second process trying to inject on
   the same interface bettercap already has open).

## What changed in this rewrite

- Rebuilt on the real `plugins.Plugin` base class; real dict-based AP
  access via a bare-string/dict normalizer (`_as_ap_dict`, same pattern
  already used in `gps-tagger-suite`'s `GPSTaggerNG`, since
  `on_handshake`'s AP/station arguments can arrive as either a full
  dict or a bare MAC string depending on whether bettercap could match
  the session at that exact moment - confirmed in `pwnagotchi/agent.py`).
- **The core safety change**: an `authorized_networks` allowlist,
  empty by default. Every firing path (`on_association`, `on_handshake`,
  and the manual webhook trigger) checks a target against this list
  before ever calling into bettercap - nothing fires against anything
  not explicitly named. This was the whole point of doing this rebuild
  at all rather than declining it; see the project conversation for the
  full reasoning (RF signal range isn't a reliable safety boundary, so
  the plugin needed a real technical one instead of relying on that).
- Replaced the `aireplay-ng` subprocess entirely with
  `agent.run('wifi.deauth <mac>')` - the exact same bettercap REST API
  call this fork's own `pwnagotchi/agent.py` (`Agent.deauth()`) already
  uses for its normal personality-driven deauth behavior. This is a
  strictly better design than the original even ignoring the safety
  fix: no new binary dependency, no interface-name guessing, and no two
  processes contending for the same monitor interface, since it reuses
  bettercap's own already-open session via the same `agent` object
  every other hook already receives.
- Added a per-target `cooldown_seconds` (default 15s) so a target
  sitting in range for a while doesn't get re-fired on every single
  association/handshake event.
- Added firing on `on_association` (confirmed real hook,
  `plugins.on('association', self, ap)` in `pwnagotchi/agent.py`) in
  addition to the original's only trigger (`on_handshake`), specifically
  so testing against your own AP doesn't require first getting a full
  handshake capture - this was requested explicitly to make lab testing
  low-friction.
- Added a manual "fire now" webhook page (`on_webhook`) for on-demand
  testing independent of any live bettercap event - lists BSSID-form
  authorized targets with a one-click fire link. SSID-form entries
  aren't listed there (no live SSID-to-MAC lookup implemented), but
  still fire automatically via the two event hooks above.
- Added SSID matching alongside BSSID matching in `authorized_networks`,
  for convenience - typing a network name is easier than looking up its
  MAC. BSSID remains the more precise match and is what the manual
  webhook trigger requires.

## Design decisions worth being explicit about

- **The allowlist is the safety mechanism, not physical signal
  containment.** This was a deliberate design choice made in response
  to the user's own framing (basement lab, rural property, distant
  neighbor) - RF propagation isn't reliably bounded by walls or
  property lines the way that framing assumes, so the plugin needed a
  real technical guarantee instead. An empty `authorized_networks` list
  means total inaction; a populated one means action *only* against
  what's listed, regardless of what else is in range.
- **Deauths the AP's own MAC, not a specific client station.** Bettercap's
  `wifi.deauth <mac>` targets whatever MAC you give it; passing the AP's
  own MAC (what this plugin does) deauths all of that AP's associated
  clients at once, matching the original plugin's evident intent
  ("jam this AP"). This is unchanged bettercap behavior, not something
  this rewrite altered.
- **No packet-count/burst-size option.** Unlike the original's
  `aireplay-ng --deauth N` count parameter, `agent.run('wifi.deauth
  <mac>')` is a single bettercap module invocation - bettercap's own
  `wifi.deauth` module controls how many frames one invocation sends
  internally, not this plugin. The `cooldown_seconds` option is what
  bounds *how often* this plugin re-invokes it, which is the exposure
  that actually mattered here (an aireplay-ng call with count=0 run
  once is just as unbounded as calling a bounded command in a tight
  loop with no cooldown).

## Testing done (sandbox, no real hardware)

See `tests/test_wifi_jammer_ng.py`. Covered against the REAL cloned
jayofelony framework (`pwnagotchi.plugins`, confirmed genuine
`Plugin.__init_subclass__` registration): an empty `authorized_networks`
list results in zero calls to `agent.run` under any event; a BSSID-form
authorized target fires on both `on_association` and `on_handshake`; an
SSID-form authorized target matches by hostname and fires using the
AP's real MAC; an unauthorized AP never fires; the cooldown genuinely
blocks a second fire within its window and allows one after it elapses;
the bare-MAC-string `on_handshake` argument shape (not just a full dict)
is handled via `_as_ap_dict`; the manual webhook trigger fires only for
BSSIDs present in `authorized_networks` and refuses anything else, even
when directly requested through the webhook path.

## Still open / needs real-hardware testing

- Whether a real target device actually disconnects/reconnects visibly
  when fired at - `agent.run()` is mocked in the test suite (asserting
  it's called with the right command string), not exercised against a
  live bettercap/target radio.
- Real-world cooldown tuning - 15 seconds was chosen as a reasonable
  default, not measured against an actual test session.
