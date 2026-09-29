"""
banthex_ng.py - pwnagotchi plugin (runs ON the pi)

Rewrite of banthex-de.py (itsdarklikehell/pwnagotchi-plugins, originally
adi1708), fixed for the jayofelony 64-bit fork. Auto-uploads captured
handshakes to https://banthex.de/wpa/ (a wpa-sec-compatible service) and
optionally downloads cracked results back as a potfile.

`banthex-de.py` was picked over its near-identical sibling `banthex.py`
(same original author's later contributor, dadav) because `banthex.py`
has its own extra bug - a corrupted-state-file recovery path that deletes
the WRONG file (see `test-plugins:plugin-upgrade-proposals/
cluster-06-cloud-crack-upload/NOTES.md` for that history; `banthex.py`
was removed from the master list rather than fixed, since this plugin is
the better starting point).

Bugs fixed vs. the original (source-verified):
  1. The recurring bug in this whole project: `filename.endswith('.pcap')`
     never matches this fork's real `.pcapng` captures. Fixed.
  2. A handshake that failed to upload once was added to a permanent
     skip list with nothing ever clearing it - a single transient
     network hiccup meant that handshake would never be retried again
     until the whole plugin reloaded. Fixed with bounded retries (see
     `max_upload_attempts` below) - the same fix already applied to
     hashespwnagotchi_ng.py in this same cluster, for consistency.

What's added (approved quality-of-life improvements):
  - `upload_timeout` / `download_timeout` config options (the original
    hardcoded 30 seconds for both with no way to change it).
  - `download_check_interval_hours` config option (the original
    hardcoded "don't re-download more than once per hour" with no way to
    change it).
  - Bounded upload retries (fix #2 above).

What's deliberately unchanged: `on_webhook()` sets your `api_key` as a
plaintext cookie on a redirect to banthex.de - that's how this service's
own web-based auth works (the same pattern the real wpa-sec site uses),
not something this plugin can change on its own. Worth knowing: if
anything is inspecting that redirect (a proxy, browser history on a
shared machine), your API key is visible in it. Documented in README.md.

See config.toml.example in this folder for what to add to config.toml,
and README.md for full install/usage notes.
"""

import logging
import os
from datetime import datetime
from threading import Lock

import requests

import pwnagotchi.plugins as plugins
from pwnagotchi.utils import StatusFile, remove_whitelisted
from json.decoder import JSONDecodeError

STATE_FILE = "/root/.banthex_uploads"


class BanthexNG(plugins.Plugin):
    __author__ = "itsdarklikehell, adi1708 (original); rewritten for jayofelony fork"
    __version__ = "2.0.0"
    __license__ = "GPL3"
    __description__ = "Uploads handshakes to https://banthex.de/wpa/ and optionally downloads cracked results."
    __name__ = "BanthexNG"
    __help__ = __description__
    __dependencies__ = {
        "pip": ["requests"],
    }
    # NOTE: this fork's plugin loader does NOT read __defaults__ - set
    # every option explicitly in config.toml. See config.toml.example.
    __defaults__ = {
        "enabled": False,
        "api_key": "",
        "api_url": "https://banthex.de/wpa/",
        "download_results": False,
        "whitelist": [],
    }

    def __init__(self):
        self.ready = False
        self.lock = Lock()
        self.options = dict()
        self.skip = set()
        self.attempts = {}
        self.report = self._open_report()
        logging.debug(f"[{self.__class__.__name__}] plugin init")

    @staticmethod
    def _open_report():
        try:
            return StatusFile(STATE_FILE, data_format="json")
        except JSONDecodeError:
            os.remove(STATE_FILE)
            return StatusFile(STATE_FILE, data_format="json")

    def on_loaded(self):
        if not self.options.get("api_key"):
            logging.error("BANTHEX: api_key isn't set. Can't upload to banthex.de")
            return
        if not self.options.get("api_url"):
            logging.error("BANTHEX: api_url isn't set. Can't upload, no endpoint configured.")
            return
        self.ready = True
        logging.info(f"[{self.__class__.__name__}] plugin loaded")

    def on_webhook(self, path, request):
        from flask import make_response, redirect

        response = make_response(redirect(self.options["api_url"], code=302))
        response.set_cookie("key", self.options["api_key"])
        return response

    def on_internet_available(self, agent):
        if not self.ready or self.lock.locked():
            return

        with self.lock:
            config = agent.config()
            display = agent.view()
            reported = set(self.report.data_field_or("reported", default=list()))
            handshake_dir = config["bettercap"]["handshakes"]

            handshake_paths = [
                os.path.join(handshake_dir, f)
                for f in os.listdir(handshake_dir)
                if f.endswith(".pcapng")  # FIX #1 (was .pcap)
            ]
            handshake_paths = remove_whitelisted(handshake_paths, self.options.get("whitelist", []))
            pending = [h for h in handshake_paths if h not in reported and h not in self.skip]

            if pending:
                logging.info("BANTHEX: internet connectivity detected, uploading new handshakes")
                max_attempts = self.options.get("max_upload_attempts", 3)
                for idx, handshake in enumerate(pending):
                    display.on_uploading(f"banthex.de ({idx + 1}/{len(pending)})")
                    try:
                        self._upload_to_banthex(handshake)
                        reported.add(handshake)
                        self.report.update(data={"reported": list(reported)})
                        self.attempts.pop(handshake, None)
                        logging.debug(f"BANTHEX: successfully uploaded {handshake}")
                    except (requests.exceptions.RequestException, OSError) as e:
                        self._note_failed_attempt(handshake, max_attempts, e)  # FIX #2
                        continue
                display.on_normal()

            if self.options.get("download_results", False):
                self._maybe_download_cracked(handshake_dir)

    def _note_failed_attempt(self, handshake, max_attempts, error):
        count = self.attempts.get(handshake, 0) + 1
        self.attempts[handshake] = count
        if count >= max_attempts:
            logging.warning(f"BANTHEX: giving up on {handshake} after {count} failed attempts: {error}")
            self.skip.add(handshake)
        else:
            logging.debug(f"BANTHEX: upload attempt {count}/{max_attempts} failed for {handshake}: {error}")

    def _maybe_download_cracked(self, handshake_dir):
        cracked_file = os.path.join(handshake_dir, "banthex.cracked.potfile")
        interval_hours = self.options.get("download_check_interval_hours", 1)
        if os.path.exists(cracked_file):
            last_check = datetime.fromtimestamp(os.path.getmtime(cracked_file))
            if (datetime.now() - last_check).total_seconds() / 3600 < interval_hours:
                return
        try:
            self._download_from_banthex(cracked_file)
            logging.info("BANTHEX: downloaded cracked passwords")
        except (requests.exceptions.RequestException, OSError) as e:
            logging.debug(f"BANTHEX: could not download cracked results: {e}")

    def _upload_to_banthex(self, path):
        timeout = self.options.get("upload_timeout", 30)
        with open(path, "rb") as file_to_upload:
            cookie = {"key": self.options["api_key"]}
            payload = {"file": file_to_upload}
            result = requests.post(self.options["api_url"], cookies=cookie, files=payload, timeout=timeout)
            if " already submitted" in result.text:
                logging.debug(f"BANTHEX: {path} was already submitted")

    def _download_from_banthex(self, output):
        timeout = self.options.get("download_timeout", 30)
        api_url = self.options["api_url"].rstrip("/") + "/?api&dl=1"
        cookie = {"key": self.options["api_key"]}
        result = requests.get(api_url, cookies=cookie, timeout=timeout)
        with open(output, "wb") as output_file:
            output_file.write(result.content)

    def on_unload(self, ui):
        with ui._lock:
            logging.info(f"[{self.__class__.__name__}] plugin unloaded")
