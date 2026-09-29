# Notes: TerminalNG

## Why this one

`terminal2.py` (`WebSSH2Plugin`) stands up a real systemd service
(WebSSH2) and serves an in-browser terminal through the pwnagotchi web
UI - genuinely useful, no separate SSH client needed. It had two
missing imports that broke its own first-run verification, plus a
hardcoded access-control gap.

## Bugs found

- `extract_ip_address()` calls `re.search(...)`, but `re` is never
  imported anywhere in the file - guaranteed `NameError` the first time
  this method is called.
- The polling loop calls `time.sleep(5)`, but `time` is never imported
  either - guaranteed `NameError` on that branch.
- Between these two, the first-run verification loop (`for i in
  range(12): ...`) was guaranteed to crash on essentially every fresh
  install: iteration 0 either looks active (hits the `re` NameError) or
  doesn't (hits the `time` NameError). Since `on_loaded` runs in a
  framework-managed background thread whose exceptions are caught and
  logged, this didn't crash the daemon - it just meant `self.ready`
  never became `True` and the "started successfully" log line never
  appeared, even though the `systemctl enable`/`start` calls that ran
  *before* the loop had already fired and likely worked.
- The webhook's iframe only rendered for two hardcoded IP prefixes
  (`10.0.0.*`, `192.168.44.44`) - the original author's own network,
  not a general default. Anyone on a different subnet (or even a
  slightly different 192.168.x.x range) got a silently empty page.

## What this rebuild keeps, drops, and adds

- **Keeps**: the systemd unit file content, the enable/start/
  stop/disable lifecycle, the overall on_loaded/on_unload structure.
- **Fixes**: the two missing imports; `_service_status_output()` now
  wraps `subprocess.check_output` and handles the nonzero exit code
  `systemctl status`/`is-active` returns for an inactive unit (ordinary
  output, not an exceptional case, but the original's direct
  `check_output` call would have let a `CalledProcessError` propagate
  uncaught in that case too - fixed as part of the same cleanup).
- **Adds**: `allowed_networks`, a configurable CIDR list (default: the
  three standard private ranges) checked with Python's `ipaddress`
  module instead of hardcoded string prefixes; a real "access denied"
  message instead of a silently empty iframe container when the
  request doesn't match; `ws_host`/`ws_port`/poll-attempt/poll-interval
  as config options instead of further hardcoded values.

## Testing

26 tests in `tests/test_terminal_ng.py`, all passing against the real
cloned `jayofelony/pwnagotchi` framework and the real `flask` library
(`os.system`/`subprocess` are mocked since this sandbox has no systemd
to actually install a unit against). Covers: real plugin registration;
`extract_ip_address` correctly parsing a real `systemctl status` block
(and returning `None` when there's no match) - the method that was
previously broken by the missing `re` import; the full
unit-missing → create → enable → start → poll → ready=True happy path;
the polling loop never raising `NameError` even when the service never
comes up (the two fixed imports, under test); the existing-unit-file +
already-active-service short-circuit path; the existing-unit-file +
inactive-service recreate-and-restart path; `_service_status_output`
handling a real `CalledProcessError` from a nonzero exit without
raising; CIDR-based access control for both configured and default
`allowed_networks` (10.x, 172.16-31.x, 192.168.x, and correctly denying
a public IP); a garbage or empty/`None` remote address being denied
rather than crashing; one malformed CIDR entry in config not breaking
matching against the other valid entries; `on_webhook` rendering with
the correct `allowed` flag for both an in-range and out-of-range
address; and `on_unload` correctly stopping/disabling the service and
clearing `ready`.

## Still open

- No real-device test of the systemd service or the rendered terminal
  against a live WebSSH2 instance - see README's "Still open" section.
