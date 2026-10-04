"""
crack_pipeline_ng.py - pwnagotchi plugin

CrackPipelineNG merges FOUR of the repo owner's (patrickato's) own
hand-written plugins into one properly-architected cracking pipeline:

  - ClaudeCrackAuto.py   (test-plugins/pwnagotchi-plugins/claudecrackauto/)
    whitelist-gated: hcxpcapngtool conversion, then either a local
    hashcat run against one configured wordlist, or export-only for
    offline cracking elsewhere; ntfy/Discord notifications; on-screen
    status element.
  - RuleMutationCrack.py (test-plugins/pwnagotchi-plugins/rulemutationcrack/)
    whitelist-gated: a plain wordlist pass first, then a hashcat
    rule-file mutation pass on top of the same wordlist if the plain
    pass fails.
  - HashFormatDetector.py (test-plugins/pwnagotchi-plugins/hashformatdetector/)
    unscoped classifier: routes WEP to aircrack-ng, WPA/WPA2/PMKID to
    hashcat -m 22000, open networks to "nothing to crack", and writes a
    `.route` sidecar file next to each capture.
  - best_quickdic.py     (test-plugins/pwnagotchi-plugins/best_quickdic/)
    NOT whitelist-scoped: converts via hcxpcapngtool, then loops over
    every `*.txt` wordlist in a folder running hashcat against each in
    turn with a per-wordlist timeout, stopping at the first hit.

All four were real, working plugins the repo owner wrote for his own
fork - not upstream community plugins - so there is no "preserve the
original community config" story here (see NOTES.md instead of a
config.original.toml, which this suite deliberately does not ship).

====================================================================
THE SINGLE MOST IMPORTANT BEHAVIOR CHANGE IN THIS MERGE
====================================================================
best_quickdic.py argued in its own docstring that it didn't need an
authorized_networks-style allowlist, because it "only operates on a
handshake file already captured... not active behavior against any
network." That reasoning does NOT hold: a plugin that automatically
runs hashcat against the password of every single network a
pwnagotchi happens to pick up - not just ones the user owns - is an
unauthorized password-cracking attempt against other people's
networks, regardless of whether it ever touches the radio directly.

In this merged plugin, the SAME `authorized_networks` allowlist (the
same name/semantics as wifiJtest.py's allowlist (now in patrickato/test-plugins))
gates every actual hashcat invocation - plain pass AND rule pass -
full stop, no exceptions, no "but this part is just local file
processing" carve-out. An empty `authorized_networks` list (the
default) means hashcat is NEVER invoked by this plugin, against
anything. See NOTES.md for the full writeup of why best_quickdic's own
framing was overridden rather than preserved.

====================================================================
Pipeline (per captured handshake, on_handshake)
====================================================================
1. Classify (from HashFormatDetector's idea; runs for EVERY capture,
   regardless of authorized_networks - this step is read-only, never
   touches hashcat): inspect the AP's `encryption` field (after
   `_as_ap_dict()` normalization).
     - WEP               -> write a `.route` sidecar recommending
                             aircrack-ng, log, done.
     - open/no-encryption -> write a `.route` sidecar saying so, log,
                             done.
     - no `encryption` field at all (the bare-MAC-string AP shape -
                             see below) -> write a `.route` sidecar
                             saying classification could not be
                             determined, log, done. This is a judgment
                             call this merge makes beyond what any of
                             the four originals did (none of them
                             handled this shape at all) - see NOTES.md.
     - WPA/WPA2/PMKID/anything else -> proceed to step 2.
2. Allowlist gate: check the AP's SSID/BSSID against
   `authorized_networks` (empty by default = total inaction past this
   point, same semantics as wifiJtest.py). Not authorized -> log
   at INFO that this SSID was classified as crackable but is not
   authorized, write a `.route` sidecar noting this, stop. Authorized
   -> enqueue a job on the internal work queue and return immediately
   (on_handshake never blocks).
3. A single worker thread (started in on_loaded, stopped via a
   threading.Event in on_unload - same pattern as
   apprise_notify_ng.py's own queue.Queue() + one worker thread)
   processes jobs strictly one at a time:
     a. Convert via hcxpcapngtool to .hc22000 (convert_timeout_secs).
     b. Nothing usable produced -> log + stop (can genuinely happen
        even after classification looked promising).
     c. run_local=false -> stop here, .hc22000 left in export_dir for
        offline cracking, log + notify.
     d. Plain-wordlist pass: every *.txt in wordlist_folder (capped by
        max_wordlists_per_run, 0=no limit), per_wordlist_timeout_secs
        each, stop at first hit.
     e. Still nothing, and rules_file configured and exists -> a
        second pass over the same wordlists with -r rules_file
        applied, rule_pass_timeout_secs each.
     f. Log the outcome (cracked password, or genuinely not found),
        update the on-screen status + queue depth, fire a
        notification.
4. self.processed (a set of filenames already enqueued) means the
   same capture is never enqueued twice - same idea as all four
   originals, scoped here to the enqueue step only (classification
   itself always runs, per step 1 above).

====================================================================
Bugs found in the four originals, fixed here (see NOTES.md for the
full per-original breakdown)
====================================================================
1. `access_point` bare-MAC-string crash (all four) - every original
   called `.get()` directly on `access_point`, which raises
   AttributeError and silently drops the handshake if a bare-string AP
   ever comes through (confirmed real behavior of this fork's
   agent.py - see wifiJtest.py's own `_as_ap_dict`). Fixed with
   this plugin's own `_as_ap_dict()`.
2. Untrusted-SSID-into-filesystem-path risk (RuleMutationCrack,
   best_quickdic built `os.path.join(export_dir, f"{ssid}_...")`
   directly from the raw SSID, with zero sanitization - an SSID is an
   arbitrary up-to-32-byte string a nearby AP can set to anything,
   including path separators or `..` sequences). Fixed by reusing
   ClaudeCrackAuto's own `_safe_name()` idea for EVERY SSID-derived
   path component in this merged plugin.
3. `os.makedirs(os.path.dirname(log_file), exist_ok=True)` with no
   guard for a `log_file` with no directory component - all four
   originals had this. `os.path.dirname("results.log")` is `""`, and
   `os.makedirs("", exist_ok=True)` raises `FileNotFoundError`,
   silently aborting the rest of `on_loaded` (including a trailing
   `self.ready = True`). Fixed: every `os.makedirs` call is guarded
   with `if dirname:`, and `on_loaded` is restructured so each
   independent setup step is in its own try/except, with
   `self.ready = True` set unconditionally at the very end.
4. ClaudeCrackAuto's `on_ui_setup` passed a FONT object
   (`fonts.Small`) where a COLOR constant belongs, and used a
   hardcoded, non-configurable, float-producing position
   (`ui.width() / 2 + 10, 0`). Fixed: real `BLACK` import from
   `pwnagotchi.ui.view`, and fully configurable `position_x`/
   `position_y` (plain ints).
5. None of the four had `on_config_changed`. Added here, reloading
   `authorized_networks` without a full plugin/process restart -
   exactly like wifiJtest.py.
6. Resource contention: each original spun up its own
   `threading.Thread` per handshake and/or its own separate
   `threading.Lock` - multiple concurrent hashcat/hcxpcapngtool
   subprocesses could end up fighting over one Pi's CPU. Fixed with a
   real single-worker job queue (apprise_notify_ng.py's
   queue.Queue() + one background thread + threading.Event pattern):
   `on_handshake` only classifies-and-enqueues (fast, never blocks);
   the ONE worker thread processes jobs strictly one at a time.
7. **best_quickdic's missing scoping** - see the big banner above.
   This is the single most important fix in this merge.
8. Inconsistent/scattered defaults across the four (different default
   wordlist paths, different log/export directory conventions, three
   different option names for the same allowlist concept - `whitelist`,
   `targets`, and no concept at all in two of them). Unified under one
   set of option names, `authorized_networks` as the canonical
   allowlist name (matching wifiJtest.py - see CONVENTIONS.md's
   new "authorized_networks allowlist" section).

====================================================================
Notifications
====================================================================
This plugin does NOT reimplement raw `requests.post` calls to ntfy/
Discord the way ClaudeCrackAuto did. It follows CONVENTIONS.md's
sibling-plugin-lookup pattern exactly: a configurable
`apprise_plugin_name` option (default "apprise_notify_ng"), looked up
via `plugins.loaded.get(...)`, calling that plugin's real
`_queue_notification(title, body, agent)` method if found. This is
gated entirely behind `notify_enabled` (default false) - nothing
happens, not even a lookup, unless a user opts in.

====================================================================
Framework facts this rebuild relies on (verified against the cloned
jayofelony/pwnagotchi fork)
====================================================================
- Config section name must exactly match this file's basename:
  [main.plugins.crack_pipeline_ng]. `__defaults__` is NEVER merged by
  this fork's loader - every option is read via `_opt()` against the
  module-level `DEFAULTS` dict.
- `on_loaded` runs in its own dedicated per-plugin thread with a
  try/except around the whole call that just logs-and-swallows any
  exception - a crash partway through does not take down the daemon,
  but DOES silently abort the rest of that call. Every independent
  setup step in `on_loaded` below is therefore in its own try/except,
  with `self.ready = True` set unconditionally at the end.
- Real hook signatures: on_loaded(self), on_ready(self, agent),
  on_ui_setup(self, ui), on_ui_update(self, ui), on_unload(self, ui),
  on_config_changed(self, config), on_handshake(self, agent, filename,
  access_point, client_station), on_webhook(self, path, request).
  Constructor is always cls() - zero arguments, no file/thread I/O in
  __init__, only in on_loaded.
- `subprocess.run([...], ...)` with a real argument list (never
  shell=True) is how hcxpcapngtool/hashcat are invoked - unchanged
  from the originals, it was already correct and safe.
"""

import collections
import glob
import html
import logging
import os
import queue
import re
import subprocess
import threading
import time
from datetime import datetime

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK

LOG = "[CrackPipelineNG]"
ELEMENT_NAME = "crack_pipeline_ng"

# Same MAC-matching approach as wifiJtest.py: a module-level
# regex, uppercase-normalized BSSID set, lowercase-normalized SSID set.
_MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")

# This fork's loader does NOT merge a plugin's __defaults__ into
# self.options - every option is read through _opt() against this dict.
DEFAULTS = {
    # The allowlist is the real safety gate, not this flag - matching
    # wifiJtest.py's own convention (enabled defaults true there
    # too, since an empty authorized_networks already means inaction).
    "enabled": True,
    # Empty by default = total inaction past classification, for every
    # SSID/BSSID this plugin ever sees. See the big banner above.
    "authorized_networks": [],
    # The four originals disagreed across /root/wordlists/,
    # /home/pi/wordlists/, with no attempt made to standardize. This
    # default matches this repo's established /etc/pwnagotchi/...-
    # rooted path convention (see crack_house_ng.py's saving_path,
    # bluetooth_recon_ng.py's device_table_path).
    "wordlist_folder": "/etc/pwnagotchi/wordlists",
    "max_wordlists_per_run": 0,  # 0 = no limit
    "per_wordlist_timeout_secs": 300,
    "rules_file": "/usr/share/hashcat/rules/best64.rule",
    "rule_pass_timeout_secs": 1800,
    "convert_timeout_secs": 120,
    "run_local": True,
    "export_dir": "/etc/pwnagotchi/crack_pipeline_ng/exports",
    "log_file": "/etc/pwnagotchi/crack_pipeline_ng/results.log",
    "notify_enabled": False,
    "apprise_plugin_name": "apprise_notify_ng",
    "show_on_screen": True,
    # (0, 150) checked against every other suite's own default
    # position in this repo at the time this was built (crack_house_ng
    # 180/61 + 0/30, bluetooth_recon_ng 0/15 + 0/25, handshaker_ng
    # 0/220, sigstr_ng 0/205, mad_hatter_ng -80/0, weather_ng 120/80,
    # timer_ng 0/60, fortune-thoughts 0/112, dossier 0/0) - no
    # collision found, but as always, check your own running suites.
    "position_x": 0,
    "position_y": 150,
}

HISTORY_MAXLEN = 50


def _opt_default(key):
    return DEFAULTS[key]


def _as_ap_dict(ap):
    """Normalize an AP/handshake argument that may be a full dict OR a
    bare MAC string (this fork's agent.py falls back to bare MAC
    strings for on_handshake when it can't match the AP in the live
    bettercap session at that exact moment - same shape
    wifiJtest.py's own _as_ap_dict normalizes for). All four
    original plugins being merged here called .get() directly on
    access_point with no such normalization - a real, confirmed crash
    risk this merge fixes for every classification/matching path."""
    if isinstance(ap, dict):
        return ap
    if ap is None:
        return {}
    return {"mac": str(ap), "hostname": ""}


def _safe_name(name):
    """Strip a string (an SSID, in practice) down to alnum-or-
    underscore only, for safe use as a filesystem path component.

    An SSID is an arbitrary up-to-32-byte string a nearby AP can set
    to anything, including '/' or '..' sequences - RuleMutationCrack
    and best_quickdic both built export paths directly from the raw
    SSID with no sanitization at all. This is the exact approach
    ClaudeCrackAuto's own _safe_name used (and the only one of the
    four originals that sanitized anything), reused here for every
    SSID-derived path component in this merged plugin, not just some
    of them."""
    cleaned = "".join(c if (c.isalnum() or c == "_") else "_" for c in str(name))
    return cleaned or "unknown"


def _classify(ap):
    """Pure, directly-unit-testable classification of a normalized AP
    dict. Returns (route, detail).

    route is one of: "wep", "open", "unknown", "wpa".

    This only ever inspects the `encryption` field - it never shells
    out to hcxpcapngtool (that happens later, in the worker thread's
    actual conversion step) and never runs hashcat. HashFormatDetector
    (one of the four originals) ran a full hcxpcapngtool conversion
    just to classify, which meant converting every capture twice
    (once to classify, once to actually crack). This merge classifies
    from metadata alone and only converts once, in the worker.

    The "unknown" route is a judgment call beyond what any of the four
    originals did: none of them handled an AP argument with no
    `encryption` field at all (the bare-MAC-string shape - see
    _as_ap_dict above) - they would have crashed on `.get()` before
    ever reaching this logic. Treating "no encryption field available"
    as "cannot classify, leave alone" rather than silently falling
    through to "open" (nothing to crack) was chosen deliberately: a
    wrong "open" classification would permanently skip a potentially
    crackable WPA network, which is a worse failure mode than a
    `.route` sidecar asking for manual inspection. See NOTES.md.
    """
    if "encryption" not in ap or ap.get("encryption") is None:
        return "unknown", (
            "No 'encryption' field available for this capture (this "
            "usually means on_handshake received a bare-MAC-string AP "
            "argument rather than a full AP dict) - cannot classify "
            "automatically. Inspect this capture manually."
        )

    encryption = str(ap.get("encryption") or "").upper()

    if "WEP" in encryption:
        return "wep", (
            "WEP-encrypted - not a hashcat job. Use aircrack-ng's "
            "IV-based statistical attack instead."
        )

    if not encryption or encryption in ("", "NONE", "OPEN"):
        return "open", "No encryption detected - open network, nothing to crack."

    return "wpa", f"{encryption} detected - WPA/WPA2/PMKID pathway (hashcat -m 22000)."


class CrackPipelineNG(plugins.Plugin):
    __author__ = (
        "merged/rebuilt for this project's plugin audit from patrickato's own "
        "ClaudeCrackAuto.py, RuleMutationCrack.py, HashFormatDetector.py, and "
        "best_quickdic.py"
    )
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Classifies every captured handshake (WEP/open/WPA), then for "
        "WPA/WPA2/PMKID captures from an authorized_networks-listed "
        "network, converts via hcxpcapngtool and runs a single-worker "
        "hashcat pipeline (plain wordlist pass, then a rule-mutation "
        "pass) or exports for offline cracking. Merges ClaudeCrackAuto, "
        "RuleMutationCrack, HashFormatDetector, and best_quickdic into "
        "one suite, with best_quickdic's missing authorization scoping "
        "corrected."
    )
    __name__ = "CrackPipelineNG"
    __help__ = __description__
    __dependencies__ = {"pip": [], "apt": ["hcxtools", "hashcat"]}

    def __init__(self):
        self.ready = False
        self._agent = None
        self._authorized_macs = set()
        self._authorized_ssids = set()
        self.processed = set()
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._worker_thread = None
        self._state_lock = threading.Lock()
        self._current_job = None
        self.last_result = "idle"
        self.history = collections.deque(maxlen=HISTORY_MAXLEN)

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _load_targets(self):
        """(Re)build the authorized-target lookup sets from config.
        Called from on_loaded and again from on_config_changed, so a
        config edit + reload takes effect without a full plugin/process
        restart - same as wifiJtest.py."""
        authorized_macs = set()
        authorized_ssids = set()
        for entry in self._opt("authorized_networks") or []:
            entry = str(entry).strip()
            if not entry:
                continue
            if _MAC_RE.match(entry):
                authorized_macs.add(entry.upper())
            else:
                authorized_ssids.add(entry.lower())
        self._authorized_macs = authorized_macs
        self._authorized_ssids = authorized_ssids

    def on_loaded(self):
        # Each independent setup step gets its own try/except: on_loaded
        # runs in its own thread with a try/except around the WHOLE call
        # that just logs-and-swallows any exception, which means a crash
        # partway through silently aborts everything after it - including
        # a trailing `self.ready = True`. One failing step must not take
        # the rest down. See the module docstring's framework-facts
        # section and NOTES.md bug #3.
        try:
            self._load_targets()
        except Exception as e:
            logging.warning(f"{LOG} could not load authorized_networks: {e}")

        try:
            export_dir = self._opt("export_dir")
            if export_dir:
                os.makedirs(export_dir, exist_ok=True)
        except Exception as e:
            logging.warning(f"{LOG} could not create export_dir: {e}")

        try:
            log_file = self._opt("log_file")
            dirname = os.path.dirname(log_file)
            # THE FIX: all four originals did os.makedirs(dirname, ...)
            # unconditionally. os.path.dirname("results.log") is "", and
            # os.makedirs("", exist_ok=True) raises FileNotFoundError -
            # guarding with `if dirname:` means a log_file with no
            # directory component (e.g. a relative "results.log") no
            # longer aborts the rest of on_loaded.
            if dirname:
                os.makedirs(dirname, exist_ok=True)
        except Exception as e:
            logging.warning(f"{LOG} could not create log_file directory: {e}")

        try:
            for tool in ("hcxpcapngtool", "hashcat"):
                if not self._tool_exists(tool):
                    logging.warning(
                        f"{LOG} required tool '{tool}' not found on PATH - "
                        f"related pipeline steps will fail/degrade until "
                        f"it's installed."
                    )
        except Exception as e:
            logging.warning(f"{LOG} tool-existence check failed: {e}")

        try:
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._worker_loop, daemon=True, name="CrackPipelineNGWorker"
            )
            self._worker_thread.start()
        except Exception as e:
            logging.warning(f"{LOG} could not start worker thread: {e}")

        total = len(self._authorized_macs) + len(self._authorized_ssids)
        if total == 0:
            logging.warning(
                f"{LOG} loaded, but authorized_networks is empty - every "
                f"capture will still be classified and get a .route "
                f"sidecar, but hashcat will NEVER be invoked against "
                f"anything until you add at least one BSSID or SSID to "
                f"config.toml."
            )
        else:
            logging.info(
                f"{LOG} loaded with {len(self._authorized_macs)} authorized "
                f"BSSID(s) and {len(self._authorized_ssids)} authorized "
                f"SSID(s)."
            )

        # Set unconditionally, regardless of which steps above succeeded -
        # see the module docstring's framework-facts section.
        self.ready = True

    def on_config_changed(self, config):
        self._load_targets()
        logging.info(f"{LOG} authorized target list reloaded")

    def on_ready(self, agent):
        self._agent = agent
        self._log_notify_sibling_status()

    def on_unload(self, ui):
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)
        if self._opt("show_on_screen"):
            try:
                with ui._lock:
                    try:
                        ui.remove_element(ELEMENT_NAME)
                    except KeyError:
                        pass
            except Exception as e:
                logging.warning(f"{LOG} error removing UI element on unload: {e}")
        logging.info(f"{LOG} plugin unloaded")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def on_ui_setup(self, ui):
        if not self._opt("show_on_screen"):
            return
        pos_x = self._opt("position_x")
        pos_y = self._opt("position_y")
        ui.add_element(
            ELEMENT_NAME,
            LabeledValue(
                color=BLACK,
                label="CRK",
                value="Q:0 idle",
                position=(pos_x, pos_y),
                label_font=fonts.Bold,
                text_font=fonts.Small,
            ),
        )

    def on_ui_update(self, ui):
        if not self._opt("show_on_screen"):
            return
        qdepth = self._queue.qsize()
        with self._state_lock:
            result = self.last_result
        ui.set(ELEMENT_NAME, f"Q:{qdepth} {result}"[:20])

    # ------------------------------------------------------------------
    # on_handshake - classify, gate, enqueue. Never blocks.
    # ------------------------------------------------------------------

    def on_handshake(self, agent, filename, access_point, client_station):
        if not self.ready or not self._opt("enabled"):
            return

        ap = _as_ap_dict(access_point)
        ssid = str(ap.get("hostname") or "")
        bssid = str(ap.get("mac") or "")

        route, detail = _classify(ap)

        if route == "wep":
            self._write_route(filename, "aircrack-ng", detail)
            self._log(f"'{ssid}' ({bssid}): classified WEP - {detail}")
            self._record_history(ssid, bssid, filename, "skipped-wep", detail)
            return

        if route == "open":
            self._write_route(filename, "none", detail)
            self._log(f"'{ssid}' ({bssid}): classified open/no-encryption - {detail}")
            self._record_history(ssid, bssid, filename, "skipped-open", detail)
            return

        if route == "unknown":
            self._write_route(filename, "unknown", detail)
            self._log(f"'{ssid}' ({bssid}): classification unknown - {detail}")
            self._record_history(ssid, bssid, filename, "skipped-unknown", detail)
            return

        # route == "wpa" from here on.
        if not self._is_authorized(ap):
            note = (
                f"'{ssid}' ({bssid}) classified as crackable ({detail}) but "
                f"is not in authorized_networks, so it is being left alone."
            )
            self._write_route(filename, "unauthorized", note)
            logging.info(f"{LOG} {note}")
            self._record_history(ssid, bssid, filename, "skipped-unauthorized", note)
            return

        if filename in self.processed:
            return
        self.processed.add(filename)
        self._enqueue(filename, ssid, bssid)

    def _is_authorized(self, ap):
        mac = str(ap.get("mac") or "").upper()
        hostname = str(ap.get("hostname") or "").lower()
        if mac and mac in self._authorized_macs:
            return True
        if hostname and hostname in self._authorized_ssids:
            return True
        return False

    def _enqueue(self, filename, ssid, bssid):
        job = {
            "filename": filename,
            "ssid": ssid,
            "bssid": bssid,
            "enqueued_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._queue.put(job)
        self._set_status("queued")
        self._log(
            f"'{ssid}' ({bssid}): enqueued {filename} for cracking "
            f"(queue depth={self._queue.qsize()})"
        )

    def _write_route(self, filename, route, detail):
        sidecar_path = filename + ".route"
        try:
            with open(sidecar_path, "w") as f:
                f.write(f"route: {route}\n")
                f.write(f"detail: {detail}\n")
                f.write(f"checked_at: {datetime.now().isoformat(timespec='seconds')}\n")
        except Exception as e:
            logging.warning(f"{LOG} could not write .route sidecar for {filename}: {e}")

    # ------------------------------------------------------------------
    # Worker thread - processes jobs ONE AT A TIME.
    # ------------------------------------------------------------------

    def _worker_loop(self):
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            with self._state_lock:
                self._current_job = job
            try:
                self._process_job(job)
            except Exception as e:
                logging.exception(f"{LOG} unhandled error processing job: {e}")
            finally:
                with self._state_lock:
                    self._current_job = None
                self._queue.task_done()

    def _process_job(self, job):
        pcap_path = job["filename"]
        ssid = job["ssid"]
        bssid = job["bssid"]

        if not os.path.exists(pcap_path):
            self._log(f"'{ssid}': {pcap_path} no longer exists, skipping")
            self._record_history(ssid, bssid, pcap_path, "skipped",
                                  "capture file no longer exists")
            return

        hc_path = os.path.join(
            self._opt("export_dir"),
            f"{_safe_name(ssid)}_{int(time.time())}.hc22000",
        )

        self._set_status("converting")
        if not self._convert(pcap_path, hc_path, self._opt("convert_timeout_secs")):
            self._log(f"'{ssid}': no crackable handshake/PMKID found in "
                      f"{pcap_path} after conversion")
            self._set_status("no handshake")
            self._record_history(
                ssid, bssid, pcap_path, "no_handshake",
                "hcxpcapngtool produced no usable .hc22000 data"
            )
            self._notify(
                "CrackPipelineNG: no crackable material",
                f"'{ssid}': capture looked promising but hcxpcapngtool "
                f"found nothing crackable."
            )
            return

        self._log(f"'{ssid}': converted to {hc_path}")

        if not self._opt("run_local"):
            self._set_status("exported")
            self._log(f"'{ssid}': run_local=false, exported for offline "
                      f"cracking: {hc_path}")
            self._record_history(ssid, bssid, pcap_path, "exported",
                                  f"exported to {hc_path}")
            self._notify(
                "CrackPipelineNG: exported",
                f"'{ssid}' handshake exported for offline cracking: {hc_path}"
            )
            return

        if not self._tool_exists("hashcat"):
            self._set_status("no hashcat")
            self._log(f"'{ssid}': hashcat not installed, left as export "
                      f"only: {hc_path}")
            self._record_history(ssid, bssid, pcap_path, "exported",
                                  "hashcat not installed, export-only")
            self._notify(
                "CrackPipelineNG: exported (no hashcat)",
                f"'{ssid}' exported, hashcat not installed: {hc_path}"
            )
            return

        wordlists = sorted(glob.glob(os.path.join(self._opt("wordlist_folder"), "*.txt")))
        max_wl = self._opt("max_wordlists_per_run")
        if max_wl and max_wl > 0:
            wordlists = wordlists[:max_wl]

        if not wordlists:
            self._set_status("no wordlist")
            self._log(f"'{ssid}': converted OK but no .txt wordlists found "
                      f"in {self._opt('wordlist_folder')}")
            self._record_history(ssid, bssid, pcap_path, "exported",
                                  "no wordlists configured, export-only")
            self._notify(
                "CrackPipelineNG: exported (no wordlists)",
                f"'{ssid}' exported, no wordlists found: {hc_path}"
            )
            return

        self._set_status("cracking")
        plain_timeout = self._opt("per_wordlist_timeout_secs")
        result = None
        for wl in wordlists:
            self._log(f"'{ssid}': plain pass trying {os.path.basename(wl)} "
                      f"(timeout={plain_timeout}s)")
            result = self._run_hashcat(hc_path, wl, plain_timeout)
            if result:
                break

        if not result:
            rules_file = self._opt("rules_file")
            if rules_file and os.path.exists(rules_file):
                rule_timeout = self._opt("rule_pass_timeout_secs")
                for wl in wordlists:
                    self._log(f"'{ssid}': rule pass trying "
                              f"{os.path.basename(wl)} with {rules_file} "
                              f"(timeout={rule_timeout}s)")
                    result = self._run_hashcat(hc_path, wl, rule_timeout,
                                                rule_file=rules_file)
                    if result:
                        break
            elif rules_file:
                self._log(f"'{ssid}': rules_file '{rules_file}' not found, "
                          f"skipping rule-based pass")

        if result:
            self._set_status("cracked!")
            self._log(f"'{ssid}': CRACKED -> {result}")
            self._record_history(ssid, bssid, pcap_path, "cracked",
                                  f"password: {result}")
            self._notify("CrackPipelineNG: cracked!",
                         f"'{ssid}' password found: {result}")
        else:
            self._set_status("not found")
            self._log(f"'{ssid}': not found in any configured "
                      f"wordlist/rule pass")
            self._record_history(ssid, bssid, pcap_path, "not_found",
                                  "not found in any configured wordlist/rule pass")
            self._notify("CrackPipelineNG: not found",
                         f"'{ssid}' handshake did not match any configured "
                         f"wordlist.")

    # ------------------------------------------------------------------
    # hashcat / hcxpcapngtool helpers
    # ------------------------------------------------------------------

    def _convert(self, pcap_path, hc_path, timeout):
        try:
            proc = subprocess.run(
                ["hcxpcapngtool", "-o", hc_path, pcap_path],
                capture_output=True, text=True, timeout=timeout,
            )
            if proc.returncode != 0:
                logging.debug(f"{LOG} hcxpcapngtool exit {proc.returncode}: "
                              f"{proc.stderr}")
            return os.path.exists(hc_path) and os.path.getsize(hc_path) > 0
        except subprocess.TimeoutExpired:
            logging.warning(f"{LOG} hcxpcapngtool timed out on {pcap_path}")
            return False
        except FileNotFoundError:
            logging.error(f"{LOG} hcxpcapngtool not installed")
            return False

    def _run_hashcat(self, hc_path, wordlist, timeout, rule_file=None):
        out_path = hc_path + ".cracked"
        cmd = [
            "hashcat", "-m", "22000", hc_path, wordlist,
            "--quiet", "--potfile-disable",
            "-o", out_path, "--outfile-format", "2",
        ]
        if rule_file:
            cmd += ["-r", rule_file]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                with open(out_path, "r") as f:
                    line = f.readline().strip()
                    return line or None
            return None
        except subprocess.TimeoutExpired:
            logging.warning(f"{LOG} hashcat timed out after {timeout}s "
                            f"against {wordlist}")
            return None
        except FileNotFoundError:
            logging.error(f"{LOG} hashcat not installed")
            return None

    # ------------------------------------------------------------------
    # Notifications (sibling-lookup convention - see CONVENTIONS.md)
    # ------------------------------------------------------------------

    def _log_notify_sibling_status(self):
        """Logged once, from on_ready (after every plugin has had a
        chance to load), gated behind notify_enabled - a suite that
        never uses the integration shouldn't warn about a sibling it
        was never going to look for. Matches mad-hatter-suite's own
        _log_notify_sibling_status()."""
        if not self._opt("notify_enabled"):
            return
        name = self._opt("apprise_plugin_name")
        target = plugins.loaded.get(name)
        if target is not None:
            logging.info(f"{LOG} notification sibling '{name}' found - "
                        f"results will be pushed through it.")
        else:
            logging.warning(
                f"{LOG} notify_enabled is true but '{name}' was not found "
                f"in plugins.loaded - no notifications will be sent. "
                f"Install/enable apprise-notify-suite, or set "
                f"apprise_plugin_name to match whatever notification "
                f"plugin you're actually running."
            )

    def _notify(self, title, body):
        if not self._opt("notify_enabled"):
            return
        try:
            target = plugins.loaded.get(self._opt("apprise_plugin_name"))
            if target is None:
                return
            queue_fn = getattr(target, "_queue_notification", None)
            if queue_fn is None:
                return
            queue_fn(title, body, self._agent)
        except (AttributeError, TypeError, Exception) as e:
            logging.warning(f"{LOG} notification failed: {e}")

    # ------------------------------------------------------------------
    # Status / history / logging
    # ------------------------------------------------------------------

    def _set_status(self, status):
        with self._state_lock:
            self.last_result = status

    def _record_history(self, ssid, bssid, filename, result, detail):
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "ssid": ssid,
            "bssid": bssid,
            "filename": filename,
            "result": result,
            "detail": detail,
        }
        with self._state_lock:
            self.history.append(entry)
            self.last_result = result

    def _log(self, message):
        line = f"{datetime.now().isoformat(timespec='seconds')} - {message}"
        logging.info(f"{LOG} {message}")
        try:
            with open(self._opt("log_file"), "a") as f:
                f.write(line + "\n")
        except Exception as e:
            logging.warning(f"{LOG} could not write log file: {e}")

    @staticmethod
    def _tool_exists(name):
        from shutil import which
        return which(name) is not None

    # ------------------------------------------------------------------
    # Webhook status page
    # ------------------------------------------------------------------

    def on_webhook(self, path, request):
        return self._status_page()

    def _status_page(self):
        qdepth = self._queue.qsize()
        with self._state_lock:
            current = dict(self._current_job) if self._current_job else None
            history = list(self.history)

        if current:
            current_html = (
                f"{html.escape(str(current.get('ssid', '')))} "
                f"({html.escape(str(current.get('bssid', '')))})"
            )
        else:
            current_html = "<i>idle</i>"

        rows = []
        for entry in reversed(history):
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(entry.get('time', '')))}</td>"
                f"<td>{html.escape(str(entry.get('ssid', '')))}</td>"
                f"<td>{html.escape(str(entry.get('bssid', '')))}</td>"
                f"<td>{html.escape(str(entry.get('result', '')))}</td>"
                f"<td>{html.escape(str(entry.get('detail', '')))}</td>"
                "</tr>"
            )
        rows_html = "".join(rows) or (
            "<tr><td colspan=5><i>no results yet</i></td></tr>"
        )

        return (
            "<html><head><title>CrackPipelineNG</title></head>"
            "<body style='font-family: sans-serif;'>"
            "<h2>CrackPipelineNG</h2>"
            f"<p>Queue depth: {qdepth}</p>"
            f"<p>Currently processing: {current_html}</p>"
            "<table border='1' cellpadding='6'>"
            "<tr><th>Time</th><th>SSID</th><th>BSSID</th><th>Result</th>"
            "<th>Detail</th></tr>"
            f"{rows_html}"
            "</table>"
            "</body></html>"
        )
