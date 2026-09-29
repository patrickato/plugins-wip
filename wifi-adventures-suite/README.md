# WifiAdventuresNG

On-screen handshake/streak achievement tracker with a title ladder and a
new-networks counter. Rewritten from `wifi_adventures.py`.

## What was actually broken

The original (`FunAchievements`) shipped six "adventure type" hook-shaped
methods, but the real pwnagotchi framework only ever calls two of them:

- `on_handshake` and `on_unfiltered_ap_list` are real hooks.
- `on_packet_party`, `on_pixel_parade`, `on_data_dazzle`, and
  `on_speedy_scan` are **not real hook names this framework calls at
  all** - they're dead code the framework never invokes. Everything
  behind them (a treasure-hunt minigame using blocking `input()` calls,
  a wifi-auto-connect routine that shells out to `wpa_supplicant`/
  `wpa_cli` with a cracked password, fake "stat boosts" on attributes
  like `self.speed`/`self.intelligence`/`self.luck` that are never
  initialized anywhere) never runs.

Of the two real hooks, both were broken:

- `on_unfiltered_ap_list(self, agent)` is missing the real
  `access_points` parameter the framework actually passes
  (`on_unfiltered_ap_list(self, agent, access_points)`), so it
  `TypeError`s immediately every single time the framework calls it.
- `on_handshake` calls `self.show_status_message(status_message)` with
  one argument against a `show_status_message(self, ui, message)`
  two-argument signature - a guaranteed `TypeError` on every real
  handshake, though the counting/saving logic above it does run first.

It also POSTed full plugin state to a hardcoded personal IP
(`http://192.168.68.16:5000/...`) on every completed "adventure" -
meaningless for anyone but the original author and a privacy-smelling
pattern regardless.

## What this rewrite keeps and drops

**Keeps**: the on-screen achievement/streak flavor - a handshake
counter, a simplified title ladder (10 tiers instead of ~20, since the
original's extra tiers existed to serve adventure types that never ran),
a day-based streak, and a new-networks counter, now built correctly on
`on_unfiltered_ap_list`'s real `access_points` list instead of blindly
incrementing on every call.

**Drops**: all four fake "adventure type" hooks and everything behind
them - the treasure hunt, the wifi-auto-connect/password-cracking
lookup, the blocking `input()` calls (never acceptable in a background
daemon plugin), the fake stat boosts, and the hardcoded remote telemetry
POST. None of it ever ran correctly (or at all, in the dead-hook cases),
so nothing here is a functional regression.

Also replaced: the pointless `on_webhook` (a single log line) now
returns a real status page with handshake count, new-networks count,
current title, and streak.

## Configuration

See `config.toml`. State persists to a JSON file next to the plugin by
default, or to a configured `data_path`.

## Still open

- No real-device test against a live pwnagotchi capturing an actual
  handshake or scanning real access points - the test suite exercises
  the plugin's logic directly (state load/save, streak math, title
  thresholds, new-network deduplication) against the real cloned
  `jayofelony/pwnagotchi` framework's hook signatures.
- See `NOTES.md` for the merge-vs-standalone decision regarding
  `achievements.py`.
