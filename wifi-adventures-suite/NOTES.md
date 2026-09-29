# Notes: WifiAdventuresNG

## Why a full rewrite

`wifi_adventures.py` (`FunAchievements`) is ~850 lines, but only two of
its six "adventure type" hook methods are real framework hooks
(`on_handshake`, `on_unfiltered_ap_list`), and both of those were broken
(wrong signature on one, wrong-arity call on the other - see README).
The other four hooks (`on_packet_party`, `on_pixel_parade`,
`on_data_dazzle`, `on_speedy_scan`) are not hook names this framework
ever calls, so roughly 500 of those 850 lines - the treasure hunt,
wifi-auto-connect/potfile password lookup, fake stat boosts, blocking
`input()` prompts - are unreachable dead code. A patch couldn't fix
this; the salvageable core (a handshake counter with a title ladder,
plus a new-network counter) is a small fraction of the file, so a clean
rebuild around just the two real hooks was the right call rather than
trying to preserve the dead branches "just in case."

## What this build does

- `on_handshake(self, agent, filename, access_point, client_station)`:
  increments a handshake counter, updates a day-based streak (increments
  on a consecutive calendar day, resets to 1 on a gap, no-ops on a
  repeat same-day call), and recomputes the current title from a
  10-tier ladder keyed by handshake count.
- `on_unfiltered_ap_list(self, agent, access_points)`: uses the real
  `access_points` argument (a list of dicts, each with a `mac` key - the
  same shape `agent.py`'s `get_access_points()` passes to this hook) to
  find BSSIDs not already in a persisted `seen_bssids` set, and only
  counts genuinely new ones. The original risked just incrementing a
  counter on every call regardless of what was actually new (and
  couldn't have worked at all, since its version of the method didn't
  even accept the list).
- State persists to JSON (`wifi_adventures_ng.json` next to the plugin,
  or a configured `data_path`), same pattern as the original.
- `on_webhook` now renders an actual status page (handshake count,
  new-networks count, title, streak) instead of a single log line.
- No wifi-auto-connect, no treasure hunt, no blocking `input()`, no
  hardcoded telemetry server - all removed, none of it belongs in a
  background daemon plugin regardless of whether the original hooks
  calling it ever fired.
- `__init__` does no file or network I/O (state loading is deferred to
  `on_ready`), since `Plugin.__init_subclass__` instantiates the class
  immediately with no try/except - an exception in `__init__` would
  crash loading for every plugin, not just this one.

## Merge-with-achievements.py decision

`achievements.py`, a sibling in this same audit cluster, was flagged as
a possible merge target since it covers similar handshake/achievement-
tracking territory. I decided **against** merging into it this round,
and built this as its own standalone suite instead:

- `achievements.py` was explicitly not approved for work this round -
  the user said to skip it and revisit it later. Merging into a plugin
  that hasn't itself been audited or rebuilt yet means merging into an
  unknown, possibly-also-broken target, and would make this suite's
  completeness depend on work that hasn't happened.
- It would also create an undocumented cross-suite dependency between
  two plugins sitting at very different stages of this audit (one
  rebuilt, one untouched), which cuts against treating each approved
  suite as a self-contained, independently testable unit.
- Practically, "merge" isn't well-defined yet anyway - without having
  read and audited `achievements.py`'s own current state, I can't say
  what its data model looks like or what a clean merge would even keep.

Recommendation for later: once `achievements.py` comes up for its own
audit/rebuild round, revisit whether the two should be consolidated into
one suite (they'd likely share a handshake-count/streak/title-ladder
core). Until then, this stays standalone so it's fully functional and
testable on its own.

## Testing

`tests/test_wifi_adventures_ng.py`, run against the real cloned
`jayofelony/pwnagotchi` framework. Covers: real `plugins.Plugin`
registration; state load/save round-tripping through JSON, including a
missing/corrupt state file not crashing; `data_path` config override vs.
the default next-to-plugin path; `on_handshake` incrementing the
counter and recomputing the title across ladder thresholds; streak logic
across three cases (first-ever handshake, a consecutive-day handshake,
a same-day repeat, and a gap-day reset); `on_unfiltered_ap_list` only
counting BSSIDs not already in the seen set (and not double-counting a
BSSID seen again later), using the real dict-with-`mac`-key shape the
framework actually passes; `on_webhook` rendering without crashing and
including the current stats; and `on_unload` not raising.

## Still open

- No real-device test against a live handshake capture or a live AP
  scan - see README's "Still open" section.
- The streak is purely calendar-day based (local system date); no
  timezone handling beyond whatever `datetime.date.today()` gives the
  host.
