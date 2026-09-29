# HandshakerNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass (Tailscale
detection, multi-interface LAN IP guessing, verifying `/boot` paths on
a real image) before it's considered done (see NOTES.md's "Still
open" section).

A bugfix + feature-upgrade rebuild of `handshaker.py`
(itsdarklikehell/Allordacia, v1.0.1) - a plugin meant to help you
check on your pwnagotchi's handshake captures and boot-time
state without needing SSH access.

**Read this whole file before enabling this plugin.** The original
never actually worked at all - `on_loaded()` called a `load_data()`
method that was never defined anywhere in the class, so it crashed
with `AttributeError` on every single load - and its `on_webhook`
(the plugin's entire stated purpose) only logged and returned `None`.
This rebuild fixes both (see NOTES.md for the full writeup) and adds
the 5 approved features on top: a real JSON status endpoint, a simple
HTML status/file-listing page, safe per-file handshake downloads, an
easy `bind_scope` helper, and a live on-screen handshake count.

## Requirements & dependencies

- **Flask** and **Werkzeug** (Python packages). Pwnagotchi's own
  built-in web UI is itself a Flask application, so both are normally
  already present on a stock jayofelony pwnagotchi image - **this
  still needs verifying against a real device**, since this project's
  sandbox has no way to inspect a real image's installed Python
  packages directly. If either is missing, install with:
  ```
  sudo pip3 install flask werkzeug
  ```
- **Tailscale** (optional, CLI tool `tailscale`) - only needed if you
  want `bind_scope = "auto"` (the default) or `bind_scope =
  "tailscale"` to actually find a Tailscale address. If it's not
  installed/connected, `"auto"` safely falls back to localhost-only
  and tells you so; `"tailscale"` refuses to start and tells you why.
  Not required for `"localhost"` or `"lan"`.
- Python: nothing else beyond the standard library (`glob`, `os`,
  `subprocess`, `threading`). No `scapy` - the original declared it as
  a dependency but never actually used it anywhere (a bogus
  dependency, removed here).

## Install

1. Copy `handshaker_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
3. Check `data_path` matches where your handshakes actually get
   written (default `/root/handshakes`, matching the original).
4. Decide `bind_scope` (default `"auto"` is reasonable for most
   setups - see "Choosing a bind_scope" below). **Read the "No
   authentication" section below first.**
5. If you were running the original `handshaker.py`, disable/remove
   it first (`enabled = false` under its own `[main.plugins.handshaker]`
   section, or delete the file) - this plugin replaces it entirely.
6. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```
7. Check the pwnagotchi log for a line like:
   ```
   [HandshakerNG] listening (auto-tailscale) - visit http://100.x.y.z:8083/
   ```
   That's the exact URL to open in your browser. It's also shown as a
   banner at the top of the page itself once you're in.

## What you get

- **On the pwnagotchi's screen**: a live handshake count (re-scans
  the data directory every `ui_refresh_interval` seconds, default 30
  - not on every single screen redraw).
- **A status page** (`/`) - handshake count, total capture size, most
  recent capture timestamp, and a table of every captured `.pcapng`
  file with its size, capture time, and a download link.
- **A JSON endpoint** (`/status.json`) - the same data as the status
  page, as JSON, for scripting. Add `?format=json` to `/` itself to
  get the byte-identical payload from that same route.
- **Per-file downloads** (`/download/<filename>`) - the raw `.pcapng`
  capture file only. Requested filenames are sanitized and checked to
  make sure the resolved path is actually inside `data_path` before
  anything is served - a traversal attempt (`../`, an absolute path,
  anything outside `data_path`, or a non-existent file) gets a plain
  404, nothing more.

This is deliberately narrower than the separate `handshakes_dl_ng.py`
suite already in this repo, which lists hash files, GPS data, and
offers a bulk ZIP download through pwnagotchi's shared web UI. This
plugin's version is just the raw captures, through its own separate
server - see NOTES.md's "Scope vs. handshakes-dl-suite" section for
why both exist.

## Choosing a `bind_scope`

| Value | Behavior |
|---|---|
| `"auto"` (default) | Uses your Tailscale IP if Tailscale is detected (safest remote option); otherwise binds `127.0.0.1` only and logs/shows how to reach it anyway. |
| `"tailscale"` | Requires Tailscale. Refuses to start if it's not detected - use this if you specifically never want a fallback to anything broader. |
| `"localhost"` | Always `127.0.0.1` only, no matter what. Reachable on-device, or via `ssh -L 8083:localhost:8083 pi@<device>` from elsewhere. |
| `"lan"` | Binds every interface (`0.0.0.0`) - reachable by **anyone on your local WiFi/network**, not just you. Logs a loud warning every time it starts this way. Only choose this if you understand and accept that. |

## No authentication - read this

Unlike `web2ssh_ng.py`, this plugin's server has **no login of any
kind**. HTTP Basic Auth was one of the 7 ideas presented for this
rebuild but was explicitly **not approved** for this round (see
NOTES.md's "not built this round" section) - it's a real option for a
future pass, not an oversight.

That means `bind_scope` is your *only* access control. Anyone who can
reach the bound address can see your handshake count/list and
download your raw capture files (which can potentially be used to
crack the WiFi password of networks you've captured). Prefer
`bind_scope = "auto"` (with Tailscale actually running) or
`"tailscale"` over `"lan"` unless you specifically understand and
accept the wider exposure - the plugin logs a loud warning every time
it starts in `"lan"` mode for exactly this reason.

## `sync_to_boot`

The original's `on_ready()` always rsynced your handshakes to
`/boot/handshakes`, copied logs to `/boot`, and (if present) moved
`/boot/custom_plugins` into place - useful if you pull the SD card to
grab data, but not everyone wants it. `sync_to_boot = true` (the
default) preserves this exactly; set it `false` to skip all of it.
Each step now fails gracefully on its own (a missing
`/boot/custom_plugins` folder, for example, no longer matters) instead
of being able to take the rest of the sequence down.

## Troubleshooting

**"I can't reach the page"**

1. Check the pwnagotchi log for the exact line
   `[HandshakerNG] listening (...) - visit http://...`. If you don't
   see it, and instead see `server not started`, check your
   `bind_scope` choice against the table above (e.g. `"tailscale"`
   refuses to start with no Tailscale detected).
2. `"auto"` fell back to `127.0.0.1` (log says "Tailscale not
   detected")? You can only reach it from the device itself, or via
   an SSH tunnel: `ssh -L 8083:localhost:8083 pi@<device-ip>`, then
   visit `http://localhost:8083/` on your own machine. Or set
   `bind_scope = "lan"` to reach it from your WiFi network instead
   (see the "No authentication" section above before doing this).

**"The status page shows 0 handshakes but I know I have some"**

Check `data_path` matches where your handshakes actually land (some
setups use a different bettercap handshake directory than
`/root/handshakes`) - check `[bettercap]` / your capture plugin's
config, or just `ls` the directory on the device.

**"A download link 404s"**

That means either the file no longer exists, or the requested
filename didn't resolve inside `data_path` (this is the traversal
protection working as intended, not a bug) - re-visit `/` to get a
fresh, current list of what's actually there.

## Security notes

- No authentication (see above) - `bind_scope` is your only real
  control.
- Download requests are sanitized with `os.path.basename()` and the
  resolved absolute path is checked to be inside `data_path` (and to
  end in `.pcapng`) before anything is served - see NOTES.md for the
  exact function and its test coverage.
- Not implemented this round (see NOTES.md "not built this round"):
  HTTP Basic Auth, and "since last check" delta tracking of new
  handshakes.
