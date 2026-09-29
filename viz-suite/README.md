# VizNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done.

A fixed rebuild of `viz.py` (itsdarklikehell/dadav) - a webhook page
that graphs every nearby AP and its clients (signal strength vs.
channel) using plotly.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image. This one's webhook page is viewed from a phone/laptop
browser, not on the device's own small screen.

## Requirements & dependencies

- Python: `plotly`, `flask` (already a pwnagotchi dependency). The
  original also declared `pandas` as a dependency - it's never
  imported anywhere in the file, and is dropped here.
- Real hardware needs `pip install plotly` (this sandbox's package
  index didn't have it reachable, so this build's tests use a small
  local stand-in for plotly's public interface - see NOTES.md).

## What's fixed vs. the original

1. **The plugin could never load at all, on this fork, ever.** The
   original's import line,
   `from pwnagotchi.wifi import freq_to_channel`, points at a module
   that simply does not exist on this fork - confirmed there is no
   `pwnagotchi/wifi.py` anywhere in the real cloned framework. The
   actual module is `pwnagotchi/mesh/wifi.py`. Every attempt to load
   this plugin failed immediately with `ModuleNotFoundError`, before
   any of the plugin's own code ever ran. This is a bigger, previously
   undocumented bug that supersedes the originally-flagged issue
   below - fixed by importing from `pwnagotchi.mesh.wifi` instead.
2. **Uninitialized `self.channel`** (originally flagged): only ever
   set inside `on_channel_hop`. Hitting the `/plugins/viz_ng/update`
   webhook before the very first channel-hop event fired (plausible
   right after boot) raised an unhandled `AttributeError` inside
   `create_graph`, 500ing the request. Fixed by initializing
   `self.channel = None` in `__init__` - `create_graph` already
   handled a falsy channel gracefully (`channel_line = ... if channel
   else dict()`), so this alone fully closes the gap.
3. **Dependency-declaration bug**: declared `pandas` as a pip
   dependency, never imported anywhere in the file - dropped.

## What's kept

- The whole graph-building approach and its plotly-based webhook page,
  unchanged in substance - this rebuild is a minimal-diff fix, not a
  redesign, since the underlying visualization idea and the original
  code around it were otherwise sound.

## Install

1. Copy `viz_ng.py` into your custom plugins folder.
2. `pip install plotly` if you don't already have it (`flask` ships
   with pwnagotchi already).
3. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
4. If you were running the original `viz.py`, disable/remove it first.
5. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```
6. Visit `http://pwnagotchi.local:8080/plugins/viz_ng/` to see the
   graph.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Page loads but graph never appears | No `on_unfiltered_ap_list` event has fired yet - wait for a wifi scan, or check the page's own "Waiting for data..." placeholder is still showing |
| Import error on load | Confirm `plotly` is actually installed for the same Python pwnagotchi runs under |

## Still open / needs real-hardware testing

- The real `plotly` package wasn't installable in this build's sandbox
  (network-restricted), so `create_graph`'s exact JSON output shape
  was verified against a small local stand-in covering plotly's public
  interface used here (`go.Scatter`, `plotly.utils.PlotlyJSONEncoder`),
  not the genuine library. The graph logic itself is unchanged from
  the original - only the import path and channel initialization
  changed - but a real-hardware/real-plotly check is still worthwhile
  before calling this fully done.
