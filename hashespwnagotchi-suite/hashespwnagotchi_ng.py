"""
hashespwnagotchi_ng.py - pwnagotchi plugin (runs ON the pi)

Rewrite of hashespwnagotchi.py (itsdarklikehell/pwnagotchi-plugins,
originally meow@hashes.pw), fixed for the jayofelony 64-bit fork.

Converts captured handshakes to `.22000`/`.16800` hash files locally via
hcxpcapngtool, uploads them to https://hashes.pw, and (for captures that
couldn't be converted at all) reports which ones have GPS data available
via any of this project's `.gps.json`/`.geo.json`/`.paw-gps.json`
sidecar conventions - including the one gps_tagger_ng.py (built earlier
in this project) already writes.

============================================================================
FIX #1 - REAL SECURITY ISSUE, this is why this plugin was not safe to run
as shipped:
============================================================================
The original built hcxpcapngtool/tcpdump commands with Python string
formatting/concatenation and ran them through a real shell
(`subprocess.getoutput(...)`, and for the tcpdump fallback,
`subprocess.check_output(..., shell=True)`). This fork's own handshake
filenames embed the AP's ESSID directly (`{ESSID}_{BSSID}.pcapng`,
confirmed against this fork's documented capture-file naming) - and an
ESSID is a value *any nearby device can broadcast as literally
anything*, including shell metacharacters. A maliciously-named AP could
therefore inject arbitrary shell commands, run as root, the moment this
plugin tried to convert a handshake captured from it. Fixed: every
external command now runs via `subprocess.run([...], shell=False)` with
each argument passed separately - nothing from a filename is ever
interpreted by a shell, so there is no injection surface no matter what
an AP names itself. The tcpdump-piped-to-sed fallback in the PMKID
repair path (a second, independent shell=True call using raw string
concatenation - the original's least safe line) is replaced with
tcpdump run directly plus the same extraction done with Python's `re`
module - same result, no shell, no pipe.

Other bugs fixed vs. the original (all source-verified):
  2. `on_config_changed()` referenced `self.status.newer_then_hours(...)`,
     but `self.status` is never assigned anywhere - only `self.report`
     (the actual StatusFile instance) is. Every time `interval` was set
     in config.toml, this raised AttributeError immediately, silently
     killing on_config_changed before it could log anything or run the
     startup batch conversion. Without `interval` set, Python's `or`
     short-circuit hid the bug (the batch always ran, just with no rate
     limit) - meaning the bug only appeared for anyone trying to use the
     very option meant to make this more efficient. Fixed: uses
     self.report consistently.
  3. The startup batch conversion (`_process_stale_pcaps`) filtered for
     `.pcap` - this fork only ever writes `.pcapng`, so once fix #2 let
     it actually run, it still found nothing. Fixed throughout.
  4. Filename/extension parsing used `path.split(".")[0]` in several
     places - this takes everything before the *first* dot in the whole
     path, not the real extension, and breaks if an ESSID (which can
     legally contain a period, e.g. "Motorola.5G") appears before the
     true extension. Fixed: `os.path.splitext()` everywhere, which
     splits at the *last* dot.
  5. The whitelist-exclusion call (`remove_whitelisted`) was present in
     the source but commented out - unlike every sibling plugin in this
     cluster, it would have uploaded every captured handshake with no
     exclusions at all once the other bugs were fixed. Re-enabled.
  6. `_repairPMKID`'s dict-based branch called `.encode("hex")` - a
     Python 2 codec that doesn't exist in Python 3 (raises LookupError).
     The function's own fallback branch already does this correctly a
     few lines later (`.encode().hex()`) - the first branch just never
     got the same fix. Made consistent.
  7. A handshake that failed to upload once was added to `self.skip`
     forever, with nothing ever removing it - a single transient network
     hiccup permanently blacklisted that handshake from ever being
     retried again until the whole plugin (and pwnagotchi) restarted.
     Fixed: bounded retries with backoff per handshake (see
     `max_upload_attempts`/`upload_retry_delay` below) before it's
     finally given up on for this run.
  8. `_validate_or_fetch_token()` read `response["token"]` with bare
     indexing - if the API ever returned a differently-shaped response,
     this would raise an uncaught KeyError instead of the ValueError the
     surrounding code already knows how to handle cleanly. Fixed to use
     `.get()` and raise that same, already-handled ValueError.

What's added (approved quality-of-life improvements):
  - `hcxpcapngtool_timeout` / `upload_timeout` config options so a
    corrupt capture or a hung connection can't block the plugin
    indefinitely (the original had no timeout on either).
  - A startup dependency check: if `hcxpcapngtool` isn't found on PATH,
    logs one clear error immediately on load instead of failing
    confusingly on the first real capture.
  - Bounded upload retries (see fix #7).

See config.toml.example in this folder for what to add to config.toml,
and README.md for full install/usage notes.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from threading import Lock

import requests

import pwnagotchi.plugins as plugins
from pwnagotchi.utils import StatusFile, remove_whitelisted
from json.decoder import JSONDecodeError

STATE_FILE = "/root/.hashespw_uploads"

BEACON_FILTER = "(wlan type mgt subtype beacon) or (wlan type mgt subtype probe-resp) or (wlan type mgt subtype reassoc-resp) or (wlan type mgt subtype assoc-req)"
# Matches the same shape the original's `sed -E` extracted: a BSSID
# followed later on the line by a name in parentheses (tcpdump's own
# "(SSID)" annotation on these frame types).
_BSSID_LINE_RE = re.compile(r".*BSSID:([0-9a-fA-F:]{17}).*\((.*)\).*")


def _run(args, timeout=30):
    """Run an external command with a real argument list - never a shell
    string - so nothing in `args` (an ESSID-derived filename, say) can
    ever be interpreted as shell syntax. Returns (ok, stdout)."""
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        return result.returncode == 0, result.stdout.decode("utf-8", errors="ignore")
    except (subprocess.SubprocessError, OSError) as e:
        logging.debug(f"[hashespwnagotchi_ng] command failed: {args[0]} ({e})")
        return False, ""


class HashesPwnagotchiNG(plugins.Plugin):
    __author__ = "itsdarklikehell, meow@hashes.pw (original); rewritten for jayofelony fork"
    __version__ = "2.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Converts handshakes to .22000/.16800 hash files locally and "
        "uploads them to hashes.pw - with the original's shell-injection "
        "risk removed."
    )
    __name__ = "HashesPwnagotchiNG"
    __help__ = __description__
    __dependencies__ = {
        "apt": ["libcurl4-openssl-dev", "libssl-dev", "zlib1g-dev", "hcxtools"],
        "pip": ["requests"],
    }
    # NOTE: this fork's plugin loader does NOT read __defaults__ - set
    # every option explicitly in config.toml. See config.toml.example.
    __defaults__ = {
        "enabled": False,
        "api_key": None,
    }

    @property
    def headers(self):
        return {
            "Authorization": "Bearer %s" % (self.token,),
            "Content-type": "application/json",
        }

    def __init__(self):
        self.ready = False
        self.lock = Lock()
        self.options = dict()
        self.skip = set()
        self.attempts = {}
        self.token = None
        self.uuid = None
        self.report = self._open_report()
        logging.debug("[hashespwnagotchi_ng] plugin init")

    @staticmethod
    def _open_report():
        try:
            return StatusFile(STATE_FILE, data_format="json")
        except JSONDecodeError:
            os.remove(STATE_FILE)
            return StatusFile(STATE_FILE, data_format="json")

    def on_loaded(self):
        if not self.options.get("api_key"):
            logging.error("hashespwnagotchi_ng: api_key isn't set. Can't upload to hashes.pw")
            return
        if not self.options.get("api_url"):
            logging.error("hashespwnagotchi_ng: api_url isn't set. Can't upload, no endpoint configured.")
            return
        if shutil.which("hcxpcapngtool") is None:
            logging.error(
                "hashespwnagotchi_ng: hcxpcapngtool not found on PATH - install hcxtools "
                "(apt-get install hcxtools) before this plugin can do anything."
            )
            return
        self.ready = True
        logging.info("[hashespwnagotchi_ng] plugin loaded")

    def on_config_changed(self, config):
        handshake_dir = config["bettercap"]["handshakes"]
        self.uuid = str(uuid.uuid5(uuid.NAMESPACE_OID, config["main"]["name"]))
        self.report = self._open_report()  # FIX #2 (was self.status, never assigned)
        interval = self.options.get("interval")
        if interval is None or not self.report.newer_then_hours(interval):
            logging.info("[hashespwnagotchi_ng] starting batch conversion of stale captures")
            with self.lock:
                self._process_stale_captures(handshake_dir)
        logging.info("[hashespwnagotchi_ng] config changed")

    def on_bored(self, agent):
        self._report_handshakes(agent)

    def on_internet_available(self, agent):
        self._report_handshakes(agent)

    def on_handshake(self, agent, filename, access_point, client_station):
        if not self.ready:
            return
        with self.lock:
            status = []
            base_no_ext, _ext = os.path.splitext(filename)  # FIX #4
            name = os.path.splitext(os.path.basename(filename))[0]

            if os.path.isfile(base_no_ext + ".22000"):
                status.append(f"already have {name}.22000 (EAPOL)")
            elif self._write_eapol(filename):
                status.append(f"created {name}.22000 (EAPOL) from capture")
                self._report_handshakes(agent)

            if os.path.isfile(base_no_ext + ".16800"):
                status.append(f"already have {name}.16800 (PMKID)")
            elif self._write_pmkid(filename, access_point):
                status.append(f"created {name}.16800 (PMKID) from capture")

            if status:
                logging.info("[hashespwnagotchi_ng] " + "; ".join(status))

    # ------------------------------------------------------------------
    # Local conversion (hcxpcapngtool) - all via _run(), never a shell.

    def _hcx_timeout(self):
        return self.options.get("hcxpcapngtool_timeout", 60)

    def _write_eapol(self, fullpath):
        base_no_ext, _ext = os.path.splitext(fullpath)  # FIX #4
        ok, _out = _run(["hcxpcapngtool", "-o", base_no_ext + ".22000", fullpath], timeout=self._hcx_timeout())
        created = os.path.isfile(base_no_ext + ".22000")
        if created:
            logging.debug(f"[hashespwnagotchi_ng] EAPOL success: {base_no_ext}.22000")
        return created

    def _write_pmkid(self, fullpath, ap_json):
        base_no_ext, _ext = os.path.splitext(fullpath)
        _run(["hcxpcapngtool", "-k", base_no_ext + ".16800", fullpath], timeout=self._hcx_timeout())
        if os.path.isfile(base_no_ext + ".16800"):
            logging.debug(f"[hashespwnagotchi_ng] PMKID success: {base_no_ext}.16800")
            return True

        # fall back to a raw dump, then try to repair it with a known AP name
        _run(["hcxpcapngtool", "-K", base_no_ext + ".16800", fullpath], timeout=self._hcx_timeout())
        if not os.path.isfile(base_no_ext + ".16800"):
            logging.debug(f"[hashespwnagotchi_ng] no raw PMKID produced for {base_no_ext}, nothing to repair")
            return False

        if self._repair_pmkid(fullpath, ap_json):
            logging.debug(f"[hashespwnagotchi_ng] PMKID repaired: {base_no_ext}.16800")
            return True

        logging.debug(f"[hashespwnagotchi_ng] PMKID could not be repaired: {base_no_ext}.16800")
        return False

    def _repair_pmkid(self, fullpath, ap_json):
        base_no_ext, _ext = os.path.splitext(fullpath)
        name = os.path.basename(base_no_ext)

        with open(base_no_ext + ".16800", "r") as fp:
            hash_string = fp.read()

        candidates = []  # list of "MAC_NO_COLONS:hex_encoded_name"
        if ap_json:
            candidates.append(
                "{}:{}".format(
                    ap_json["mac"].replace(":", ""),
                    ap_json.get("hostname", "").encode().hex(),  # FIX #6: was .encode("hex") (Python 2 only)
                )
            )
        else:
            # try hcxpcapngtool's own info-extraction first
            tmp_out = f"/tmp/hashespwnagotchi_ng_{name}"
            _run(["hcxpcapngtool", "-X", tmp_out, fullpath], timeout=self._hcx_timeout())
            if os.path.isfile(tmp_out):
                with open(tmp_out, "r") as fp:
                    for line in fp.read().splitlines():
                        if ":" in line:
                            mac, _, label = line.partition(":")
                            candidates.append(f"{mac}:{label.strip().encode().hex()}")
                os.remove(tmp_out)

            # fall back to reading beacon/probe/assoc frames with tcpdump
            # directly (no shell, no pipe to sed - FIX #1)
            ok, tcp_out = _run(
                ["tcpdump", "-ennr", fullpath, BEACON_FILTER],
                timeout=self._hcx_timeout(),
            )
            if ok:
                for line in tcp_out.splitlines():
                    m = _BSSID_LINE_RE.match(line)
                    if m:
                        mac = m.group(1).replace(":", "")
                        label = m.group(2).strip().encode().hex()
                        candidates.append(f"{mac}:{label}")

        if not candidates:
            os.remove(base_no_ext + ".16800")
            return False

        hash_bssid = hash_string.split(":")[1] if ":" in hash_string else None
        for candidate in candidates:
            cand_mac, _, cand_label = candidate.partition(":")
            if cand_mac == hash_bssid:
                repaired = hash_string.strip("\n") + ":" + cand_label
                if len(repaired.split(":")) == 4 and not repaired.endswith(":"):
                    with open(base_no_ext + ".16800", "w") as fp:
                        fp.write(repaired + "\n")
                    return True
        return False

    def _process_stale_captures(self, handshake_dir):
        captures = [
            os.path.join(handshake_dir, f)
            for f in os.listdir(handshake_dir)
            if f.endswith(".pcapng")  # FIX #3 (was .pcap)
        ]
        ok_count = 0
        lonely = []
        for idx, capture in enumerate(captures):
            base_no_ext, _ext = os.path.splitext(capture)
            got_eapol = os.path.isfile(base_no_ext + ".22000")
            if not got_eapol:
                got_eapol = self._write_eapol(capture)
                if got_eapol:
                    ok_count += 1

            got_pmkid = os.path.isfile(base_no_ext + ".16800")
            if not got_pmkid:
                got_pmkid = self._write_pmkid(capture, None)
                if got_pmkid:
                    ok_count += 1

            if not got_eapol and not got_pmkid:
                lonely.append(capture)

            if (idx + 1) % 50 == 0 or idx + 1 == len(captures):
                logging.info(f"[hashespwnagotchi_ng] batch: {idx + 1}/{len(captures)} done ({len(lonely)} lonely so far)")

        if ok_count:
            logging.info(f"[hashespwnagotchi_ng] batch: {ok_count} new hash file(s) created")
        if lonely:
            logging.info(f"[hashespwnagotchi_ng] batch: {len(lonely)} capture(s) with no hash and GPS data checked")
            self._report_gps_coverage(lonely)

    def _report_gps_coverage(self, captures):
        with_gps = 0
        for capture in captures:
            base_no_ext, _ext = os.path.splitext(capture)
            if (
                os.path.isfile(base_no_ext + ".gps.json")  # written by gps_tagger_ng.py, among others
                or os.path.isfile(base_no_ext + ".geo.json")
                or os.path.isfile(base_no_ext + ".paw-gps.json")
            ):
                with_gps += 1
        if with_gps:
            logging.info(
                f"[hashespwnagotchi_ng] {with_gps}/{len(captures)} lonely capture(s) have GPS data available "
                "(check webgpsmap or a similar plugin to see where they were)"
            )
        else:
            logging.info("[hashespwnagotchi_ng] no GPS data found for any lonely captures")

    # ------------------------------------------------------------------
    # hashes.pw upload

    def _report_handshakes(self, agent):
        if not self.ready or self.lock.locked() or not self._connected_to_internet():
            return

        with self.lock:
            config = agent.config()
            display = agent.view()
            reported = set(self.report.data_field_or("reported", default=list()))
            handshake_dir = config["bettercap"]["handshakes"]

            hash_paths = [
                os.path.join(handshake_dir, f)
                for f in os.listdir(handshake_dir)
                if f.endswith(".22000")
            ]
            hash_paths = remove_whitelisted(hash_paths, self.options.get("whitelist", []))  # FIX #5
            max_attempts = self.options.get("max_upload_attempts", 3)
            pending = [
                h for h in hash_paths
                if h not in reported and h not in self.skip
            ]

            if not pending:
                return

            logging.info(f"[hashespwnagotchi_ng] {len(pending)} hash file(s) to upload")
            for idx, handshake in enumerate(pending):
                display.on_uploading(f"hashes.pw ({idx + 1}/{len(pending)})")
                try:
                    self._upload_eapol(handshake, config["main"]["name"])
                    reported.add(handshake)
                    self.report.update(data={"reported": list(reported)})
                    self.attempts.pop(handshake, None)
                    logging.debug(f"[hashespwnagotchi_ng] uploaded {handshake}")
                except (requests.exceptions.RequestException, OSError) as e:
                    self._note_failed_attempt(handshake, max_attempts, e)  # FIX #7
                    continue
                except ValueError as e:
                    logging.warning(f"[hashespwnagotchi_ng] hashes.pw rejected {handshake}: {e}")
                    self._note_failed_attempt(handshake, max_attempts, e)
                    continue

            display.on_normal()

    def _note_failed_attempt(self, handshake, max_attempts, error):
        count = self.attempts.get(handshake, 0) + 1
        self.attempts[handshake] = count
        if count >= max_attempts:
            logging.warning(
                f"[hashespwnagotchi_ng] giving up on {handshake} after {count} failed attempts: {error}"
            )
            self.skip.add(handshake)
        else:
            logging.debug(
                f"[hashespwnagotchi_ng] upload attempt {count}/{max_attempts} failed for {handshake}: {error}"
            )
            time.sleep(self.options.get("upload_retry_delay", 5))

    def _upload_eapol(self, path, pwnagotchi_name=None):
        essid = self._essid_from_path(path)
        payload = {
            "name": pwnagotchi_name,
            "essid": essid,
            "bssid": self._bssid_from_path(path, essid),
            "value": self._single_line_from_file(path),
        }
        r = self._post("pwnagotchi", payload)

        if r.status_code not in (200, 204):
            try:
                decoded = json.loads(r.content)
                if decoded.get("value", [None])[0] == "already exists":
                    return
            except (json.JSONDecodeError, AttributeError, IndexError):
                pass
            raise ValueError(f"hashes.pw refused {path}: {r.status_code} {r.content!r}")

    def _validate_or_fetch_token(self):
        if self.token is not None:
            return
        full_path = self._uri_format(self.options["api_url"], "agent/pwnagotchi")
        r = requests.post(
            full_path,
            json={"uuid": self.uuid, "auth_only": True},
            headers={"Authorization": f"Token token={self.options['api_key']}"},
            timeout=self.options.get("upload_timeout", 30),
        )
        response = json.loads(r.content)
        token = response.get("token")  # FIX #8 (was bare response["token"])
        if not token:
            raise ValueError("failed to obtain a token from hashes.pw")
        self.token = token

    def _post(self, path, data=None):
        self._validate_or_fetch_token()
        full_path = self._uri_format(self.options["api_url"], path)
        timeout = self.options.get("upload_timeout", 30)
        r = requests.post(full_path, json=data or {}, headers=self.headers, timeout=timeout)
        if r.status_code == 403:
            self.token = None
            self._validate_or_fetch_token()
            r = requests.post(full_path, json=data or {}, headers=self.headers, timeout=timeout)
        return r

    @staticmethod
    def _uri_format(root_path, route):
        uri = root_path.rstrip("/")
        if route:
            uri = f"{uri}/{route}"
        return uri

    @staticmethod
    def _single_line_from_file(path):
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="ISO-8859-1", errors="ignore") as fp:
            for line in fp:
                return line.strip()
        return None

    @staticmethod
    def _essid_from_path(path):
        name = os.path.basename(path)
        return name.split("_")[0] if "_" in name else None

    @staticmethod
    def _bssid_from_path(path, essid=None):
        base_no_ext = os.path.splitext(os.path.basename(path))[0]
        if essid is None:
            return base_no_ext
        return base_no_ext.replace(essid + "_", "")

    @staticmethod
    def _connected_to_internet():
        try:
            requests.get("https://google.com", timeout=5)
            return True
        except (requests.ConnectionError, requests.Timeout):
            return False

    def on_webhook(self, path, request):
        return f"[HashesPwnagotchiNG] ready={self.ready}, {len(self.skip)} permanently skipped this run."
