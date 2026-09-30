"""
handshaker_ng.py - pwnagotchi plugin (runs ON the pi)

HandshakerNG is a bugfix + feature-upgrade rebuild of handshaker.py
(itsdarklikehell/Allordacia, v1.0.1) - source at
pwnagotchi-plugins/handshaker.py, and functionally mirrored by
plugins_archive/allordacia/Pwnagotchi-Handshaker/handshaker.py (cosmetic
differences only). The original's stated purpose is "help access
important pwnagotchi information when the device cannot be accessed via
SSH", plus a boot-time sync of the handshakes folder/logs to /boot.

Confirmed bugs in the original (all source-verified - see NOTES.md for
the full writeup with line-by-line detail):

  1. **`on_loaded()` calls `self.load_data(data_path)`, but `load_data`
     is never defined anywhere in the class.** This is a guaranteed
     `AttributeError` on every single plugin load - the plugin has
     never actually worked. Fixed: `load_data(self, data_path)` is a
     real method below - it scans `data_path` for `*.pcapng` files
     (never `*.pcap` - see the fork-fact section below), builds one
     record per capture, sorts newest-first, and sets `self.handshakes`
     to the count (the exact attribute name the original's own
     `on_loaded`/`on_ready` logging already reads, kept working) plus a
     separate `self.handshake_records` list for the new web
     endpoints/UI to use.
  2. **`on_webhook(self, path, request)` only logs and returns `None`
     - the plugin's entire stated purpose was completely
     unimplemented.** Fixed: see "New features" below - a real JSON
     status endpoint, an HTML status/file-listing page, and per-file
     downloads, all served through this plugin's own small dedicated
     Flask server (not through the shared pwnagotchi web UI's
     `on_webhook` route, which this rebuild's `on_webhook` now just
     points at that server's URL instead of silently doing nothing).
  3. **`__dependencies__` declared `pip: ["scapy"]`, but `scapy` was
     never imported or used anywhere in the file.** A bogus dependency.
     Fixed: removed. This file's real dependencies are `flask` and
     `werkzeug` (both normally already present on a stock pwnagotchi
     image, same as `web2ssh_ng.py`/`handshakes_dl_ng.py` note).

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
    `[main.plugins.handshaker_ng]` (matching `handshaker_ng.py`), or
    the plugin silently never even appears in the "enabled" list.
  - `plugins.load()` does `plugin.options =
    config['main']['plugins'][name]` - a RAW assignment of the parsed
    TOML table. `__defaults__` is NEVER merged by the framework
    itself. Every option read in this file goes through
    `_opt()`/`_opt_int()`/`_opt_bool()`/`_opt_float()` against the
    module-level `DEFAULTS` dict below, never bare
    `self.options[...]` - the same pattern already established in
    this repo by `web2ssh_ng.py`/`sigstr_ng.py`.
  - `Plugin.__init_subclass__` does `plugin_instance = cls()` - a
    ZERO-ARGUMENT construction. `__init__` here takes no arguments
    beyond `self`.
  - Real hook signatures used: `on_loaded(self)`, `on_ready(self,
    agent)`, `on_ui_setup(self, ui)`, `on_ui_update(self, ui)`,
    `on_unload(self, ui)` (note the required `ui` parameter),
    `on_webhook(self, path, request)`.
  - **This fork only ever writes `.pcapng` handshake capture files,
    never `.pcap`.** Filtering for `.pcap` instead of `.pcapng` is a
    recurring, previously-found bug in this exact project (see
    `banthex_ng.py`/`hashespwnagotchi_ng.py`'s docstrings for the
    history) - `scan_handshakes()` below globs `*.pcapng` only, and
    nowhere in this file does anything filter/slice for `.pcap`.

Naming note: matching `web2ssh_ng.py`/`sigstr_ng.py`'s convention:
snake_case file `handshaker_ng.py`, config section
`[main.plugins.handshaker_ng]` - matching the file's exact basename,
per the verified framework fact above, NOT the class name
(`HandshakerNG`) and NOT the original's section name (`handshaker`).

What was preserved from the original, unchanged in spirit (its
`on_ready`/`on_unload` boot-flow behavior is real, intentional device
setup, not a bug):
  - `on_ready(self, agent)` still rsyncs the handshakes data directory
    to `/boot/handshakes` (`rsync -a --delete`), copies
    `/var/log/pwnagotchi.log` to `/boot/pwnagotchi-start.log`, and -
    only if `/boot/custom_plugins` exists - moves its contents into
    `/home/pi/custom_plugins/` and copies
    `/etc/pwnagotchi/config.toml` to `/boot/config.toml` (this last
    copy stays nested inside the `if` block, exactly as in the
    original - it was never meant to run unconditionally).
  - `on_unload(self, ui)` still copies `/var/log/pwnagotchi.log` to
    `/boot/pwnagotchi-end.log`.
  Three deliberate improvements were made to this otherwise-unchanged
  behavior:
    (a) the handshakes data directory is now the `data_path` config
        option instead of the hardcoded `/root/handshakes` string used
        twice in the original. Post-cluster-review update: the default
        value is now `/etc/pwnagotchi/handshakes` (this fork's actual
        real handshake directory, confirmed via discohash-suite's and
        discord-suite's own code) rather than the original's hardcoded
        `/root/handshakes`, which was the legacy/upstream path and
        never actually matches a real capture on this fork. Still
        fully overridable via `data_path` either way.
    (b) every `os.system(...)` call in `on_ready`/`on_unload` is now
        wrapped in its own try/except (`_safe_system()`) - one failing
        step (e.g. `/boot/custom_plugins` absent on some installs, or a
        permissions error) logs a warning and lets the rest continue,
        instead of being able to take down plugin load/unload.
    (c) a new boolean option `sync_to_boot` (default `true`, preserving
        the original's always-on behavior) lets a user skip all of
        `on_ready`'s boot-sync behavior entirely by setting it `false`.

The 5 approved new features built this round (see NOTES.md for the
full design writeup; items 3/6 from the original 7-item list -
HTTP Basic Auth and "since last check" delta tracking - were
explicitly NOT approved and are not built here):

  1. **A real JSON status endpoint** (`/status.json`) - handshake
     count, the sanitized-filename display name of every capture (see
     "what SSID/BSSID list means" below), the most recent capture
     timestamp, and total handshake file size across all captures.
  2. **A simple HTML status page** (`/`) as an alternative to raw
     JSON - one glance from a phone browser, including the file
     listing (feature 5). The same route also accepts `?format=json`
     and returns the byte-identical JSON payload the dedicated
     endpoint returns, for scripting convenience.
  4. **`bind_scope`** - the same easy option `web2ssh_ng.py`
     established (`resolve_bind_plan()`/`detect_tailscale_ip()`,
     reproduced/adapted here since each suite in this repo is
     standalone): `"auto"` (default - Tailscale IP if detected, else
     localhost with an explanation), `"tailscale"` (requires
     detection, fail-safe refuse-to-start otherwise), `"localhost"`
     (always 127.0.0.1), `"lan"` (0.0.0.0, loud warning every start).
     Whichever is used, the reachable URL is logged AND shown as a
     banner at the top of the rendered HTML page. The Flask app is
     served via `werkzeug.serving.make_server` in a background daemon
     thread (never a blocking `.run()`) - `on_loaded()` starts the
     thread and returns immediately; `on_unload(self, ui)` calls the
     server's `.shutdown()`. Since there is **no authentication** on
     this plugin (explicitly out of scope this round - see NOTES.md),
     `bind_scope` is the only exposure control.
  5. **List/download individual handshake capture files** - `.pcapng`
     files only (filename, size, capture time), each with a simple
     per-file download link (`/download/<filename>`), served through
     THIS plugin's own dedicated server. Deliberately scoped DOWN
     compared to the already-existing `handshakes_dl_ng.py` suite (see
     "Scope vs. handshakes-dl-suite" below) - no ZIP bulk download, no
     hash-file/GPS-file surfacing here. Downloads are served safely:
     the requested filename is sanitized with `os.path.basename()`,
     resolved against the configured data directory, and the resolved
     absolute path is verified to actually be inside that directory
     (and to end in `.pcapng`) before ever calling `send_from_directory`
     - anything that fails this check, or doesn't exist as a regular
     file, is a 404 (`resolve_download_target()` below).
  7. **Live handshake count on-screen** - `on_ui_setup`/`on_ui_update`
     + `LabeledValue`, copied from `sigstr_ng.py`'s exact convention
     (configurable `ui_position_x`/`ui_position_y`, a `label` option,
     `BLACK` color, `fonts.Bold`/`fonts.Medium`). The data directory is
     only re-scanned on a configurable `ui_refresh_interval` (default
     30s, since handshake counts change far less often than e.g.
     signal strength) rather than on every single UI tick. The element
     is removed in `on_unload` via `ui.remove_element(...)`, inside a
     try/except so a UI teardown failure can't crash unload.

What "SSID/BSSID list" actually means here: like
`handshakes_dl_ng.py`'s own `_collect()`, this treats the whole
sanitized capture-file basename (minus `.pcapng`) as the capture's
display name, without assuming any particular `SSID_BSSID` filename
format - pwnagotchi capture filenames vary, and there's no reliable
delimiter to safely parse a real ESSID/BSSID back out of every one.
The sanitized filename is used consistently as each capture's
identifier in the status page, the JSON payload, and download links.

Scope vs. `handshakes_dl_ng.py` (already in this repo): that suite
already lists captured handshakes with size/date/sort, per-file
download links, companion hash-file (.2500/.16800/.22000) and GPS-file
(.gps.json/.geo.json) surfacing, a size cap, and a bulk
download-all-as-ZIP button, served through the pwnagotchi's own shared
web UI (`on_webhook`). This plugin's file listing/download is
deliberately narrower - just the raw `.pcapng` files themselves, no
ZIP, no hash/GPS surfacing - and is framed as a separate, independently
network-restrictable channel (its own dedicated server + `bind_scope`,
not the shared web UI), not a replacement for that fuller-featured
page. See NOTES.md for the full writeup.

No authentication of any kind is implemented on this plugin's server
(explicitly out of scope this round, along with HTTP Basic Auth as a
whole feature idea) - `bind_scope` is the only control over who can
reach the status page and download capture files. See config.toml and
NOTES.md for this tradeoff spelled out.
"""

import glob
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK
from flask import Flask, abort, jsonify, render_template_string, request, send_from_directory
from werkzeug.serving import make_server

# This fork's plugin loader does NOT merge a plugin's __defaults__ into
# self.options (see module docstring) - every option must be read via
# _opt()/_opt_int()/_opt_bool()/_opt_float() with a real fallback here,
# never bare self.options[...].
DEFAULTS = {
    "enabled": False,
    # Where captured handshakes live. The original hardcoded
    # "/root/handshakes" (the legacy/upstream path) in two places;
    # post-cluster-review update: defaults to this fork's actual real
    # handshake directory, "/etc/pwnagotchi/handshakes" (confirmed via
    # discohash-suite's and discord-suite's own code), and is now a
    # real option instead of a hardcoded string. Override if yours
    # differs.
    "data_path": "/etc/pwnagotchi/handshakes",
    # Preserves the original's always-on boot-sync behavior
    # (on_ready's rsync-to-/boot/handshakes + log/config.toml copying)
    # by default. Set false to skip all of that.
    "sync_to_boot": True,
    "port": 8083,
    # "auto" (recommended/default), "tailscale", "localhost", or "lan" -
    # see resolve_bind_plan() below (adapted from web2ssh_ng.py's
    # implementation of the same option).
    "bind_scope": "auto",
    "label": "Handshakes",
    # On-screen position - see sigstr_ng.py's identical convention.
    # Defaults chosen to avoid the exact pixel sigstr_ng.py itself uses
    # (0, 205); adjust in config.toml if this collides with another
    # plugin's on-screen element on your layout.
    "ui_position_x": 0,
    "ui_position_y": 220,
    # Minimum seconds between re-scanning data_path for the on-screen
    # count. The UI refresh loop may call on_ui_update more often than
    # this - between scans, the last known count is simply redrawn, no
    # re-glob repeated. Handshake counts change far less often than
    # e.g. signal strength, so this defaults higher than sigstr_ng.py's
    # refresh_interval.
    "ui_refresh_interval": 30,
}

ELEMENT_NAME = "handshaker_ng"

# Curated, deliberately-not-exhaustive extension check - this fork only
# ever writes .pcapng handshake captures, never .pcap (see module
# docstring's fork-fact section; .pcap has been a recurring, previously
# -found bug in this exact project).
CAPTURE_EXT = ".pcapng"

IPV4_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


# ---------------------------------------------------------------------------
# Pure/testable helpers
# ---------------------------------------------------------------------------

def detect_tailscale_ip():
    """Best-effort detection of this device's Tailscale IPv4 address.

    Adapted from web2ssh_ng.py's implementation (each suite in this
    repo is standalone, so the logic is reproduced here rather than
    imported across suites). Tries `tailscale ip -4` first, falls back
    to parsing `ip -4 addr show tailscale0`. Returns None - NEVER
    raises - if neither source yields a usable address.
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
        logging.debug("[HandshakerNG] unexpected error running 'tailscale ip -4': %r", exc)

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
        logging.debug("[HandshakerNG] unexpected error parsing tailscale0: %r", exc)

    return None


def get_local_lan_ip():
    """Best-effort guess at this device's LAN-facing IPv4 address, used
    only to make the 'lan' bind_scope's logged/displayed URL more
    useful. Adapted from web2ssh_ng.py's implementation. Returns None
    (never raises) if it doesn't work, e.g. no network at all."""
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
    the caller must not start any server at all (fail-safe -
    bind_scope="tailscale" with no Tailscale detected). Adapted from
    web2ssh_ng.py's implementation of the same option/semantics.
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
            "connections on every interface. There is also no "
            "authentication on this plugin at all - anyone who can reach "
            "it can see your handshake count/list and download capture "
            "files. Use 'tailscale' or 'auto' for safer remote access if "
            "you don't need this."
        )
        return {
            "ok": True, "bind_host": "0.0.0.0",
            "display_url": f"http://{display_ip}:{port}/",
            "warnings": warnings, "mode_used": "lan",
        }

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
        "safe - there is no authentication on this plugin, so anyone on "
        "that network could reach it), or install a Tailscale plugin for "
        "safe remote access instead."
    )
    return {
        "ok": True, "bind_host": "127.0.0.1",
        "display_url": f"http://127.0.0.1:{port}/",
        "warnings": warnings, "mode_used": "auto-localhost-fallback",
    }


def scan_handshakes(data_path):
    """The real `load_data()` implementation (module docstring bug #1).

    Globs `data_path/*.pcapng` (non-recursive, matching the original's
    flat `/root/handshakes` assumption) - never `*.pcap` (see the
    module docstring's fork-fact section). Returns a list of
    {"name": <sanitized basename minus ".pcapng">, "filename": <real
    basename>, "size_bytes": int, "mtime": float} dicts, sorted
    newest-first. A missing/unreadable data directory is handled
    gracefully - `glob.glob` on a nonexistent path simply returns []
    (no exception), and any per-file `OSError` (e.g. a file removed
    mid-scan) just skips that one file - this function never raises.
    """
    try:
        entries = glob.glob(os.path.join(data_path or "", "*" + CAPTURE_EXT))
    except Exception as exc:  # pragma: no cover - defense in depth
        logging.warning("[HandshakerNG] couldn't scan data_path %r: %r", data_path, exc)
        entries = []

    records = []
    for full_path in entries:
        try:
            size_bytes = os.path.getsize(full_path)
            mtime = os.path.getmtime(full_path)
        except OSError as exc:
            logging.debug("[HandshakerNG] skipping %r: %r", full_path, exc)
            continue
        filename = os.path.basename(full_path)
        name = filename[: -len(CAPTURE_EXT)] if filename.endswith(CAPTURE_EXT) else filename
        records.append({
            "name": name, "filename": filename,
            "size_bytes": size_bytes, "mtime": mtime,
        })

    records.sort(key=lambda r: r["mtime"], reverse=True)
    return records


def build_status_payload(records):
    """Builds the JSON-serializable status payload shared by the
    dedicated JSON endpoint and the `?format=json` branch of the HTML
    route (feature 1/2) - a single choke point so both are guaranteed
    byte-identical."""
    total_size_bytes = sum(r["size_bytes"] for r in records)
    most_recent = records[0] if records else None
    return {
        "handshake_count": len(records),
        "total_size_bytes": total_size_bytes,
        "most_recent_capture": (
            datetime.fromtimestamp(most_recent["mtime"]).isoformat()
            if most_recent else None
        ),
        "capture_names": [r["name"] for r in records],
        "captures": [
            {
                "name": r["name"],
                "filename": r["filename"],
                "size_bytes": r["size_bytes"],
                "captured_at": datetime.fromtimestamp(r["mtime"]).isoformat(),
                "download_url": "/download/" + r["filename"],
            }
            for r in records
        ],
    }


def resolve_download_target(data_path, requested_filename):
    """The single choke point every download request passes through
    (feature 5's safety requirement). Sanitizes `requested_filename`
    with `os.path.basename()`, resolves it against `data_path`, and
    verifies the resolved absolute path is actually inside `data_path`
    and ends in `.pcapng`. Returns the resolved absolute path string if
    all of that holds, or None otherwise (a `None` return - covering
    `../` traversal, an absolute-path payload, a non-`.pcapng`
    extension, or an empty/`.`/`..` name - means the caller must 404;
    this function does NOT check whether the file actually exists,
    that's the caller's job so this stays trivially unit-testable
    without a filesystem).
    """
    if not requested_filename:
        return None
    safe_name = os.path.basename(str(requested_filename))
    if not safe_name or safe_name in (".", ".."):
        return None
    if not safe_name.endswith(CAPTURE_EXT):
        return None

    data_dir_abs = os.path.abspath(data_path or "")
    candidate = os.path.abspath(os.path.join(data_dir_abs, safe_name))
    if candidate != data_dir_abs and not candidate.startswith(data_dir_abs + os.sep):
        return None
    return candidate


def human_size(size_bytes):
    """Renders a byte count as a short human-readable string for the
    HTML page (e.g. '4.2MB'). Never raises on odd input."""
    try:
        size = float(size_bytes)
    except (TypeError, ValueError):
        return "n/a"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"  # pragma: no cover - unreachable in practice


INDEX_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HandshakerNG</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 0;
               background-color: #f4f4f9; }
        .banner { width: 100%; text-align: center; padding: 8px 0;
                  background: #12325c; color: #fff; font-size: 0.9rem; }
        .no-auth-banner { width: 100%; text-align: center; padding: 6px 0;
                           background: #6c4a00; color: #fff; font-size: 0.8rem; }
        .container { max-width: 700px; margin: 20px auto; background: #ffffff;
                     padding: 20px; border-radius: 8px;
                     box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        h1 { font-size: 1.4rem; margin-bottom: 10px; }
        .summary p { margin: 4px 0; }
        table { width: 100%; border-collapse: collapse; margin-top: 12px; }
        th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee;
                 font-size: 0.9rem; }
        a { color: #007BFF; text-decoration: none; }
        .json-link { display: inline-block; margin-top: 10px; font-size: 0.85rem; }
    </style>
</head>
<body>
    <div class="banner">Reachable at: {{ display_url }}</div>
    <div class="no-auth-banner">No authentication on this page - bind_scope
        ({{ bind_mode }}) is the only access control. See NOTES.md/README.md.</div>
    <div class="container">
        <h1>HandshakerNG</h1>
        <div class="summary">
            <p>Handshakes captured: <b>{{ payload.handshake_count }}</b></p>
            <p>Total capture size: <b>{{ total_size_human }}</b></p>
            <p>Most recent capture: <b>{{ payload.most_recent_capture or "n/a" }}</b></p>
        </div>
        <a class="json-link" href="/status.json">Raw JSON status</a>
        <table>
            <thead><tr><th>Name</th><th>Size</th><th>Captured</th><th>Download</th></tr></thead>
            <tbody>
            {% for hs in captures %}
                <tr>
                    <td>{{ hs.name }}</td>
                    <td>{{ hs.size_human }}</td>
                    <td>{{ hs.captured_at }}</td>
                    <td><a href="{{ hs.download_url }}">download</a></td>
                </tr>
            {% else %}
                <tr><td colspan="4"><i>no captures found in the configured data_path yet</i></td></tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
"""


class HandshakerNG(plugins.Plugin):
    __author__ = ("bugfix + feature-upgrade rebuild for this project's plugin "
                 "audit, of handshaker.py (itsdarklikehell/Allordacia)")
    __version__ = "1.0.0"
    __license__ = "MIT"
    __description__ = (
        "Fixes handshaker.py's guaranteed-crash on_loaded() bug and its "
        "unimplemented on_webhook, and adds a real JSON/HTML status page, "
        "safe per-file handshake downloads, an easy bind-scope helper, and "
        "a live on-screen handshake count - served on this plugin's own "
        "dedicated, unauthenticated server (bind_scope is the only access "
        "control)."
    )
    __name__ = "HandshakerNG"
    __help__ = __description__
    __dependencies__ = {
        # Flask + Werkzeug - already present on a stock pwnagotchi image
        # (pwnagotchi's own web UI is itself a Flask app), same note as
        # web2ssh_ng.py. No scapy (module docstring bug #3 - the
        # original declared it but never used it).
        "apt": [],
        "pip": ["flask", "werkzeug"],
    }

    def __init__(self):
        self.handshakes = 0
        self.handshake_records = []
        self.app = Flask(__name__)
        self._routes_registered = False
        self._server = None
        self._server_thread = None
        self._display_url = None
        self._bind_mode = None
        self._last_ui_scan_ts = 0.0

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

    def _opt_float(self, key, default=0.0):
        try:
            return float(self._opt(key))
        except (TypeError, ValueError):
            return float(default)

    def _safe_system(self, command, description):
        """Runs one `os.system(...)` boot-flow step, never letting a
        failure (unexpected exception, missing folder, permissions
        issue) take down the rest of on_ready/on_unload - logs a
        warning and continues instead (module docstring, preserved-
        behavior item (b))."""
        try:
            os.system(command)
        except Exception as exc:
            logging.warning(
                f"[{self.__class__.__name__}] {description} failed: {exc!r} - continuing"
            )

    # ------------------------------------------------------------------
    # load_data - the real implementation (module docstring bug #1 fix)

    def load_data(self, data_path):
        records = scan_handshakes(data_path)
        self.handshake_records = records
        self.handshakes = len(records)
        return records

    # ------------------------------------------------------------------
    # Lifecycle

    def on_loaded(self):
        logging.info(f"[{self.__class__.__name__}] plugin loaded")

        data_path = self._opt("data_path")
        self.load_data(data_path)
        logging.info(
            f"[{self.__class__.__name__}] found %d handshake(s) in %s",
            self.handshakes, data_path,
        )

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
            name="handshaker_ng-server",
            daemon=True,
        )
        self._server_thread.start()
        logging.info(
            f"[{self.__class__.__name__}] listening ({self._bind_mode}) - "
            f"visit {self._display_url}"
        )

    def on_ready(self, agent):
        logging.info(f"[{self.__class__.__name__}] plugin ready")

        if not self._opt_bool("sync_to_boot", True):
            logging.info(
                f"[{self.__class__.__name__}] sync_to_boot is false - "
                f"skipping boot-sync steps"
            )
            return

        data_path = self._opt("data_path")

        # rsync the data_path to /boot/handshakes (preserved-behavior
        # item (a): data_path is now configurable, default unchanged).
        self._safe_system(
            f"rsync -a --delete '{data_path}/' /boot/handshakes/",
            "rsync handshakes to /boot/handshakes",
        )
        logging.info(
            f"[{self.__class__.__name__}] synced %d handshake(s)", self.handshakes
        )

        self._safe_system(
            "cp /var/log/pwnagotchi.log /boot/pwnagotchi-start.log",
            "copy pwnagotchi.log to pwnagotchi-start.log",
        )
        logging.info(
            f"[{self.__class__.__name__}] copied pwnagotchi.log to pwnagotchi-start.log"
        )

        # Only if /boot/custom_plugins exists - matches the original's
        # exact structure, including the config.toml copy staying
        # nested inside this same `if` block.
        if os.path.exists("/boot/custom_plugins"):
            self._safe_system(
                "mv /boot/custom_plugins/* /home/pi/custom_plugins/",
                "move /boot/custom_plugins into /home/pi/custom_plugins",
            )
            self._safe_system(
                "rm -rf /boot/custom_plugins",
                "remove /boot/custom_plugins",
            )
            logging.info(
                f"[{self.__class__.__name__}] moved custom_plugins to "
                f"/home/pi/custom_plugins"
            )
            self._safe_system(
                "cp /etc/pwnagotchi/config.toml /boot/config.toml",
                "copy config.toml to /boot/config.toml",
            )
            logging.info(
                f"[{self.__class__.__name__}] copied config.toml to /boot/config.toml"
            )

    def on_unload(self, ui):
        logging.info(f"[{self.__class__.__name__}] plugin unloading")

        self._safe_system(
            "cp /var/log/pwnagotchi.log /boot/pwnagotchi-end.log",
            "copy pwnagotchi.log to pwnagotchi-end.log",
        )
        logging.info(
            f"[{self.__class__.__name__}] copied pwnagotchi.log to pwnagotchi-end.log"
        )

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

        try:
            with ui._lock:
                try:
                    ui.remove_element(ELEMENT_NAME)
                except KeyError:
                    pass
        except Exception as exc:
            logging.warning(
                f"[{self.__class__.__name__}] error removing UI element: {exc!r}"
            )

    # ------------------------------------------------------------------
    # UI (feature 7 - copied from sigstr_ng.py's exact convention)

    def on_ui_setup(self, ui):
        try:
            configured_x = self._opt_int("ui_position_x", 0)
            # Negative x means "this many pixels in from the right
            # edge" - same convention as sigstr_ng.py/MadHatterNG.py.
            pos_x = ui.width() + configured_x if configured_x < 0 else configured_x
            pos_x = max(0, min(pos_x, max(0, ui.width() - 10)))
            pos_y = self._opt_int("ui_position_y", 220)

            label = str(self._opt("label") or "").strip() or None

            ui.add_element(ELEMENT_NAME, LabeledValue(
                color=BLACK,
                label=label,
                value="",
                position=(pos_x, pos_y),
                label_font=fonts.Bold,
                text_font=fonts.Medium,
            ))
        except Exception as exc:
            logging.error(f"[{self.__class__.__name__}] UI setup failed: {exc!r}")

    def on_ui_update(self, ui):
        now = time.time()
        interval = self._opt_float("ui_refresh_interval", 30.0)
        if self._last_ui_scan_ts == 0.0 or interval <= 0 or (now - self._last_ui_scan_ts) >= interval:
            self.load_data(self._opt("data_path"))
            self._last_ui_scan_ts = now
        try:
            ui.set(ELEMENT_NAME, str(self.handshakes))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Dedicated server routes (features 1/2/5)

    def _register_routes(self):
        if self._routes_registered:
            return
        self._routes_registered = True

        @self.app.route("/")
        def index():
            return self._render_index()

        @self.app.route("/status.json")
        def status_json():
            _records, payload = self._current_status()
            return jsonify(payload)

        @self.app.route("/download/<filename>")
        def download(filename):
            return self._handle_download(filename)

    def _current_status(self):
        """Re-scans data_path fresh for this request (rather than only
        once at on_loaded()) so the count/list reflect newly captured
        handshakes without a plugin reload - a real improvement over
        the original, which only ever loaded data once, at startup."""
        records = self.load_data(self._opt("data_path"))
        payload = build_status_payload(records)
        return records, payload

    def _render_index(self):
        records, payload = self._current_status()
        if str(request.args.get("format", "")).strip().lower() == "json":
            return jsonify(payload)

        captures = [
            {
                "name": c["name"],
                "size_human": human_size(c["size_bytes"]),
                "captured_at": c["captured_at"],
                "download_url": c["download_url"],
            }
            for c in payload["captures"]
        ]
        return render_template_string(
            INDEX_TEMPLATE,
            display_url=self._display_url or "(unknown - check the plugin log)",
            bind_mode=self._bind_mode or "n/a",
            payload=payload,
            total_size_human=human_size(payload["total_size_bytes"]),
            captures=captures,
        )

    def _handle_download(self, filename):
        data_path = self._opt("data_path")
        target = resolve_download_target(data_path, filename)
        if target is None or not os.path.isfile(target):
            abort(404)
        directory, basename = os.path.split(target)
        logging.info(f"[{self.__class__.__name__}] serving download: {basename}")
        return send_from_directory(directory=directory, path=basename, as_attachment=True)

    def on_webhook(self, path, request):
        """The original's on_webhook was the bug #2 unimplemented stub
        (logged and returned None - see module docstring). This
        rebuild's real status/JSON/HTML/download functionality lives on
        this plugin's own dedicated server (bind_scope - feature 4),
        not through the shared pwnagotchi web UI's on_webhook route, so
        this now returns a short, honest pointer to that URL instead of
        silently doing nothing."""
        if self._display_url:
            return (
                f"HandshakerNG serves its status and download pages on its "
                f"own dedicated server, not through this shared web UI "
                f"route: {self._display_url}"
            )
        return (
            "HandshakerNG's dedicated server is not currently running - "
            "check the pwnagotchi log for why (see NOTES.md)."
        ), 503
