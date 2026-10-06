"""
passscroll.py - scroll the last N cracked passwords on the pwnagotchi screen.

Instead of showing only the single most-recent crack, this reads from one or
more result sources, dedupes them, keeps the most recent N, and cycles through
them on the display on a timer.

Sources default to the plugins-wip data-bus canonical paths (crack-house-suite
and the standard potfiles), so it integrates out of the box; all overridable.

Target: jayofelony 64-bit (Pi 4 + 3.5" MPI3501 TFT). Config section = file
basename (`passscroll`); every option read via DEFAULTS + _opt helpers because
this fork does NOT merge __defaults__.
"""

import os
import glob
import time
import logging
import binascii
import re

import pwnagotchi.plugins as plugins
import pwnagotchi.ui.fonts as fonts
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK

DEFAULTS = {
    # data-bus canonical cracked-password producers (see plugins-wip CLAUDE.md)
    "sources": [
        "/etc/pwnagotchi/handshakes/crack_house_ng.potfile",
        "/etc/pwnagotchi/handshakes/wpa-sec.cracked.potfile",
        "/etc/pwnagotchi/handshakes/my.potfile",
        "/etc/pwnagotchi/handshakes/OnlineHashCrack.cracked",
        "/etc/pwnagotchi/handshakes/*.cracked",
    ],
    "max_entries": 7,
    "scroll_interval": 5,
    "refresh_interval": 60,
    "show_ssid": True,
    "mask": False,
    "max_len": 22,
    "position": [5, 95],
    "label": "pwd",
}

_MAC = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


def _looks_mac(s):
    return bool(_MAC.match(s.strip()))


def _parse_line(line):
    """Return (ssid, password) from one result line, or None."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # hashcat 22000/2500 potfile: "<hash-with-*-fields>:<password>",
    # ESSID is the 6th '*'-separated field, hex-encoded.
    if "*" in line and ":" in line:
        h, _, pw = line.rpartition(":")
        fields = h.split("*")
        if len(fields) >= 6 and pw:
            try:
                essid = binascii.unhexlify(fields[5]).decode("utf-8", "replace")
                return (essid or "?", pw)
            except Exception:
                pass

    parts = line.split(":")
    # wpa-sec style: bssid:station:essid:password (password may contain ':')
    if len(parts) >= 4 and _looks_mac(parts[0]) and _looks_mac(parts[1]):
        return (parts[2] or "?", ":".join(parts[3:]))
    # plain essid:password
    if len(parts) == 2 and parts[1]:
        return (parts[0] or "?", parts[1])
    return None


class PassScroll(plugins.Plugin):
    __author__ = "patrickato + Claude"
    __version__ = "0.1.0"
    __license__ = "GPL3"
    __description__ = "Scrolls the most recent N cracked passwords from multiple sources on the display."

    def __init__(self):
        self._entries = []          # list of (ssid, password), oldest -> newest
        self._idx = 0
        self._last_scroll = 0
        self._last_refresh = 0
        self._elem = "passscroll"

    # --- option helpers (fork ignores __defaults__; read everything safely) ---
    def _opt(self, key):
        try:
            return self.options.get(key, DEFAULTS[key])
        except Exception:
            return DEFAULTS[key]

    def _opt_int(self, key):
        try:
            return int(self._opt(key))
        except Exception:
            return int(DEFAULTS[key])

    def _opt_bool(self, key):
        v = self._opt(key)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    def _load(self):
        found = []  # (mtime, ssid, pass)
        for pattern in self._opt("sources"):
            try:
                for path in glob.glob(pattern):
                    if not os.path.isfile(path):
                        continue
                    mtime = os.path.getmtime(path)
                    with open(path, "r", errors="replace") as f:
                        for line in f:
                            parsed = _parse_line(line)
                            if parsed:
                                found.append((mtime, parsed[0], parsed[1]))
            except Exception as e:
                logging.debug("[passscroll] source %s: %s", pattern, e)

        # newest files/lines last; dedupe on (ssid, pass) keeping the latest spot
        found.sort(key=lambda t: t[0])
        order = []
        for _, ssid, pw in found:
            key = (ssid, pw)
            if key in order:
                order.remove(key)
            order.append(key)

        n = max(1, self._opt_int("max_entries"))
        self._entries = order[-n:]
        if self._idx >= len(self._entries):
            self._idx = 0
        logging.info("[passscroll] loaded %d password(s)", len(self._entries))

    def on_loaded(self):
        logging.info("[passscroll] loaded")
        self._load()
        self._last_refresh = time.time()

    def on_ui_setup(self, ui):
        try:
            pos = self._opt("position")
            ui.add_element(
                self._elem,
                LabeledValue(
                    color=BLACK,
                    label=str(self._opt("label")),
                    value="-",
                    position=(int(pos[0]), int(pos[1])),
                    label_font=fonts.Bold,
                    text_font=fonts.Small,
                ),
            )
        except Exception as e:
            logging.error("[passscroll] ui_setup: %s", e)

    def _format(self, entry):
        ssid, pw = entry
        if self._opt_bool("mask"):
            pw = "*" * len(pw)
        text = "%s: %s" % (ssid, pw) if self._opt_bool("show_ssid") else pw
        mx = self._opt_int("max_len")
        return text if len(text) <= mx else text[: mx - 1] + "…"

    def on_ui_update(self, ui):
        now = time.time()

        if now - self._last_refresh >= self._opt_int("refresh_interval"):
            self._load()
            self._last_refresh = now

        if not self._entries:
            ui.set(self._elem, "none yet")
            return

        if now - self._last_scroll >= self._opt_int("scroll_interval"):
            self._idx = (self._idx + 1) % len(self._entries)
            self._last_scroll = now

        ui.set(self._elem, self._format(self._entries[self._idx]))

    def on_unload(self, ui):
        try:
            ui.remove_element(self._elem)
        except Exception:
            pass
        logging.info("[passscroll] unloaded")
