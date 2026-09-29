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

## Testing

13 tests in `tests/test_viz_ng.py`, all passing against the real
cloned `jayofelony/pwnagotchi` framework for everything except plotly
itself: real plugin registration; the module importing
`freq_to_channel` successfully at all (directly proving the import
fix, since the original import would have raised
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
