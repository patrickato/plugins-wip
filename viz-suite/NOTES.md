# Notes: VizNG

## Why this one needed more than the originally documented fix

Cluster 34's table flagged `viz.py` for one bug: `self.channel` never
initialized in `__init__`, only set inside `on_channel_hop`, causing
an `AttributeError` on the update webhook if hit before the first
channel-hop event. Reading the source in full for the rebuild
surfaced something more fundamental underneath it.

## Bugs found

- **Fatal import-time crash (new finding, supersedes the earlier
  note)**: the file's very first non-stdlib import,
  `from pwnagotchi.wifi import freq_to_channel`, points at a module
  that does not exist anywhere on this fork. Confirmed directly:
  `python3 -c "import pwnagotchi.wifi"` against the real cloned
  `jayofelony/pwnagotchi` raises `ModuleNotFoundError`, and there is
  no `pwnagotchi/wifi.py` file anywhere in the tree - only
  `pwnagotchi/mesh/wifi.py`, which does define `freq_to_channel`. This
  plugin could never load on this fork at all, on any hardware, before
  even reaching the bug the audit table already knew about - the same
  shape of problem found in `crack_house.py` earlier in this batch
  (a real API existing, just not where the plugin assumed).
- **Uninitialized `self.channel`** (originally flagged, confirmed):
  `create_graph` already tolerated a falsy `channel` argument
  gracefully (`channel_line = ... if channel else dict()`) - the bug
  was purely that `self.channel` didn't exist as an attribute at all
  until the first `on_channel_hop` call, so reading it any earlier
  raised `AttributeError` rather than getting a falsy `None`.
- **Dependency-declaration bug**: `__dependencies__` lists `pandas`,
  which is never imported or used anywhere in the file - the same
  stray-dependency pattern found repeatedly elsewhere in this audit.

## What this rebuild keeps, drops, and fixes

- **Keeps**: the entire graph-building design as-is - node/edge
  layout, per-node color memoization, the plotly-based webhook page
  and its client-side polling JavaScript. This file was otherwise
  well-written; nothing here needed a redesign, just two real bugs
  fixed.
- **Drops**: the unused `pandas` dependency declaration.
- **Fixes**: imports `freq_to_channel` from the real
  `pwnagotchi.mesh.wifi` module instead of the nonexistent
  `pwnagotchi.wifi`; initializes `self.channel = None` in `__init__`.

## What's added, on top of the bug fixes (user-approved)

Suggested after the initial rebuild, and all 3 approved for building:

1. **Last-updated timestamp** (`_last_update`, set in
   `on_unfiltered_ap_list`, served via a new `meta` webhook path). The
   page previously had no way to signal staleness at all - a stalled
   wifi scan and a perfectly current graph looked identical.
2. **Cracked-node cross-referencing** (`_cracked_hostnames`,
   `create_graph`'s new `cracked` parameter). Reads CrackHouseNG's
   `saving_path` file directly - the only shared-state mechanism
   available between plugins on this fork, since there's no plugin
   registry or shared cache to query instead. `create_graph` is a
   `@staticmethod` with `@lru_cache`, so `cracked` is passed as a
   `frozenset` (hashable, required for the cache key) rather than a
   plain `set`. Matching is done on `.lower()` on both sides, mirroring
   CrackHouseNG's own case-insensitive-matching addition, so the two
   plugins agree on what counts as "the same network" even if their
   two data sources disagree on casing.
3. **Configurable poll interval** (`poll_interval_ms`, substituted into
   `TEMPLATE` via a `__POLL_INTERVAL_MS__` placeholder token and a
   plain string `.replace()` at render time - not Jinja's own
   `{{ }}`/`.format()`, since the template is already full of its own
   literal `{` characters from Jinja block syntax and JS object
   literals, which would collide with `str.format()`). A second
   endpoint (`meta`) is polled on the same interval as the graph data.

Both new numeric options (`poll_interval_ms`) go through a small
validating helper (`_poll_interval_ms`) that falls back to the
original hardcoded default on anything non-numeric or `<= 0`, the same
defensive pattern used for MoreUptimeNG's `cycle_interval` earlier in
this batch.

## Testing

26 tests in `tests/test_viz_ng.py`, all passing against the real
cloned `jayofelony/pwnagotchi` framework for everything except plotly
itself. The original 13 cover: real plugin registration; the module
importing `freq_to_channel` successfully at all (directly proving the
import fix, since the original import would have raised
`ModuleNotFoundError` at collection time); `self.channel` being
initialized; the update webhook not raising `AttributeError` before
any channel-hop event has fired (directly reproducing and confirming
the fix for the original bug); AP data storage and channel updates
via the two real hooks (confirmed real via
`pwnagotchi/agent.py`: `plugins.on("unfiltered_ap_list", ...)` and
`plugins.on('channel_hop', ...)`); `create_graph` handling both a
`None` channel and a real one, including with no data at all;
per-node color memoization; the "/" webhook path rendering its
template; and unknown paths correctly 404ing.
13 new tests cover the additions above: `_last_update` starting unset
and being set by `on_unfiltered_ap_list`; the `meta` webhook reporting
that timestamp (and "never" before any data has arrived); a configured
`poll_interval_ms` appearing in the rendered page's source with the
placeholder fully substituted; an invalid `poll_interval_ms` falling
back to the default; `_cracked_hostnames` correctly reading
CrackHouseNG's `saving_path` file; a cracked AP showing up
`[CRACKED]`/starred in `create_graph`'s output; that cross-referencing
matching case-insensitively; and a missing or disabled
(`crack_house_saving_path=""`) file yielding no matches instead of
crashing.

`plotly` itself wasn't installable in this build's sandboxed network
(pip couldn't reach a distribution for it), so
`tests/stub_deps/plotly/` provides a small local stand-in covering
just the two things `create_graph` actually touches -
`plotly.graph_objects.Scatter` (a plain kwargs container) and
`plotly.utils.PlotlyJSONEncoder` (a `json.JSONEncoder` subclass that
falls back to an object's `__dict__`). This is enough to exercise the
graph-building logic's real control flow and confirm both fixes; it
is not a substitute for testing against the genuine plotly library.

## Still open

- Real `plotly` hasn't been exercised, only a local stand-in covering
  its public surface used here - see README's "Still open" section.
- No real-hardware/browser check yet of the actual rendered graph.

## Post-cluster-review fix: real handshake directory default

`crack_house_saving_path` defaulted to a path under `/root/handshakes`.
Changed to `/etc/pwnagotchi/handshakes` to match crack-house-suite's
own updated `saving_path` default, so the already-cracked-network
cross-referencing still works out of the box between the two suites.
Still fully overridable.
