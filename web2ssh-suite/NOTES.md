# Notes: Web2SSHNG

## Why this one was a security-hardening rebuild, not a light bugfix

`web2ssh.py` does something genuinely useful in concept - a browser
button panel for common root maintenance commands (reboot, check
Wi-Fi, tail the config, one-click Pwnkill) - but its implementation
combined three separate bugs into what is, in practice, an
effectively unauthenticated root-shell backend reachable from the
whole network. This was the single most severe finding in this
project's entire plugin audit. The job here was not "polish it up" -
it was "make sure this specific failure mode can never happen again",
while keeping the original's actual useful behavior (a shortcut panel
+ optional free command box) intact and improving it.

## Bugs found in web2ssh.py (all source-verified)

1. **`self.app.run(...)` called directly inside `on_loaded()` -
   hangs the entire device at startup.**
   ```python
   def on_loaded(self):
       ...
       self.app.run(host='::', port=self.options["port"])
   ```
   `Flask.run()` is a blocking, `serve_forever()`-style call - it
   never returns while the app is serving requests. This fork's real
   loader calls every plugin's `on_loaded(self)` SYNCHRONOUSLY during
   plugin load (confirmed against `pwnagotchi/plugins/__init__.py`).
   That means this line doesn't just fail to behave correctly - it
   hangs the ENTIRE pwnagotchi plugin-loading process, forever, the
   moment this plugin is enabled. This is a fatal, whole-device-
   hanging bug, not a bug local to this one plugin. Fixed: the real
   Flask WSGI app is served via `werkzeug.serving.make_server(...)`
   in a background `threading.Thread` (`_start_server`), so
   `on_loaded()` starts the thread and returns immediately. The
   server object is kept on the instance and its `.shutdown()` is
   called from `on_unload(self, ui)`.

2. **`self.config` is always `{}` - configured credentials/port are
   silently ignored, and the plugin always falls back to hardcoded
   `"changeme"`/`"changeme"`/`8082`.**
   ```python
   def __init__(self, config=None):
       ...
       self.config = config or {}
   ...
   self.options = {
       "username": self.config.get("main.plugins.web2ssh.username", "changeme"),
       "password": self.config.get("main.plugins.web2ssh.password", "changeme"),
       "port": self.config.get("main.plugins.web2ssh.port", 8082),
   }
   ```
   The real loader constructs every plugin with `cls()` - zero
   arguments (`plugin_instance = cls()` in
   `Plugin.__init_subclass__`) - so `config` is always `None` and
   `self.config` is always `{}`, no matter what's in
   `/etc/pwnagotchi/config.toml`. On top of that, even a populated
   `self.config` would be read wrong: flat dotted-string keys
   (`"main.plugins.web2ssh.username"`) don't match the real nested
   dict shape (`config['main']['plugins']['web2ssh']['username']`) -
   a second, independent bug. **The net effect: the username/
   password/port a user sets in config.toml are silently ignored -
   the plugin always uses the hardcoded fallback
   `"changeme"`/`"changeme"`/`8082`, no matter what the user
   configures.** Combined with bug #1's `host='::'` (every interface)
   and the plugin's designed purpose (arbitrary root shell command
   execution via `subprocess.check_output(command, shell=True, ...)`,
   with one-click Shutdown/Reboot/Pwnkill buttons), this amounts to
   an effectively unauthenticated root-shell backend reachable from
   the network. Fixed: this rebuild never touches `self.config` at
   all. Credentials come from the real framework-assigned
   `self.options` via `_opt()`. Critically, `validate_credentials()`
   is the single choke point every configured value passes through,
   and a missing/blank/obviously-default value makes `on_loaded()`
   log an error and refuse to start any server - see "New feature 1"
   below, and the grep-verified confirmation at the end of this file.

3. **Basic Auth served over plain HTTP with no bind restriction.**
   `host='::'` (bug #1's own line) binds every interface, IPv4 and
   IPv6 at once - credentials and command output travel in the clear
   and the reachable surface is the whole network, not just this
   device or a private link. Fixed: see "New feature 2" - the server
   now binds to one specific, deliberately-resolved address by
   default (a detected Tailscale IP, or localhost-only if none is
   found), and binding every interface (`bind_scope = "lan"`) is now
   an explicit, logged, opt-in choice rather than the original's
   unconditional-and-silent behavior.

## Framework facts this rebuild relies on

Same verified facts as `sigstr-suite`/`fix-region-suite`/
`bluetooth-recon-suite` in this repo (`pwnagotchi/plugins/__init__.py`,
the cloned `jayofelony/pwnagotchi` fork):
- `load_from_file()` registers a plugin as
  `plugin_name = os.path.basename(filename.replace(".py", ""))` - the
  file's exact basename, case-sensitive. `load()` looks up both the
  `enabled` flag and the options table in
  `config['main']['plugins'][name]` under that SAME key.
- `plugins.load()` does `plugin.options = config['main']['plugins'][name]`
  - a raw assignment of the parsed TOML table. `__defaults__` is never
  merged by the framework itself, so every option read in this file
  goes through `_opt()`/`_opt_int()`/`_opt_bool()` against the
  module-level `DEFAULTS` dict, never bare `self.options[...]`.
- `Plugin.__init_subclass__` does `plugin_instance = cls()` - a
  ZERO-ARGUMENT construction. This is the direct confirmation of bug
  #2: any `__init__(self, config=None)` parameter a plugin declares is
  never actually supplied by the real loader, so a plugin that reads
  `self.config` is reading something that is always empty.
- Real hook signatures relevant here: `on_loaded(self)` (called
  SYNCHRONOUSLY during plugin load - the reason bug #1 is
  device-hanging, not just locally broken), `on_unload(self, ui)`,
  `on_webhook(self, path, request)`.

## A naming note (same convention as sigstr-suite/fix-region-suite/bluetooth-recon-suite)

Unlike `MadHatterNG.py` (an explicit one-off capital-NG rename request
for that suite only), this suite follows the same convention as
`sigstr_ng.py`/`fix_region_ng.py`/`bluetooth_recon_ng.py`: the plugin
file is `web2ssh_ng.py` (snake_case), and its config section is
`[main.plugins.web2ssh_ng]` - matching the file's exact basename, NOT
the class name (`Web2SSHNG`), and NOT the original's section name
(`web2ssh`). This is a verified framework fact, not a stylistic
choice - see the framework-facts section above. A config section
spelled `web2ssh` (the original's name) or `Web2SSHNG` (the class
name) for a file named `web2ssh_ng.py` would never appear in the
framework's `enabled` list at all - a total no-load, not an options
bug.

## What was preserved from the original design, and why

**Kept:** the core concept - a small web panel with one-click
shortcut buttons for common root maintenance commands, behind HTTP
Basic Auth, plus (optionally) a free-text command box for anything
else. The original's own 10 shortcuts (Shutdown, Reboot, Ping IP,
Ping DNS, Inbox, Pwnkill, Plugins, Status, Config, Check Wi-Fi) are
kept verbatim as the shipped default `shortcuts` table - nothing
about *what* this plugin lets you do was changed, only *how safely*
it does it. The page layout/CSS is also kept close to the original's
look, with an added banner (reachable URL, and a warning banner in
free mode).

## What's new (the exact 3 approved features)

1. **Real credential handling that refuses to start unsafely** (fixes
   bug #2 properly). `validate_credentials(username, password)` is
   the single function every configured value passes through before
   the server is ever started. It rejects: `None`, an empty/
   whitespace-only string, or a value (case-insensitively, trimmed)
   in `PLACEHOLDER_CREDENTIALS` - a curated list of exactly the kind
   of value a user would leave in place by accident or copy from an
   example (`changeme`, `password`, `admin`, `root`, `toor`, `guest`,
   `letmein`, `12345`, `pwnagotchi`, etc). A rejection makes
   `on_loaded()` log a clear, specific error naming which check
   failed, and return WITHOUT calling `_start_server()` at all - no
   socket is ever bound, and there is no other code path in this file
   that assigns a working default credential pair. See the "No
   hardcoded default credential" confirmation at the end of this
   file.

2. **`bind_scope`, designed to be easy, not just "safe but
   confusing".** `resolve_bind_plan(bind_scope, port)` is a pure,
   directly-unit-testable function (no logging or I/O side effects of
   its own) that resolves the four documented values:
   - `"auto"` (default): `detect_tailscale_ip()` first tries
     `tailscale ip -4` (via `subprocess.run`, 3s timeout), then falls
     back to parsing `ip -4 addr show tailscale0`'s `inet` line if
     that fails - and returns `None`, never raises, if neither works.
     If an IP is found, the plan binds to that exact address (never
     `0.0.0.0`) and the display URL uses it directly. If not found,
     the plan binds `127.0.0.1` and carries a warning string
     explaining: it's reachable on-device or via an SSH tunnel/port-
     forward right now; `bind_scope = "lan"` reaches it from the LAN
     instead (one sentence on why that's less safe); or a Tailscale
     plugin can be installed for safe remote access.
   - `"tailscale"`: same detection, but `ok=False` (refuses to start)
     if it comes back empty - a deliberate fail-safe, never silently
     widening to `auto`'s fallback behavior.
   - `"localhost"`: always `127.0.0.1`, unconditionally, no detection
     attempted at all.
   - `"lan"`: always `0.0.0.0`, with a mandatory warning every time.
     `get_local_lan_ip()` (a side-effect-free UDP-`connect()` trick,
     no packet actually sent) makes a best-effort guess at the real
     LAN IP just to make the *displayed* URL more useful - it never
     affects what's actually bound (`0.0.0.0` either way).
   Whatever plan is chosen, `on_loaded()` logs every warning and, on
   success, logs the exact URL and stores it on the instance
   (`self._display_url`) so `_render_index()` can also show it as a
   banner at the top of the page in the browser - not just in a log
   file the user might never open. This is also where bug #1 is
   actually fixed: `_start_server()` uses
   `werkzeug.serving.make_server(bind_host, port, self.app)` in a
   background thread, never `app.run()`.

3. **`command_mode` - an allowlist, on by default.**
   `command_mode = "shortcuts"` (the default): `_handle_execute()`
   builds the set of exactly-configured shortcut command strings
   (`set(shortcuts.values())`) and rejects anything not an exact
   member of that set, WITHOUT ever calling `run_command()`/
   `subprocess.run` for the rejected value - confirmed directly in
   the tests by mocking `run_command` and asserting it's never
   called. `_render_index()` also omits the free-text `<input>`
   entirely in this mode (not just hides it with CSS - it isn't in
   the rendered HTML at all), so nothing on the page invites typing
   something that will just be rejected. `command_mode = "free"`
   shows both the shortcut buttons and the original's free-text box,
   but `_render_index()` always renders a visible warning banner
   ("WARNING: free command mode is enabled...") in this mode. The
   shortcut table itself
   (`[main.plugins.web2ssh_ng.shortcuts]`) is fully user-editable in
   config.toml - see config.toml.

## Additional correctness fixes (natural parts of the rebuild)

- **Constant-time credential comparison.** `_check_credentials()`
  uses `hmac.compare_digest()` on UTF-8-encoded bytes for both the
  username and password checks - never Python's `==` - to avoid a
  timing side-channel on either field. Verified directly in the tests
  by grepping the source for the call, not just by observing correct
  accept/reject behavior (a timing side-channel wouldn't show up as a
  behavioral test failure at all).
- **Timeout on every executed command.** `run_command()` uses
  `subprocess.run(command, shell=True, timeout=command_timeout_seconds,
  capture_output=True, text=True)` instead of the original's untimed
  `subprocess.check_output` - a hung command (e.g. a shortcut or
  free-text command that blocks on stdin, or just runs long) is
  killed and reported as a timeout instead of hanging the request
  (and, since this server can currently only handle work as fast as
  its request-handling allows, the whole plugin) forever. stdout and
  stderr are concatenated in the returned output, matching the
  original's `stderr=subprocess.STDOUT` behavior.
- **Output truncation.** `run_command()` truncates output past
  `max_output_chars` (default 20000) and reports `truncated=True`,
  which `_render_output()` turns into a visible "(output truncated at
  N characters)" note - a runaway command (e.g. `cat` on a huge file)
  can no longer produce an unusably huge rendered page.
- **No raw/unescaped HTML interpolation anywhere.** Both pages are
  rendered via Jinja2's `render_template_string`, which auto-escapes
  `{{ ... }}` substitutions by default - command output and the
  shortcut labels/commands are never concatenated into HTML by hand
  anywhere in this file.

## Explicitly NOT built this round (available, not approved)

These were presented as options but the user approved only the 3
features above - they are NOT implemented, and nothing in this file
depends on them existing:
- **CSRF token handling** on the `/execute` form.
- **A persistent audit log file** of executed commands.
- **Brute-force lockout / rate-limiting** on failed auth attempts.

If any of these get approved in a future round, the natural places to
add them are: a hidden CSRF field in `INDEX_TEMPLATE` + a check in
`_require_auth`/a new before-request hook; a simple append-only log
call inside `_handle_execute` right before/after `run_command`; and a
small in-memory failed-attempt counter keyed by remote address inside
`_check_credentials`/`_require_auth`, respectively.

## Testing

Run with (from this directory):
```
python3 tests/test_web2ssh_ng.py
```
(`pytest` isn't installed in this sandbox - the test file is a
self-contained script with its own `check()`/pass-fail harness, same
as every other suite's tests in this repo, so it runs directly with
plain `python3`.) Flask and Werkzeug are REAL installed packages in
this sandbox (not stubbed) - the tests run a real
`werkzeug.serving.make_server` instance bound to `127.0.0.1` on an
ephemeral free port and make real HTTP requests against it with
`urllib.request`, rather than mocking Flask's request/response cycle.

Covers:
- Plugin registration with the real framework loader.
- `validate_credentials()`: valid pair, `None`/blank username or
  password, a range of placeholder values (case-insensitive,
  whitespace-trimmed), and a real admin/password-style pair.
- A source-level grep confirming `DEFAULTS["username"]`/
  `DEFAULTS["password"]` are `None` (no working default) and that no
  line anywhere assigns `"changeme"` (or similar) as an actual
  credential - the only place that string may legitimately appear is
  inside `PLACEHOLDER_CREDENTIALS`/comments.
- `resolve_bind_plan()` (pure function, mocked `detect_tailscale_ip`/
  `get_local_lan_ip`): `"tailscale"` detected vs. not detected (the
  latter must refuse, `ok=False`); `"auto"` detected vs. not detected
  (the latter must fall back to localhost, `ok=True`, with a warning);
  `"localhost"` and `"lan"` bind exactly what's documented,
  unconditionally; an unrecognized value never crashes.
- `run_command()`: a real harmless command's real output; a real
  `sleep` that exceeds a short configured timeout, confirmed to come
  back as a timeout message rather than hanging or raising; output
  truncation actually caps length and marks `truncated=True`; stdout
  and stderr both appear in the combined output.
- A source grep confirming `hmac.compare_digest` is actually called
  (not just correct accept/reject behavior, which a timing-unsafe `==`
  would also produce).
- End-to-end against a REAL server on a REAL ephemeral port: no-auth
  request gets a real 401; wrong password gets a real 401; correct
  credentials get a real 200; the index page shows the reachable-at
  banner and (in the default shortcuts mode) shows the shortcut
  buttons but NOT the free-text input; an arbitrary `/execute` command
  is rejected with `run_command` never even called (mocked and
  asserted); an exact configured shortcut command is accepted.
- `on_unload` actually stops the server: a follow-up real TCP connect
  to the same port fails/connection-refused afterward.
- `on_loaded` refuses to start (no `_server`, and the port never
  becomes connectable) for missing, blank, and placeholder-looking
  credentials.
- `bind_scope` end-to-end with `on_loaded`: `"tailscale"` with no
  detection refuses to start for real (no reachable port); `"auto"`
  with no detection falls back to a real, reachable `127.0.0.1`
  server.
- `command_mode = "free"` end-to-end: the free-text input and warning
  banner are present on the rendered page, and a real, harmless
  free-text command (`echo ...`) actually executes and its output
  appears in the response.
- Output-length truncation end-to-end through a real HTTP round trip
  (a real Python one-liner produces long output; the response shows
  the truncation note).

## Still open / needs real-hardware testing

- **Tailscale detection.** `detect_tailscale_ip()`'s two parsing paths
  (`tailscale ip -4`'s stdout, and `ip -4 addr show tailscale0`'s
  `inet` line) are verified against the documented/typical output
  shape of each command, not against a live Tailscale installation on
  real pwnagotchi hardware - this sandbox has neither `tailscale` nor
  a `tailscale0` interface to test against directly.
- **`get_local_lan_ip()`'s guess** is a standard, well-known trick
  (UDP `connect()` to pick a local route) but was only exercised in
  this sandbox's own network namespace, not on a real device with
  multiple real network interfaces (WiFi AP mode, USB gadget ethernet,
  etc. - pwnagotchi hardware commonly has several).
- **The placeholder-credential list is a curated heuristic, not an
  exhaustive one.** A sufficiently unusual-but-still-weak credential
  (e.g. a short dictionary word not on the list) would still be
  accepted - this feature closes off the exact original failure mode
  (falling back to a well-known hardcoded value), it is not a general
  password-strength checker, and was never asked to be one.
- **CSRF, persistent audit logging, and brute-force lockout** remain
  unimplemented by design this round - see "Explicitly NOT built"
  above.
