# Web2SSHNG

**Status: work-in-progress (in `plugins-wip`).** Not yet moved to
`complete-plugins` - still needs a real-hardware pass (Tailscale
detection, multi-interface LAN IP guessing) before it's considered
done (see NOTES.md's "Still open" section).

A security-hardening rebuild of `web2ssh.py` (WPA2, v0.1.3) - a
browser-based panel for running root shell commands on your
pwnagotchi (one-click shortcuts like Reboot/Shutdown/Pwnkill, plus an
optional free-text command box), behind HTTP Basic Auth.

**Read this whole file before enabling this plugin.** The original had
three real, source-verified bugs that combined into the single most
severe finding of this project's plugin audit: the plugin always ran
with hardcoded `"changeme"`/`"changeme"` credentials (configured
values were silently ignored) on every network interface at once, and
`Flask.run()` being called directly would have hung the entire
pwnagotchi plugin-loading process at startup the moment it was
enabled. This rebuild fixes all three (see NOTES.md for the full
writeup) and adds three approved safety/usability features on top:
mandatory real credentials with no default fallback, an easy
bind-scope helper (auto-detected Tailscale, or localhost/LAN), and an
allowlisted "shortcuts" command mode that's on by default.

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
- Python: nothing else beyond the standard library
  (`subprocess`, `threading`, `hmac`, `re`).

## Install

1. Copy `web2ssh_ng.py` into your custom plugins folder.
2. Add the block from `config.toml` to `/etc/pwnagotchi/config.toml`.
3. **Set `username` and `password`.** These are left blank in
   `config.toml` on purpose - the plugin will log an error and refuse
   to start its server at all if they're missing, blank, or look like
   an obvious default value (e.g. `"changeme"`, `"admin"`,
   `"password"`). Pick a real username and a real password.
4. Decide `bind_scope` (default `"auto"` is reasonable for most
   setups - see "Choosing a bind_scope" below).
5. If you were running the original `web2ssh.py`, disable/remove it
   first (`enabled = false` under its own `[main.plugins.web2ssh]`
   section, or delete the file) - this plugin replaces it entirely.
6. Restart pwnagotchi:
   ```
   sudo systemctl restart pwnagotchi
   ```
7. Check the pwnagotchi log for a line like:
   ```
   [Web2SSHNG] listening (auto-tailscale) - visit http://100.x.y.z:8082/
   ```
   That's the exact URL to open in your browser. It's also shown as a
   banner at the top of the page itself once you're in.

## Choosing a `bind_scope`

| Value | Behavior |
|---|---|
| `"auto"` (default) | Uses your Tailscale IP if Tailscale is detected (safest remote option); otherwise binds `127.0.0.1` only and logs/shows how to reach it anyway. |
| `"tailscale"` | Requires Tailscale. Refuses to start if it's not detected - use this if you specifically never want a fallback to anything broader. |
| `"localhost"` | Always `127.0.0.1` only, no matter what. Reachable on-device, or via `ssh -L 8082:localhost:8082 pi@<device>` from elsewhere. |
| `"lan"` | Binds every interface (`0.0.0.0`) - reachable by **anyone on your local WiFi/network**, not just you. Logs a loud warning every time it starts this way. Only choose this if you understand and accept that. |

## `command_mode`: shortcuts vs. free

- **`"shortcuts"` (default, recommended).** Only the exact commands
  listed in `[main.plugins.web2ssh_ng.shortcuts]` can ever run - the
  page only shows those buttons, no free-text box at all. Add/edit
  entries in that table to add your own shortcuts without touching
  the plugin code.
- **`"free"`.** Also shows the original's free-text command box,
  which runs *anything* you type as root. The page shows a clear
  warning banner whenever this mode is active. Only enable this if you
  specifically need to run ad-hoc commands you haven't pre-defined as
  a shortcut, and you've already restricted `bind_scope` appropriately.

## What you get on screen

Nothing on the pwnagotchi's e-ink display - this is a web-only plugin,
same as the original. Visit the URL logged/shown at startup in a
browser; you'll be prompted for the username/password you configured.

## Troubleshooting

**"I can't reach the page"**

1. Check the pwnagotchi log for the exact line
   `[Web2SSHNG] listening (...) - visit http://...`. If you don't see
   it, and instead see `refusing to start`, see the next section - the
   server never started at all.
2. Check which `bind_scope` you're using (`config.toml`) against the
   table above:
   - `"auto"` fell back to `127.0.0.1` (log will say "Tailscale not
     detected")? You can only reach it from the device itself, or via
     an SSH tunnel: `ssh -L 8082:localhost:8082 pi@<device-ip>`, then
     visit `http://localhost:8082/` on your own machine. Or set
     `bind_scope = "lan"` to reach it from your WiFi network instead
     (less safe - see the table above), or get Tailscale running for
     safe remote access.
   - `"tailscale"` and it refused to start? Tailscale isn't detected -
     run `tailscale status`/`tailscale ip -4` on the device to check,
     or switch to `"auto"`/`"lan"`/`"localhost"`.
   - `"lan"` and still unreachable? Check your device's actual IP
     (`ip addr`) and make sure you're on the same network segment -
     `0.0.0.0` binds all interfaces on the device itself, but doesn't
     change your network's routing/firewalling.

**"It won't start at all" (nothing listening, no URL logged)**

This means credential validation failed - check the log for a line
like:
```
[Web2SSHNG] refusing to start: username is missing or blank. ...
```
or
```
[Web2SSHNG] refusing to start: password looks like a default/placeholder value. ...
```
This is **intentional, not a bug** - the plugin will never start its
server with a missing, blank, or obviously-default credential pair
(see NOTES.md, "new feature 1"). Set a real `username` and `password`
in `config.toml` and restart pwnagotchi.

If credentials look fine but it still won't start with
`bind_scope = "tailscale"`, check the log for the Tailscale-specific
refusal message - that mode intentionally refuses rather than falling
back to something broader.

## Security notes

- This plugin's entire purpose is running root shell commands from a
  browser - treat the URL/credentials with the same care you would a
  root SSH key. Prefer `bind_scope = "tailscale"` or the default
  `"auto"` (with Tailscale actually running) over `"lan"` whenever
  possible.
- Basic Auth credentials are compared with `hmac.compare_digest`
  (constant-time), but Basic Auth itself is still just base64, not
  encryption - this plugin does not add TLS. Binding to Tailscale (an
  already-encrypted overlay network) or an SSH tunnel is how this
  project recommends getting confidentiality in transit, rather than
  adding a self-signed TLS certificate story to this plugin.
- Not implemented this round (see NOTES.md "still open"): CSRF
  protection, a persistent audit log of executed commands, and
  brute-force lockout/rate-limiting on failed logins.
