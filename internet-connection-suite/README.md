# InternetConnectionNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass before it's
considered done.

A consolidation of three separate, competing internet-status plugins
found in the audit - `internet-connection.py` (itsdarklikehell),
`wanmon.py` (CyberGladius), and `internet-conection.py` (neonlightning,
typo in the filename is original) - into one plugin, keeping the best
design choice from each and fixing the real bugs found in the other
two.

Target hardware: Raspberry Pi 4 + 3.5" TFT screen, jayofelony 64-bit
pwnagotchi image.

## Requirements & dependencies

- Python: none beyond the standard library (`socket`). Both
  `internet-connection.py` and `wanmon.py` declared an unused `scapy`
  pip dependency in their `__dependencies__` block that neither ever
  actually imported - dropped entirely here.
- No config values are required to get something useful - every
  option has a working default.

## What's fixed vs. the originals

1. **`wanmon.py`'s crash-on-load bug** - it read
   `self.options["position_x"]`, `["position_y"]`, `["testip"]`,
   `["testdns"]` via direct indexing with no fallback. This fork's
   loader never merges a plugin's `__defaults__` into `self.options`
   (see `pwnagotchi/plugins/__init__.py`'s `load()`), so a
   config.toml missing any of those keys would `KeyError`-crash the
   plugin on load. Every option here is read via a real
   `self.options.get(key, default)` helper.
2. **`internet-conection.py`'s blocking-render-path bug** - it called
   `urllib.request.urlopen(url, timeout=0.5)` synchronously inside
   `on_ui_update`, which fires on every single UI refresh tick -
   meaning up to half a second of the render thread blocking, every
   refresh, whenever the connection was slow or down. That design is
   dropped entirely; the only network probe here runs from `on_epoch`
   (once per epoch, not per render) and never from a UI hook.
3. **The "stuck on connected forever" gap.** `internet-connection.py`
   only ever turns its icon on, via the real `internet_available`
   event (confirmed in `pwnagotchi/cli.py` - it only fires when
   `grid.is_connected()` is true, and there is no matching "lost"
   event on this fork). It can never show disconnected again once
   connected, even if the connection actually drops later. `wanmon.py`
   tried to solve this with its own periodic `on_epoch` re-check, but
   its `internet_available`/`dns_resolving` booleans were only ever
   set to `True` in `test_internet_connection()` and never reset to
   `False` on a failed check - so it had the exact same "stuck"
   problem in practice, just for a subtler reason. This rebuild's
   optional active re-check (see below) actually resets state both
   ways.

## What's added

- **`active_recheck`** (default: on) - an optional periodic
  connectivity probe, run once per epoch and once at startup, using a
  plain `socket.create_connection()` TCP probe (no HTTP request, no
  extra dependency). Unlike both `internet-conection.py`'s blocking
  urllib call and `wanmon.py`'s `os.system("ping ...")` shell-out,
  this never blocks the UI thread and needs no external binary. Set
  `active_recheck = false` to reproduce `internet-connection.py`'s
  original behavior exactly (purely event-driven, never probes on its
  own).
- **Configurable position, label, and connected/disconnected values**
  - `wanmon.py` had position options that `internet-connection.py`
  lacked; this rebuild has them for both, plus the label text and the
  two short status strings shown.
- **Configurable probe target** (`recheck_test_host`/`recheck_test_port`/
  `recheck_timeout`) instead of a hardcoded `8.8.8.8`/`google.com`.

## Install

1. Copy `internet_connection_ng.py` into your custom plugins folder
   (`custom_plugins` in `config.toml`, typically
   `/etc/pwnagotchi/custom-plugins/`).
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
   Every default works out of the box - no required edits.
3. If you were running `internet-connection.py`, `wanmon.py`, or
   `internet-conection.py`, disable/remove those first - all three
   add their own UI element and would visually collide with this one.
4. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Icon never shows "connected" even though you have internet | Check `recheck_test_host`/`recheck_test_port` are reachable from your pwnagotchi (some networks block outbound port 53 probes specifically) - try a different host/port, or disable `active_recheck` and rely on the framework's own `internet_available` event instead |
| Icon shows "connected" but you've since unplugged/lost internet | Only happens with `active_recheck = false` - that setting intentionally reproduces the original's event-only, one-way behavior. Turn it back on to get an active re-check each epoch. |
| Position looks off on a non-standard screen | Set `position_x`/`position_y` explicitly in `config.toml` instead of relying on the auto-centered default, which assumes a display width similar to the reference 3.5" TFT |

## Still open / needs real-hardware testing

- The `active_recheck` probe's real-world latency/false-negative rate
  on your actual network hasn't been measured outside a sandbox - if
  1 second (`recheck_timeout`) is too aggressive or too lax for your
  connection, adjust it.
- Visual placement/overlap with other UI elements on the real 3.5"
  TFT screen needs a look once installed.
