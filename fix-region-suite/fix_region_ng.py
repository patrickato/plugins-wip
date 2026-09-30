"""
fix_region_ng.py - pwnagotchi plugin (runs ON the pi)

FixRegionNG is a bug-fix rebuild of fix_region.py (itsdarklikehell /
@V0rT3x) - a small plugin that forces the WiFi radio's regulatory
domain (country code) via `iw reg set`, persisting the change across
reboots with a small root-owned shell script + systemd service, so
channels the stock regulatory domain blocks (e.g. 12/13 outside the
US) become available. See NOTES.md for the full writeup.

Bugs fixed vs. the original (all source-verified against
itsdarklikehell/pwnagotchi-plugins/fix_region.py):

  1. **Import-time `KeyError` crash risk.**
     `REGION = pwnagotchi.config["main"]["plugins"]["fix_region"]["region"]`
     ran at MODULE IMPORT TIME, indexing the config dict directly with
     no `.get()` fallback - a user who set `enabled = true` but forgot
     `region` (reasonably assuming it had the documented default of
     "NL") would raise `KeyError` while the module was still being
     imported, before the plugin object even existed. Fixed: no config
     access happens at module import time at all. Region is read (via
     `_opt()`, see the framework fact below) and validated inside
     `on_loaded`, with a safe, documented fallback default and a real
     ISO 3166-1 alpha-2 format check (2 letters, normalized to
     uppercase) before it is ever used for anything. An invalid region
     is logged as an error and never applied - the plugin refuses
     rather than passing garbage to a root-level shell command.
  2. **Command injection surface.** Every shell interaction was raw
     string concatenation executed with `os.system(...)`
     (`os.system("sudo iw reg set " + REGION)`,
     `os.system("rm " + SERV_PATH)`, etc.), and `REGION` was written
     directly into a bash script file via `NETFIX_SH + REGION`. Since
     `REGION` only ever came from local config.toml this was low
     severity in practice, but it is still bad practice. Fixed: every
     command runs via `subprocess.run([...])` with an argument LIST -
     `shell=True` and string-interpolated command lines are never used
     anywhere in this file. The region string written into the
     generated shell script is validated against the same 2-uppercase-
     letter ISO alpha-2 pattern before it is ever written to a file
     that systemd will later execute as root.
  3. **Config changes silently ignored after first load.** `on_loaded`
     only wrote the shell script/service `if not os.path.exists(...)`
     - once those files existed, changing `region` in config.toml and
     reloading did NOTHING; the stale region stayed applied forever
     until the user manually deleted the generated files. Fixed: the
     currently-applied region is tracked in a small JSON state file
     (`state_path`) written every time a region is actually applied.
     `on_loaded` compares the configured region against the persisted
     applied region (and confirms the script/service files still
     exist) and only rewrites the files / re-runs `iw reg set` /
     restarts pwnagotchi when something has genuinely changed - this
     both fixes "changes never take effect" AND avoids a needless
     restart on every single boot when nothing changed.
  4. **`on_unload` unconditionally shelled out to remove/stop things
     that may not exist**, using `os.system("rm " + SERV_PATH)` (no
     `-f`, errors loudly to stderr if the file's already gone) and
     unconditionally trying to stop/disable a systemd unit that may
     never have been created. Fixed: every step is guarded. File
     removal uses `os.remove()` inside `try/except FileNotFoundError`
     instead of shelling out to `rm`. The systemd service is only
     stopped/disabled if `systemctl is-enabled` actually reports it as
     enabled (checked via `subprocess.run`, never assumed).
  5. **Bogus dependency declaration.** `__dependencies__` claimed a
     pip dependency on `scapy`, which this plugin never imports or
     uses anywhere - not even the original did. Removed entirely. The
     plugin's real dependency is the `iw` command-line tool, which is
     standard on every pwnagotchi image (an apt-level expectation, not
     a pip package) - see `__dependencies__` below and README.md.

Framework facts this rebuild relies on (verified against the cloned
jayofelony/pwnagotchi fork, same as bluetooth-recon-suite and
mad-hatter-suite in this repo):
  - `plugins.load()` assigns `plugin.options` directly from the parsed
    TOML dict - `__defaults__` is NEVER merged by the framework. Every
    option read in this file goes through `_opt()`, never bare
    `self.options[...]`, matching the pattern already established by
    `bluetooth_recon_ng.py` (a module-level `DEFAULTS` dict + an
    `_opt()` reader).
  - `pwnagotchi.plugins.loaded` is a plain `{plugin_name: instance}`
    dict, keyed by the plugin *file's* basename - this is how the
    optional GPS-region-suggestion feature below looks up an
    already-loaded `gps_tagger_ng` (or similarly-shaped) plugin
    instance, the same `plugins.loaded.get(name)` pattern
    `MadHatterNG.py` already uses to look up `apprise_notify_ng`/
    `discord_ng`.
  - `on_unload(self, ui)` REQUIRES the `ui` parameter - the real
    framework always calls it with one, even though this plugin has no
    on-screen element and never touches `ui` itself.
  - There is no `pwnagotchi.plugins.notify()` function in the real
    framework; this plugin never calls anything like it.

Naming note: the plugin FILE is `fix_region_ng.py` (snake_case) and its
config section is `[main.plugins.fix_region_ng]` - matching the file's
exact basename, per this repo's `bluetooth_recon_ng.py` convention
(NOT `MadHatterNG.py`'s capital-NG rename, which was an explicit
one-off request for that suite only). The underlying framework fact is
the same either way: `pwnagotchi/plugins/__init__.py` registers and
looks up a plugin - both its "enabled" flag and its options table - by
the plugin file's exact basename with `.py` dropped, never by anything
written inside the Python class body. A config section that doesn't
match the file's basename exactly means the plugin never even appears
in the framework's "enabled" list - a total no-load, not an options
bug. See NOTES.md for the full writeup.

Approved improvements added on top of the bug fixes (see NOTES.md for
the full design writeup):
  - Real ISO 3166-1 alpha-2 format validation (covered in fix #1).
  - All shell interaction via `subprocess.run([...])` argument lists
    (covered in fix #2).
  - The regulatory domain reported by `iw reg get` is read and logged
    once on `on_loaded`, BEFORE this plugin changes anything, so
    there's a record in the log of what it was. Also shown live on the
    webhook status page.
  - Optional, off-by-default GPS-based region auto-suggestion
    (`gps_region_suggestion`): if a GPS-providing sibling plugin is
    loaded and has usable coordinates, a small offline bounding-box
    table suggests a probable ISO country code as a LOGGED SUGGESTION
    ONLY - it is shown in the log and on the webhook page, and NEVER
    auto-applied. The configured `region` value is the only thing this
    plugin ever actually applies. This feature is best-effort by
    design: no GPS plugin loaded, no coordinates available, or a
    lookup failure of any kind all degrade silently (a debug log line,
    nothing more) and never block plugin load. See NOTES.md for the
    bounding-box table's scope and honest limitations.

See config.toml for every option, README.md for install/verification
steps, and NOTES.md for the full bug-fix/design writeup.
"""

import _thread
import html
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone

import pwnagotchi.plugins as plugins
from pwnagotchi import restart


# This fork's plugin loader does NOT merge a plugin's __defaults__ into
# self.options (see module docstring) - every option must be read via
# _opt() with a real fallback here, never bare self.options[...].
DEFAULTS = {
    "enabled": False,
    # ISO 3166-1 alpha-2 country code to force. Validated at load time -
    # see _normalize_region(). "US" is a safe, always-legal-everywhere
    # fallback default; you should still set this explicitly for your
    # actual location (see config.toml).
    "region": "US",
    # Off by default - a purely informational, best-effort feature.
    # Never auto-applies anything; see the module docstring.
    "gps_region_suggestion": False,
    # Post-cluster-review update: GPS_SIBLING_PLUGIN_NAMES below was a
    # hardcoded tuple - a rename of gps-tagger-suite's .py file would
    # silently break this lookup. Now overridable: set this to a list
    # of plugin-loaded names to check *instead of* the built-in
    # defaults, in priority order. Leave unset (None) to use the
    # built-ins. on_ready logs whether a sibling was actually found.
    "gps_sibling_names": None,
    # Where the small "what region did we last actually apply"
    # state file lives - this is what makes change-detection (bug fix
    # #3) possible without re-parsing the generated shell script.
    "state_path": "/root/.fix_region_ng_state.json",
    # The persisted script + systemd unit this plugin writes/manages.
    "sh_path": "/root/network-fix.sh",
    "service_path": "/etc/systemd/system/network-fix.service",
    "service_name": "network-fix",
}

# Real ISO 3166-1 alpha-2 codes are exactly two letters. This is a
# format check, not a check against the real ISO list (embedding the
# full list isn't necessary here - `iw reg set` itself is the real
# authority on whether a code is meaningful, and will simply have no
# effect / log its own error for a well-formed-but-bogus code).
REGION_RE = re.compile(r"^[A-Za-z]{2}$")

SERVICE_TEMPLATE = """[Unit]
Description=Custom iw regulatory domain set script (fix_region_ng)
After=default.target

[Service]
ExecStart=/root/network-fix.sh

[Install]
WantedBy=default.target
"""

# Parses the "country XX:" line from `iw reg get`'s output, e.g.:
#   global
#   country US: DFS-FCC
REG_GET_RE = re.compile(r"^country\s+([A-Za-z]{2}):", re.MULTILINE)

# ---------------------------------------------------------------------------
# Small, offline, best-effort lat/lon -> ISO country code lookup for the
# optional GPS-suggestion feature. This is explicitly NOT a real geo
# database - it's a small bounding-box table for a handful of common
# countries, good enough for a "you might be in XX" log line, nothing
# more. See NOTES.md for the honest scope/limitations and what a real
# implementation would want instead (e.g. the `reverse_geocode` or
# `geopy` packages, deliberately not added as a dependency for a
# "nice to have" logged-suggestion-only feature).
#
# Each entry: (code, min_lat, max_lat, min_lon, max_lon). Checked in
# order; the first bounding box that contains the point wins. Boxes are
# deliberately simple rectangles (not real borders), so a point near a
# shared border/coastline can match the wrong neighbor - again, a
# suggestion, never applied automatically.
# ---------------------------------------------------------------------------
GPS_COUNTRY_BBOXES = (
    ("US", 24.0, 49.5, -125.0, -66.0),
    ("CA", 41.5, 83.5, -141.0, -52.0),
    ("GB", 49.8, 60.9, -8.7, 1.9),
    ("IE", 51.3, 55.5, -10.7, -5.9),
    ("NL", 50.7, 53.6, 3.3, 7.3),
    ("BE", 49.4, 51.6, 2.5, 6.5),
    ("DE", 47.2, 55.1, 5.8, 15.1),
    ("FR", 41.3, 51.2, -5.2, 9.6),
    ("ES", 35.9, 43.9, -9.4, 4.4),
    ("IT", 36.5, 47.2, 6.6, 18.6),
    ("CH", 45.8, 47.9, 5.9, 10.6),
    ("AT", 46.3, 49.1, 9.4, 17.3),
    ("PL", 49.0, 55.0, 14.0, 24.2),
    ("SE", 55.3, 69.1, 11.0, 24.2),
    ("NO", 57.9, 71.3, 4.5, 31.3),
    ("DK", 54.5, 57.9, 8.0, 15.3),
    ("FI", 59.7, 70.1, 20.5, 31.6),
    ("AU", -43.7, -10.0, 112.8, 153.7),
    ("NZ", -47.4, -34.0, 166.3, 178.6),
    ("JP", 24.0, 45.6, 122.9, 145.9),
    ("BR", -33.8, 5.3, -74.0, -34.7),
    ("MX", 14.5, 32.7, -118.5, -86.7),
    ("IN", 6.5, 35.5, 68.0, 97.4),
    ("ZA", -34.9, -22.1, 16.3, 32.9),
)


def _normalize_region(value):
    """-> a valid, uppercase 2-letter region code, or None.

    Never raises - the caller decides what "None" means (refuse to
    apply, in this plugin's case). This is the single choke point every
    region value passes through before it is used for anything: a shell
    command argument, a file written to disk, or a log line.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    if REGION_RE.match(candidate):
        return candidate
    return None


def _sh_script(region):
    return "#!/bin/bash\niw reg set %s\n" % region


def _parse_reg_get(output):
    match = REG_GET_RE.search(output or "")
    return match.group(1) if match else None


def _guess_country_from_latlon(lat, lon):
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    for code, min_lat, max_lat, min_lon, max_lon in GPS_COUNTRY_BBOXES:
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return code
    return None


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# Plugin names (this fork's file-basename registration key) that might
# expose GPS coordinates on their instance, checked in this order. Only
# `gps_tagger_ng` (this repo's own GPSTaggerNG suite, `pn_gps_coords` +
# `gps_hot`) is confirmed; the rest are best-effort generic attribute
# guesses that degrade silently if they don't match anything real -
# same "an internal rename/absence just means a skipped, logged
# feature, never a crash" spirit as MadHatterNG.py's notification
# lookups.
GPS_SIBLING_PLUGIN_NAMES = ("gps_tagger_ng", "gps_tagger", "gps")


class FixRegionNG(plugins.Plugin):
    __author__ = (
        "rebuilt for this project's plugin audit from itsdarklikehell's "
        "fix_region.py (edited from @V0rT3x's original network-fix "
        "service idea, credit: Dal/FikolmijReturns)"
    )
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Forces the WiFi regulatory domain (iw reg set) to unlock "
        "region-blocked channels, persisted across reboots via a "
        "systemd service, with format-validated config, change "
        "detection, and an optional GPS-based region suggestion."
    )
    __name__ = "FixRegionNG"
    __help__ = __description__
    __dependencies__ = {
        # `iw` is standard on every pwnagotchi image already - listed
        # here for completeness/documentation, not because it's
        # normally missing. No pip package is required (the original's
        # `scapy` dependency was bogus - see the module docstring,
        # fix #5).
        "apt": ["iw"],
        "pip": [],
    }

    def __init__(self):
        self.mode = "MANU"
        self._current_domain_before = None
        self._last_gps_suggestion = None

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    # ------------------------------------------------------------------
    # Lifecycle

    def on_loaded(self):
        logging.info(f"[{self.__class__.__name__}] plugin loaded")
        self._log_current_domain()

        configured_raw = self._opt("region")
        configured = _normalize_region(configured_raw)
        if configured is None:
            logging.error(
                f"[{self.__class__.__name__}] configured region "
                f"{configured_raw!r} is not a valid ISO 3166-1 alpha-2 "
                f"code (two letters, e.g. 'US', 'NL', 'GB') - refusing "
                f"to apply anything. Fix `region` in config.toml."
            )
            if self._opt("gps_region_suggestion"):
                self._log_gps_sibling_status()
                self._maybe_suggest_gps_region(None)
            return

        if self._opt("gps_region_suggestion"):
            self._log_gps_sibling_status()
            self._maybe_suggest_gps_region(configured)

        state = self._load_state()
        sh_path = self._opt("sh_path")
        service_path = self._opt("service_path")
        files_present = os.path.exists(sh_path) and os.path.exists(service_path)

        if state.get("applied_region") == configured and files_present:
            logging.info(
                f"[{self.__class__.__name__}] region {configured} is "
                f"already applied - nothing to do"
            )
            return

        self._apply_region(configured)

    def on_unload(self, ui):
        logging.info(f"[{self.__class__.__name__}] plugin unloading")
        service_path = self._opt("service_path")
        service_name = self._opt("service_name")
        sh_path = self._opt("sh_path")

        if os.path.exists(service_path) and self._service_is_enabled(service_name):
            try:
                subprocess.run(["systemctl", "stop", service_name], check=False)
            except (subprocess.SubprocessError, OSError) as err:
                logging.warning(
                    f"[{self.__class__.__name__}] couldn't stop "
                    f"{service_name}: {err!r}"
                )
            try:
                subprocess.run(["systemctl", "disable", service_name], check=False)
            except (subprocess.SubprocessError, OSError) as err:
                logging.warning(
                    f"[{self.__class__.__name__}] couldn't disable "
                    f"{service_name}: {err!r}"
                )
        else:
            logging.debug(
                f"[{self.__class__.__name__}] {service_name} isn't "
                f"installed/enabled - nothing to stop/disable"
            )

        for path in (service_path, sh_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as err:
                logging.warning(
                    f"[{self.__class__.__name__}] couldn't remove {path}: {err!r}"
                )

    # ------------------------------------------------------------------
    # Applying a region change

    def _apply_region(self, region):
        sh_path = self._opt("sh_path")
        service_path = self._opt("service_path")
        service_name = self._opt("service_name")

        # Belt-and-braces: `region` reaching here has always already
        # passed _normalize_region(), but this is the boundary where a
        # bad value would land in a root-executed script, so it's
        # checked again right before writing anything to disk.
        if _normalize_region(region) != region:
            logging.error(
                f"[{self.__class__.__name__}] refusing to write an "
                f"invalid region {region!r} to disk"
            )
            return

        try:
            with open(sh_path, "w") as f:
                f.write(_sh_script(region))
            os.chmod(sh_path, 0o755)
        except OSError as err:
            logging.error(
                f"[{self.__class__.__name__}] couldn't write {sh_path}: {err!r}"
            )
            return

        try:
            with open(service_path, "w") as f:
                f.write(SERVICE_TEMPLATE)
        except OSError as err:
            logging.error(
                f"[{self.__class__.__name__}] couldn't write {service_path}: {err!r}"
            )
            return

        try:
            subprocess.run(["iw", "reg", "set", region], check=True)
        except (subprocess.SubprocessError, OSError) as err:
            logging.warning(
                f"[{self.__class__.__name__}] 'iw reg set {region}' failed: {err!r}"
            )

        try:
            subprocess.run(["systemctl", "enable", service_name], check=True)
            subprocess.run(["systemctl", "start", service_name], check=True)
        except (subprocess.SubprocessError, OSError) as err:
            logging.warning(
                f"[{self.__class__.__name__}] couldn't enable/start "
                f"{service_name}: {err!r}"
            )

        state = self._load_state()
        state["applied_region"] = region
        state["applied_at"] = _now_iso()
        self._save_state(state)

        logging.info(
            f"[{self.__class__.__name__}] region set to {region}, "
            f"restarting pwnagotchi to apply it immediately"
        )
        try:
            _thread.start_new_thread(restart, (self.mode,))
        except Exception as err:
            logging.error(
                f"[{self.__class__.__name__}] couldn't trigger restart: {err!r}"
            )

    # ------------------------------------------------------------------
    # Regulatory domain introspection

    def _iw_reg_get(self):
        try:
            result = subprocess.run(
                ["iw", "reg", "get"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return result.stdout
        except (subprocess.SubprocessError, OSError) as err:
            logging.debug(
                f"[{self.__class__.__name__}] couldn't run 'iw reg get': {err!r}"
            )
            return None

    def _log_current_domain(self):
        output = self._iw_reg_get()
        self._current_domain_before = _parse_reg_get(output)
        logging.info(
            f"[{self.__class__.__name__}] regulatory domain before this "
            f"plugin touches anything: {self._current_domain_before or 'unknown'}"
        )

    def _service_is_enabled(self, service_name):
        try:
            result = subprocess.run(
                ["systemctl", "is-enabled", service_name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return result.stdout.strip() == "enabled"
        except (subprocess.SubprocessError, OSError):
            return False

    # ------------------------------------------------------------------
    # Persistence (bug fix #3: detect real config changes)

    def _load_state(self):
        path = self._opt("state_path")
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except FileNotFoundError:
            pass
        except Exception as err:
            logging.warning(
                f"[{self.__class__.__name__}] couldn't read {path}: {err!r}"
            )
        return {}

    def _save_state(self, state):
        path = self._opt("state_path")
        try:
            directory = os.path.dirname(path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
            with open(path, "w") as f:
                json.dump(state, f)
        except OSError as err:
            logging.warning(
                f"[{self.__class__.__name__}] couldn't write {path}: {err!r}"
            )

    # ------------------------------------------------------------------
    # Optional GPS-based region suggestion (logged only, never applied)

    def _log_gps_sibling_status(self):
        """Post-cluster-review addition: this lookup previously degraded
        completely silently (no log at all) if none of
        GPS_SIBLING_PLUGIN_NAMES were found - e.g. because gps-tagger-
        suite was renamed, disabled, or never installed. Logs a clear
        INFO/WARNING line at load time stating which name (if any) was
        actually found, so a misconfiguration is visible in the normal
        log rather than a silently-empty feature."""
        names = self._gps_sibling_names()
        for name in names:
            if plugins.loaded.get(name) is not None:
                logging.info(
                    f"[{self.__class__.__name__}] gps_region_suggestion: "
                    f"sibling plugin '{name}' found"
                )
                return
        logging.warning(
            f"[{self.__class__.__name__}] gps_region_suggestion: none of "
            f"{names!r} found in plugins.loaded - the GPS-based region "
            f"hint won't fire until one is installed/enabled, or "
            f"gps_sibling_names is corrected if it was renamed"
        )

    def _gps_sibling_names(self):
        configured = self._opt("gps_sibling_names")
        if isinstance(configured, list) and configured:
            return tuple(str(n) for n in configured)
        return GPS_SIBLING_PLUGIN_NAMES

    def _find_gps_coords(self):
        for name in self._gps_sibling_names():
            target = plugins.loaded.get(name)
            if target is None:
                continue

            coords = getattr(target, "pn_gps_coords", None)
            if isinstance(coords, dict):
                lat, lon = coords.get("Latitude"), coords.get("Longitude")
                if lat is not None and lon is not None:
                    return lat, lon

            lat = getattr(target, "lat", None)
            lat = lat if lat is not None else getattr(target, "latitude", None)
            lon = getattr(target, "lon", None)
            lon = lon if lon is not None else getattr(target, "longitude", None)
            if lat is not None and lon is not None:
                return lat, lon
        return None, None

    def _maybe_suggest_gps_region(self, configured_region):
        self._last_gps_suggestion = None
        try:
            lat, lon = self._find_gps_coords()
            if lat is None or lon is None:
                logging.debug(
                    f"[{self.__class__.__name__}] gps_region_suggestion is "
                    f"on, but no GPS coordinates are available - skipping"
                )
                return

            guess = _guess_country_from_latlon(lat, lon)
            if guess is None:
                logging.debug(
                    f"[{self.__class__.__name__}] GPS coordinates "
                    f"({lat}, {lon}) didn't match any known bounding box - "
                    f"no suggestion"
                )
                return

            self._last_gps_suggestion = guess
            if configured_region and guess != configured_region:
                logging.info(
                    f"[{self.__class__.__name__}] GPS suggests you may be "
                    f"in region {guess}; configured region is currently "
                    f"{configured_region}. This is a LOGGED SUGGESTION "
                    f"ONLY - update `region` in config.toml yourself if "
                    f"you want to use it; nothing is applied automatically."
                )
            else:
                logging.debug(
                    f"[{self.__class__.__name__}] GPS-suggested region "
                    f"{guess} matches (or no configured region to compare "
                    f"against)"
                )
        except Exception as err:
            # Explicitly best-effort/optional - never let a lookup
            # failure of any kind block plugin load.
            logging.debug(
                f"[{self.__class__.__name__}] GPS region suggestion "
                f"skipped: {err!r}"
            )
            self._last_gps_suggestion = None

    # ------------------------------------------------------------------
    # Webhook: status page

    def on_webhook(self, path, request):
        path = (path or "").strip("/")
        if path in ("", "status"):
            return self._status_page()
        return "Not found", 404

    def _status_page(self):
        configured_raw = self._opt("region")
        configured = _normalize_region(configured_raw)

        state = self._load_state()
        applied = state.get("applied_region")
        applied_at = state.get("applied_at")

        current_domain = _parse_reg_get(self._iw_reg_get())

        service_path = self._opt("service_path")
        service_name = self._opt("service_name")
        installed = os.path.exists(service_path)
        enabled = installed and self._service_is_enabled(service_name)

        suggestion = self._last_gps_suggestion

        rows = [
            ("Configured region", html.escape(str(configured_raw))),
            (
                "Configured region valid?",
                "yes" if configured else "NO - see the plugin log",
            ),
            ("Last-applied region", html.escape(str(applied)) if applied else "(never applied)"),
            ("Last applied at", html.escape(str(applied_at)) if applied_at else ""),
            (
                "Current `iw reg get` domain",
                html.escape(str(current_domain)) if current_domain else "unknown",
            ),
            ("Persistence service installed", "yes" if installed else "no"),
            ("Persistence service enabled", "yes" if enabled else "no"),
        ]
        if suggestion:
            rows.append(("GPS-suggested region (never auto-applied)", html.escape(str(suggestion))))

        table_rows = "".join(
            f"<tr><th align='left'>{label}</th><td>{value}</td></tr>"
            for label, value in rows
        )

        return (
            "<html><head><title>FixRegionNG</title></head>"
            "<body style='font-family: sans-serif;'>"
            "<h2>FixRegionNG</h2>"
            "<table border='1' cellpadding='4'>"
            f"{table_rows}"
            "</table>"
            "<p>Verify manually with <code>iw reg get</code> and "
            "<code>iwlist wlan0 channel</code>.</p>"
            "</body></html>"
        )
