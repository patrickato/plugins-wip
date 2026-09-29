# Notes: TimerNG

## Why extend rather than just fix

`timer.py` (itsdarklikehell/idoloninmachina) was the one Cluster 34
plugin with no crash bug and a genuinely useful idea - it measures
time-to-deauth and time-to-handshake by watching `on_wifi_update`,
`on_deauthentication`, and `on_handshake` in sequence. It was
initially marked "keep, with a documented bug" during the cluster
review. Once the user deferred the choice of which improvements to
build, the small real bug found below was folded into a full rebuild
alongside the four proposed additions, following the same
plugins-wip pattern as `InternetConnectionNG` and `TweakViewNG`
rather than patching the original file in place.

## What I told the user earlier that turned out to be wrong

I described `timer.py` to the user as logging "per-network" timing
data. Reading the actual source corrected that: it keeps a single
flat, module-level list of three numbers
(`wifi_update_time`/`wifi_deauth_time`/`wifi_handshake_time`
deltas) with no network identifier attached to a row at all - every
capture, from every network, lands in the same three columns with
nothing to tell them apart later. Per-network tracking is a genuine
new addition here (see below), not a fix of something that was
already there.

## Bugs found

- **A dependency-declaration bug.** `__dependencies__` lists
  `pip: ["scapy"]`, which the file never imports anywhere - the same
  stray, unused dependency found in `internet-connection.py` and
  `wanmon.py` earlier in this cluster. But unlike those two, this one
  also has the opposite problem: it imports and uses `pandas` to
  build a `DataFrame` and write the CSV, and `pandas` is never
  declared as a dependency at all. So the one dependency it actually
  needs is undocumented, and the one it declares is fictional.
- **Unbounded, full-file rewrite on every handshake.** `process_data`
  loads the *entire* existing CSV into a pandas `DataFrame` with
  `pd.read_csv`, appends one row, and calls `to_csv` to rewrite the
  whole file from scratch - on every single handshake, with no size
  cap. On a long-running device this both grows without bound and
  gets slower over time as the read-modify-rewrite cycle scales with
  file size.
- **No bare-MAC-string handling.** Like `WifiJammerNG` found earlier
  in this audit, `on_handshake`'s `access_point` argument can be a
  plain MAC string instead of a full AP dict, in the
  "couldn't match the session" branch of `pwnagotchi/agent.py`. The
  original never guards against this; since it never reads any
  per-AP field in the first place (see above), it happened not to
  crash on it, but any of the new per-network tracking added here
  would have if not handled - fixed with the same
  `_as_ap_dict`/`_network_name` normalizer pattern.
- **Hardcoded output path.** `/home/pi/data/pwnagotchi_times.csv` -
  assumes a specific user and a specific pre-existing directory that
  won't exist on every setup.

None of these are severe - the original is inert rather than
actively broken - which is why it was a "keep with documented bug"
rather than a removal candidate in the first place.

## What this rebuild keeps, drops, fixes, and adds

- **Keeps**: the core idea and event sequence exactly -
  `on_wifi_update` marks the start of a network's visibility window,
  `on_deauthentication` marks when a deauth was sent, `on_handshake`
  marks capture and triggers the calculation. A handshake with no
  prior deauth this epoch is still treated as a passive capture and
  intentionally not timed or logged, matching the original.
- **Drops**: `pandas` entirely, replaced with the stdlib `csv`
  module - this also resolves the dependency-declaration bug by
  removing the need to declare any pip dependency at all.
- **Fixes**: the hardcoded path (now `output_path`, configurable),
  the unbounded rewrite (see `max_rows` below), and adds the
  bare-MAC-string normalizer so the new per-network fields can't
  crash on that AP shape.
- **Adds** (the four items the user deferred to my judgment):
  1. **Configurable `output_path`.**
  2. **Optional on-screen element** (`show_on_screen`, off by
     default) showing either the last capture's time-to-handshake or
     a rolling average over `ui_average_window` captures
     (`ui_metric`).
  3. **Per-network fastest/slowest tracking** - every row now also
     records a network name (SSID from the AP dict, falling back to
     MAC if hidden/unavailable/a bare string), and an in-memory
     `_network_stats` dict tracks count/best/worst time-to-handshake
     per network, surfaced on a real webhook page (the original's
     `on_webhook` just logged that it was pressed and returned
     nothing).
  4. **`max_rows` CSV rotation** (default 5000, 0 = unlimited) -
     replaces the unbounded full-DataFrame-rewrite-on-every-handshake
     design with a bounded read-append-truncate-rewrite using the
     stdlib `csv` module.

## Testing

22 tests in `tests/test_timer_ng.py`, all passing against the real
cloned `jayofelony/pwnagotchi` framework: real plugin registration;
`_network_name` handling dict, hidden-SSID, and bare-MAC-string AP
shapes; a full deauth-to-handshake sequence producing one correctly
timed and labeled row; a passive capture (no prior deauth) writing
nothing; a bare-MAC-string `on_handshake` call not crashing and still
recording; per-network best/worst stats accumulating correctly across
several captures; CSV rotation keeping only the most recent
`max_rows` rows; `max_rows=0` preserving every row (reproducing the
original's unbounded behavior on request); `show_on_screen=false`
never touching the UI; `show_on_screen=true` updating the element for
both `ui_metric` settings; the webhook page listing a captured
network and not crashing with zero data; and `on_epoch` resetting
in-flight timing state so a later handshake is correctly treated as
passive.

Two real bugs were caught by the tests during the build itself (not
present in the original, both introduced and then fixed during this
rewrite - see the commit's test history): `_append_and_rotate` only
read the existing CSV when `max_rows` was truthy, so `max_rows=0`
silently discarded all prior history instead of preserving it
unbounded as intended; and the webhook "no data yet" test crashed
with an `AttributeError` from a test that constructed `TimerNG()`
directly without setting `.options` first.

## Still open

- No real-hardware measurement yet of realistic CSV growth rate, to
  sanity-check whether `max_rows = 5000`'s default is a reasonable
  cap in practice - see README's "Still open" section.
- On-screen element placement on the user's actual 3.5" TFT screen
  hasn't been visually checked yet.
