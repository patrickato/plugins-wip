"""
web2ssh_ng.py - pwnagotchi plugin (runs ON the pi)

Web2SSHNG is a security-hardening rebuild of web2ssh.py (WPA2,
v0.1.3) - source at pwnagotchi-plugins/web2ssh.py, copied verbatim
into this project's source tree for audit. The original exposes a
tiny Flask web app with a text box (plus one-click shortcut buttons
for Shutdown/Reboot/Pwnkill/etc.) that runs whatever it's given
through `subprocess.check_output(command, shell=True, ...)` as root,
behind HTTP Basic Auth.

Confirmed bugs in the original (all source-verified - see NOTES.md
for the full writeup with line-by-line detail):

  1. **`self.app.run(...)` called directly inside `on_loaded()`.**
     ```python
     self.app.run(host='::', port=self.options["port"])
     ```
     `Flask.run()` is a blocking, `serve_forever()`-style call - it
     never returns while the app is serving. This fork's real loader
     calls `on_loaded(self)` SYNCHRONOUSLY during plugin load (see the
     framework facts below), so this doesn't just fail to serve
     correctly - it hangs the ENTIRE pwnagotchi plugin-loading
     process at startup, forever. This is a fatal, whole-device-
     hanging bug, not something local to this one plugin. Fixed: the
     real Flask WSGI app is served via
     `werkzeug.serving.make_server(...)` in a background
     `threading.Thread`, so `on_loaded()` starts the thread and
     returns immediately. The server object is stored and its
     `.shutdown()` is called from `on_unload(self, ui)` so it stops
     cleanly - see `_start_server`/`on_unload` below.

  2. **`self.config` is always `{}`, so configured credentials/port
     are silently ignored.**
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
     The real framework's loader constructs every plugin with `cls()`
     - zero arguments (confirmed against
     `pwnagotchi/plugins/__init__.py`'s `__init_subclass__`, which
     does `plugin_instance = cls()`) - so `config` is always `None`
     and `self.config` is always `{}`, regardless of what's actually
     in `/etc/pwnagotchi/config.toml`. Even if `self.config` WERE
     populated, it's read with flat dotted-string keys
     (`"main.plugins.web2ssh.username"`), which is not how the real
     nested config dict is shaped
     (`config['main']['plugins']['web2ssh']['username']`) - a second,
     independent bug on top of the first. The net effect: **the
     username/password/port a user sets in config.toml are silently
     ignored - the plugin always uses the hardcoded fallback
     `"changeme"`/`"changeme"`/`8082`, no matter what the user
     configures.** Combined with bug #1's `host='::'` (every
     interface, IPv6 and IPv4) and this plugin's designed purpose
     (arbitrary root shell command execution, with built-in one-click
     "Shutdown"/"Reboot"/"Pwnkill" buttons), this amounts to an
     effectively unauthenticated root-shell backend reachable from the
     network - the single most severe finding in this project's
     entire plugin audit. Fixed: this rebuild never reads
     `self.config` at all. Credentials are read from the REAL
     framework-assigned `self.options` (via `_opt()`, see the
     framework-facts section below) and, critically, if they are
     missing, blank, or an obviously-default placeholder value, the
     plugin logs a clear error and REFUSES TO START - it never falls
     back to a working default credential pair, under any code path.
     See "New feature 1" below.

  3. **Basic Auth served over plain HTTP with no bind restriction.**
     `host='::'` (bug #1's code) binds every interface, IPv4 and
     IPv6 - credentials and command output travel in the clear and
     the attack surface is the whole network, not just this device or
     a private link. Fixed: see "New feature 2" (`bind_scope`) below -
     the server now binds to a specific, deliberately-chosen address
     by default (Tailscale IP if available, else localhost-only), and
     binding to every interface (`bind_scope = "lan"`) is an explicit,
     logged, opt-in choice rather than the unconditional-and-silent
     original behavior.

Framework facts this rebuild relies on (verified against the cloned
jayofelony/pwnagotchi fork, `pwnagotchi/plugins/__init__.py` - same
file every other suite in this repo's fixes have been verified
against):
  - `load_from_file()` registers a plugin as
    `plugin_name = os.path.basename(filename.replace(".py", ""))` -
    the file's exact basename, case-sensitive. `load()` looks up both
    the `enabled` flag and the options table in
    `config['main']['plugins'][name]` under that SAME key. So the
    config section for this file MUST be
    `[main.plugins.web2ssh_ng]` (matching `web2ssh_ng.py`, see the
    naming section below), or the plugin silently never even appears
    in the "enabled" list.
  - `plugins.load()` does
    `plugin.options = config['main']['plugins'][name]` - a RAW
    assignment of the parsed TOML table. `__defaults__` is NEVER
    merged by the framework itself. Every option read in this file
    goes through `_opt()`/`_opt_int()`/`_opt_bool()` against the
    module-level `DEFAULTS` dict below, never bare
    `self.options[...]` - the same pattern already established in
    this repo by `sigstr_ng.py`/`fix_region_ng.py`/
    `bluetooth_recon_ng.py`.
  - `Plugin.__init_subclass__` does `plugin_instance = cls()` - a
    ZERO-ARGUMENT construction. This confirms bug #2 above: any
    `__init__(self, config=None)` argument a plugin declares is never
    actually supplied by the real loader.
  - Real hook signatures: `on_loaded(self)` (called SYNCHRONOUSLY
    during plugin load - if it blocks, it hangs the whole pwnagotchi
    startup process, confirming bug #1's severity), `on_unload(self,
    ui)`, `on_webhook(self, path, request)`.

Naming note: unlike `MadHatterNG.py` (an explicit one-off capital-NG
rename request for that suite only), this plugin follows
`sigstr_ng.py`/`fix_region_ng.py`/`bluetooth_recon_ng.py`'s
convention: snake_case file `web2ssh_ng.py`, config section
`[main.plugins.web2ssh_ng]` - matching the file's exact basename, per
the verified framework fact above, NOT the class name (`Web2SSHNG`)
and NOT the original's section name (`web2ssh`). A config section
spelled either of those for this file would never appear in the
framework's "enabled" list at all - a total no-load, not an options
bug. See NOTES.md for the full writeup.

What's new on top of the fixed original - the exact 3 items approved
this round (see NOTES.md for the full design writeup):

  1. **Real credential handling that refuses to start unsafely.**
     Credentials are read from the real `self.options` (never
     `self.config`). `validate_credentials()` is the single choke
     point every configured username/password passes through before
     the server is ever started: missing, blank, or a value matching
     a curated list of obvious default/placeholder strings
     (`PLACEHOLDER_CREDENTIALS` - "changeme", "password", "admin",
     "root", "12345", etc.) all cause `on_loaded()` to log a clear
     error and return WITHOUT starting any server at all - no socket
     is ever bound. There is no code path anywhere in this file that
     assigns or falls back to a working default credential pair (see
     NOTES.md's grep-verified confirmation of this).

  2. **`bind_scope`, designed to be easy to actually use, not just
     "safe but confusing".** A new option with four friendly values,
     resolved by `resolve_bind_plan()`:
       - `"auto"` (the default/recommended value): tries to detect a
         live Tailscale interface IP first (`detect_tailscale_ip()` -
         `tailscale ip -4` via `subprocess.run` with a short timeout,
         falling back to parsing `ip -4 addr show tailscale0` output;
         returns `None`, never raises, if neither works). If found,
         binds to that specific IP (never `0.0.0.0`) and logs/shows
         the exact reachable URL. If NOT found, falls back to
         `127.0.0.1` only and logs a friendly warning explaining: (a)
         it's reachable on-device or via an SSH tunnel/port-forward
         right now, and (b) `bind_scope = "lan"` reaches it from the
         local WiFi network instead (with a one-sentence explanation
         of why that's less safe), or a Tailscale plugin can be
         installed for safe remote access.
       - `"tailscale"`: REQUIRES Tailscale detection. If not found,
         logs a clear error and does NOT start the server at all -
         fail-safe, never silently widening to something broader than
         what was explicitly asked for.
       - `"localhost"`: always binds `127.0.0.1` only, unconditionally.
       - `"lan"`: binds `0.0.0.0` (explicit, deliberate opt-in) and
         logs a clear, impossible-to-miss warning every time it starts
         in this mode.
     Whatever scope actually gets used, the exact URL to visit is
     always logged AND rendered as a one-line banner at the very top
     of the index page itself, so it's visible in the browser too, not
     just in a log file the user may never open. This also required
     actually fixing bug #1 above: the Flask app is served via
     `werkzeug.serving.make_server(...)` in a background thread, never
     `app.run()`.

  3. **`command_mode` - an allowlist mode, on by default.** A new
     option with two values:
       - `"shortcuts"` (the default/recommended value): `/execute`
         only accepts a command that EXACTLY matches one of the
         configured `shortcuts` table's values (label -> exact shell
         command); anything else is rejected with a clear "not an
         allowed command" message and `subprocess.run` is never
         invoked for a rejected value. The rendered index page shows
         ONLY the shortcut buttons in this mode - the free-text input
         is not rendered at all, so nothing invites a user to type
         something that will just be rejected.
       - `"free"`: shows both the shortcut buttons AND the original's
         free-text command box, but the rendered page always shows a
         clear, visible warning banner reminding the user that this
         executes arbitrary root shell commands.
     The shortcut set itself is a configurable label -> command table
     in `config.toml` (`[main.plugins.web2ssh_ng.shortcuts]`),
     pre-populated with the original's own 10 shortcuts, so a user can
     edit/add their own without touching this file.

Additional correctness fixes made alongside the 3 approved features
(natural parts of "fix and rebuild", not separate scope):
  - Basic Auth credentials are compared with `hmac.compare_digest`
    (constant-time), never `==`, to avoid a timing side-channel -
    see `_check_credentials()`.
  - Commands run via `subprocess.run(command, shell=True,
    timeout=<command_timeout_seconds>, capture_output=True,
    text=True)` instead of the original's untimed
    `subprocess.check_output` - a hung command can no longer hang the
    request (and this server's single-threaded-by-default request
    handling) forever. stdout+stderr are combined in the displayed
    output, matching the original's `stderr=subprocess.STDOUT`
    behavior - see `run_command()`.
  - Very long command output is truncated before rendering
    (`max_output_chars`, default 20000), with a visible "output was
    truncated" note, so a runaway command can't produce an unusably
    huge page.
  - Both pages are still rendered with Jinja2's
    `render_template_string` (auto-escapes `{{ ... }}` by default) -
    no raw/unescaped interpolation of command output or user input
    into HTML is introduced anywhere in this rebuild.

Explicitly NOT built this round (presented as options, not approved -
see NOTES.md's "still open" section): CSRF token handling, a
persistent audit log file, and brute-force lockout/rate-limiting.

See config.toml for every option, README.md for install/verification/
troubleshooting steps, and NOTES.md for the full bug-fix/design
writeup.
"""

import hmac
import logging
import re
import subprocess
import threading

import pwnagotchi.plugins as plugins
from flask import Flask, Response, render_template_string, request
from werkzeug.serving import make_server

# This fork's plugin loader does NOT merge a plugin's __defaults__ into
# self.options (see module docstring) - every option must be read via
# _opt()/_opt_int()/_opt_bool() with a real fallback here, never bare
# self.options[...].
#
# NOTE: "username"/"password" default to None here, on purpose. There
# is NO working default credential pair anywhere in this file - see
# validate_credentials() and the module docstring's "new feature 1".
DEFAULTS = {
    "enabled": False,
    # >>> USER INPUT REQUIRED <<< - no working default is shipped for
    # either of these (see config.toml and validate_credentials()
    # below). A missing, blank, or obviously-default value means the
    # plugin refuses to start its server at all.
    "username": None,
    "password": None,
    "port": 8082,
    # "auto" (recommended/default), "tailscale", "localhost", or "lan"
    # - see resolve_bind_plan() and the module docstring's "new
    # feature 2" for the full behavior of each.
    "bind_scope": "auto",
    # "shortcuts" (recommended/default) or "free" - see the module
    # docstring's "new feature 3".
    "command_mode": "shortcuts",
    # A hung command can never hang a request past this many seconds -
    # see run_command().
    "command_timeout_seconds": 30,
    # Rendered command output longer than this is truncated (with a
    # visible note) rather than producing an unbounded page.
    "max_output_chars": 20000,
    # label -> exact shell command. In "shortcuts" mode, ONLY a
    # command exactly matching one of these values is ever executed.
    # Pre-populated with the original plugin's own shortcut set.
    "shortcuts": {
        "Shutdown": "sudo shutdown -h now",
        "Reboot": "sudo reboot",
        "Ping IP": "ping -c 4 8.8.8.8",
        "Ping DNS": "ping -c 4 google.com",
        "Inbox": "sudo pwngrid --inbox",
        "Pwnkill": "sudo killall -USR1 pwnagotchi",
        "Plugins": "ls /etc/pwnagotchi/custom-plugins",
        "Status": "systemctl status pwnagotchi --no-pager",
        "Config": "cat /etc/pwnagotchi/config.toml",
        "Check Wi-Fi": "lsusb && iwconfig",
    },
}

# Curated, deliberately-not-exhaustive list of values that are clearly
# "somebody left the default in place", not a real chosen credential.
# This is a format/heuristic check, not a password-strength checker -
# its only job is to close off the exact failure mode bug #2 created
# (a plugin silently using a well-known fallback value). Matching is
# case-insensitive and whitespace-trimmed.
PLACEHOLDER_CREDENTIALS = frozenset({
    "changeme", "change_me", "change-me", "changepassword", "changeit",
    "password", "passw0rd", "admin", "administrator", "default",
    "root", "toor", "guest", "letmein", "test", "user", "username",
    "pwnagotchi", "pwnagotchi123", "raspberry", "12345", "123456",
    "1234", "0000", "",
})

# Parses `tailscale ip -4`-style single-line IPv4 output, and the
# "inet X.Y.Z.W/NN" line from `ip -4 addr show tailscale0` output.
IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


# ---------------------------------------------------------------------------
# Pure/testable helpers
# ---------------------------------------------------------------------------

def _looks_like_placeholder(value):
    """True for None, blank/whitespace-only, or a known default value."""
    if value is None:
        return True
    return str(value).strip().lower() in PLACEHOLDER_CREDENTIALS


def validate_credentials(username, password):
    """The single choke point every configured username/password pair
    passes through before the server is ever started.

    -> (True, None) if both are present, non-blank, and don't match a
    known placeholder value. -> (False, "<human-readable reason>")
    otherwise. Never raises. There is no fallback value returned or
    used here or anywhere else in this file - a rejection here means
    the caller (on_loaded) must not start the server, full stop.
    """
    if username is None or str(username).strip() == "":
        return False, "username is missing or blank"
    if password is None or str(password).strip() == "":
        return False, "password is missing or blank"
    if _looks_like_placeholder(username):
        return False, f"username {username!r} looks like a default/placeholder value"
    if _looks_like_placeholder(password):
        return False, "password looks like a default/placeholder value"
    return True, None


def detect_tailscale_ip():
    """Best-effort detection of this device's Tailscale IPv4 address.

    Tries `tailscale ip -4` first, falls back to parsing `ip -4 addr
    show tailscale0` if that fails (binary missing, not logged in,
    interface down, etc). Returns None - NEVER raises - if neither
    source yields a usable address. This is the only place either
    command is invoked.
    """
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if result.returncode == 0:
            match = IPV4_RE.search(result.stdout or "")
            if match:
                return match.group(1)
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        pass
    except Exception as exc:  # pragma: no cover - defense in depth
        logging.debug("[Web2SSHNG] unexpected error running 'tailscale ip -4': %r", exc)

    try:
        result = subprocess.run(
            ["ip", "-4", "addr", "show", "tailscale0"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if result.returncode == 0:
            match = re.search(r"inet\s+(\d{1,3}(?:\.\d{1,3}){3})", result.stdout or "")
            if match:
                return match.group(1)
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        pass
    except Exception as exc:  # pragma: no cover - defense in depth
        logging.debug("[Web2SSHNG] unexpected error parsing tailscale0: %r", exc)

    return None


def get_local_lan_ip():
    """Best-effort guess at this device's LAN-facing IPv4 address, used
    only to make the 'lan' bind_scope's logged/displayed URL more
    useful. Opens a UDP socket toward a public IP with no data ever
    actually sent (UDP `connect()` just picks a local route/address) -
    a standard, side-effect-free trick for this. Returns None (never
    raises) if it doesn't work, e.g. no network at all."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def resolve_bind_plan(bind_scope, port, detect_tailscale=None, local_lan_ip=None):
    """Resolves a `bind_scope` config value into a concrete plan.

    -> {"ok": bool, "bind_host": str or None, "display_url": str or
    None, "warnings": [str, ...], "mode_used": str}. `ok=False` means
    the caller must not start any server at all (fail-safe - see the
    module docstring's "new feature 2", bind_scope="tailscale" with
    no Tailscale detected). Every warning string is meant to be
    logged by the caller; nothing here calls logging directly, which
    keeps this function trivially unit-testable.
    """
    detect_tailscale = detect_tailscale or detect_tailscale_ip
    local_lan_ip = local_lan_ip or get_local_lan_ip
    scope = str(bind_scope or "auto").strip().lower()
    warnings = []

    if scope == "tailscale":
        ip = detect_tailscale()
        if ip is None:
            return {
                "ok": False, "bind_host": None, "display_url": None,
                "warnings": [
                    "bind_scope='tailscale' but no Tailscale interface was "
                    "detected - refusing to start (fail-safe, never falling "
                    "back to something broader than what was explicitly "
                    "configured). Connect/start Tailscale, or change "
                    "bind_scope in config.toml."
                ],
                "mode_used": "tailscale-not-found",
            }
        return {
            "ok": True, "bind_host": ip, "display_url": f"http://{ip}:{port}/",
            "warnings": [], "mode_used": "tailscale",
        }

    if scope == "localhost":
        return {
            "ok": True, "bind_host": "127.0.0.1",
            "display_url": f"http://127.0.0.1:{port}/",
            "warnings": [], "mode_used": "localhost",
        }

    if scope == "lan":
        display_ip = local_lan_ip() or "0.0.0.0"
        warnings.append(
            "bind_scope='lan' - this plugin is now reachable by ANYONE on "
            "your local network, not just this device, because it accepts "
            "connections on every interface. Use 'tailscale' or 'auto' for "
            "safer remote access if you don't need this."
        )
        return {
            "ok": True, "bind_host": "0.0.0.0",
            "display_url": f"http://{display_ip}:{port}/",
            "warnings": warnings, "mode_used": "lan",
        }

    # "auto" (the default) and any unrecognized value both fall back to
    # auto's behavior - never to something broader/less safe than auto.
    if scope != "auto":
        warnings.append(
            f"unrecognized bind_scope {bind_scope!r} - treating it as 'auto'."
        )
    ip = detect_tailscale()
    if ip:
        return {
            "ok": True, "bind_host": ip, "display_url": f"http://{ip}:{port}/",
            "warnings": warnings, "mode_used": "auto-tailscale",
        }
    warnings.append(
        "Tailscale not detected - binding to 127.0.0.1 only (localhost). "
        "It's reachable on this device itself, or via an SSH tunnel/port-"
        "forward, right now. Set bind_scope = \"lan\" in config.toml if "
        "you want it reachable from your local WiFi network instead (less "
        "safe - anyone on that network could reach it), or install a "
        "Tailscale plugin for safe remote access instead."
    )
    return {
        "ok": True, "bind_host": "127.0.0.1",
        "display_url": f"http://127.0.0.1:{port}/",
        "warnings": warnings, "mode_used": "auto-localhost-fallback",
    }


def run_command(command, timeout_seconds=30, max_output_chars=20000):
    """Runs `command` through a shell with a hard timeout, combining
    stdout+stderr (matching the original's `stderr=subprocess.STDOUT`
    behavior) and truncating very long output. Never raises - every
    failure mode (timeout, a missing shell, any other OSError) is
    turned into a human-readable message in the returned dict instead.

    -> {"output": str, "truncated": bool, "timed_out": bool,
    "returncode": int or None}.
    """
    try:
        timeout_seconds = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout_seconds = 30.0

    try:
        max_output_chars = int(max_output_chars)
    except (TypeError, ValueError):
        max_output_chars = 20000

    try:
        result = subprocess.run(
            command, shell=True, timeout=timeout_seconds,
            capture_output=True, text=True,
        )
        output = (result.stdout or "") + (result.stderr or "")
        returncode = result.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        output = f"Command timed out after {timeout_seconds:g}s and was killed."
        returncode = None
        timed_out = True
    except (OSError, subprocess.SubprocessError) as exc:
        output = f"Error executing command: {exc!r}"
        returncode = None
        timed_out = False

    truncated = False
    if max_output_chars > 0 and len(output) > max_output_chars:
        output = output[:max_output_chars]
        truncated = True

    return {
        "output": output, "truncated": truncated,
        "timed_out": timed_out, "returncode": returncode,
    }


INDEX_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WEB2SSH Command Executor</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 0;
               display: flex; flex-direction: column; align-items: center;
               background-color: #f4f4f9; }
        .banner { width: 100%; text-align: center; padding: 8px 0;
                  background: #12325c; color: #fff; font-size: 0.9rem; }
        .warning-banner { width: 100%; text-align: center; padding: 8px 0;
                           background: #c0392b; color: #fff; font-size: 0.9rem; }
        .container { text-align: center; background: #ffffff; padding: 20px;
                     border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                     width: 90%; max-width: 400px; margin-top: 20px; }
        h1 { font-size: 1.5rem; margin-bottom: 20px; }
        form { display: flex; flex-direction: column; }
        input[type="text"] { font-size: 1rem; padding: 10px; margin-bottom: 15px;
                              border: 1px solid #ccc; border-radius: 4px; }
        input[type="submit"] { font-size: 1rem; padding: 10px; color: #fff;
                                background-color: #007BFF; border: none;
                                border-radius: 4px; cursor: pointer; }
        .shortcuts { margin-top: 20px; }
        .shortcuts button { font-size: 1rem; margin: 5px; padding: 10px; color: #fff;
                             background-color: #28a745; border: none;
                             border-radius: 4px; cursor: pointer; }
    </style>
</head>
<body>
    <div class="banner">Reachable at: {{ display_url }}</div>
    {% if show_free_warning %}
    <div class="warning-banner">WARNING: free command mode is enabled - the
        box below executes ARBITRARY ROOT SHELL COMMANDS with no allowlist.</div>
    {% endif %}
    <div class="container">
        <h1>WEB2SSH Command Executor</h1>
        {% if show_free_input %}
        <form action="/execute" method="post">
            <input type="text" id="commandInput" name="command" placeholder="Enter command" required>
            <input type="submit" value="Execute">
        </form>
        {% endif %}
        <div class="shortcuts">
            <h2>Command Shortcuts</h2>
            {% for label, command in shortcuts %}
            <form action="/execute" method="post" style="display: inline;">
                <input type="hidden" name="command" value="{{ command }}">
                <button type="submit">{{ label }}</button>
            </form>
            {% endfor %}
        </div>
    </div>
</body>
</html>
"""

OUTPUT_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Command Output</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px;
               background-color: #f4f4f9; }
        .output-container { max-width: 800px; margin: 0 auto; background: #ffffff;
                             padding: 20px; border-radius: 8px;
                             box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        h1 { font-size: 1.5rem; margin-bottom: 20px; }
        pre { text-align: left; background: #f8f9fa; padding: 15px; border-radius: 5px;
              overflow-x: auto; font-size: 0.9rem; white-space: pre-wrap; }
        a { display: inline-block; margin-top: 15px; font-size: 1rem;
            text-decoration: none; color: #007BFF; }
        .note { color: #c0392b; font-weight: bold; }
    </style>
</head>
<body>
    <div class="output-container">
        <h1>Command Output</h1>
        {% if truncated %}<p class="note">(output truncated at {{ max_output_chars }} characters)</p>{% endif %}
        <pre>{{ output }}</pre>
        <a href="/">Back</a>
    </div>
</body>
</html>
"""


class Web2SSHNG(plugins.Plugin):
    __author__ = "rebuilt for this project's plugin audit from WPA2's web2ssh.py"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Browser-based root shell command execution over HTTP Basic Auth, "
        "with mandatory real credentials (no default fallback), an easy "
        "bind-scope helper (auto-detected Tailscale / localhost / LAN), "
        "and an allowlisted shortcuts mode."
    )
    __name__ = "Web2SSHNG"
    __help__ = __description__
    __dependencies__ = {
        # Flask + Werkzeug - already present on a stock pwnagotchi image
        # (pwnagotchi's own web UI is a Flask app) - see README.md for
        # the verification note.
        "apt": [],
        "pip": ["flask", "werkzeug"],
    }

    def __init__(self):
        self.app = Flask(__name__)
        self._routes_registered = False
        self._server = None
        self._server_thread = None
        self._username = None
        self._password = None
        self._display_url = None
        self._bind_mode = None

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    def _opt_int(self, key, default=0):
        try:
            return int(self._opt(key))
        except (TypeError, ValueError):
            return int(default)

    def _opt_bool(self, key, default=False):
        value = self._opt(key)
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    # ------------------------------------------------------------------
    # Lifecycle

    def on_loaded(self):
        logging.info(f"[{self.__class__.__name__}] plugin loaded")

        username = self._opt("username")
        password = self._opt("password")
        ok, reason = validate_credentials(username, password)
        if not ok:
            logging.error(
                f"[{self.__class__.__name__}] refusing to start: {reason}. "
                f"Set a real `username`/`password` under "
                f"[main.plugins.web2ssh_ng] in config.toml - see "
                f"config.toml's '>>> USER INPUT REQUIRED <<<' markers."
            )
            return

        # Stored once, here, after passing validation - never re-read
        # from a code path that could fall back to a default.
        self._username = str(username)
        self._password = str(password)

        port = self._opt_int("port", DEFAULTS["port"])
        plan = resolve_bind_plan(self._opt("bind_scope"), port)
        for warning in plan["warnings"]:
            logging.warning(f"[{self.__class__.__name__}] {warning}")

        if not plan["ok"]:
            logging.error(
                f"[{self.__class__.__name__}] server not started - see the "
                f"warning above."
            )
            return

        self._display_url = plan["display_url"]
        self._bind_mode = plan["mode_used"]

        self._register_routes()
        self._start_server(plan["bind_host"], port)

    def _start_server(self, bind_host, port):
        try:
            server = make_server(bind_host, port, self.app)
        except OSError as exc:
            logging.error(
                f"[{self.__class__.__name__}] couldn't bind "
                f"{bind_host}:{port}: {exc!r} - server not started."
            )
            return

        self._server = server
        self._server_thread = threading.Thread(
            target=server.serve_forever,
            name="web2ssh_ng-server",
            daemon=True,
        )
        self._server_thread.start()
        logging.info(
            f"[{self.__class__.__name__}] listening ({self._bind_mode}) - "
            f"visit {self._display_url}"
        )

    def on_unload(self, ui):
        logging.info(f"[{self.__class__.__name__}] plugin unloading")
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
            except Exception as exc:
                logging.warning(
                    f"[{self.__class__.__name__}] error shutting down "
                    f"server: {exc!r}"
                )
            thread = self._server_thread
            if thread is not None:
                thread.join(timeout=5)
            try:
                server.server_close()
            except Exception as exc:
                logging.warning(
                    f"[{self.__class__.__name__}] error closing server "
                    f"socket: {exc!r}"
                )
        self._server_thread = None

    # ------------------------------------------------------------------
    # Auth (bug #2/#3 fix + hmac constant-time comparison)

    def _check_credentials(self, username, password):
        expected_user = (self._username or "").encode("utf-8")
        expected_pass = (self._password or "").encode("utf-8")
        given_user = (username or "").encode("utf-8")
        given_pass = (password or "").encode("utf-8")
        user_ok = hmac.compare_digest(given_user, expected_user)
        pass_ok = hmac.compare_digest(given_pass, expected_pass)
        return user_ok and pass_ok

    def _unauthorized_response(self):
        response = Response(
            "Unauthorized access. Please provide valid credentials.",
            status=401,
        )
        response.headers["WWW-Authenticate"] = 'Basic realm="web2ssh_ng"'
        return response

    # ------------------------------------------------------------------
    # Routes

    def _register_routes(self):
        if self._routes_registered:
            return
        self._routes_registered = True

        @self.app.before_request
        def _require_auth():
            auth = request.authorization
            if not auth or not self._check_credentials(auth.username, auth.password):
                return self._unauthorized_response()

        @self.app.route("/")
        def index():
            return self._render_index()

        @self.app.route("/execute", methods=["POST"])
        def execute():
            command = request.form.get("command", "")
            return self._handle_execute(command)

    def _render_index(self):
        mode = str(self._opt("command_mode") or "shortcuts").strip().lower()
        show_free = mode == "free"
        shortcuts = self._opt("shortcuts") or {}
        return render_template_string(
            INDEX_TEMPLATE,
            display_url=self._display_url or "(unknown - check the plugin log)",
            show_free_input=show_free,
            show_free_warning=show_free,
            shortcuts=list(shortcuts.items()),
        )

    def _handle_execute(self, command):
        mode = str(self._opt("command_mode") or "shortcuts").strip().lower()
        timeout_seconds = self._opt_int("command_timeout_seconds", DEFAULTS["command_timeout_seconds"])
        max_output_chars = self._opt_int("max_output_chars", DEFAULTS["max_output_chars"])

        if not command:
            return self._render_output(
                "No command was submitted.", truncated=False, max_output_chars=max_output_chars,
            )

        if mode != "free":
            shortcuts = self._opt("shortcuts") or {}
            allowed_commands = set(shortcuts.values())
            if command not in allowed_commands:
                logging.warning(
                    f"[{self.__class__.__name__}] rejected a command not in "
                    f"the configured shortcuts allowlist (command_mode="
                    f"'shortcuts')"
                )
                return self._render_output(
                    "Rejected: that is not an allowed command "
                    "(command_mode = \"shortcuts\" only permits the "
                    "configured shortcut commands). Set command_mode = "
                    "\"free\" in config.toml to allow arbitrary commands.",
                    truncated=False, max_output_chars=max_output_chars,
                )

        result = run_command(command, timeout_seconds=timeout_seconds, max_output_chars=max_output_chars)
        return self._render_output(
            result["output"], truncated=result["truncated"], max_output_chars=max_output_chars,
        )

    def _render_output(self, output, truncated, max_output_chars):
        return render_template_string(
            OUTPUT_TEMPLATE,
            output=output,
            truncated=truncated,
            max_output_chars=max_output_chars,
        )
