# TerminalNG

Stands up a WebSSH2 systemd service and serves an in-browser terminal
through the pwnagotchi web UI, so you get a shell without a separate SSH
client. Rebuilt from `terminal2.py` (`WebSSH2Plugin`).

## What was actually broken

Two missing imports made the first-run service-verification loop crash
every time it needed to check whether the newly-started service was
actually listening yet:

- `re` was never imported, but `extract_ip_address()` calls `re.search(...)`.
- `time` was never imported, but the polling loop calls `time.sleep(5)`.

Since `systemctl status` on a freshly-started service almost never
reports "listening on" on its very first check, the loop reliably hit
one or the other on a typical first install - either `re` (if the
service already looked active on iteration 0) or `time` (if not).
Both are caught by the framework's own exception handling around
`on_loaded` (it runs in a background thread there), so this didn't crash
the whole daemon - it just meant the verification loop silently gave up
after one failed iteration, `self.ready` never became `True`, and you'd
never get a "started successfully" log line even though the underlying
`systemctl enable`/`start` calls (which ran *before* the loop) likely
still worked.

Separately, the web terminal iframe only appeared for two specific,
hardcoded IP prefixes (`10.0.0.*` and `192.168.44.44`) baked in from the
original author's own network - anyone on a different subnet would just
see a blank container with no terminal and no explanation.

## What this rebuild adds

- Fixed the two missing imports.
- `_service_status_output()` now handles `systemctl status`/`is-active`
  returning a nonzero exit code for an inactive unit (a real
  `subprocess.CalledProcessError`) instead of letting it propagate -
  this is expected, ordinary output for this command, not an error.
- **`allowed_networks`** - a configurable list of CIDR ranges (default:
  the three standard private ranges, `10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`) instead of two hardcoded IPs. Real CIDR matching via
  Python's `ipaddress` module, not string-prefix guessing.
- When access is denied, the page now says so and shows which ranges
  are allowed, instead of silently rendering an empty container.

## Configuration

See `config.toml`. The defaults should work for most home networks
as-is; narrow `allowed_networks` down to your specific subnet if you
want it tighter.

## Still open

- No real-device test of the actual systemd unit install/WebSSH2
  service, or of the rendered iframe against a live WebSSH2 instance -
  the sandbox test suite exercises the plugin's own control-flow logic
  (service detection, polling, access control) with `os.system`/
  `subprocess` mocked out, since this sandbox has no systemd.
