import logging
import os
import time

import pwnagotchi
import pwnagotchi.plugins as plugins
from pwnagotchi.ui.components import LabeledValue
from pwnagotchi.ui.view import BLACK
import pwnagotchi.ui.fonts as fonts


# This fork's loader does NOT merge a plugin's __defaults__ into
# self.options - every option must be read with a real fallback.
DEFAULTS = {
    "enabled": False,
    "orientation": "vertical",
    "files": [
        "/root/handshakes/wpa-sec.cracked.potfile",
        "/root/handshakes/my.potfile",
        "/root/handshakes/OnlineHashCrack.cracked",
    ],
    "saving_path": "/root/handshakes/crack_house_ng.potfile",
    "display_stats": True,
    # None on all four means: use the built-in default position for the
    # chosen orientation (see _default_position/_default_stats_position
    # below). The original picked a position by asking the UI object
    # which physical display model it was ("ui.is_waveshare_v2()" etc.)
    # - those methods live on the Display class, never on the plain
    # View object plugin hooks actually receive on this fork, so every
    # call crashed with AttributeError before a position was ever
    # chosen. Explicit, configurable positions replace that entirely.
    "position_x": None,
    "position_y": None,
    "stats_position_x": None,
    "stats_position_y": None,
    "iface": None,  # None = read from the real pwnagotchi config at runtime
}

ELEMENT_NAME = "crack_house_ng"
STATS_ELEMENT_NAME = "crack_house_ng_stats"


class CrackHouseNG(plugins.Plugin):
    __author__ = "fixed/extended by this project's plugin audit, from itsdarklikehell/V0rT3x's crack_house.py"
    __version__ = "1.0.0"
    __license__ = "GPL3"
    __description__ = (
        "Shows the closest nearby cracked network and password from your "
        "potfiles, with a fastest-glance nearby/total stat."
    )
    __name__ = "CrackHouseNG"
    __help__ = __description__

    def __init__(self):
        self._crack_menu = []  # ["hostname:password", ...], deduped
        self._best_rssi = None
        self._best_crack = None  # (hostname, password)
        self._total_crack_nearby = 0
        self._last_wifi_update = None

    def _opt(self, key):
        return self.options.get(key, DEFAULTS[key])

    def _iface(self):
        iface = self._opt("iface")
        if iface:
            return iface
        try:
            return pwnagotchi.config["main"]["iface"]
        except Exception:
            return "wlan0"

    def on_loaded(self):
        entries = set()

        # ADDED: also seed from our own previous saving_path output, so
        # a network cracked in a past run stays known even if the
        # source `files` are later rotated, cleared, or a wpa-sec
        # export simply isn't re-downloaded - the original (and the
        # first version of this rebuild) rebuilt the list from `files`
        # alone on every load, silently losing history whenever a
        # source file's own retention policy dropped an old entry.
        saving_path = self._opt("saving_path")
        try:
            with open(saving_path) as f:
                for line in f:
                    line = line.rstrip()
                    if ":" in line:
                        entries.add(line)
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.debug(
                "[CrackHouseNG] couldn't read previous %s: %s", saving_path, e
            )

        for file_path in self._opt("files"):
            try:
                entries.update(self._parse_file(file_path))
            except FileNotFoundError:
                # ADDED: the original's plain open() crashed on_loaded
                # entirely if even one configured file didn't exist yet
                # (a very likely first-run state - e.g. no
                # OnlineHashCrack.cracked file at all). Missing files are
                # now skipped, not fatal.
                logging.debug(
                    "[CrackHouseNG] configured file not found, skipping: %s",
                    file_path,
                )
            except Exception as e:
                logging.warning(
                    "[CrackHouseNG] couldn't read %s: %s", file_path, e
                )

        self._crack_menu = sorted(entries)

        try:
            with open(self._opt("saving_path"), "w") as f:
                for crack in self._crack_menu:
                    f.write(crack + "\n")
        except Exception as e:
            logging.warning(
                "[CrackHouseNG] couldn't write %s: %s", self._opt("saving_path"), e
            )

        logging.info(
            "[CrackHouseNG] plugin loaded, %d cracked network(s) known",
            len(self._crack_menu),
        )

    def _parse_file(self, file_path):
        """Yields 'hostname:password' strings for one potfile/.cracked file."""
        lower = file_path.lower()
        out = []
        if lower.endswith(".potfile"):
            # BSSID:STAMAC:ESSID:password (BSSID/STAMAC are plain hex, no
            # colons of their own) - split with maxsplit=3 so an ESSID
            # that happens to contain a colon doesn't shift the fields.
            with open(file_path) as f:
                for line in f:
                    parts = line.rstrip().split(":", 3)
                    if len(parts) < 4:
                        continue
                    hostname, password = parts[2], parts[3]
                    if hostname and password:
                        out.append(f"{hostname}:{password}")
        elif lower.endswith(".cracked"):
            # datetime,ESSID,BSSID,STAMAC,password,note
            with open(file_path) as f:
                for line in f:
                    parts = line.rstrip().split(",")
                    if len(parts) < 5:
                        continue
                    hostname, password = parts[1], parts[4]
                    if hostname and password:
                        out.append(f"{hostname}:{password}")
        else:
            logging.info(
                "[CrackHouseNG] %s type is not managed", os.path.splitext(file_path)
            )
        return out

    def _default_position(self):
        if self._opt("orientation") == "vertical":
            return (180, 61)
        return (0, 91)

    def _default_stats_position(self):
        return (0, 30)

    def on_ui_setup(self, ui):
        pos_x = self._opt("position_x")
        pos_y = self._opt("position_y")
        if pos_x is None or pos_y is None:
            pos_x, pos_y = self._default_position()

        ui.add_element(
            ELEMENT_NAME,
            LabeledValue(
                color=BLACK,
                label="",
                value="",
                position=(pos_x, pos_y),
                label_font=fonts.Bold,
                text_font=fonts.Small,
            ),
        )

        if self._opt("display_stats"):
            s_x = self._opt("stats_position_x")
            s_y = self._opt("stats_position_y")
            if s_x is None or s_y is None:
                s_x, s_y = self._default_stats_position()
            ui.add_element(
                STATS_ELEMENT_NAME,
                LabeledValue(
                    color=BLACK,
                    label="",
                    value="",
                    position=(s_x, s_y),
                    label_font=fonts.Bold,
                    text_font=fonts.Small,
                ),
            )

    def on_unload(self, ui):
        with ui._lock:
            for name in (ELEMENT_NAME, STATS_ELEMENT_NAME):
                try:
                    ui.remove_element(name)
                except KeyError:
                    pass
        logging.info("[CrackHouseNG] plugin unloaded")

    def on_wifi_update(self, agent, access_points):
        self._last_wifi_update = time.strftime("%H:%M", time.localtime())

        try:
            associated = "Not-Associated" not in os.popen(
                "iwconfig %s" % self._iface()
            ).read()
        except Exception as e:
            logging.debug("[CrackHouseNG] iwconfig check failed: %s", e)
            associated = False

        if associated:
            # Already associated to something - keep showing whatever was
            # last found rather than re-scanning (matches the original).
            return

        best_rssi = None
        best_crack = None
        count = 0
        for network in access_points:
            hostname = str(network.get("hostname", ""))
            rssi = network.get("rssi")
            for entry in self._crack_menu:
                cracked_host, _, password = entry.partition(":")
                # ADDED: case-insensitive comparison - the original (and
                # this rebuild's first version) compared hostnames with
                # a plain "==", so a network seen as "MyLab" would never
                # match a potfile entry saved as "mylab" or "MYLAB",
                # even though it's obviously the same network. The
                # matched entry's original casing is still what's shown.
                if hostname and hostname.lower() == cracked_host.lower():
                    count += 1
                    if best_rssi is None or (rssi is not None and rssi > best_rssi):
                        best_rssi = rssi
                        best_crack = (cracked_host, password)

        self._best_rssi = best_rssi
        self._best_crack = best_crack
        self._total_crack_nearby = count

    def on_ui_update(self, ui):
        if self._best_crack is not None:
            hostname, password = self._best_crack
            if self._opt("orientation") == "vertical":
                msg = f"{hostname}({self._best_rssi})\n{password}"
            else:
                msg = f"{hostname}:{password}"
            ui.set(ELEMENT_NAME, msg)
        else:
            # No nearby match - fall back to the most recently learned
            # crack from our own merged file, instead of the original's
            # hardcoded shell-out to a single specific upstream potfile
            # (which could be empty, missing, or simply not the file the
            # user actually configured).
            ui.set(ELEMENT_NAME, self._last_known_crack())

        if self._opt("display_stats"):
            when = self._last_wifi_update or "--:--"
            ui.set(
                STATS_ELEMENT_NAME,
                f"({when}){self._total_crack_nearby}/{len(self._crack_menu)}",
            )

    def _last_known_crack(self):
        if not self._crack_menu:
            return ""
        hostname, _, password = self._crack_menu[-1].partition(":")
        return f"{hostname}\n{password}"

    def on_webhook(self, path, request):
        logging.info("[CrackHouseNG] webhook pressed")
        return (
            "<html><body><h1>CrackHouseNG</h1>"
            f"<p>{len(self._crack_menu)} cracked network(s) loaded from "
            f"{len(self._opt('files'))} configured file(s).</p>"
            f"<p>Nearby right now: {self._total_crack_nearby}</p>"
            "</body></html>"
        )
