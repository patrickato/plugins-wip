# Notes: InternetConnectionNG

## Why a consolidation instead of three separate fixes

`MASTER_PLUGIN_LIST.md`'s Display/UI category (Cluster 34) tracked
three independent implementations of the exact same idea - an
internet-connectivity status icon - under one bullet:
`internet-connection.py` (itsdarklikehell), `wanmon.py`
(CyberGladius), and `internet-conection.py` (neonlightning, typo in
the filename is original, not a mistake here). Rather than fix each
one separately and keep three competing plugins on the list, this
rebuild merges the best design choice from each into one plugin,
matching the precedent already set by `DiscoHash Suite` (consolidated
`DiscoHash`/`discoBoss.py`/`hashbot.py`) and `HashesPwnagotchiNG`/
`BanthexNG` (Cluster 31) earlier in this audit.

## Bugs found in each original

- **`internet-connection.py`** - no crash bugs; correctly uses the
  real `internet_available` event. Its only real limitation: that
  event only ever fires when the framework detects a connection
  (confirmed in `pwnagotchi/cli.py` - both `do_manual_mode` and
  `do_auto_mode` call `plugins.on('internet_available', agent)` only
  inside an `if grid.is_connected():` branch), and there is no
  matching "connection lost" event anywhere on this fork. So once this
  plugin shows "C", it shows "C" for the rest of the session, even if
  the connection later drops. Also declares `__dependencies__ =
  {"pip": ["scapy"]}` despite never importing `scapy` anywhere in the
  file - a stray, unused dependency.
- **`wanmon.py`** - `on_ui_setup` reads `self.options["position_x"]`
  and `self.options["position_y"]` via direct indexing with no
  fallback; `test_internet_connection()` reads `self.options["testip"]`
  and `self.options["testdns"]` the same way. Since this fork's loader
  never merges `__defaults__` into `self.options`
  (`pwnagotchi/plugins/__init__.py`'s `load()` only ever does
  `plugin.options = config['main']['plugins'][name]` - a straight
  assignment, no merge with the class's `__defaults__` dict), any
  config.toml missing one of these four keys crashes the plugin with
  `KeyError` on load. Separately, and more subtly: `self.internet_available`
  and `self.dns_resolving` are initialized `False` in `__init__`, but
  `test_internet_connection()` only ever sets them to `True` (lines 99
  and 113 in the original) - never resets them to `False` on a failed
  ping. So once either flag flips true, it stays true for the rest of
  the run regardless of what later pings actually find - the same
  "stuck" problem as `internet-connection.py`, just caused by a
  reset-forgetting bug instead of a missing event. Also declares the
  same unused `scapy` dependency.
- **`internet-conection.py`** - `_is_internet_available()` calls
  `urllib.request.urlopen('https://www.google.com', timeout=0.5)`
  synchronously, and this is called from `on_ui_update`, which fires
  on every single UI refresh tick. A slow or down connection means up
  to 0.5s of the render thread blocking on every refresh - a visible,
  repeating stutter. It does correctly flip back to "Disconnected" on
  a failed check, unlike the other two, but at that real cost.

## What this rebuild keeps, drops, and fixes

- **Keeps**: `internet-connection.py`'s use of the real
  `internet_available` event as the primary, zero-cost signal.
- **Drops**: `internet-conection.py`'s custom `InetIcon` widget/image
  overlay (loads a `wifi.png` expected to sit next to the plugin file
  - adds a packaging dependency for a cosmetic icon this rebuild
  doesn't need) and its blocking urllib design entirely; the unused
  `scapy` dependency from both other originals.
- **Fixes and adds**: an `active_recheck` option (default on) that
  runs a plain `socket.create_connection()` TCP probe from `on_epoch`
  (once per epoch, never from a render hook) and once at `on_ready`,
  so the icon can now correctly flip back to "disconnected" if the
  connection actually drops - addressing the "stuck" gap common to
  both `internet-connection.py` and `wanmon.py`, without inheriting
  either's blocking-call or reset-forgetting bug.

## Testing

18 tests in `tests/test_internet_connection_ng.py`, all passing
against the real cloned `jayofelony/pwnagotchi` framework: real plugin
registration, default vs. configured position/label/values, the
`internet_available` event setting "connected", `active_recheck=false`
never probing and never reverting a "connected" state (reproducing the
original's exact one-way behavior), `active_recheck=true` correctly
flipping the icon both ways across separate epochs, `on_ready`
performing (or skipping) an initial probe per the same setting, clean
element removal on unload, and the probe using the configured
host/port/timeout rather than anything hardcoded.

## Still open

- No real-hardware verification yet of probe latency/reliability on
  the user's actual network - see README's "Still open" section.
